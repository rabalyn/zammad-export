# 0008 – Deployment: rootless Podman (Quadlet) on RHEL 9

- Status: accepted
- Date: 2026-10-14

## Context

Target is a RHEL 9 server. Services should run rootless, start at boot, and be
manageable with the normal systemd tools. SELinux stays enforcing.

## Decision

- A dedicated user `zarchive` with `loginctl enable-linger`, so its user services run
  without a login session.
- **Quadlet** files in `~/.config/containers/systemd/` (Podman ≥ 4.4, RHEL ≥ 9.2):
  - `zammad-archive.network` – private network for both containers.
  - `meilisearch.container` – `getmeili/meilisearch` (pinned tag), data in
    `~/zammad-archive/meili_data` (`:Z`), master key from a Podman secret, port
    published **only on 127.0.0.1:7700** (for the indexer on the host).
  - `archive-web.container` – `caddy` serving the static UI, proxying a restricted set of
    Meilisearch paths, serving attachments read-only (`:ro,z`). Published on port 8080.
- The **exporter runs on the host** in a Python venv (RHEL 9's `python3` 3.9 is enough),
  started by a systemd user **timer** (`zammad-archive.timer` → `zammad-archive.service`,
  `Type=oneshot`: `export` then `index`). Secrets come from
  `~/.config/zammad-archive/env` (mode 600) via `EnvironmentFile=`.
- `podman auto-update` is **not** enabled. Image tags are pinned and updated on purpose (ADR 0007).

## Consequences

- No root daemon, no root containers. Ports ≥ 1024 only (8080). TLS/port 443 comes from
  an existing reverse proxy, or from `firewalld` port forwarding (see setup guide).
- SELinux: `:Z` for private volumes (Meilisearch data), `:z` for the archive, because the host
  exporter writes it and Caddy reads it.
- Logs: `journalctl --user -u meilisearch -u archive-web -u zammad-archive`.

## Alternatives considered

- **Exporter as a container too** – possible, but a oneshot Quadlet + timer is more fiddly,
  and a venv is easy to debug. Can be changed later without affecting anything else.
- **podman-compose / docker-compose** – not supported on RHEL 9, and Quadlet is the native way.
- **Root Podman / Docker** – not needed, larger attack surface.
