"""Export: search Zammad -> middleware -> file archive."""
from __future__ import annotations

import logging
import os
import shutil
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .archive import SCHEMA_VERSION, Archive, safe_filename
from .extract import extract_text
from .middleware import Middleware
from .zammad import ZammadClient, ZammadError

log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_query(query: str, incremental_days: Optional[int]) -> str:
    if not incremental_days:
        return query
    return f"({query}) AND updated_at:>now-{int(incremental_days)}d"


def select_queries(queries: List[Dict[str, Any]], names: Optional[Iterable[str]]) -> List[Dict[str, Any]]:
    if not names:
        return queries
    wanted = set(names)
    unknown = wanted - {q["name"] for q in queries}
    if unknown:
        raise ValueError(f"unknown query name(s): {', '.join(sorted(unknown))}")
    return [q for q in queries if q["name"] in wanted]


def collect_tickets(client: ZammadClient, queries: List[Dict[str, Any]], incremental_days: Optional[int],
                    max_per_query: Optional[int] = None) -> "OrderedDict[Any, Tuple[Dict[str, Any], Set[str]]]":
    """Run all queries; a ticket found by several queries is returned once with all query names."""
    found: "OrderedDict[Any, Tuple[Dict[str, Any], Set[str]]]" = OrderedDict()
    for q in queries:
        query = build_query(q["query"], incremental_days)
        count = 0
        for ticket in client.search_tickets(query, max_results=max_per_query):
            count += 1
            entry = found.setdefault(ticket["id"], (ticket, set()))
            entry[1].add(q["name"])
        log.info("query %-20s %5d tickets  (%s)", q["name"], count, query)
    return found


class Exporter:
    def __init__(self, cfg: Dict[str, Any], client: ZammadClient, mw: Middleware, archive: Archive) -> None:
        self.cfg = cfg
        self.client = client
        self.mw = mw
        self.archive = archive
        self.att_cfg = cfg["attachments"]
        self.stats = {"found": 0, "written": 0, "unchanged": 0, "dropped": 0, "failed": 0,
                      "attachments_downloaded": 0, "attachments_reused": 0}

    def run(self, full: bool = False, dry_run: bool = False, query_names: Optional[List[str]] = None) -> Dict[str, int]:
        queries = select_queries(self.cfg["queries"], query_names)
        if not queries:
            raise ValueError("no queries configured")
        days = None if full else self.cfg["export"]["incremental_days"]
        self.archive.cleanup_staging()
        found = collect_tickets(self.client, queries, days)
        self.stats["found"] = len(found)
        delay = float(self.cfg["export"].get("delay_seconds") or 0)
        for ticket, names in found.values():
            try:
                self.export_one(ticket, names, full=full, dry_run=dry_run)
            except (ZammadError, OSError, ValueError) as exc:
                self.stats["failed"] += 1
                log.error("ticket %s failed: %s", ticket.get("number"), exc)
            if delay:
                time.sleep(delay)
        log.info("export done: %s", ", ".join(f"{k}={v}" for k, v in self.stats.items()))
        return self.stats

    def export_one(self, ticket: Dict[str, Any], names: Set[str], full: bool, dry_run: bool) -> None:
        number = ticket.get("number") or ticket.get("id")
        existing = self.archive.load(number)
        matched = sorted(names | set((existing or {}).get("archive", {}).get("matched_queries", [])))
        if existing and not full and existing["archive"].get("source_updated_at") == ticket.get("updated_at"):
            if matched != existing["archive"].get("matched_queries"):
                existing["archive"]["matched_queries"] = matched
                if not dry_run:
                    Archive.write_json(self.archive.ticket_dir(number) / "ticket.json", existing)
            self.stats["unchanged"] += 1
            return

        raw = {
            "ticket": ticket,
            "articles": self.client.articles(ticket["id"]),
            "tags": self.client.tags(ticket["id"]) if self.cfg["zammad"]["fetch_tags"] else [],
        }
        result = self.mw.process(raw)
        if result.bundle is None:
            self.stats["dropped"] += 1
            log.info("ticket %s dropped by middleware", number)
            return
        if dry_run:
            self.stats["written"] += 1
            log.info("[dry-run] would write ticket %s (%d articles)", number, len(result.bundle["articles"]))
            return

        staging = self.archive.staging_dir(number)
        try:
            old_dir = self.archive.ticket_dir(number)
            for article in result.bundle["articles"]:
                for att in article.get("attachments") or []:
                    self._store_attachment(ticket["id"], article["id"], att, staging, old_dir, result.scrub)
            data = {
                "schema_version": SCHEMA_VERSION,
                "ticket": result.bundle["ticket"],
                "articles": result.bundle["articles"],
                "archive": {
                    "exported_at": now_iso(),
                    "source_updated_at": ticket.get("updated_at"),
                    "matched_queries": matched,
                    "zammad_url": self.client.ticket_url(ticket["id"]),
                },
            }
            Archive.write_json(staging / "ticket.json", data)
            self.archive.commit(number, staging)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.stats["written"] += 1
        log.info("ticket %s written (%d articles)", number, len(result.bundle["articles"]))

    def _store_attachment(self, ticket_id: Any, article_id: Any, att: Dict[str, Any], staging: Path,
                          old_dir: Path, scrub) -> None:
        rel = Path("attachments") / safe_filename(f"{article_id}_{att['id']}_{att['filename']}")
        dest = staging / rel
        previous = old_dir / rel
        if previous.is_file() and previous.stat().st_size == att.get("size"):
            os.link(previous, dest) if _same_fs(previous, staging) else shutil.copy2(previous, dest)
            self.stats["attachments_reused"] += 1
        else:
            self.client.download_attachment(ticket_id, article_id, att["id"], dest)
            self.stats["attachments_downloaded"] += 1
        att["path"] = rel.as_posix()

        if not self.att_cfg.get("extract_text"):
            return
        txt_rel = rel.with_name(rel.name + ".txt")
        prev_txt = old_dir / txt_rel
        if previous.is_file() and prev_txt.is_file():
            shutil.copy2(prev_txt, staging / txt_rel)
            att["text_path"] = txt_rel.as_posix()
            return
        text = extract_text(dest, att.get("content_type") or "", self.att_cfg)
        if text:
            (staging / txt_rel).write_text(scrub(text), encoding="utf-8")
            att["text_path"] = txt_rel.as_posix()


def _same_fs(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False
