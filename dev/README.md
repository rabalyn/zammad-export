# Local test Zammad with dummy data

Try zammad-archive end to end without touching a real Zammad: a throwaway Zammad 7.2
(official [zammad-docker-compose](https://github.com/zammad/zammad-docker-compose), run with
podman-compose) with ~20 fictional German IT tickets.

```bash
dev/zammad-dev.sh up        # ~5 min the first time (image pulls), Zammad on http://localhost:8095
dev/zammad-dev.sh down      # remove everything again, including volumes
```

What you get:

- groups `1st Level`, `2nd Level`, `Netzwerk`, three customer organisations, six customers
- agent **Julia Agent** (`j.agent@example.com`, groups 1st + 2nd Level, *not* Netzwerk) and
  **Paul Netz**
- 20 tickets: HTML mails with signatures, signature logos (inline images), phone numbers,
  an IBAN, quoted replies, agent replies, internal notes with the actual fix, tags, log/PDF/conf
  attachments, dates spread over the last ~14 months
- `dev/.zammad/tokens.env`:
  - `ADMIN_TOKEN`: admin, only used for seeding
  - `AGENT_TOKEN`: Julia's personal token with `ticket.agent` only, like your real token.
    It sees 16 of the 20 tickets.
- Mailpit on http://localhost:8026: shows every mail Zammad "sent". Zammad reopens tickets
  whose mail delivery fails, so a working SMTP sink is needed for realistic data.

Nobody knows a password for the Zammad web UI (the admin gets a random one, the seeded users
none). Use the API tokens, or set a password with
`podman exec zammad-dev_zammad-railsserver_1 bundle exec rails r 'User.find_by(email: "admin@example.com").update!(password: "…")'`.

## Run the archive against it

In a separate checkout or directory (the compose stack uses `./data`, `.env`, `config.yaml`):

```bash
cp deploy/env.example .env && chmod 600 .env
# ZAMMAD_TOKEN = AGENT_TOKEN from dev/.zammad/tokens.env, plus the two random secrets (setup guide step 2)
cp config.example.yaml config.yaml
# zammad.url: http://host.containers.internal:8095   (the exporter runs in a container)
# queries: e.g.  title:*VPN* OR tags:vpn   /   tags:drucker   /   state.name:closed
mkdir -p data/archive/tickets data/meili local
podman-compose build exporter && podman-compose up -d
podman-compose run --rm exporter inspect --limit 50 --out local/inspect-out
podman-compose run --rm exporter meili-setup
podman-compose run --rm exporter run --full
# search UI: http://localhost:8080
```

Good searches to try: `zertifkat` (typo), `certbot` (only in an internal note),
`insufficient shadow storage` (only inside an attached log file), `Mustermann` (a customer's
real name: must find nothing).

## Files

| File | Purpose |
|---|---|
| `zammad-dev.sh` | fetch, start, wait, bootstrap, seed |
| `podmanize_compose.py` | patches the upstream compose file for podman-compose 1.x (see its docstring for the gaps) |
| `bootstrap.rb` | rails runner: finish setup wizard, admin + token, outgoing mail to Mailpit |
| `seed_zammad.py` | creates groups, customers, agents and tickets via the REST API |
| `finish_seed.rb` | rails runner: backdates tickets, creates Julia's `ticket.agent` token |
