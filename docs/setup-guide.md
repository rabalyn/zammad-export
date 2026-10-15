# Setup guide – RHEL 9, rootless podman-compose

This guide follows the phases in [ADR/README.md](ADR/README.md). Everything runs with
**podman-compose** ([ADR 0011](ADR/0011-deployment-podman-compose.md)) as an unprivileged user.

Result on the server (all inside one git checkout):

```
/home/zarchive/zammad-archive/      ← git clone of this repository
  compose.yaml  Containerfile  deploy/  web/  zammad_archive/   (from git)
  .env                              ← secrets, mode 600 (not in git)
  config.yaml                       ← your settings (not in git)
  data/archive/tickets/             ← THE archive (back this up)
  data/meili/                       ← search index (disposable)
  local/                            ← inspect output, custom middleware (not in git)
```

| Service | What it does |
|---|---|
| `meilisearch` | search index, only reachable inside the compose network |
| `web` | Caddy: search UI, attachment files, restricted search proxy, port `WEB_PORT` (8080) |
| `exporter` | the `zammad-archive` CLI. `up` runs `check` once; real work via `podman-compose run --rm exporter <command>` |

> Want to try everything first without touching your work Zammad? Start a local Zammad
> with dummy tickets: [dev/README.md](../dev/README.md).

---

## 0. Requirements

- RHEL 9 with EPEL (podman-compose is in EPEL), as root:

  ```bash
  dnf install -y epel-release        # or: subscription-manager repos --enable codeready-builder… + EPEL rpm
  dnf install -y podman podman-compose git systemd-container
  ```

- Outbound HTTPS to your Zammad, and to `docker.io` / `registry.access.redhat.com` for
  the images (or mirror them and change `image:` / `FROM`).
- An API token: in Zammad, **Profile → Token Access → Create**, permission `ticket.agent`.
  The token sees exactly the groups your user sees ([ADR 0003](ADR/0003-zammad-api-access.md)).

> Before Phase 1: clarify with your data protection officer whether a long-term archive is OK
> (open questions in [ADR 0005](ADR/0005-privacy-middleware.md)).

## 1. Service user (as root)

```bash
useradd --create-home zarchive
loginctl enable-linger zarchive          # user services and containers run without a login
machinectl shell zarchive@               # a real user session (su/sudo -iu break systemctl --user)
```

All following steps run as `zarchive`.

## 2. Code, secrets, config

```bash
git clone https://github.com/rabalyn/zammad-export.git ~/zammad-archive
cd ~/zammad-archive

cp deploy/env.example .env && chmod 600 .env
sed -i "s/^ZAMMAD_ARCHIVE_SECRET=.*/ZAMMAD_ARCHIVE_SECRET=$(openssl rand -hex 32)/" .env
sed -i "s/^MEILI_MASTER_KEY=.*/MEILI_MASTER_KEY=$(openssl rand -hex 32)/" .env
vi .env                                  # paste ZAMMAD_TOKEN, maybe change WEB_PORT

cp config.example.yaml config.yaml
vi config.yaml                           # zammad.url, queries, privacy.internal_domains
mkdir -p data/archive/tickets data/meili local
```

With a company CA: copy the CA file into `local/` and set
`zammad.verify_tls: /work/local/company-ca.pem` (paths are as seen inside the container).

## 3. Build and first start

```bash
podman-compose build exporter
podman-compose up -d
podman-compose logs exporter             # output of the startup self-test (check)
```

`check` should show your Zammad login, `privacy: OK` and `meilisearch: available`.
Any command: `podman-compose run --rm exporter <command>`. A handy alias:

```bash
echo "alias za='podman-compose -f ~/zammad-archive/compose.yaml run --rm exporter'" >> ~/.bashrc && . ~/.bashrc
za --help
```

## 4. Phase 1 – inspect live data ([ADR 0006](ADR/0006-inspect-live-data-first.md))

