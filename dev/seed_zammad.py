"""Fill a local test Zammad with realistic dummy tickets (see dev/README.md).

    python3 dev/seed_zammad.py http://localhost:8095 <admin-token>

Creates groups, organisations, customers, one restricted agent and ~20 tickets with
HTML mails, signatures, quoted replies, internal notes, tags and attachments.
Everything is fictional. Re-running creates additional tickets.
"""
from __future__ import annotations

import base64
import json
import sys
from typing import Any, Dict, List

import requests

GROUPS = ["1st Level", "2nd Level", "Netzwerk"]

ORGS = ["Kunde GmbH", "Stadtwerke Musterstadt", "Beispiel AG"]

CUSTOMERS = [  # firstname, lastname, email, org
    ("Max", "Mustermann", "max.mustermann@kunde.de", "Kunde GmbH"),
    ("Erika", "Musterfrau", "erika.musterfrau@kunde.de", "Kunde GmbH"),
    ("Hans", "Meier", "h.meier@stadtwerke-musterstadt.de", "Stadtwerke Musterstadt"),
    ("Sabine", "Schulz", "sabine.schulz@stadtwerke-musterstadt.de", "Stadtwerke Musterstadt"),
    ("Thomas", "Becker", "thomas.becker@beispiel-ag.de", "Beispiel AG"),
    ("Anna", "Fischer", "a.fischer@beispiel-ag.de", "Beispiel AG"),
]

AGENTS = [  # firstname, lastname, email, groups
    ("Julia", "Agent", "j.agent@example.com", ["1st Level", "2nd Level"]),
    ("Paul", "Netz", "p.netz@example.com", ["Netzwerk", "2nd Level"]),
]

# 1x1 PNG, used as "signature logo" (Zammad turns data: images into inline attachments)
LOGO = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

