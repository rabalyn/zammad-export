"""Optional text extraction from attachments (ADR 0010)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

PLAIN_EXTENSIONS = {"txt", "log", "csv", "json", "xml", "yaml", "yml", "conf", "cfg", "ini", "md"}


def extract_text(path: Path, content_type: str, att_cfg: Dict[str, Any]) -> Optional[str]:
    max_chars = int(att_cfg.get("extract_max_chars") or 50000)
    ext = path.suffix.lower().lstrip(".")
    if content_type.startswith("text/") or ext in PLAIN_EXTENSIONS:
        with path.open("rb") as fh:
            return fh.read(max_chars * 4).decode("utf-8", errors="replace")[:max_chars]
    if content_type.startswith(("image/", "video/", "audio/")):
        return None  # no OCR
    url = (att_cfg.get("tika_url") or "").rstrip("/")
    if not url:
        return None
    try:
        with path.open("rb") as fh:
            resp = requests.put(f"{url}/tika", data=fh, timeout=120,
                                headers={"Accept": "text/plain", "Content-Type": content_type or
                                         "application/octet-stream"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("text extraction failed for %s: %s", path.name, exc)
        return None
    return resp.text.strip()[:max_chars] or None
