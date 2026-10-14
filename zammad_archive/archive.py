"""File archive: one folder per ticket (docs/ADR/0004-archive-storage-format.md)."""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

SCHEMA_VERSION = 1


def safe_filename(name: str, max_len: int = 120) -> str:
    name = unicodedata.normalize("NFKC", name or "file")
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" ._") or "file"
    name = re.sub(r"\s+", "_", name)
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: max_len - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else name[:max_len]
    return name


def safe_dirname(number: Any) -> str:
    value = re.sub(r"[^\w-]+", "_", str(number)).strip("_")
    if not value:
        raise ValueError(f"invalid ticket number {number!r}")
    return value


class Archive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.tickets = root / "tickets"
        self.tickets.mkdir(parents=True, exist_ok=True)

    def ticket_dir(self, number: Any) -> Path:
        return self.tickets / safe_dirname(number)

    def load(self, number: Any) -> Optional[Dict[str, Any]]:
        path = self.ticket_dir(number) / "ticket.json"
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def iter_tickets(self) -> Iterator[Tuple[Path, Dict[str, Any]]]:
        for d in sorted(self.tickets.iterdir()):
            f = d / "ticket.json"
            if d.is_dir() and not d.name.startswith(".") and f.is_file():
                with f.open(encoding="utf-8") as fh:
                    yield d, json.load(fh)

    @staticmethod
    def write_json(path: Path, data: Any) -> None:
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)

    def staging_dir(self, number: Any) -> Path:
        tmp = self.tickets / f".tmp-{safe_dirname(number)}"
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "attachments").mkdir(parents=True)
        return tmp

    def commit(self, number: Any, staging: Path) -> Path:
        """Swap the staging folder in place of the current ticket folder."""
        final = self.ticket_dir(number)
        old = self.tickets / f".old-{safe_dirname(number)}"
        if old.exists():
            shutil.rmtree(old)
        if final.exists():
            os.replace(final, old)
        os.replace(staging, final)
        if old.exists():
            shutil.rmtree(old)
        att = final / "attachments"
        if att.is_dir() and not any(att.iterdir()):
            att.rmdir()
        return final

    def cleanup_staging(self) -> None:
        for d in self.tickets.glob(".tmp-*"):
            shutil.rmtree(d, ignore_errors=True)
