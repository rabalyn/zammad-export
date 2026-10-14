# 0004 – Archive storage format

- Status: accepted
- Date: 2026-10-14

## Context

The archive must last for years, be easy to back up, and be readable without our tools.

## Decision

One folder per ticket, named by ticket number:

```
archive/
  tickets/
    20261014000123/
      ticket.json                 # ticket + articles + archive metadata (after middleware)
      attachments/
        5012_881_screenshot.png   # <article_id>_<attachment_id>_<safe filename>
        5012_881_screenshot.png.txt   # optional extracted text (ADR 0010)
```

`ticket.json` structure:

```json
{
  "schema_version": 1,
  "ticket":   { "id": 4711, "number": "20261014000123", "title": "…", "state": "closed", … },
  "articles": [ { "id": 5012, "created_at": "…", "sender": "Customer", "from": "Person-1a2b3c4d",
                  "body": "plain text …", "attachments": [ { "id": 881, "filename": "…",
                  "size": 1234, "content_type": "image/png", "path": "attachments/5012_881_screenshot.png" } ] } ],
  "archive":  { "exported_at": "…", "source_updated_at": "…", "matched_queries": ["vpn"],
                "zammad_url": "https://zammad.example/#ticket/zoom/4711" }
}
```

- Article bodies are stored as **plain text** (HTML converted at export). Search and display
  need no HTML, and dropping it removes tracking pixels, inline styles and hidden content.
- Writes are atomic: everything is written to a temporary folder, then swapped in.
- Attachments already on disk are not downloaded again.
- `schema_version` lets future code migrate old files.

## Consequences

- `rsync`/`restic`/`borg` backups work directly.
- Thousands of small folders are fine for any normal file system (we expect thousands to
  tens of thousands of tickets, not millions).
- Formatting of HTML mails (tables, bold text) is lost. We accept this.

## Alternatives considered

- **SQLite** – a single file is nice, but attachments as BLOBs make it big and harder to
  inspect by hand. It can be generated from the files later if needed.
- **Keep raw HTML** – more faithful, but more personal data and an XSS risk in the UI.
