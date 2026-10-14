"""A tiny fake Zammad API for tests and local demos.

    python -m tests.fake_zammad 8099     # serves the fixture tickets below, token "test-token"
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "test-token"

LOG_FILE = b"2026-04-02 10:00:01 ERROR tls handshake failed: certificate expired (CN=vpn.example.com)\n"

TICKETS = {
    1: {"id": 1, "number": "26040210001", "title": "VPN bricht ab – Max Mustermann",
        "state": "closed", "priority": "2 normal", "group": "2nd Level", "owner": "j.agent@example.com",
        "customer": "max.mustermann@kunde.de", "organization": "Kunde GmbH", "created_by": "max.mustermann@kunde.de",
        "created_at": "2026-04-02T08:00:00.000Z", "updated_at": "2026-04-03T09:30:00.000Z",
        "close_at": "2026-04-03T09:30:00.000Z", "article_count": 4, "custom_contract_no": "V-123456"},
    2: {"id": 2, "number": "26040510002", "title": "Drucker druckt nicht",
        "state": "closed", "priority": "1 low", "group": "1st Level", "owner": "-",
        "customer": "erika.musterfrau@firma.de", "organization": None,
        "created_at": "2025-11-05T10:00:00.000Z", "updated_at": "2025-11-06T10:00:00.000Z",
        "close_at": None, "article_count": 1},
    3: {"id": 3, "number": "26040710003", "title": "VPN Drucker Kombi",
        "state": "open", "priority": "3 high", "group": "2nd Level", "owner": "j.agent@example.com",
        "customer": "info@partner.org", "organization": "Partner AG",
        "created_at": "2026-04-07T07:00:00.000Z", "updated_at": "2026-04-07T07:30:00.000Z",
        "close_at": None, "article_count": 1},
}

ARTICLES = {
    1: [
        {"id": 11, "ticket_id": 1, "sender": "Customer", "type": "email", "internal": False,
         "from": "Max Mustermann <max.mustermann@kunde.de>", "to": "support@example.com", "cc": "Lisa Kollegin <lisa.kollegin@kunde.de>",
         "subject": "VPN bricht ab", "content_type": "text/html", "created_at": "2026-04-02T08:00:00.000Z",
         "created_by": "max.mustermann@kunde.de", "message_id": "<abc@kunde.de>",
         "body": "<div>Hallo,</div><div>seit heute bricht das VPN nach 5 Minuten ab. "
                 "Bitte rufen Sie mich an: +49 171 1234567 oder 030 12345678.</div>"
                 "<div>Kollegin Lisa hat das gleiche Problem.</div>"
                 "<div><br></div><div>Mit freundlichen Grüßen</div><div>Max Mustermann</div>"
                 "<div>Kunde GmbH, Musterweg 1</div><script>alert(1)</script>",
         "attachments": [
             {"id": 101, "filename": "vpn-client.log", "size": len(LOG_FILE),
              "preferences": {"Content-Type": "text/plain"}},
             {"id": 102, "filename": "image001.png", "size": 2048,
              "preferences": {"Content-Type": "image/png", "Content-ID": "<image001@01D>",
                              "Content-Disposition": "inline"}},
         ]},
        {"id": 12, "ticket_id": 1, "sender": "Agent", "type": "email", "internal": False,
         "from": "Julia Agent <j.agent@example.com>", "to": "Max Mustermann <max.mustermann@kunde.de>", "cc": "",
         "subject": "AW: VPN bricht ab", "content_type": "text/html", "created_at": "2026-04-03T09:00:00.000Z",
         "body": "<p>Hallo Herr Mustermann,</p><p>das Zertifikat war abgelaufen, ich habe es erneuert.</p>"
                 "<p>Viele Grüße<br>Julia Agent<br>Tel. 0221 9876543</p>"
                 "<blockquote type=\"cite\">Am 02.04.2026 schrieb Max Mustermann:<br>seit heute bricht das VPN ab</blockquote>",
         "attachments": []},
        {"id": 13, "ticket_id": 1, "sender": "Agent", "type": "note", "internal": True,
         "from": "Julia Agent", "to": "", "cc": "", "subject": "",
         "content_type": "text/html", "created_at": "2026-04-03T09:10:00.000Z",
         "body": "<p>Lösung: <code>certbot renew</code> auf vpn01, IBAN DE89 3704 0044 0532 0130 00 stand im Mail-Footer.</p>",
         "attachments": []},
        {"id": 14, "ticket_id": 1, "sender": "System", "type": "email", "internal": False,
         "from": "Zammad <noreply@example.com>", "to": "max.mustermann@kunde.de", "cc": "",
         "subject": "Ticket geschlossen", "content_type": "text/plain",
         "created_at": "2026-04-03T09:30:00.000Z", "body": "Ihr Ticket wurde geschlossen.", "attachments": []},
    ],
    2: [
        {"id": 21, "ticket_id": 2, "sender": "Customer", "type": "email", "internal": False,
         "from": "erika.musterfrau@firma.de", "to": "support@example.com", "cc": "",
         "subject": "Drucker", "content_type": "text/plain", "created_at": "2025-11-05T10:00:00.000Z",
         "body": "Hallo,\nder Drucker im 2. OG druckt nicht. Gruß, Erika Musterfrau\n\n"
                 "-----Original Message-----\nFrom: someone@else.de\n> alte Mail",
         "attachments": []},
    ],
    3: [
        {"id": 31, "ticket_id": 3, "sender": "Customer", "type": "web", "internal": False,
         "from": "info@partner.org", "to": "", "cc": "", "subject": "Kombi",
         "content_type": "text/plain", "created_at": "2026-04-07T07:00:00.000Z",
         "body": "VPN und Drucker gehen beide nicht. Kontakt: hans.dampf@partner.org", "attachments": []},
    ],
}

TAGS = {1: ["vpn", "zertifikat"], 2: ["drucker"], 3: []}
ATTACHMENT_DATA = {(1, 11, 101): LOG_FILE, (1, 11, 102): b"\x89PNG fake"}


def _search(query: str):
    q = query.lower()
    ids = [i for i, t in TICKETS.items() if any(w in t["title"].lower() for w in ("vpn", "drucker") if w in q)]
    return ids


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):  # quiet
        pass

    def _send(self, status, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}
        Handler.calls.append(url.path)
        if self.headers.get("Authorization") != f"Token token={TOKEN}":
            return self._send(401, {"error": "authentication failed"})
        parts = url.path.strip("/").split("/")
        if url.path == "/api/v1/users/me":
            return self._send(200, {"login": "j.agent@example.com", "firstname": "Julia", "lastname": "Agent"})
        if url.path == "/api/v1/tickets/search":
            ids = _search(qs.get("query", ""))
            page, per = int(qs.get("page", 1)), int(qs.get("per_page", 50))
            chunk = ids[(page - 1) * per: page * per]
            return self._send(200, [TICKETS[i] for i in chunk])
        if parts[:4] == ["api", "v1", "ticket_articles", "by_ticket"]:
            return self._send(200, ARTICLES.get(int(parts[4]), []))
        if url.path == "/api/v1/tags":
            return self._send(200, {"tags": TAGS.get(int(qs["o_id"]), [])})
        if parts[:3] == ["api", "v1", "ticket_attachment"]:
            key = tuple(int(p) for p in parts[3:6])
            if key in ATTACHMENT_DATA:
                return self._send(200, ATTACHMENT_DATA[key], "application/octet-stream")
        return self._send(404, {"error": "not found"})


def serve(port: int = 0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    srv = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8099)
    print(f"fake zammad on http://127.0.0.1:{srv.server_address[1]}  token={TOKEN}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
