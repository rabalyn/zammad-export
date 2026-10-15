"""Command line entry point: zammad-archive <command>."""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .archive import Archive
from .config import ConfigError, env_secret, load_config, require_zammad, resolve_path
from .exporter import REGISTRY_FILE, Exporter, prune_topics
from .indexer import Meili, MeiliError, run_index, write_web_config
from .inspector import run_inspect
from .middleware import Middleware
from .zammad import ZammadClient, ZammadError

log = logging.getLogger("zammad_archive")


def _client(cfg) -> ZammadClient:
    require_zammad(cfg)
    token = env_secret(cfg["zammad"]["token_env"], True, "Zammad API token")
    return ZammadClient.from_config(cfg["zammad"], token)


def _middleware(cfg, allow_ephemeral: bool = False) -> Middleware:
    name = cfg["privacy"]["secret_env"]
    secret = env_secret(name, not allow_ephemeral, "pseudonym secret")
    registry = resolve_path(cfg, cfg["export"]["archive_dir"]) / REGISTRY_FILE
    if not secret:
        log.warning("%s not set: using a random secret, pseudonyms will differ from the real archive", name)
        secret = secrets.token_hex(32)
        registry = None  # its hashes only match with the real secret
    return Middleware(cfg, secret, registry_path=registry)


def _archive(cfg) -> Archive:
    return Archive(resolve_path(cfg, cfg["export"]["archive_dir"]))


def _meili(cfg) -> Meili:
    m = cfg["meilisearch"]
    return Meili(m["url"], env_secret(m["key_env"], True, "Meilisearch key"))


def cmd_check(cfg, args) -> int:
    ok = True
    try:
        me = _client(cfg).me()
        print(f"zammad: OK, token belongs to {me.get('login')} ({me.get('firstname')} {me.get('lastname')})")
    except (ZammadError, ConfigError) as exc:
        ok = False
        print(f"zammad: FAILED – {exc}")
    try:
        _middleware(cfg)
        print("privacy: OK, pseudonym secret set")
    except ConfigError as exc:
        ok = False
        print(f"privacy: FAILED – {exc}")
    try:
        print(f"meilisearch: {_meili(cfg).health().get('status')}")
    except (MeiliError, ConfigError) as exc:
        print(f"meilisearch: not reachable – {exc} (only needed for index/meili-setup)")
    print(f"archive: {resolve_path(cfg, cfg['export']['archive_dir'])}")
    return 0 if ok else 1


def cmd_inspect(cfg, args) -> int:
    out = Path(args.out)
    report = run_inspect(cfg, _client(cfg), _middleware(cfg, allow_ephemeral=True), out,
                         limit=args.limit, query=args.query)
    print(f"report: {report}")
    print(f"raw API data: {out / 'raw'}   processed preview: {out / 'preview'}")
    print("⚠ the output contains unprocessed personal data – delete it when you are done.")
    return 0


def cmd_export(cfg, args) -> int:
    exporter = Exporter(cfg, _client(cfg), _middleware(cfg), _archive(cfg))
    stats = exporter.run(full=args.full, dry_run=args.dry_run, query_names=args.query_name)
    print(" ".join(f"{k}={v}" for k, v in stats.items()))
    return 1 if stats["failed"] else 0


def cmd_index(cfg, args) -> int:
    stats = run_index(cfg, _archive(cfg), _meili(cfg), reset=args.reset, all_tickets=args.all)
    print(f"indexed={stats['indexed']} total={stats['total']}")
    return 0


def cmd_meili_setup(cfg, args) -> int:
    m = cfg["meilisearch"]
    meili = _meili(cfg)
    meili.ensure_index(m["index"])
    key = meili.ensure_search_key(m["index"])
    target = Path(args.web_config) if args.web_config else resolve_path(cfg, m["web_config_js"])
    write_web_config(target, m, key)
    print(f"index '{m['index']}' ready, search-only key written to {target}")
    return 0


def cmd_prune_topics(cfg, args) -> int:
    names = [q["name"] for q in cfg["queries"]]
    if not names:
        raise ValueError("no queries configured – refusing to prune every topic")
    stats = prune_topics(_archive(cfg), names, remove_orphans=args.remove_orphans, dry_run=args.dry_run)
    print(" ".join(f"{k}={v}" for k, v in stats.items()) + (" (dry run, nothing written)" if args.dry_run else ""))
    if not args.dry_run and stats["removed"]:
        print("removed tickets are still in the search index: run `index --reset`")
    elif not args.dry_run and stats["changed"]:
        print("run `index` to update the search index")
    return 0


def cmd_run(cfg, args) -> int:
    args.dry_run = False
    args.query_name = None
    rc = cmd_export(cfg, args)
    args.reset = False
    args.all = False
    return cmd_index(cfg, args) or rc


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="zammad-archive", description=__doc__)
    ap.add_argument("-c", "--config", default="config.yaml", help="config file (default: config.yaml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="test Zammad token, secret and Meilisearch connection").set_defaults(func=cmd_check)

    p = sub.add_parser("inspect", help="sample live tickets: raw vs. processed + field report")
    p.add_argument("--limit", type=int, default=30, help="max tickets per query (default 30)")
    p.add_argument("--query", help="ad-hoc Zammad search query instead of the configured ones")
    p.add_argument("--out", default="inspect-out", help="output folder (default inspect-out)")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("export", help="export tickets into the archive")
    p.add_argument("--full", action="store_true", help="ignore incremental window and refetch everything")
    p.add_argument("--dry-run", action="store_true", help="fetch and process, but write nothing")
    p.add_argument("--query-name", action="append", help="only run this configured query (repeatable)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("index", help="push the archive into Meilisearch")
    p.add_argument("--all", action="store_true", help="push all tickets, not only changed ones")
    p.add_argument("--reset", action="store_true", help="delete and rebuild the index")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("meili-setup", help="create index + settings + search-only key, write web/config.js")
    p.add_argument("--web-config", help="where to write config.js (default meilisearch.web_config_js)")
    p.set_defaults(func=cmd_meili_setup)

    p = sub.add_parser("prune-topics", help="drop query names no longer in the config from archived tickets")
    p.add_argument("--dry-run", action="store_true", help="only report what would change")
    p.add_argument("--remove-orphans", action="store_true",
                   help="delete tickets that match no configured query from the archive")
    p.set_defaults(func=cmd_prune_topics)

    p = sub.add_parser("run", help="export, then index (what the nightly timer runs)")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_run)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stderr)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        cfg = load_config(args.config)
        return args.func(cfg, args)
    except (ConfigError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    except (ZammadError, MeiliError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
