"""Push the file archive into Meilisearch (docs/ADR/0007-search-engine-meilisearch.md)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .archive import Archive

log = logging.getLogger(__name__)

SEARCH_KEY_NAME = "zammad-archive-search"
STATE_FILE = ".last-index"
MAX_ATTACHMENT_TEXT = 100_000

INDEX_SETTINGS = {
    "searchableAttributes": [
        "title", "number", "content", "attachment_names", "attachment_text",
        "tags", "topics", "customer", "organization", "owner",
    ],
    "filterableAttributes": [
        "topics", "state", "group", "priority", "owner", "tags", "year",
        "created_ts", "customer", "organization",
    ],
    "sortableAttributes": ["created_ts", "updated_ts"],
    "faceting": {"maxValuesPerFacet": 200},
    "pagination": {"maxTotalHits": 5000},
    "typoTolerance": {"disableOnAttributes": ["number"]},
}


class MeiliError(Exception):
    pass


class Meili:
    def __init__(self, url: str, key: Optional[str], timeout: float = 60) -> None:
        self.base = url.rstrip("/")
        self.session = requests.Session()
        if key:
            self.session.headers["Authorization"] = f"Bearer {key}"
        self.timeout = timeout

    def call(self, method: str, path: str, ok404: bool = False, **kwargs: Any) -> Any:
        try:
            resp = self.session.request(method, self.base + path, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise MeiliError(f"{method} {path}: {exc}") from exc
        if ok404 and resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise MeiliError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else None

    def wait(self, task: Dict[str, Any], timeout: float = 600) -> Dict[str, Any]:
        uid = task.get("taskUid", task.get("uid"))
        deadline = time.time() + timeout
        while True:
            info = self.call("GET", f"/tasks/{uid}")
            if info["status"] in ("succeeded", "failed", "canceled"):
                if info["status"] != "succeeded":
                    raise MeiliError(f"task {uid} {info['status']}: {info.get('error')}")
                return info
            if time.time() > deadline:
                raise MeiliError(f"task {uid} still {info['status']} after {timeout}s")
            time.sleep(0.5)

    def health(self) -> Dict[str, Any]:
        return self.call("GET", "/health")

    def ensure_index(self, uid: str) -> None:
        if self.call("GET", f"/indexes/{uid}", ok404=True) is None:
            self.wait(self.call("POST", "/indexes", json={"uid": uid, "primaryKey": "id"}))
        self.wait(self.call("PATCH", f"/indexes/{uid}/settings", json=INDEX_SETTINGS))

    def delete_index(self, uid: str) -> None:
        task = self.call("DELETE", f"/indexes/{uid}", ok404=True)
        if task:
            self.wait(task)

    def ensure_search_key(self, uid: str) -> str:
        keys = self.call("GET", "/keys", params={"limit": 1000})["results"]
        for k in keys:
            if k.get("name") == SEARCH_KEY_NAME and k.get("indexes") == [uid] and k.get("actions") == ["search"]:
                return k["key"]
        created = self.call("POST", "/keys", json={
            "name": SEARCH_KEY_NAME,
            "description": "read-only key for the zammad archive web UI",
            "actions": ["search"], "indexes": [uid], "expiresAt": None,
        })
        return created["key"]


def _ts(value: Any) -> Optional[int]:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def build_document(ticket_dir: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    t = data["ticket"]
    arch = data.get("archive") or {}
    parts, att_names, att_text, attachments = [], [], [], []
    for a in data.get("articles") or []:
        parts.append("\n".join(x for x in (a.get("subject"), a.get("body")) if x))
        for att in a.get("attachments") or []:
            att_names.append(att.get("filename") or "")
            if att.get("text_path"):
                p = ticket_dir / att["text_path"]
                if p.is_file():
                    att_text.append(p.read_text(encoding="utf-8", errors="replace"))
            attachments.append({"filename": att.get("filename"), "size": att.get("size"),
                                "content_type": att.get("content_type"), "path": att.get("path")})
    created_ts = _ts(t.get("created_at"))
    return {
        "id": ticket_dir.name,
        "number": str(t.get("number") or ticket_dir.name),
        "title": t.get("title") or "",
        "state": t.get("state"),
        "priority": t.get("priority"),
        "group": t.get("group"),
        "owner": t.get("owner"),
        "customer": t.get("customer"),
        "organization": t.get("organization"),
        "tags": t.get("tags") or [],
        "topics": arch.get("matched_queries") or [],
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
        "close_at": t.get("close_at"),
        "created_ts": created_ts,
        "updated_ts": _ts(t.get("updated_at")),
        "year": datetime.fromtimestamp(created_ts, timezone.utc).year if created_ts else None,
        "article_count": len(data.get("articles") or []),
        "content": "\n\n".join(parts),
        "attachment_names": att_names,
        "attachment_text": "\n\n".join(att_text)[:MAX_ATTACHMENT_TEXT],
        "attachments": attachments,
        "zammad_url": arch.get("zammad_url"),
    }


def run_index(cfg: Dict[str, Any], archive: Archive, meili: Meili, reset: bool = False,
              all_tickets: bool = False, batch_size: int = 200) -> Dict[str, int]:
    uid = cfg["meilisearch"]["index"]
    if reset:
        meili.delete_index(uid)
    meili.ensure_index(uid)

    state = archive.root / STATE_FILE
    since = 0.0
    if not (reset or all_tickets) and state.is_file():
        since = float(state.read_text().strip() or 0) - 3600  # overlap against clock skew
    started = time.time()

    batch: List[Dict[str, Any]] = []
    tasks = []
    count = 0
    for ticket_dir, data in archive.iter_tickets():
        if (ticket_dir / "ticket.json").stat().st_mtime < since:
            continue
        batch.append(build_document(ticket_dir, data))
        if len(batch) >= batch_size:
            tasks.append(meili.call("POST", f"/indexes/{uid}/documents", json=batch))
            count += len(batch)
            batch = []
    if batch:
        tasks.append(meili.call("POST", f"/indexes/{uid}/documents", json=batch))
        count += len(batch)
    for task in tasks:
        meili.wait(task)
    state.write_text(f"{started}\n")
    stats = meili.call("GET", f"/indexes/{uid}/stats")
    log.info("indexed %d tickets, index now holds %s documents", count, stats.get("numberOfDocuments"))
    return {"indexed": count, "total": stats.get("numberOfDocuments", 0)}


def write_web_config(path: Path, mcfg: Dict[str, Any], search_key: str) -> None:
    conf = {
        "meiliPath": mcfg["public_meili_path"],
        "index": mcfg["index"],
        "apiKey": search_key,
        "filesPath": mcfg["public_files_path"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// generated by `zammad-archive meili-setup`\nwindow.ZAMMAD_ARCHIVE = "
                    + json.dumps(conf, indent=2) + ";\n", encoding="utf-8")
