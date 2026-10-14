#!/usr/bin/env bash
# Local throwaway Zammad with dummy tickets, for trying zammad-archive end to end.
#
#   dev/zammad-dev.sh up       # fetch zammad-docker-compose, start it (podman-compose), seed data
#   dev/zammad-dev.sh tokens   # print the token file location
#   dev/zammad-dev.sh down     # stop and delete everything (volumes too)
#
# Zammad runs on http://localhost:${ZAMMAD_PORT:-8095}, sent mails at http://localhost:8026 (Mailpit).
# Tokens are written to dev/.zammad/tokens.env:
#   ADMIN_TOKEN  (admin, used for seeding)    AGENT_TOKEN  (Julia Agent, ticket.agent only – use this one)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HERE/.zammad"
REF="${ZAMMAD_COMPOSE_REF:-b51cba16ab6d753efcb6676a30afaf97753b72cb}"   # zammad-docker-compose, Zammad 7.2.2
PORT="${ZAMMAD_PORT:-8095}"
PROJECT=zammad-dev
compose() { (cd "$WORK" && podman-compose -p "$PROJECT" -f compose.podman.yml "$@" </dev/null); }
rails() { # rails <file.rb>
  podman cp "$1" "${PROJECT}_zammad-railsserver_1:/tmp/$(basename "$1")"
  podman exec -e "ZAMMAD_FQDN=localhost:$PORT" "${PROJECT}_zammad-railsserver_1" \
    bundle exec rails r "/tmp/$(basename "$1")" 2>/dev/null
}

up() {
  if [ ! -d "$WORK/upstream" ]; then
    git clone -q https://github.com/zammad/zammad-docker-compose.git "$WORK/upstream"
  fi
  git -C "$WORK/upstream" fetch -q origin && git -C "$WORK/upstream" checkout -q "$REF"
  python3 "$HERE/podmanize_compose.py" "$WORK/upstream/docker-compose.yml" "$WORK/compose.podman.yml"
  printf 'NGINX_EXPOSE_PORT=%s\nRESTART=no\n' "$PORT" > "$WORK/.env"

  # podman-compose 1.x does not start services in dependency order (podmanize_compose.py
  # removes depends_on), so start them one by one: infrastructure, wait for health, Zammad.
  for s in zammad-postgresql zammad-redis zammad-memcached zammad-elasticsearch mailpit; do compose up -d "$s" >/dev/null; done
  echo "waiting for postgres/redis/memcached to become healthy …"
  for _ in $(seq 1 60); do
    ok=1
    for s in postgresql redis memcached; do
      podman healthcheck run "${PROJECT}_zammad-${s}_1" >/dev/null 2>&1 || ok=0
    done
    [ "$ok" = 1 ] && break
    sleep 3
  done
  for s in init railsserver scheduler websocket nginx; do compose up -d "zammad-$s" >/dev/null; done

  echo "waiting for Zammad on http://localhost:$PORT …"
  for _ in $(seq 1 90); do
    state=$(podman inspect -f '{{.State.Status}} {{.State.ExitCode}}' "${PROJECT}_zammad-init_1" 2>/dev/null || true)
    if [ "$state" = "exited 0" ] && curl -fs -o /dev/null "http://127.0.0.1:$PORT/"; then break; fi
    sleep 5
  done
  [ "$state" = "exited 0" ] || { echo "zammad-init did not finish: $state"; exit 1; }

  touch "$WORK/tokens.env" && chmod 600 "$WORK/tokens.env"
  if ! grep -q '^ADMIN_TOKEN=' "$WORK/tokens.env"; then
    rails "$HERE/bootstrap.rb" | grep '^ADMIN_TOKEN=' >> "$WORK/tokens.env"
  fi
  if ! grep -q '^AGENT_TOKEN=' "$WORK/tokens.env"; then
    python3 "$HERE/seed_zammad.py" "http://127.0.0.1:$PORT" "$(sed -n 's/^ADMIN_TOKEN=//p' "$WORK/tokens.env")"
    rails "$HERE/finish_seed.rb" | grep '^AGENT_TOKEN=' >> "$WORK/tokens.env"
  fi
  echo "Zammad: http://localhost:$PORT   tokens: $WORK/tokens.env"
}

case "${1:-}" in
  up) up ;;
  tokens) echo "$WORK/tokens.env" ;;
  down) compose down -v >/dev/null 2>&1 || true; rm -f "$WORK/tokens.env"; echo "removed" ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
