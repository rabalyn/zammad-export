import copy
import json
import os
import tempfile
import unittest

from zammad_archive.config import DEFAULTS, _deep_merge
from zammad_archive.middleware import EMAIL_RE, IBAN_RE, PHONE_RE, Middleware

from . import fake_zammad as fz


def make_mw(**privacy):
    cfg = _deep_merge(DEFAULTS, {"privacy": dict({"internal_domains": ["example.com"]}, **privacy)})
    cfg["_config_dir"] = "."
    return Middleware(cfg, "secret")


def raw(ticket_id):
    return {"ticket": copy.deepcopy(fz.TICKETS[ticket_id]),
            "articles": copy.deepcopy(fz.ARTICLES[ticket_id]), "tags": list(fz.TAGS[ticket_id])}


def dump(bundle):
    return json.dumps(bundle, ensure_ascii=False)


class Regexes(unittest.TestCase):
    def test_phone(self):
        for s in ["+49 171 1234567", "0049 (0)221 98765-43", "030 12345678", "0221/9876543", "+49-30-1234567"]:
            self.assertTrue(PHONE_RE.search(f"Tel: {s}."), s)
        for s in ["Ticket 26040210001", "10.0.0.1", "01.10.2026", "v1.54.3", "Port 8080", "KD-123456", "2026-04-02"]:
            self.assertIsNone(PHONE_RE.search(s), s)

    def test_iban_and_email(self):
        self.assertTrue(IBAN_RE.search("DE89 3704 0044 0532 0130 00"))
        self.assertTrue(IBAN_RE.search("DE89370400440532013000"))
        self.assertEqual(EMAIL_RE.findall("x a.b+c@sub.firma.de, y"), ["a.b+c@sub.firma.de"])


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.mw = make_mw()

    def test_no_customer_data_left(self):
        for tid in fz.TICKETS:
            text = dump(self.mw.process(raw(tid)).bundle).lower()
            for leak in ["mustermann", "max.", "lisa", "kollegin", "erika", "musterfrau", "kunde.de",
                         "1234567", "12345678", "9876543", "de89", "musterweg", "hans.dampf", "kunde gmbh",
                         "partner ag", "v-123456"]:
                self.assertNotIn(leak, text, f"ticket {tid} leaks {leak!r}")

    def test_pseudonyms_are_stable_across_tickets_and_runs(self):
        a = self.mw.process(raw(1)).bundle
        b = make_mw().process(raw(1)).bundle
        self.assertEqual(a["ticket"]["customer"], b["ticket"]["customer"])
        self.assertEqual(a["articles"][0]["from"], a["ticket"]["customer"])
        self.assertTrue(a["ticket"]["customer"].startswith("Person-"))
        other = Middleware(_deep_merge(DEFAULTS, {"_config_dir": "."}), "other-secret")
        self.assertNotEqual(other.process(raw(1)).bundle["ticket"]["customer"], a["ticket"]["customer"])

    def test_agents(self):
        b = self.mw.process(raw(1)).bundle
        self.assertEqual(b["ticket"]["owner"], "j.agent")
        self.assertEqual(b["articles"][1]["from"], "j.agent")
        self.assertEqual(b["articles"][2]["from"], "Julia Agent")  # internal note, name only
        b = make_mw(agent_display="pseudonym").process(raw(1)).bundle
        self.assertTrue(b["ticket"]["owner"].startswith("Agent-"))
        self.assertNotIn("julia", dump(b).lower())

    def test_allowlist_and_article_rules(self):
        b = self.mw.process(raw(1)).bundle
        self.assertNotIn("custom_contract_no", b["ticket"])
        self.assertNotIn("message_id", b["articles"][0])
        self.assertEqual([a["id"] for a in b["articles"]], [11, 12, 13])  # system article dropped
        b = make_mw(skip_internal_articles=True).process(raw(1)).bundle
        self.assertEqual([a["id"] for a in b["articles"]], [11, 12])

    def test_body_processing(self):
        arts = self.mw.process(raw(1)).bundle["articles"]
        self.assertIn("[PHONE]", arts[0]["body"])
        self.assertNotIn("alert", arts[0]["body"])
        self.assertTrue(arts[0]["body"].rstrip().endswith("Mit freundlichen Grüßen"))
        self.assertNotIn("schrieb", arts[1]["body"])  # blockquote removed
        self.assertIn("certbot renew", arts[2]["body"])
        self.assertIn("[IBAN]", arts[2]["body"])

    def test_attachments_filtered(self):
        atts = self.mw.process(raw(1)).bundle["articles"][0]["attachments"]
        self.assertEqual([a["filename"] for a in atts], ["vpn-client.log"])  # inline image dropped
        mw = make_mw()
        mw.att["enabled"] = False
        self.assertEqual(mw.process(raw(1)).bundle["articles"][0]["attachments"], [])

    def test_custom_patterns_and_trim(self):
        mw = make_mw(scrub={"custom_patterns": [{"name": "cert", "pattern": "certbot"}]}, max_body_chars=10)
        body = mw.process(raw(1)).bundle["articles"][2]["body"]
        self.assertTrue(body.endswith("[… truncated]"))
        mw = make_mw(scrub={"custom_patterns": [{"name": "cert", "pattern": "certbot", "replacement": "[X]"}]})
        self.assertIn("[X] renew", mw.process(raw(1)).bundle["articles"][2]["body"])

    def test_custom_hook(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "hook.py"), "w") as fh:
                fh.write("def process(bundle, raw):\n"
                         "    if raw['ticket']['id'] == 2:\n        return None\n"
                         "    bundle['ticket']['hooked'] = True\n    return bundle\n")
            cfg = _deep_merge(DEFAULTS, {"custom_middleware": "hook.py:process"})
            cfg["_config_dir"] = d
            mw = Middleware(cfg, "s")
            self.assertTrue(mw.process(raw(1)).bundle["ticket"]["hooked"])
            self.assertIsNone(mw.process(raw(2)).bundle)


if __name__ == "__main__":
    unittest.main()