```bash
za inspect --limit 30 --out local/inspect-out
za inspect --query 'title:*VPN*' --limit 20 --out local/inspect-out     # ad-hoc query
less local/inspect-out/report.md
```

1. Read `report.md`:
   - **Ticket/article fields**: what exists, how full, ⚠ where mail addresses/phone numbers live.
     `kept` shows your current allowlist.
   - **Article kinds**: are internal notes / phone notes worth keeping?
   - **Attachments**: types, sizes, how many would be kept.
   - **Possible personal data left**: what the middleware did not catch.
2. Compare a few `raw/<nr>.json` with `preview/<nr>.txt`.
3. Change `privacy:` / `attachments:` in `config.yaml`. For special cases write a custom hook
   (copy `examples/custom_middleware.py` to `local/`, set
   `custom_middleware: "local/custom_middleware.py:process"`). Repeat.
4. When happy: `rm -rf local/inspect-out` and write the decision into ADR 0005 ("Decision updates").

| Problem in preview | Setting |
|---|---|
| Field not needed / personal | remove from `ticket_fields` / `article_fields` |
| Custom field useful (e.g. `category`) | add it to `ticket_fields` (string values get scrubbed) |
| Colleague names should be hidden too | `agent_display: pseudonym` |
| Common word replaced by `Person-…` | add it to `name_part_ignore` |
| Customer numbers / addresses in text | `scrub.custom_patterns` |
| Names of people who are not in this ticket's headers | `scrub.custom_patterns` or a custom hook (known limitation, ADR 0005) |
| Quoted mails/signatures not cut | extend `quote_markers` / `signature_markers` (regex per line) |
| Too many/large attachments | `allowed_extensions`, `max_size_mb`, or `enabled: false` |

## 5. Phase 2 – search index and first full export

```bash
za meili-setup                 # index settings + search-only key -> web/config.js
za export --dry-run --full     # fetch + process, write nothing: check the counts
za run --full                  # export everything that matches, then index
```

Open `http://<server>:8080/` (after step 6 opened the firewall) and search.

Everything older than 6 months is already gone in Zammad, so do the first full run soon.
Very broad queries can hit Zammad's Elasticsearch paging cap (~10,000 hits). Then split
the query by date, e.g. `… AND created_at:[2026-01-01 TO 2026-03-31]`, as separate entries.

## 6. Phase 3 – access for colleagues ([ADR 0009](ADR/0009-search-ui-and-access-control.md))

Open the port (as root): `firewall-cmd --permanent --add-port=8080/tcp && firewall-cmd --reload`

Pick **one** access method:

**a) Basic auth in Caddy**

```bash
podman run --rm docker.io/library/caddy:2.10 caddy hash-password --plaintext 'a-long-password'
```

Uncomment the `basic_auth` block in `deploy/Caddyfile`, paste the hash (one line per user),
`podman-compose restart web`. Basic auth needs TLS (b or c).

**b) Company reverse proxy (recommended).** Let the existing proxy (with SSO/LDAP) forward
`https://tickets.example.com` to `http://<server>:8080`, and allow port 8080 only from the proxy:

```bash
firewall-cmd --permanent --remove-port=8080/tcp
firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=<proxy-ip> port port=8080 protocol=tcp accept'
firewall-cmd --reload
```

**c) TLS directly in Caddy** with a certificate from your company CA: put `cert.pem`/`key.pem`
into `local/tls/`, mount it in `compose.yaml` (`- ./local/tls:/tls:ro,Z` under `web`), change the
site address in the Caddyfile from `:8080` to `tickets.example.com:8443`, add
`tls /tls/cert.pem /tls/key.pem`, publish `8443:8443`, and forward 443 → 8443 as root:
`firewall-cmd --permanent --add-forward-port=port=443:proto=tcp:toport=8443`.

## 7. Start at boot and the nightly run

Containers with `restart: always` are restarted at boot by Podman's own service:

