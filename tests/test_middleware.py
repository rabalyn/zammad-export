import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from zammad_archive.config import DEFAULTS, _deep_merge
from zammad_archive.middleware import EMAIL_RE, IBAN_RE, PHONE_RE, Middleware, PeopleRegistry, Pseudonymiser

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

    def test_customer_domain_in_text(self):
        r = raw(1)
        r["articles"][2]["body"] = "<p>intranet.kunde.de und mail.gmx.de, intern: wiki.example.com</p>"
        r["articles"][2]["created_by"] = None
        r["articles"].append({"id": 15, "sender": "Customer", "type": "email", "from": "x@gmx.de",
                              "body": "hi", "content_type": "text/plain"})
        body = self.mw.process(r).bundle["articles"][2]["body"]
        self.assertRegex(body, r"^intranet\.Domain-[0-9a-f]{8} und mail\.gmx\.de, intern: wiki\.example\.com$")
        body = make_mw(scrub={"customer_domains": False}).process(r).bundle["articles"][2]["body"]
        self.assertIn("intranet.kunde.de", body)

    def test_agent_mail_via_shared_mailbox(self):
        r = raw(1)
        r["articles"][1]["from"] = "Julia Agent via Support <support@example.com>"
        r["articles"][1]["created_by"] = "p.netz@example.com"
        b = self.mw.process(r).bundle
        self.assertEqual(b["articles"][1]["from"], "p.netz")
        b = make_mw(agent_display="login").process(raw(1)).bundle
        self.assertEqual(b["articles"][1]["from"], "j.agent@example.com")

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


class CrossTicketNames(unittest.TestCase):
    """People known from the headers of one ticket are replaced in every other ticket."""

    def mention(self, body):
        r = raw(2)
        r["articles"][0]["body"] = body
        return r

    def test_name_from_other_ticket_is_replaced(self):
        mw = make_mw()
        customer = mw.process(raw(1)).bundle["ticket"]["customer"]
        r = self.mention("Schreibrechte für Max Mustermann und mich, Mustermann weiß Bescheid. "
                         "Mail an max.mustermann@kunde.de, Lisa Kollegin auch.")
        body = mw.process(r).bundle["articles"][0]["body"]
        for leak in ("max", "mustermann", "lisa", "kollegin", "kunde.de"):
            self.assertNotIn(leak, body.lower())
        self.assertTrue(body.startswith(f"Schreibrechte für {customer} und mich, {customer} weiß"), body)
        self.assertIn(f"Mail an {customer},", body)
        # the same text without the other ticket: the name stays (only known from ticket 1)
        self.assertIn("Max Mustermann", make_mw().process(r).bundle["articles"][0]["body"])
        mw = make_mw(cross_ticket_names=False)
        mw.process(raw(1))
        self.assertIsNone(mw.registry)
        self.assertIn("Max Mustermann", mw.process(r).bundle["articles"][0]["body"])

    def test_registry_file_has_no_clear_text_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".people-registry.json"
            cfg = _deep_merge(DEFAULTS, {"privacy": {"internal_domains": ["example.com"]}})
            cfg["_config_dir"] = d
            mw = Middleware(cfg, "secret", registry_path=path)
            customer = mw.process(raw(1)).bundle["ticket"]["customer"]
            mw.registry.save()
            text = path.read_text(encoding="utf-8").lower()
            for leak in ("max", "mustermann", "lisa", "kollegin", "kunde", "@", "partner"):
                self.assertNotIn(leak, text)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(customer, data["entries"].values())
            self.assertTrue(all(len(k) == 32 for k in data["entries"]))
            self.assertFalse(any(k in v for k, v in data["entries"].items()))  # keys are no pseudonym hex
            # a later run (new process) knows the person from the file alone
            body = Middleware(cfg, "secret", registry_path=path).process(
                self.mention("Bitte Mustermann fragen")).bundle["articles"][0]["body"]
            self.assertEqual(body, f"Bitte {customer} fragen")
            # another secret cannot use (or read) the registry
            body = Middleware(cfg, "other", registry_path=path).process(
                self.mention("Bitte Mustermann fragen")).bundle["articles"][0]["body"]
            self.assertEqual(body, "Bitte Mustermann fragen")

    def test_matching_rules(self):
        mw = make_mw(name_part_ignore=["kunde"])
        reg = mw.registry
        reg.add("thomas becker", "Person-aaaaaaaa")
        reg.add("becker", "Person-aaaaaaaa")
        reg.add("doe, john", "Person-bbbbbbbb")
        reg.add("kunde", "Person-cccccccc")  # e.g. registered before it was put on the ignore list
        reg.add("ute", "Person-dddddddd")    # shorter than name_part_min_length
        reg.add("kunde.de", "Domain-eeeeeeee")
        scrub = mw.scrub_known_people
        self.assertEqual(scrub("Thomas Becker, BECKER und Thomas_Becker.log"),
                         "Person-aaaaaaaa, Person-aaaaaaaa und Person-aaaaaaaa.log")
        self.assertEqual(scrub("Doe, John schrieb"), "Person-bbbbbbbb schrieb")
        self.assertEqual(scrub("Kunde Ute auf intranet.kunde.de"), "Kunde Ute auf intranet.Domain-eeeeeeee")
        self.assertEqual(scrub("Beckerstraße, Person-aaaaaaaa"), "Beckerstraße, Person-aaaaaaaa")

    def test_same_name_part_for_two_people(self):
        reg = PeopleRegistry(Pseudonymiser("s"))
        reg.add("becker", "Person-aaaaaaaa")
        reg.add("becker", "Person-bbbbbbbb")
        reg.add("becker", "Person-aaaaaaaa")
        self.assertEqual(reg.lookup("Becker"), "[NAME]")


if __name__ == "__main__":
    unittest.main()
