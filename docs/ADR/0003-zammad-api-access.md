# 0003 – Zammad API access, search queries and incremental export

- Status: accepted
- Date: 2026-10-14

## Context

We have no shell access to the work Zammad, only the web UI and the REST API.
We want to keep only tickets on certain topics, chosen by configurable search strings.

## Decision

**Authentication:** a personal API token (Profile → Token Access), permission
`ticket.agent`, sent as `Authorization: Token token=<TOKEN>`. The token is read from an
environment variable, never from the config file.

**Endpoints used:**

| Purpose | Endpoint |
|---|---|
| Search | `GET /api/v1/tickets/search?query=…&expand=true&page=N&per_page=100` |
| Articles | `GET /api/v1/ticket_articles/by_ticket/{ticket_id}?expand=true` |
| Tags (optional) | `GET /api/v1/tags?object=Ticket&o_id={ticket_id}` |
| Attachment | `GET /api/v1/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}` |

**Search queries** are named entries in `config.yaml`. The query string uses the Zammad
search syntax (Elasticsearch `query_string`), the same as the search box in the UI, e.g.
`title:*VPN* OR tags:netzwerk`, `group.name:"2nd Level" AND state.name:closed`. The query
name is stored with the ticket (`archive.matched_queries`) and becomes a "topic" filter in
the search UI. A ticket found by several queries is stored once.

**Incremental runs:** a nightly run adds `AND updated_at:>now-<N>d` to every query
(`export.incremental_days`, default 7, so a few failed nights do not lose data).
A ticket whose `updated_at` matches the archived copy is skipped. `--full` ignores both
and fetches everything that matches.

**Robustness:** retries with backoff on HTTP 429/5xx and network errors. Paging stops on
a short page or when a page brings no new ticket IDs (guards against servers that ignore
`page`).

## Consequences

- The token sees only tickets of groups the token owner can access. A colleague with
  other groups can run a second instance with their own token, or we ask for a
  dedicated technical user.
- The token user shows up in Zammad as the one who reads tickets via the API. That is
  fine, but tell your Zammad admin.
- Search needs Elasticsearch on the Zammad side (standard in production setups). Without
  it Zammad falls back to a simple DB search that does not understand the full syntax.
- Elasticsearch paging is capped (often at 10,000 hits). Split very broad queries, e.g. by
  `created_at:[2026-01-01 TO 2026-03-31]`.
- Attachment downloads have been reported to fail with tokens that lack permissions.
  Test this in phase 1 (inspect).
- The first full run must happen soon: everything older than 6 months is already gone.

## Alternatives considered

- **Rails console / DB dump** – complete, but needs server access we do not have.
- **Ticket overviews/reports (Excel export)** – no article bodies, no attachments.