```bash
systemctl --user enable --now podman-restart.service
```

Nightly export + index:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/zammad-archive.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now zammad-archive.timer
systemctl --user list-timers
systemctl --user start zammad-archive.service      # run once now to test
journalctl --user -u zammad-archive -n 50
```

The service runs `podman-compose run --rm exporter run` = incremental `export` + `index`.
Tickets updated in the last `incremental_days` (default 7) are checked, unchanged ones are
skipped cheaply.

## 8. Operations

**Logs:** `journalctl --user -u zammad-archive`, `podman-compose logs web meilisearch`.
A failed nightly run leaves the service `failed` (exit code 1 if any ticket failed).

**Backup:** `data/archive/`, `config.yaml` and `.env` (contains the pseudonym secret).
`data/meili/` needs no backup: rebuild it with `za index --reset`.

**Update the app:**

```bash
cd ~/zammad-archive && git pull
podman-compose build exporter
podman-compose up -d                     # recreates changed services, reruns the self-test
```

Browsers revalidate the UI files on every load, so colleagues get UI changes immediately.

**Upgrade Meilisearch** ([ADR 0007](ADR/0007-search-engine-meilisearch.md)): change the tag in
`compose.yaml`, then:

```bash
podman-compose down
mv data/meili data/meili.old && mkdir data/meili     # delete data/meili.old later
podman-compose up -d && za meili-setup && za index --reset
```

`meili-setup` writes a new search key to `web/config.js`. Users only need to reload the page.

**Changed or removed queries:** archived tickets keep every query name they ever matched, so a
renamed or deleted query stays visible as a topic in the UI. Clean up with:

```bash
za prune-topics --dry-run    # report: changed tickets, orphans (match no configured query)
za prune-topics              # drop unknown query names, then: za index
```

Orphans are kept by default. `--remove-orphans` deletes them from the archive, then run
`za index --reset` so they leave the search index too.

**Removing tickets from the archive** (e.g. a retention rule): delete their folders in
`data/archive/tickets/`, then `za index --reset`. Plain `index` only adds and updates.
Don't move or replace the whole `data/archive` folder while `web` runs: the container keeps
serving the old folder until `podman-compose restart web`.

**Attachment text search (optional, [ADR 0010](ADR/0010-attachment-text-extraction.md)):**
plain text attachments (`.log`, `.txt`, `.conf`, …) are indexed as soon as
`attachments.extract_text: true`. For PDF/Office add a Tika service to `compose.yaml`:

```yaml
  tika:
    image: docker.io/apache/tika:4.0.0
    restart: always
```

and set `attachments.tika_url: http://tika:9998`. Text is extracted only for attachments
downloaded from now on.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `HTTP 401` from Zammad | token wrong/expired, or not in `.env` |
| `HTTP 403` on attachments | token lacks permissions for that group (ADR 0003), ask the Zammad admin |
| Query finds nothing / ignores syntax | test the query in Zammad's own search box first. Without Elasticsearch Zammad falls back to a simple DB search |
| Tickets suddenly have another state | normal Zammad behaviour, e.g. a failed mail delivery reopens a ticket. The archive stores what Zammad has |
| `Failed to connect to bus` | you used `su`/`sudo`. Use `machinectl shell zarchive@` |
| `Permission denied` in a container | SELinux: `ausearch -m avc -ts recent`. The `:Z`/`:z` volume flags must stay in `compose.yaml` |
| `web` starts slowly with a huge archive | `:z` relabels everything at each start. Relabel once with `chcon -R -t container_file_t data/archive` and drop `,z` |
| Web UI says "config.js fehlt" | run `za meili-setup` |
| `podman-compose up` hangs | podman-compose 1.x has trouble with `depends_on` ordering. Don't add `depends_on` with conditions to `compose.yaml` |
| Port < 1024 not possible | rootless. Use 8080 + reverse proxy / firewalld forward (step 6) |
