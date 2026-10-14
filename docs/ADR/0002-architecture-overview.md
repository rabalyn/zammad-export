# 0002 – Architecture: file archive as source of truth, search index as cache

- Status: accepted
- Date: 2026-10-14

## Context

We need (a) a durable copy of ticket histories including attachments and (b) a
convenient search for colleagues. Search engines change versions and data formats.
Archived tickets must stay readable for years.

## Decision

Three separate parts, each replaceable on its own:

1. **Exporter** (`zammad-archive export`, Python). It fetches tickets matching configured
   search queries from the Zammad REST API, runs them through the privacy middleware
   (ADR 0005) and writes them to the file archive.
2. **File archive** (`archive/tickets/<number>/`), plain JSON + attachment files (ADR 0004).
   This is the **only source of truth** and the only thing that needs a backup.
3. **Search** (`zammad-archive index` → Meilisearch, ADR 0007) plus a static web UI
   behind Caddy (ADR 0009). The index can be thrown away and rebuilt from the archive at
   any time (`zammad-archive index --reset`).

Raw (unprocessed) data is never written to the archive. Only `inspect` writes raw data,
into a separate, temporary folder (ADR 0006).

## Consequences

- Upgrading or replacing Meilisearch is easy: delete its data, reindex.
- The archive stays readable without any of our tools (`jq`, a file browser).
- If the privacy rules get stricter later, a re-export only helps for tickets still in
  Zammad. Data already archived (and already deleted in Zammad) must be cleaned with a
  one-off script. So rules should be strict from the start (ADR 0006).

## Alternatives considered

- **Store directly in the search engine only** – one component less, but data is tied to
  one engine version, and backups/migrations are harder.
- **Second Zammad instance as archive** – heavy, and import of history/attachments is not
  supported well.
- **PDF export per ticket** – good to read, bad to search and filter.
