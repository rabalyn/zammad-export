"""End-to-end: fake Zammad over HTTP -> real client -> exporter -> archive -> index documents."""
import json
import tempfile
import unittest
from pathlib import Path

from zammad_archive.archive import Archive
from zammad_archive.config import DEFAULTS, _deep_merge
from zammad_archive.exporter import REGISTRY_FILE, Exporter, rescrub_archive
from zammad_archive.indexer import build_document
from zammad_archive.middleware import Middleware
from zammad_archive.zammad import ZammadClient, ZammadError

from . import fake_zammad as fz


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = fz.serve()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = _deep_merge(DEFAULTS, {
            "zammad": {"url": self.url, "per_page": 1},  # per_page 1 forces paging
            "queries": [{"name": "vpn", "query": "title:*VPN*"}, {"name": "drucker", "query": "Drucker"}],
            "privacy": {"internal_domains": ["example.com"]},
            "attachments": {"extract_text": True},
        })
        self.cfg["_config_dir"] = self.tmp.name
        self.archive = Archive(Path(self.tmp.name) / "archive")
        self.client = ZammadClient.from_config(self.cfg["zammad"], fz.TOKEN)
        fz.Handler.calls.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def exporter(self):
        mw = Middleware(self.cfg, "secret", registry_path=self.archive.root / REGISTRY_FILE)
        return Exporter(self.cfg, self.client, mw, self.archive)

    def test_bad_token(self):
        with self.assertRaises(ZammadError):
            ZammadClient(self.url, "wrong", max_retries=0).me()

    def test_export_and_incremental(self):
        stats = self.exporter().run(full=True)
        self.assertEqual((stats["found"], stats["written"], stats["failed"]), (3, 3, 0))
        self.assertEqual(stats["attachments_downloaded"], 1)

        d = self.archive.ticket_dir("26040210001")
        data = json.loads((d / "ticket.json").read_text())
        att = data["articles"][0]["attachments"][0]
        self.assertEqual((d / att["path"]).read_bytes(), fz.LOG_FILE)
        self.assertIn("certificate expired", (d / att["text_path"]).read_text())
        self.assertEqual(data["archive"]["matched_queries"], ["vpn"])
        self.assertEqual(data["archive"]["zammad_url"], f"{self.url}/#ticket/zoom/1")
        both = self.archive.load("26040710003")
        self.assertEqual(both["archive"]["matched_queries"], ["drucker", "vpn"])
        self.assertFalse(any(p.name.startswith(".") for p in self.archive.tickets.iterdir()))

        # second run: nothing changed -> no article/attachment calls
        fz.Handler.calls.clear()
        stats = self.exporter().run(full=False)
        self.assertEqual((stats["unchanged"], stats["written"]), (3, 0))
        self.assertFalse([c for c in fz.Handler.calls if "by_ticket" in c or "attachment" in c])

        # ticket updated in Zammad -> rewritten, attachment reused from disk
        fz.TICKETS[1]["updated_at"] = "2026-04-04T00:00:00.000Z"
        try:
            stats = self.exporter().run()
        finally:
            fz.TICKETS[1]["updated_at"] = "2026-04-03T09:30:00.000Z"
        self.assertEqual((stats["written"], stats["attachments_reused"], stats["attachments_downloaded"]), (1, 1, 0))

    def test_people_from_later_tickets_are_rescrubbed(self):
        # ticket 1 is exported first and mentions the customer of ticket 2 (exported last)
        note = fz.ARTICLES[1][2]
        old_body = note["body"]
        note["body"] = "<p>Erika Musterfrau hat das gleiche Problem.</p>"
        try:
            stats = self.exporter().run(full=True)
        finally:
            note["body"] = old_body
        self.assertGreaterEqual(stats["rescrubbed"], 1)
        text = (self.archive.ticket_dir("26040210001") / "ticket.json").read_text().lower()
        self.assertNotIn("musterfrau", text)
        customer = self.archive.load("26040510002")["ticket"]["customer"]
        self.assertIn(f"{customer.lower()} hat das gleiche problem", text)
        registry = (self.archive.root / REGISTRY_FILE).read_text().lower()
        for leak in ("musterfrau", "erika", "mustermann", "firma", "kunde"):
            self.assertNotIn(leak, registry)
        self.assertFalse(json.loads(registry)["pending_rescrub"])
        # nothing new learned -> no second re-scrub
        self.assertEqual(self.exporter().run(full=True)["rescrubbed"], 0)

    def test_rescrub_renames_attachments(self):
        d = self.archive.ticket_dir("1")
        (d / "attachments").mkdir(parents=True)
        (d / "attachments" / "5_6_Becker_log.txt").write_text("x")
        (d / "attachments" / "5_6_Becker_log.txt.txt").write_text("Thomas Becker war hier")
        Archive.write_json(d / "ticket.json", {"ticket": {"number": "1", "title": "Becker"}, "articles": [
            {"id": 5, "created_at": "Becker", "attachments": [
                {"id": 6, "filename": "Becker_log.txt", "path": "attachments/5_6_Becker_log.txt",
                 "text_path": "attachments/5_6_Becker_log.txt.txt"}]}]})
        mw = Middleware(self.cfg, "secret")
        mw.registry.add("thomas becker", "Person-aaaaaaaa")
        mw.registry.add("becker", "Person-aaaaaaaa")
        self.assertEqual(rescrub_archive(self.archive, mw), 1)
        data = self.archive.load("1")
        att = data["articles"][0]["attachments"][0]
        self.assertEqual(data["ticket"]["title"], "Person-aaaaaaaa")
        self.assertEqual(data["articles"][0]["created_at"], "Becker")  # structural field untouched
        self.assertEqual(att["filename"], "Person-aaaaaaaa_log.txt")
        self.assertEqual(att["path"], "attachments/5_6_Person-aaaaaaaa_log.txt")
        self.assertEqual((d / att["text_path"]).read_text(), "Person-aaaaaaaa war hier")
        self.assertEqual(sorted(p.name for p in (d / "attachments").iterdir()),
                         ["5_6_Person-aaaaaaaa_log.txt", "5_6_Person-aaaaaaaa_log.txt.txt"])
        self.assertEqual(rescrub_archive(self.archive, mw), 0)

    def test_dry_run_writes_nothing(self):
        stats = self.exporter().run(full=True, dry_run=True)
        self.assertEqual(stats["written"], 3)
        self.assertEqual(list(self.archive.tickets.iterdir()), [])

    def test_index_document(self):
        self.exporter().run(full=True)
        d = self.archive.ticket_dir("26040210001")
        doc = build_document(d, json.loads((d / "ticket.json").read_text()))
        self.assertEqual(doc["id"], "26040210001")
        self.assertEqual(doc["year"], 2026)
        self.assertEqual(doc["topics"], ["vpn"])
        self.assertIn("Zertifikat war abgelaufen", doc["content"])
        self.assertIn("certificate expired", doc["attachment_text"])
        self.assertEqual(doc["attachment_names"], ["vpn-client.log"])


if __name__ == "__main__":
    unittest.main()
