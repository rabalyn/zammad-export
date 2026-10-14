# 0011 – Deployment: everything with podman-compose

- Status: accepted (supersedes [0008](0008-deployment-rootless-podman-rhel9.md))
- Date: 2026-10-14

## Context

ADR 0008 used Quadlet units for the containers and a Python venv on the host for the
exporter. We want a single, familiar way to run and operate everything: podman-compose.
Target stays RHEL 9 with rootless Podman and SELinux enforcing.

## Decision

- One `compose.yaml` in the repository root, run with **podman-compose** (RHEL 9: from EPEL,
  `dnf install podman-compose`) as a dedicated rootless user with linger.
- Services:
  - `meilisearch`: pinned image, data in `./data/meili` (`:Z`), not published to the host.
  - `web`: Caddy, search UI + attachment files + restricted Meilisearch proxy (ADR 0009),
    published on `${WEB_PORT:-8080}`.
  - `exporter`: built from the `Containerfile` (UBI 9 Python 3.12), profile `tools`, so
    `up` does not start it. Every CLI command runs as
    `podman-compose run --rm exporter <command>`.
- Secrets in `.env` next to `compose.yaml` (mode 600, gitignored), settings in `config.yaml`.
  The exporter reaches Meilisearch as `http://meilisearch:7700` (`MEILI_URL` override).
- Data layout inside the checkout (gitignored): `data/archive` (source of truth, back it up),
  `data/meili` (disposable), `local/` (inspect output, custom middleware files).
- Start at boot: `restart: always` + `systemctl --user enable podman-restart.service`
  (Podman restarts all containers with that policy).
- Nightly run: a systemd user timer runs `podman-compose run --rm exporter run`.

## Consequences

- Only `podman` and `podman-compose` on the host, no Python venv to maintain. Updating =
  `git pull && podman-compose build exporter && podman-compose up -d`.
- SELinux: `:Z` for private volumes, `:z` for `web/` and `data/archive`, which the exporter
  writes and Caddy reads.
- podman-compose is less complete than Docker Compose (we hit several gaps with the
  upstream Zammad compose file, see `dev/podmanize_compose.py`). Our `compose.yaml`
  deliberately uses only simple features: no nested variable defaults, no inline configs,
  no `depends_on` health conditions.

## Alternatives considered

- **Quadlet (ADR 0008)**: native systemd integration, but a second tool to learn, and
  the exporter needed a separate venv.
- **Docker Compose with the Docker daemon**: not wanted on RHEL, and needs root.
