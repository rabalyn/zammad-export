# Zammad ticket archive – decisions and plan

Our Zammad deletes tickets older than 6 months. This project keeps a long-term,
searchable, privacy-reduced copy of the tickets that matter to us.

```
┌──────────┐  REST API   ┌──────────────────────────────┐   files    ┌───────────────┐
│  Zammad  │ ──────────► │ zammad-archive export        │ ─────────► │ archive/      │  source of truth
│ (work)   │  API token  │  search queries → middleware │            │  tickets/<nr>/│
└──────────┘             │  (pseudonymise / trim)       │            └──────┬────────┘
                         └──────────────────────────────┘                   │ zammad-archive index
                                                                            ▼
          colleagues ──► Caddy :8080 ── /        static search UI    ┌───────────────┐
                                    ── /meili/  (search-only key) ──►│ Meilisearch   │  disposable index
                                    ── /files/  attachments (ro)     └───────────────┘
```

## Decision records

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | accepted |
| [0002](0002-architecture-overview.md) | Architecture: file archive as source of truth, search index as cache | accepted |
| [0003](0003-zammad-api-access.md) | Zammad API access, search queries and incremental export | accepted |
| [0004](0004-archive-storage-format.md) | Archive storage format | accepted |
| [0005](0005-privacy-middleware.md) | Privacy middleware: allowlist, pseudonymisation, scrubbing | accepted |
| [0006](0006-inspect-live-data-first.md) | Inspect live data before deciding what to keep | accepted |
| [0007](0007-search-engine-meilisearch.md) | Search engine: Meilisearch | accepted |
| [0008](0008-deployment-rootless-podman-rhel9.md) | Deployment: rootless Podman (Quadlet) on RHEL 9 | accepted |
| [0009](0009-search-ui-and-access-control.md) | Search UI and access control | accepted |
| [0010](0010-attachment-text-extraction.md) | Attachment text extraction (optional, Apache Tika) | proposed |

New ADRs: copy [template.md](template.md), take the next number, add a row here.

## Implementation plan

Phases are done in order. Phase 1 must finish before any real data is stored long-term.

### Phase 0 – Clarify (before touching live data)
- [ ] Ask the data protection officer / IT why tickets are deleted after 6 months, and
      whether a privacy-reduced archive is acceptable (see ADR 0005). Write the answer into ADR 0005.
- [ ] Get an API token (Profile → Token Access) with the `ticket.agent` permission.
      The token only sees the groups your user can see (ADR 0003).
- [ ] Agree with colleagues on the first search queries (topics worth keeping).

### Phase 1 – Inspect live data (ADR 0006)
- [ ] Install the tool on a workstation or the server (see [setup guide](../setup-guide.md), steps 1–3).
- [ ] Run `zammad-archive inspect` with 20–50 tickets per query.
- [ ] Read `report.md`: which fields exist, how full they are, where personal data is.
- [ ] Compare `raw/` against `preview/` (output after the middleware).
- [ ] Tune `privacy:` and `attachments:` in `config.yaml` until the preview only has what is worth keeping.
- [ ] Delete the inspect output (`rm -rf inspect-out/`). It contains raw personal data.
- [ ] Record the final field choice as a short "Decision update" in ADR 0005.

### Phase 2 – Deploy on RHEL 9 (ADR 0008)
- [ ] Service user with linger, Podman Quadlets for Meilisearch + Caddy.
- [ ] Exporter in a Python venv, secrets in an env file (mode 600).
- [ ] `zammad-archive meili-setup`, then the first full export + index.
- [ ] systemd user timer: nightly export + index.

### Phase 3 – Share with colleagues (ADR 0009)
- [ ] Access control (basic auth in Caddy, or behind the company reverse proxy / SSO).
- [ ] TLS.
- [ ] Short intro for colleagues: search syntax, filters, pseudonyms.

### Phase 4 – Operate
- [ ] Back up `archive/` and the pseudonym secret. Meilisearch data needs no backup (rebuildable).
- [ ] Check the timer weekly at first (`journalctl --user -u zammad-archive`).
- [ ] Define how long the archive itself keeps tickets (open question in ADR 0005).
- [ ] Optional: attachment full-text search with Tika (ADR 0010).
