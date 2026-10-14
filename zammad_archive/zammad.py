"""Minimal Zammad REST API client (token auth, retries, paged search)."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class ZammadError(Exception):
    pass


class ZammadClient:
    def __init__(
        self,
        url: str,
        token: str,
        verify_tls: Union[bool, str] = True,
        timeout: float = 60,
        per_page: int = 100,
        max_retries: int = 5,
    ) -> None:
        self.base = url.rstrip("/")
        self.timeout = timeout
        self.per_page = per_page
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token token={token}",
            "Accept": "application/json",
            "User-Agent": "zammad-archive/0.1",
        })
        self.session.verify = verify_tls

    @classmethod
    def from_config(cls, zcfg: Dict[str, Any], token: str) -> "ZammadClient":
        return cls(
            zcfg["url"], token,
            verify_tls=zcfg.get("verify_tls", True),
            timeout=zcfg.get("timeout", 60),
            per_page=zcfg.get("per_page", 100),
        )

    def ticket_url(self, ticket_id: Any) -> str:
        return f"{self.base}/#ticket/zoom/{ticket_id}"

    # -- low level -------------------------------------------------------
    def _request(self, path: str, params: Optional[Dict[str, Any]] = None,
                 stream: bool = False) -> requests.Response:
        url = self.base + path
        for attempt in range(self.max_retries + 1):
            wait = min(2 ** attempt, 60)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout, stream=stream)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise ZammadError(f"GET {path}: {exc}") from exc
                log.warning("GET %s failed (%s), retry in %ss", path, exc, wait)
                time.sleep(wait)
                continue
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(int(retry_after), 300)
                log.warning("GET %s -> HTTP %s, retry in %ss", path, resp.status_code, wait)
                resp.close()
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                body = resp.text[:300]
                resp.close()
                raise ZammadError(f"GET {path} -> HTTP {resp.status_code}: {body}")
            return resp
        raise ZammadError(f"GET {path}: retries exhausted")  # pragma: no cover

    def _json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = self._request(path, params)
        try:
            return resp.json()
        except ValueError as exc:
            raise ZammadError(f"GET {path}: response is not JSON ({resp.text[:200]!r})") from exc

    # -- API -------------------------------------------------------------
    def me(self) -> Dict[str, Any]:
        return self._json("/api/v1/users/me")

    def search_tickets(self, query: str, max_results: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Yield expanded ticket objects for a Zammad search query, all pages."""
        seen = set()
        page = 1
        while True:
            data = self._json("/api/v1/tickets/search", {
                "query": query, "expand": "true", "page": page, "per_page": self.per_page,
            })
            tickets = self._normalise_search(data)
            new = [t for t in tickets if t.get("id") not in seen]
            for ticket in new:
                seen.add(ticket.get("id"))
                yield ticket
                if max_results and len(seen) >= max_results:
                    return
            if not new or len(tickets) < self.per_page:
                return
            page += 1

    @staticmethod
    def _normalise_search(data: Any) -> List[Dict[str, Any]]:
        # expand=true returns a list; older versions return {"tickets": [ids], "assets": {...}}
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)]
        if isinstance(data, dict):
            assets = (data.get("assets") or {}).get("Ticket") or {}
            return [assets[str(i)] for i in data.get("tickets") or [] if str(i) in assets]
        return []

    def articles(self, ticket_id: Any) -> List[Dict[str, Any]]:
        data = self._json(f"/api/v1/ticket_articles/by_ticket/{ticket_id}", {"expand": "true"})
        return data if isinstance(data, list) else []

    def tags(self, ticket_id: Any) -> List[str]:
        data = self._json("/api/v1/tags", {"object": "Ticket", "o_id": ticket_id})
        return list((data or {}).get("tags") or []) if isinstance(data, dict) else []

    def download_attachment(self, ticket_id: Any, article_id: Any, attachment_id: Any,
                            dest: Path) -> int:
        """Stream an attachment to dest (atomically). Returns bytes written."""
        resp = self._request(
            f"/api/v1/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}", stream=True)
        tmp = dest.with_name(dest.name + ".part")
        size = 0
        try:
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
                    size += len(chunk)
            os.replace(tmp, dest)
        finally:
            resp.close()
            if tmp.exists():
                tmp.unlink()
        return size
