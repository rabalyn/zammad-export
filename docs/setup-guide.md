# Setup guide – RHEL 9, rootless Podman

This guide follows the phases in [ADR/README.md](ADR/README.md). Background for each
decision is in the linked ADRs.

Result on the server:

```
/home/zarchive/
  zammad-archive/
    app/                ← this repository (UI + Caddyfile are served from here)
    venv/               ← Python venv with the zammad-archive command
    config.yaml
    archive/tickets/    ← THE archive (back this up)
    meili_data/         ← search index (disposable)
  .config/zammad-archive/env              ← secrets, mode 600
  .config/containers/systemd/*.container  ← Quadlets: meilisearch, archive-web
  .config/systemd/user/zammad-archive.*   ← nightly timer
```

---

## 0. Requirements

- RHEL 9.2 or newer (Podman ≥ 4.4 for Quadlet): `podman --version`
- Packages (as root): `dnf install -y podman python3 python3-pip git systemd-container`
- Outbound HTTPS from the server to your Zammad, and to `docker.io` for the images
  (or mirror the two images into your registry and change `Image=`).
- An API token: in Zammad, **Profile → Token Access → Create**, permission `ticket.agent`.
  The token sees exactly the groups your user sees ([ADR 0003](ADR/0003-zammad-api-access.md)).

> Before Phase 1: clarify with your data protection officer whether a long-term archive is OK.
> See the open questions in [ADR 0005](ADR/0005-privacy-middleware.md).

## 1. Service user (as root)

```bash
useradd --create-home zarchive
loginctl enable-linger zarchive        # user services run without anybody logged in
```

Work as that user **with a real systemd user session**, otherwise `systemctl --user` fails:

```bash
machinectl shell zarchive@
```

(`su - zarchive` or `sudo -iu zarchive` do not set up `XDG_RUNTIME_DIR` / the user bus.)

All following steps run as `zarchive`.

## 2. Code and Python environment

```bash
mkdir -p ~/zammad-archive && cd ~/zammad-archive
git clone <your-git-remote>/zammad-export.git app      # or: rsync the folder to ~/zammad-archive/app
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install ./app
venv/bin/zammad-archive --version
```

Optional, run the tests: `cd app && ../venv/bin/python -m unittest && cd ..`

## 3. Secrets and config

```bash
mkdir -p ~/.config/zammad-archive && chmod 700 ~/.config/zammad-archive
cp app/deploy/env.example ~/.config/zammad-archive/env
chmod 600 ~/.config/zammad-archive/env
python3 -c "import secrets; print('ZAMMAD_ARCHIVE_SECRET=' + secrets.token_hex(32))"
python3 -c "import secrets; print('MEILI_MASTER_KEY=' + secrets.token_hex(32))"
vi ~/.config/zammad-archive/env          # paste token + the two generated values
```

The Meilisearch container reads the master key from a Podman secret. Create it from the same env file:

```bash
grep '^MEILI_MASTER_KEY=' ~/.config/zammad-archive/env | cut -d= -f2- | tr -d '\n' \
  | podman secret create meili_master_key -
```

Config:

```bash
cp app/config.example.yaml config.yaml
vi config.yaml
```

At minimum set `zammad.url`, your `queries`, and `privacy.internal_domains` (your company's
mail domains). With a company CA set `zammad.verify_tls: /etc/pki/tls/certs/ca-bundle.crt`
or the CA file.

To load the secrets into your shell for manual commands:

```bash
set -a; . ~/.config/zammad-archive/env; set +a
alias za='~/zammad-archive/venv/bin/zammad-archive -c ~/zammad-archive/config.yaml'
za check
```

`check` should show your Zammad login and `privacy: OK`. Meilisearch is not running yet.

## 4. Phase 1 – inspect live data ([ADR 0006](ADR/0006-inspect-live-data-first.md))

```bash
za inspect --limit 30 --out ~/inspect-out
# or a single ad-hoc query:
za inspect --query 'title:*VPN*' --limit 20 --out ~/inspect-out
```