MINI_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 60>>stream
BT /F1 12 Tf 10 50 Td (Fehlerprotokoll Druckserver PRN01) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>
%%EOF
"""


def signature(first: str, last: str, org: str, phone: str) -> str:
    return (f"<p>Mit freundlichen Grüßen<br>{first} {last}<br>{org}<br>Tel.: {phone}<br>"
            f'<img src="data:image/png;base64,{LOGO}" alt="Logo"></p>')


def quote(first: str, last: str, text: str) -> str:
    return (f'<blockquote type="cite"><p>Am 01.04.2026 um 09:12 schrieb {first} {last}:</p>'
            f"<p>{text}</p></blockquote>")


def att(name: str, data: bytes, mime: str) -> Dict[str, str]:
    return {"filename": name, "data": base64.b64encode(data).decode(), "mime-type": mime}


# (title, group, customer index, tags, state, [articles])
# article: (kind, body, attachments) kind = customer | agent | note
SCENARIOS: List[Any] = [
    ("VPN bricht nach 5 Minuten ab", "2nd Level", 0, ["vpn", "zertifikat"], "closed", [
        ("customer", "<p>Hallo,</p><p>seit heute Morgen bricht die VPN-Verbindung nach ca. 5 Minuten ab. "
                     "Fehlermeldung im Client: <b>TLS handshake failed</b>. Können Sie mich bitte unter "
                     "+49 171 2345678 zurückrufen?</p>",
         [att("vpn-client.log", b"2026-04-01 08:59:58 INFO connecting vpn.example.com:443\n"
                                b"2026-04-01 09:05:01 ERROR TLS handshake failed: certificate has expired\n",
              "text/plain")]),
        ("note", "<p>Zertifikat auf vpn01 ist am 31.03. abgelaufen. <code>certbot renew --cert-name vpn</code> "
                 "ausgeführt, OpenVPN neu gestartet.</p>", []),
        ("agent", "<p>Hallo Herr Mustermann,</p><p>das Serverzertifikat war abgelaufen, wir haben es erneuert. "
                  "Bitte verbinden Sie sich einmal neu.</p>", []),
        ("customer", "<p>Danke, läuft wieder!</p>", []),
    ]),
    ("Drucker im 2. OG druckt nur leere Seiten", "1st Level", 1, ["drucker"], "closed", [
        ("customer", "<p>Guten Tag,</p><p>der Drucker PRN01 im 2. OG druckt seit gestern nur leere Seiten. "
                     "Anbei das Fehlerprotokoll.</p>", [att("fehlerprotokoll.pdf", MINI_PDF, "application/pdf")]),
        ("agent", "<p>Hallo Frau Musterfrau,</p><p>bitte prüfen Sie, ob die Schutzfolie vom neuen Toner "
                  "entfernt wurde.</p>", []),
        ("customer", "<p>Oh, das war es tatsächlich. Danke!</p>", []),
    ]),
    ("Outlook fragt ständig nach Passwort", "1st Level", 2, ["outlook", "m365"], "closed", [
        ("customer", "<p>Hallo Support,</p><p>Outlook fragt seit dem Update ständig nach meinem Passwort. "
                     "Meine Durchwahl ist 0221 98765-43.</p>", []),
        ("note", "<p>Bekanntes Problem nach Build 2603: Anmeldeinformationen in der Windows-Anmeldeinformationsverwaltung "
                 "löschen, danach Outlook-Profil neu anlegen.</p>", []),
        ("agent", "<p>Hallo Herr Meier,</p><p>bitte löschen Sie unter Systemsteuerung &gt; Anmeldeinformationsverwaltung "
                  "alle Einträge mit <i>MicrosoftOffice16</i> und starten Outlook neu.</p>", []),
    ]),
    ("WLAN Gäste-Netz nicht erreichbar", "Netzwerk", 3, ["wlan", "netzwerk"], "closed", [
        ("customer", "<p>Hallo,</p><p>unsere Besucher kommen nicht ins Gäste-WLAN. Das Captive Portal lädt nicht.</p>", []),
        ("note", "<p>DHCP-Scope für VLAN 30 war voll (Lease-Zeit 8 Tage). Auf 4 Stunden reduziert, Leases bereinigt.</p>", []),
        ("agent", "<p>Hallo Frau Schulz,</p><p>der Adressbereich des Gäste-Netzes war erschöpft. Ist behoben.</p>", []),
    ]),
    ("Backup-Job fileserver02 fehlgeschlagen", "2nd Level", 4, ["backup", "fileserver"], "closed", [
        ("customer", "<p>Moin,</p><p>der nächtliche Backup-Job für fileserver02 ist dreimal in Folge fehlgeschlagen. "
                     "Log im Anhang.</p>",
         [att("backup-job.log", b"ERROR: snapshot creation failed: volume D: has insufficient shadow storage\n",
              "text/plain")]),
        ("note", "<p>VSS-Schattenkopiespeicher auf D: war auf 2 GB begrenzt. <code>vssadmin resize shadowstorage "
                 "/for=D: /on=D: /maxsize=10%</code></p>", []),
        ("agent", "<p>Hallo Herr Becker,</p><p>der Schattenkopiespeicher war zu klein. Backup lief heute Nacht wieder durch.</p>", []),
    ]),
    ("MFA-Token verloren", "1st Level", 5, ["mfa", "account"], "closed", [
        ("customer", "<p>Hallo,</p><p>ich habe mein Handy verloren und komme nicht mehr in mein Konto (MFA). "
                     "Bitte melden Sie sich unter 0151 23456789.</p>", []),
        ("agent", "<p>Hallo Frau Fischer,</p><p>wir haben Ihre MFA-Methoden zurückgesetzt. Bitte richten Sie die "
                  "Authenticator-App unter https://aka.ms/mfasetup neu ein.</p>", []),
    ]),
    ("SAP Login: Benutzer gesperrt", "1st Level", 0, ["sap", "account"], "closed", [
        ("customer", "<p>Mein SAP-Benutzer ist gesperrt, nachdem ich das Passwort dreimal falsch eingegeben habe.</p>", []),
        ("agent", "<p>Benutzer entsperrt, Initialpasswort per SMS verschickt.</p>", []),
    ]),
    ("Teams: kein Ton in Besprechungen", "1st Level", 2, ["teams", "audio"], "closed", [
        ("customer", "<p>In Teams-Besprechungen hört mich niemand, obwohl das Headset in anderen Programmen geht.</p>", []),
        ("agent", "<p>Bitte in Teams unter Einstellungen &gt; Geräte das Headset als Mikrofon auswählen und "
                  "die Rauschunterdrückung auf <i>Automatisch</i> stellen.</p>", []),
    ]),
    ("Festplatte fileserver01 zu 95% voll", "2nd Level", 4, ["fileserver", "speicher"], "closed", [
        ("customer", "<p>Monitoring meldet: Laufwerk E: auf fileserver01 zu 95% belegt.</p>", []),
        ("note", "<p>Ursache: Abteilungsordner Marketing mit 400 GB Videorohmaterial. Mit Abteilung abgestimmt, "
                 "auf Archiv-NAS verschoben.</p>", []),
    ]),
    ("Neuer Mitarbeiter: Zugänge einrichten", "1st Level", 3, ["onboarding"], "closed", [
        ("customer", "<p>Zum 01.05. beginnt eine neue Kollegin in der Buchhaltung. Bitte AD-Konto, Mail und "
                     "DATEV-Zugang anlegen.</p>", []),
        ("agent", "<p>Konto ist angelegt, Zugangsdaten liegen versiegelt am Empfang.</p>", []),
    ]),
    ("VPN: Split-Tunneling für Videokonferenzen", "Netzwerk", 5, ["vpn", "teams"], "closed", [
        ("customer", "<p>Videokonferenzen über VPN ruckeln stark. Gibt es eine Lösung?</p>", []),
        ("note", "<p>Microsoft-365-Optimize-Endpunkte aus dem Tunnel ausgenommen (route-nopull + Ausnahmen). "
                 "Konfig in Git: netzwerk/vpn/split-tunnel.conf</p>",
         [att("split-tunnel.conf", b"route 13.107.64.0 255.255.192.0 net_gateway\nroute 52.112.0.0 255.252.0.0 net_gateway\n",
              "text/plain")]),
        ("agent", "<p>Wir haben Teams/Zoom-Verkehr aus dem VPN-Tunnel ausgenommen. Bitte neu verbinden.</p>", []),
    ]),
    ("Zertifikatswarnung im Intranet", "2nd Level", 1, ["zertifikat", "intranet"], "closed", [
        ("customer", "<p>Beim Aufruf von intranet.kunde.de kommt eine Zertifikatswarnung.</p>", []),
        ("note", "<p>Zwischenzertifikat fehlte in der nginx-Kette. fullchain.pem statt cert.pem eingebunden.</p>", []),
    ]),
    ("Drucker Scan-to-Mail funktioniert nicht", "1st Level", 3, ["drucker", "mail"], "closed", [
        ("customer", "<p>Scan-to-Mail am Multifunktionsgerät bringt Fehler 550.</p>"
                     + quote("Sabine", "Schulz", "Kann sich das mal jemand ansehen?"), []),
        ("note", "<p>SMTP-Relay erlaubte die IP des Geräts nicht mehr nach Netzumzug. Connector um 10.20.30.15 ergänzt.</p>", []),
        ("agent", "<p>Hallo Frau Schulz, das Gerät darf wieder Mails versenden.</p>", []),
    ]),
    ("Laptop startet nicht nach BIOS-Update", "2nd Level", 4, ["hardware", "bios"], "closed", [
        ("customer", "<p>Nach dem BIOS-Update bleibt der Bildschirm schwarz, nur die Power-LED leuchtet.</p>", []),
        ("note", "<p>CMOS-Reset (Knopfzelle 30s entfernt) hat geholfen. BIOS-Update 1.14 für Modell X13 zurückgezogen.</p>", []),
    ]),
    ("Freigabe für Projektordner", "1st Level", 5, ["berechtigung", "fileserver"], "closed", [
        ("customer", "<p>Bitte Schreibrechte für \\\\fileserver01\\projekte\\neubau für Thomas Becker und mich.</p>", []),
        ("agent", "<p>Berechtigung über AD-Gruppe G_Projekt_Neubau_RW erteilt.</p>", []),
    ]),
    ("DNS-Auflösung intern sporadisch langsam", "Netzwerk", 2, ["dns", "netzwerk"], "open", [
        ("customer", "<p>Webseiten im Intranet laden manchmal erst nach 5 Sekunden.</p>", []),
        ("note", "<p>Verdacht: zweiter DNS-Server 10.0.0.3 antwortet nicht. Weiter beobachten.</p>", []),
    ]),
    ("Rechnung IT-Dienstleistung März", "1st Level", 0, ["rechnung"], "closed", [
        ("customer", "<p>Bitte schicken Sie die Rechnung an die Buchhaltung. Unsere Bankverbindung hat sich geändert: "
                     "IBAN DE89 3704 0044 0532 0130 00.</p>", []),
        ("agent", "<p>Danke, an die Buchhaltung weitergeleitet.</p>", []),
    ]),
    ("Monitor flackert", "1st Level", 1, ["hardware"], "closed", [
        ("customer", "<p>Mein zweiter Monitor flackert ständig.</p>", []),
        ("agent", "<p>Bitte das DisplayPort-Kabel tauschen, eines liegt bei Ihnen im Postfach.</p>", []),
    ]),
    ("Firewall-Freischaltung Port 8443", "Netzwerk", 4, ["firewall", "netzwerk"], "closed", [
        ("customer", "<p>Für die neue Zeiterfassung brauchen wir ausgehend TCP 8443 zu zeit.anbieter.example.</p>", []),
        ("note", "<p>Regel FW-OUT-112 angelegt, Change CHG-2026-041.</p>", []),
    ]),
    ("Passwort abgelaufen im Homeoffice", "1st Level", 3, ["account", "vpn"], "closed", [
        ("customer", "<p>Mein Passwort ist abgelaufen und ich bin im Homeoffice, VPN geht deshalb nicht.</p>", []),
        ("agent", "<p>Bitte über https://passwort.example.com das Passwort ändern, das geht ohne VPN.</p>", []),
    ]),
]


class Api:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Token token={token}", "Content-Type": "application/json"})

    def call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.s.request(method, self.url + path, timeout=60, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else None

    def find_or_create(self, kind: str, search: Dict[str, Any], data: Dict[str, Any], match: str) -> Dict[str, Any]:
        for item in self.call("GET", f"/api/v1/{kind}/search", params=search) or []:
            if isinstance(item, dict) and str(item.get(match, "")).lower() == str(data[match]).lower():
                return item
        return self.call("POST", f"/api/v1/{kind}", data=json.dumps(data))


def main(url: str, token: str) -> None:
    api = Api(url, token)
    addresses = api.call("GET", "/api/v1/email_addresses") or []
    address_id = addresses[0]["id"] if addresses else None  # created by dev/bootstrap.rb
    groups = {g["name"]: g for g in api.call("GET", "/api/v1/groups")}
    for name in GROUPS:
        if name not in groups:
            groups[name] = api.call("POST", "/api/v1/groups", data=json.dumps(
                {"name": name, "active": True, "email_address_id": address_id}))
        elif address_id and not groups[name].get("email_address_id"):
            groups[name] = api.call("PUT", f"/api/v1/groups/{groups[name]['id']}",
                                    data=json.dumps({"email_address_id": address_id}))
    # the seeding user needs access to the groups it creates tickets in
    me = api.call("GET", "/api/v1/users/me")
    api.call("PUT", f"/api/v1/users/{me['id']}", data=json.dumps(
        {"group_ids": {str(g["id"]): ["full"] for g in groups.values()}}))
    orgs = {name: api.find_or_create("organizations", {"query": name}, {"name": name}, "name") for name in ORGS}

    customers = []
    for first, last, email, org in CUSTOMERS:
        customers.append(api.find_or_create("users", {"query": email}, {
            "firstname": first, "lastname": last, "email": email, "organization": org,
            "roles": ["Customer"]}, "email"))
    agents = []
    for first, last, email, grps in AGENTS:
        agents.append(api.find_or_create("users", {"query": email}, {
            "firstname": first, "lastname": last, "email": email, "roles": ["Agent"],
            "group_ids": {str(groups[g]["id"]): ["full"] for g in grps}}, "email"))
    print(f"groups={len(groups)} orgs={len(orgs)} customers={len(customers)} agents={len(agents)}")

    for title, group, cidx, tags, state, articles in SCENARIOS:
        first, last, email, org = CUSTOMERS[cidx]
        agent = AGENTS[1] if group == "Netzwerk" else AGENTS[0]
        agent_user = agents[1] if group == "Netzwerk" else agents[0]
        cust_from = f"{first} {last} <{email}>"
        agent_from = f"{agent[0]} {agent[1]} <{agent[2]}>"
        phone = "+49 221 1234-56" if cidx % 2 else "0221 7654321"

        kind, body, atts = articles[0]
        ticket = api.call("POST", "/api/v1/tickets", data=json.dumps({
            "title": title, "group": group, "customer_id": customers[cidx]["id"],
            "owner_id": agent_user["id"], "tags": ",".join(tags),
            "article": {"subject": title, "body": body + signature(first, last, org, phone),
                        "content_type": "text/html", "type": "email", "sender": "Customer",
                        "from": cust_from, "to": "Support <support@example.com>", "internal": False,
                        "attachments": atts},
        }))
        for kind, body, atts in articles[1:]:
            art: Dict[str, Any] = {"ticket_id": ticket["id"], "content_type": "text/html", "attachments": atts}
            if kind == "customer":
                art.update(type="email", sender="Customer", internal=False, from_=cust_from,
                           to="Support <support@example.com>", subject=f"AW: {title}",
                           body=body + signature(first, last, org, phone) + quote(agent[0], agent[1], "…"))
            elif kind == "agent":
                art.update(type="email", sender="Agent", internal=False, from_=agent_from, to=cust_from,
                           subject=f"AW: {title}",
                           body=body + f"<p>Viele Grüße<br>{agent[0]} {agent[1]}<br>IT-Service, Durchwahl -42</p>"
                           + quote(first, last, "siehe oben"))
            else:
                art.update(type="note", sender="Agent", internal=True, body=body)
            art["from"] = art.pop("from_", None)
            # replies and notes are written by the agent, like in real life ("Julia Agent via Support")
            headers = {"X-On-Behalf-Of": agent[2]} if kind != "customer" else {}
            api.call("POST", "/api/v1/ticket_articles", data=json.dumps(art), headers=headers)
        if state != "new":
            api.call("PUT", f"/api/v1/tickets/{ticket['id']}", data=json.dumps({"state": state}))
        print(f"ticket {ticket['number']}  {group:10} {title}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
