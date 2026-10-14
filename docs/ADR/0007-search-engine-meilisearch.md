# 0007 – Search engine: Meilisearch

- Status: accepted
- Date: 2026-10-14

## Context

Colleagues want "Google-like" search: typo-tolerant, fast, with filters (topic, group,
state, year). Expected size: thousands to tens of thousands of tickets. One small server.

## Decision

Meilisearch (single binary/container, small RAM footprint, good defaults, typo tolerance,
facets, highlighting, a simple HTTP API with scoped API keys).

**Document model:** one document per ticket (users look for tickets, not single mails).

| Field | Use |
|---|---|
| `id` (= ticket number) | primary key |
| `title`, `content` (all article bodies), `attachment_text`, `tags`, `customer`, `organization`, `owner` | searchable, in this ranking order |
| `topics`, `state`, `group`, `priority`, `owner`, `tags`, `year` | filterable / facets |
| `created_ts`, `updated_ts` | sortable, range filter |
| `attachments`, `article_count`, `zammad_url` | metadata for the result list |

The full ticket with all articles is **not** returned by the search. The detail view loads
`/files/<number>/ticket.json` straight from the archive (ADR 0009). That keeps search
responses small: the result list only gets a cropped, highlighted snippet of `content`.

**Keys:**
- Master key: only on the server, in a Podman secret / env file.
- The indexer uses the master key (or an admin key).
- `zammad-archive meili-setup` creates (or reuses) a key named `zammad-archive-search` with
  only the `search` action on index `tickets`. This key goes into the browser
  (`web/config.js`). It cannot read other indexes or change data.
- Indexing is incremental: only tickets whose `ticket.json` changed since the last run
  (`archive/.last-index`) are pushed. `index --all` pushes everything, `index --reset` rebuilds.

**Versioning:** the image tag is pinned. Upgrades: stop, delete `meili_data`, start new
version, `zammad-archive meili-setup && zammad-archive index`. No dump/migration
needed, because the index is disposable (ADR 0002).

## Consequences

- Meilisearch indexes only the first 65,535 words per attribute. Very long tickets are
  only partly searchable. That is fine in practice. `max_body_chars` limits this anyway.
- The index holds the same data as the archive, so it is subject to the same privacy rules.

## Alternatives considered

- **SQLite FTS5 + Datasette** – even simpler, but no typo tolerance and a more technical UI.
- **OpenSearch/Elasticsearch** – more powerful, but heavy (JVM, RAM) for this size.
- **Typesense** – similar to Meilisearch. Either would work, and Meilisearch has the simpler key model.