Then:

1. Read `~/inspect-out/report.md` (copy it to your workstation, or `less` it).
   - **Ticket/article fields**: what exists, how full, ⚠ where mail addresses or phone numbers live.
     `kept` shows your current allowlist.
   - **Article kinds**: are internal notes / phone notes worth keeping?
   - **Attachments**: types, sizes, how many would be kept.
   - **Possible personal data left**: leftovers the middleware did not catch.
2. Compare a few `raw/<nr>.json` with `preview/<nr>.txt`.
3. Change `privacy:` / `attachments:` in `config.yaml`. For special cases write a
   custom hook (`examples/custom_middleware.py`, set `custom_middleware:`). Repeat.
4. When happy: `rm -rf ~/inspect-out` and write the decision into ADR 0005 ("Decision updates").

Useful tuning knobs:

| Problem in preview | Setting |
|---|---|
| Field not needed / personal | remove from `ticket_fields` / `article_fields` |
| Custom field useful (e.g. `category`) | add it to `ticket_fields` (string values get scrubbed) |
| Colleague names should be hidden too | `agent_display: pseudonym` |
| Common word replaced by `Person-…` | add it to `name_part_ignore` |
| Customer numbers / addresses in text | `scrub.custom_patterns` |
| Quoted mails/signatures not cut | extend `quote_markers` / `signature_markers` (regex per line) |
| Too many/large attachments | `allowed_extensions`, `max_size_mb`, or `enabled: false` |

## 5. Phase 2 – start Meilisearch and the web front ([ADR 0008](ADR/0008-deployment-rootless-podman-rhel9.md))

```bash
mkdir -p ~/zammad-archive/meili_data ~/zammad-archive/archive/tickets
mkdir -p ~/.config/containers/systemd
cp ~/zammad-archive/app/deploy/quadlet/* ~/.config/containers/systemd/

# check the generated units before starting
/usr/libexec/podman/quadlet -dryrun -user | less

systemctl --user daemon-reload
systemctl --user start meilisearch.service
systemctl --user status meilisearch.service
curl -s http://127.0.0.1:7700/health          # {"status":"available"}
```

Quadlet units are enabled through their `[Install]` section. They start at boot because of
linger. You do not need `systemctl enable` (it would fail for generated units).

Create the index, the search-only key and `app/web/config.js`:

```bash
za meili-setup
```

Start the web front:

```bash
systemctl --user start archive-web.service
curl -sI http://127.0.0.1:8080/ | head -1      # HTTP/1.1 200 OK
```

## 6. First full export

```bash
za export --dry-run --full      # fetch + process, write nothing: check the counts
za export --full                # the real thing (everything older than 6 months is already gone!)
za index
```

Open `http://<server>:8080/` (after step 7 opened the firewall) and search.

Very broad queries can hit the Elasticsearch paging cap of Zammad (~10,000 hits). Then
split the query by date, e.g. `… AND created_at:[2026-01-01 TO 2026-03-31]`, as separate entries.

## 7. Phase 3 – access for colleagues ([ADR 0009](ADR/0009-search-ui-and-access-control.md))

Open the port (as root):

```bash
firewall-cmd --permanent --add-port=8080/tcp && firewall-cmd --reload
```

Pick **one** access method:

**a) Basic auth in Caddy.** Create a password hash:

```bash
podman run --rm docker.io/library/caddy:2.10 caddy hash-password --plaintext 'a-long-password'
```

Uncomment the `basic_auth` block in `~/zammad-archive/app/deploy/Caddyfile`, paste the hash
(one line per user), then `systemctl --user restart archive-web`. Basic auth needs TLS (b or c).

**b) Company reverse proxy (recommended).** Let the existing proxy (with SSO/LDAP) forward
`https://tickets.example.com` to `http://<server>:8080`. Restrict port 8080 to the proxy's IP:

```bash
firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=<proxy-ip> port port=8080 protocol=tcp accept'
firewall-cmd --permanent --remove-port=8080/tcp && firewall-cmd --reload
```

