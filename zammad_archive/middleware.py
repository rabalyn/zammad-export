"""Privacy middleware: everything a ticket passes through before it is archived.

Pipeline per ticket (see docs/ADR/0005-privacy-middleware.md):

1. collect all people from the ticket and article headers (TicketContext)
2. drop articles by rule (system / internal / type)
3. HTML -> text, strip quoted replies, cut signatures
4. replace known names / addresses with stable pseudonyms (HMAC)
5. regex scrubbing (mail addresses, phone numbers, IBANs, custom patterns)
6. trim long bodies, filter attachments, keep only allowlisted fields
7. optional custom hook (your own Python function)
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import logging
import re
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Pattern, Set, Tuple

from . import textconv

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(
    r"(?<![\w.+%-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
# International (+49 / 0049 ...) or national (0...) numbers with at least ~7 digits.
PHONE_RE = re.compile(
    r"(?<![\w/.:+-])"
    r"(?:(?:\+|00)\d{1,3}[ ./-]?(?:\(0\)[ ./-]?)?\d{1,5}|\(?0\d{2,5}\)?)"
    r"(?:[ ./-]?\d{2,}){1,4}"
    r"(?![\w/:-]|[.,]\d)")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")
PSEUDONYM_RE = re.compile(r"\b(?:Person|Org|Agent)-[0-9a-f]{8}\b")

PERSON_TICKET_FIELDS = {"customer", "created_by", "updated_by"}
AGENT_TICKET_FIELDS = {"owner"}


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


class Pseudonymiser:
    def __init__(self, secret: str) -> None:
        self.key = secret.encode("utf-8")

    def token(self, kind: str, value: str) -> str:
        digest = hmac.new(self.key, _norm(value).encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{kind}-{digest[:8]}"


class TicketContext:
    """People found in one ticket, and the text scrubber built from them."""

    def __init__(self, mw: "Middleware") -> None:
        self.mw = mw
        self.terms: Dict[str, str] = {}          # lowercased name/email -> replacement
        self.external_terms: Set[str] = set()    # raw terms of non-colleagues (for inspect)
        self._email_re: Optional[Pattern[str]] = None
        self._name_re: Optional[Pattern[str]] = None

    # -- registering -------------------------------------------------------
    def _add_term(self, term: str, display: str, external: bool) -> None:
        key = _norm(term)
        if not key or key == _norm(display):
            return
        old = self.terms.get(key)
        if old is not None and old != display and "@" not in key:
            display = "[NAME]"  # same name part for two different people
        self.terms[key] = display
        if external:
            self.external_terms.add(key)
        self._email_re = self._name_re = None

    def _name_parts(self, name: str) -> List[str]:
        p = self.mw.privacy
        parts = []
        for part in re.split(r"[\s,;()\"'/]+|(?<=\w)-(?=\w)|\.(?=\w)", name):
            part = part.strip(".-_")
            if (len(part) >= p["name_part_min_length"] and not any(c.isdigit() for c in part)
                    and part.lower() not in self.mw.name_ignore):
                parts.append(part)
        return parts

    def _register_names(self, name: str, display: str, external: bool) -> None:
        name = name.strip().strip('"').strip()
        if not name or "@" in name:
            return
        if name.lower() not in self.mw.name_ignore:
            self._add_term(name, display, external)
        if "," in name:  # "Doe, John" -> also "John Doe"
            last, _, first = name.partition(",")
            self._add_term(f"{first.strip()} {last.strip()}", display, external)
        if self.mw.privacy["replace_name_parts"]:
            for part in self._name_parts(name):
                self._add_term(part, display, external)

    def is_internal(self, email: str) -> bool:
        domain = email.rsplit("@", 1)[-1].lower()
        return any(domain == d or domain.endswith("." + d) for d in self.mw.internal_domains)

    def _agent_display(self, login: str) -> str:
        mode = self.mw.privacy["agent_display"]
        if mode == "login":
            return login
        if mode == "localpart":
            return login.split("@", 1)[0]
        return self.mw.pseudo.token("Agent", login)

    def add_address(self, name: str, email: str, force_internal: bool = False) -> str:
        """Register one person, return how they are shown in the archive."""
        email = (email or "").strip().lower()
        name = (name or "").strip().strip('"').strip()
        if not email and not name:
            return ""
        internal = force_internal or (bool(email) and self.is_internal(email))
        if internal:
            display = self._agent_display(email or name)
            if email:
                self._add_term(email, display, external=False)
            if self.mw.privacy["agent_display"] == "pseudonym":
                self._register_names(name, display, external=False)
                if email and not name:
                    self._register_names(email.split("@", 1)[0], display, external=False)
            return display
        if not self.mw.privacy["pseudonymise_customers"]:
            return f"{name} <{email}>" if name and email else (email or name)
        display = self.mw.pseudo.token("Person", email or name)
        if email:
            self._add_term(email, display, external=True)
        self._register_names(name, display, external=True)
        if email and not name:
            local = email.split("@", 1)[0]
            if re.search(r"[._]", local):
                self._register_names(re.sub(r"[._]+", " ", local), display, external=True)
        return display

    def add_header(self, value: Any, force_internal: bool = False) -> str:
        """'Max Mustermann <max@x.de>, other@y.de' -> 'Person-1a2b3c4d, Person-...'"""
        if not value or not isinstance(value, str):
            return ""
        if "@" not in value:  # just a name ("Julia Agent") – getaddresses would mangle it
            return self.add_address(value, "", force_internal)
        out = []
        for name, email in getaddresses([value]):
            if not email and not name:
                continue
            if email and "@" not in email:  # a login or bare name, not an address
                name, email = (name or email), ""
            shown = self.add_address(name, email, force_internal)
            if shown and shown not in out:
                out.append(shown)
        return ", ".join(out)

    def add_login(self, login: Any) -> str:
        """Zammad user reference of a colleague (owner): login or mail address."""
        if not login or not isinstance(login, str) or login.strip() in ("-", ""):
            return ""
        login = login.strip()
        if "@" in login:
            return self.add_address("", login, force_internal=True)
        return self._agent_display(login)

    def add_org(self, name: Any) -> str:
        if not name or not isinstance(name, str):
            return ""
        if not self.mw.privacy["pseudonymise_customers"]:
            return name
        display = self.mw.pseudo.token("Org", name)
        if name.lower() not in self.mw.name_ignore and len(name.strip()) >= 3:
            self._add_term(name, display, external=True)
        return display

    # -- scrubbing ---------------------------------------------------------
    def _compile(self) -> None:
        emails = sorted((t for t in self.terms if "@" in t), key=len, reverse=True)
        names = sorted((t for t in self.terms if "@" not in t), key=len, reverse=True)
        self._email_re = (re.compile(r"(?<![\w.+%-])(" + "|".join(map(re.escape, emails)) + r")(?![\w-])",
                                     re.IGNORECASE) if emails else None)
        self._name_re = (re.compile(r"(?<!\w)(" + "|".join(map(re.escape, names)) + r")(?!\w)",
                                    re.IGNORECASE) if names else None)

    def scrub(self, text: Any) -> Any:
        if not isinstance(text, str) or not text:
            return text
        if self._email_re is None and self._name_re is None and self.terms:
            self._compile()
        if self._email_re is not None:
            text = self._email_re.sub(lambda m: self.terms[_norm(m.group(1))], text)
        if self._name_re is not None:
            text = self._name_re.sub(lambda m: self.terms.get(_norm(m.group(1)), m.group(1)), text)
        return self.mw.regex_scrub(text, self)


class Result:
    def __init__(self, bundle: Optional[Dict[str, Any]], ctx: TicketContext) -> None:
        self.bundle = bundle
        self.ctx = ctx

    def scrub(self, text: str) -> str:
        return self.ctx.scrub(text)


class Middleware:
    def __init__(self, cfg: Dict[str, Any], secret: str) -> None:
        self.privacy = cfg["privacy"]
        self.att = cfg["attachments"]
        self.pseudo = Pseudonymiser(secret)
        self.internal_domains = [d.lower().lstrip("@") for d in self.privacy["internal_domains"]]
        self.name_ignore = {w.lower() for w in self.privacy["name_part_ignore"]} | {
            "herr", "frau", "prof", "dr", "mr", "mrs", "ms", "dear", "hallo", "hello"}
        self.quote_markers = textconv.compile_markers(self.privacy["quote_markers"])
        self.sig_markers = textconv.compile_markers(self.privacy["signature_markers"])
        self.custom_patterns: List[Tuple[Pattern[str], str]] = []
        for cp in self.privacy["scrub"]["custom_patterns"] or []:
            flags = re.IGNORECASE if cp.get("ignore_case") else 0
            self.custom_patterns.append(
                (re.compile(cp["pattern"], flags), cp.get("replacement", f"[{cp.get('name', 'REDACTED').upper()}]")))
        self.custom_hook = load_hook(cfg.get("custom_middleware"), cfg.get("_config_dir", "."))

    # -- text ---------------------------------------------------------------
    def regex_scrub(self, text: str, ctx: TicketContext) -> str:
        scrub = self.privacy["scrub"]
        mode = scrub["emails"]
        if mode != "keep":
            def _email(m: "re.Match[str]") -> str:
                addr = m.group(0)
                if ctx.is_internal(addr):
                    return ctx._agent_display(addr.lower())
                return self.pseudo.token("Person", addr) if mode == "pseudonym" else "[EMAIL]"
            text = EMAIL_RE.sub(_email, text)
        if scrub["ibans"]:
            text = IBAN_RE.sub("[IBAN]", text)
        if scrub["phones"]:
            text = PHONE_RE.sub("[PHONE]", text)
        for pattern, repl in self.custom_patterns:
            text = pattern.sub(repl, text)
        return text

    def body_to_text(self, article: Dict[str, Any]) -> str:
        body = article.get("body") or ""
        is_html = "html" in (article.get("content_type") or "").lower()
        strip = self.privacy["strip_quoted_replies"]
        if is_html:
            text = textconv.html_to_text(body, drop_blockquotes=strip)
            if strip and len(text) < 20:  # pure forward: keep the quoted content
                text = textconv.html_to_text(body)
        else:
            text = textconv.normalise_whitespace(body)
        if strip:
            text = textconv.strip_quoted(text, self.quote_markers)
        if self.privacy["cut_signatures"]:
            text = textconv.cut_signature(text, self.sig_markers, self.privacy["signature_keep_lines"])
        return text

    # -- attachments ----------------------------------------------------------
    def keep_attachment(self, att: Dict[str, Any]) -> bool:
        if not self.att["enabled"]:
            return False
        prefs = att.get("preferences") or {}
        ctype = (prefs.get("Content-Type") or prefs.get("Mime-Type") or "").lower()
        size = int(att.get("size") or 0)
        name = att.get("filename") or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        allowed = [e.lower().lstrip(".") for e in self.att["allowed_extensions"] or []]
        if allowed and ext not in allowed:
            return False
        if size <= 0 or size > float(self.att["max_size_mb"]) * 1024 * 1024:
            return False
        if self.att["skip_inline"]:
            disp = str(prefs.get("Content-Disposition") or "").lower()
            if disp.startswith("inline") or (prefs.get("Content-ID") and ctype.startswith("image/")):
                return False
        return True

    # -- main entry -------------------------------------------------------------
    def process(self, raw: Dict[str, Any]) -> Result:
        """raw = {"ticket": {...}, "articles": [...], "tags": [...]} as returned by the API."""
        p = self.privacy
        ticket_in = raw.get("ticket") or {}
        ctx = TicketContext(self)

        # 1. collect people first, so that every text can be scrubbed with all of them
        shown: Dict[str, str] = {}
        for field in AGENT_TICKET_FIELDS:
            shown[field] = ctx.add_login(ticket_in.get(field))
        for field in PERSON_TICKET_FIELDS:
            if field == "customer" or field in p["ticket_fields"]:
                shown[field] = ctx.add_header(ticket_in.get(field))
        shown["organization"] = ctx.add_org(ticket_in.get("organization"))

        articles_in = []
        for art in raw.get("articles") or []:
            if p["skip_system_articles"] and art.get("sender") == "System":
                continue
            if p["skip_internal_articles"] and art.get("internal"):
                continue
            if art.get("type") in (p["skip_article_types"] or []):
                continue
            articles_in.append(art)
        headers: Dict[Any, Dict[str, str]] = {}
        for art in articles_in:
            # the author of an agent article is a colleague, even if 'from' is only a name
            headers[art.get("id")] = {
                f: ctx.add_header(art.get(f), force_internal=(f == "from" and art.get("sender") == "Agent"))
                for f in ("from", "to", "cc", "reply_to")}
            for f in ("created_by", "updated_by"):
                if f in p["article_fields"]:
                    headers[art.get("id")][f] = ctx.add_header(art.get(f))

        # 2. ticket
        ticket: Dict[str, Any] = {}
        for field in p["ticket_fields"]:
            if field == "tags":
                ticket["tags"] = [ctx.scrub(t) for t in raw.get("tags") or []]
            elif field in shown:
                ticket[field] = shown[field]
            elif field in ticket_in:
                value = ticket_in[field]
                ticket[field] = ctx.scrub(value) if isinstance(value, str) and field not in (
                    "number", "created_at", "updated_at", "close_at") else value

        # 3. articles
        articles: List[Dict[str, Any]] = []
        max_chars = int(p["max_body_chars"] or 0)
        for art in articles_in:
            out: Dict[str, Any] = {}
            for field in p["article_fields"]:
                if field in headers[art.get("id")]:
                    out[field] = headers[art["id"]][field]
                elif field == "body":
                    body = ctx.scrub(self.body_to_text(art))
                    if max_chars and len(body) > max_chars:
                        body = body[:max_chars].rstrip() + "\n[… truncated]"
                    out["body"] = body
                elif field == "subject":
                    out["subject"] = ctx.scrub(art.get("subject") or "")
                elif field == "attachments":
                    out["attachments"] = [
                        {
                            "id": a.get("id"),
                            "filename": ctx.scrub(a.get("filename") or f"attachment-{a.get('id')}"),
                            "size": int(a.get("size") or 0),
                            "content_type": ((a.get("preferences") or {}).get("Content-Type")
                                             or (a.get("preferences") or {}).get("Mime-Type")
                                             or "application/octet-stream"),
                        }
                        for a in art.get("attachments") or [] if self.keep_attachment(a)
                    ]
                elif field in art:
                    value = art[field]
                    out[field] = ctx.scrub(value) if isinstance(value, str) and field not in (
                        "created_at", "updated_at", "sender", "type", "content_type") else value
            out.setdefault("id", art.get("id"))
            articles.append(out)

        ticket.setdefault("id", ticket_in.get("id"))
        ticket.setdefault("number", ticket_in.get("number"))
        bundle: Optional[Dict[str, Any]] = {"ticket": ticket, "articles": articles}

        # 4. your own rules
        if self.custom_hook is not None:
            bundle = self.custom_hook(bundle, raw)
        return Result(bundle, ctx)


def load_hook(spec: Optional[str], base_dir: str) -> Optional[Callable[..., Any]]:
    """'package.module:function' or 'path/to/file.py:function' (relative to the config)."""
    if not spec:
        return None
    target, _, func = spec.rpartition(":")
    if not target or not func:
        raise ValueError(f"custom_middleware must look like 'module:function', got {spec!r}")
    if target.endswith(".py"):
        path = Path(target)
        if not path.is_absolute():
            path = Path(base_dir) / path
        spec_obj = importlib.util.spec_from_file_location("zammad_archive_custom", path)
        if spec_obj is None or spec_obj.loader is None:
            raise ValueError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    hook = getattr(module, func)
    log.info("custom middleware loaded: %s", spec)
    return hook
