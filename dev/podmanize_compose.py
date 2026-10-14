"""Make the official zammad-docker-compose file work with podman-compose 1.x.

    python3 dev/podmanize_compose.py <zammad-docker-compose/docker-compose.yml> <out.yml>

podman-compose 1.6 differs from Docker Compose in a few places that the upstream file uses:
- nested defaults ${A:-${B:-x}} and ${A:+... ${A}} are not parsed
- an empty ${VAR:-} in `command` becomes an empty "" argument (postgres refuses it)
- inline `configs: content:` is not mounted (the DB role script never runs)
- ${POSTGRES_USER:-zammad} is resolved against the service's own POSTGRES_USER
- short image names (elasticsearch:9) trigger an interactive registry prompt
- `depends_on` can make `up` wait forever for a container it has not created yet
  (services are not started in dependency order), so it is removed here
"""
from __future__ import annotations

import os
import re
import sys

import yaml


def main(src: str, dst: str) -> None:
    out_dir = os.path.dirname(os.path.abspath(dst))
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("${POSTGRES_SUPERUSER_PASS:-${POSTGRES_PASS:-zammad}}", "${POSTGRES_SUPERUSER_PASS:-zammad}")
    text = re.sub(r"^\s*\$\{POSTGRES_[A-Z_]+:\+-c .*\n", "", text, flags=re.M)
    text = re.sub(r"^\s*\$\{POSTGRES_EXTRA_OPTS:-\}\s*\n", "", text, flags=re.M)
    data = yaml.safe_load(text)

    for name, cfg in (data.pop("configs", None) or {}).items():
        path = os.path.join(out_dir, f"{name}.sh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(cfg["content"].replace("$$", "$"))
        os.chmod(path, 0o755)
    for svc in data["services"].values():
        for c in svc.pop("configs", None) or []:
            svc.setdefault("volumes", []).append(f"./{c['source']}.sh:{c['target']}:ro")
        # podman-compose may create a service before its dependencies and then waits forever;
        # dev/zammad-dev.sh starts the services one by one in the right order instead
        svc.pop("depends_on", None)
        image = svc.get("image", "")
        if image and not image.startswith("$") and "/" not in image.split(":", 1)[0]:
            svc["image"] = "docker.io/library/" + image

    pg = data["services"]["zammad-postgresql"]["environment"]
    pg.update(ZAMMAD_DB="zammad_production", ZAMMAD_DB_USER="zammad", ZAMMAD_DB_PASS="zammad")
    data["services"].pop("zammad-backup", None)  # not needed for a throwaway dev stack
    # SMTP sink: Zammad's outgoing mails must succeed, otherwise Zammad reopens the tickets
    # after the failed delivery. Sent mails can be read at http://localhost:8026
    data["services"]["mailpit"] = {
        "image": "docker.io/axllent/mailpit:v1.31.4",
        "ports": ["127.0.0.1:8026:8025"],
    }

    with open(dst, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
