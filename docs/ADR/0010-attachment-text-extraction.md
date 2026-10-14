# 0010 – Attachment text extraction (optional, Apache Tika)

- Status: proposed
- Date: 2026-10-14

## Context

Many solutions live in attachments (logs, PDFs, Office files). Without text extraction
only the file names are searchable.

## Decision (proposed)

- Optional, off by default: `attachments.extract_text: true` + `attachments.tika_url`.
- Plain text types (`text/*`, `.log`, `.txt`, `.csv`, `.json`, `.xml`, `.yaml`, `.conf`, `.ini`)
  are read directly, without Tika.
- Everything else is sent to an Apache Tika server (`PUT /tika`, `Accept: text/plain`),
  which can run as a third Quadlet container (`docker.io/apache/tika`, pinned tag,
  127.0.0.1 only).
- The text is stored next to the file (`<file>.txt`), cut to `extract_max_chars`, and is
  indexed as `attachment_text`.
- The regex scrubbing of ADR 0005 is applied to the extracted text before it is stored.
  Header-based name replacement is applied too.

## Consequences

- Attachment content becomes searchable, and so does any personal data in it that the
  scrubbing misses. Decide this together with ADR 0005's open question on attachments.
- Tika needs ~1 GB RAM. Run it only if needed.
