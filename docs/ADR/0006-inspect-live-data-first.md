# 0006 – Inspect live data before deciding what to keep

- Status: accepted
- Date: 2026-10-14

## Context

We do not yet know which fields our Zammad fills (custom fields, tags, types, how often
attachments appear, how long bodies are) and how well the middleware removes personal data.
Choosing the allowlist blind would either keep too much or lose useful data.

## Decision

The tool has an `inspect` command:

```
zammad-archive -c config.yaml inspect --limit 30 --out inspect-out/
```

It runs the configured queries (or `--query "…"`), fetches up to `--limit` tickets per
query **without writing to the archive**, and produces:

- `raw/<number>.json` – exactly what the API returned (ticket, articles, tags,
  attachment metadata; attachment files are **not** downloaded).
- `preview/<number>.json` and `preview/<number>.txt` – the same ticket after the
  middleware, as it would be archived. The `.txt` is a readable conversation view.
- `report.md` – field inventory: for every ticket and article field its fill rate,
  number of distinct values, short sample values, and a mark if it looks like it holds
  mail addresses or phone numbers. Plus article type/sender counts, body length stats,
  attachment types and sizes, and leftovers after the middleware (mail addresses, phone
  numbers, and name parts from the headers still found in the processed text).

Workflow: run inspect → read report → compare raw vs. preview → tune `config.yaml` → run
again → when happy, **delete `inspect-out/`** and write the decision into ADR 0005.

## Consequences

- `inspect-out/` holds unprocessed personal data. Run it on a trusted machine, do not
  commit it (it is in `.gitignore`), and delete it afterwards.
- Inspect is read-only towards Zammad and safe to repeat.
