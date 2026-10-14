"""Example for `custom_middleware: "custom_middleware.py:process"` in config.yaml.

Runs as the LAST middleware step. Arguments:

  bundle: {"ticket": {...}, "articles": [{...}, ...]}  already processed (allowlisted,
          pseudonymised, scrubbed). Change it in place or build a new one.
  raw:    {"ticket": {...}, "articles": [...], "tags": [...]}  as returned by the API.
          Read-only. Use it to make decisions, never copy raw values into the bundle.

Return the bundle to archive it, or None to skip the ticket completely.
"""
import re

# Example: names of colleagues/customers that show up in free text but never in a mail header.
EXTRA_NAMES = re.compile(r"\b(Frau|Herr|Mrs?\.?|Ms\.?)\s+[A-ZÄÖÜ][\wäöüß-]+")
TICKET_REF = re.compile(r"\bRE-\d{6}\b")  # e.g. invoice numbers


def process(bundle, raw):
    ticket = bundle["ticket"]

    # 1. Skip tickets that are not worth keeping.
    if ticket.get("state") in ("merged", "removed"):
        return None
    if not any((a.get("body") or "").strip() for a in bundle["articles"]):
        return None

    # 2. Extra scrubbing in all texts.
    for article in bundle["articles"]:
        body = article.get("body") or ""
        body = EXTRA_NAMES.sub(r"\1 [NAME]", body)
        body = TICKET_REF.sub("[RECHNUNG]", body)
        article["body"] = body

    # 3. Add your own fields, e.g. a flag computed from raw data.
    ticket["had_phone_contact"] = any(a.get("type") == "phone" for a in raw["articles"])
    return bundle