**c) TLS directly in Caddy** with a certificate from your company CA: mount the cert/key
(`Volume=%h/zammad-archive/tls:/tls:ro,Z` in `archive-web.container`), change the site address
in the Caddyfile from `:8080` to `tickets.example.com:8443`, add `tls /tls/cert.pem /tls/key.pem`,
publish `8443:8443`, and forward 443 → 8443 as root:
`firewall-cmd --permanent --add-forward-port=port=443:proto=tcp:toport=8443`.

## 8. Nightly timer

```bash
mkdir -p ~/.config/systemd/user
cp ~/zammad-archive/app/deploy/systemd/zammad-archive.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now zammad-archive.timer
systemctl --user list-timers
systemctl --user start zammad-archive.service      # run once now to test
journalctl --user -u zammad-archive -n 50
```

The service runs `zammad-archive run` = incremental `export` + `index`. Tickets updated in
the last `incremental_days` (default 7) are checked. Unchanged ones are skipped cheaply.

## 9. Operations

**Logs**

```bash
journalctl --user -u zammad-archive -u meilisearch -u archive-web --since today
```

A failed run makes the service `failed` (exit code 1 if any ticket failed). Look for `ERROR` lines.

**Backup.** Back up `~/zammad-archive/archive/`, `config.yaml` and `~/.config/zammad-archive/env`
(contains the pseudonym secret). `meili_data/` needs no backup: rebuild it with `za index --reset`.

**Update the app**

```bash
cd ~/zammad-archive/app && git pull
~/zammad-archive/venv/bin/pip install --upgrade ~/zammad-archive/app
systemctl --user restart archive-web          # only if web/ or the Caddyfile changed
```

**Upgrade Meilisearch** ([ADR 0007](ADR/0007-search-engine-meilisearch.md)): change the tag in
`~/.config/containers/systemd/meilisearch.container`, then:

```bash
systemctl --user stop archive-web meilisearch
rm -rf ~/zammad-archive/meili_data/*
systemctl --user daemon-reload && systemctl --user start meilisearch archive-web
za meili-setup && za index --reset
```

`meili-setup` writes a new search key into `app/web/config.js`. Users only need to reload the page.

**Attachment text search (optional, [ADR 0010](ADR/0010-attachment-text-extraction.md)).**
Plain text attachments (`.log`, `.txt`, `.conf`, …) are indexed as soon as
`attachments.extract_text: true`. For PDF/Office add a Tika container,
`~/.config/containers/systemd/tika.container`:

```ini
[Container]
Image=docker.io/apache/tika:4.0.0
ContainerName=tika
PublishPort=127.0.0.1:9998:9998
[Install]
WantedBy=default.target
```

Then `daemon-reload`, `start tika`. Text is extracted only for attachments downloaded from now
on. To fill old ones, delete their `attachments/` folders and run `za export --full`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `HTTP 401` from Zammad | token wrong/expired, or `ZAMMAD_TOKEN` not loaded (`set -a; . env; set +a`) |
| `HTTP 403` on attachments | token lacks permissions for that group (ADR 0003). Ask the Zammad admin |
| Query finds nothing / ignores syntax | Zammad without Elasticsearch falls back to a simple DB search. Test the query in the Zammad search box |
| `Failed to connect to bus` | you used `su`/`sudo`. Use `machinectl shell zarchive@` |
| Container cannot read files, `Permission denied` | SELinux. Check `ausearch -m avc -ts recent`. The `:Z`/`:z` volume flags must be present |
| `archive-web` starts slowly with a huge archive | `:z` relabels everything at each start. Relabel once with `chcon -R -t container_file_t ~/zammad-archive/archive` and drop `,z` |
| Web UI says "config.js fehlt" | run `za meili-setup`, check `meilisearch.web_config_js` points to `app/web/config.js` |
| Search shows old data after editing tickets in the archive by hand | `za index --all` (changes are detected by file modification time) |
| Port < 1024 not possible | rootless. Use 8080 + reverse proxy / firewalld forward (step 7) |
