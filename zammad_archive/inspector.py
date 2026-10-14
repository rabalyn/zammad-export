"""inspect: sample live tickets, show raw vs. processed data and a field report (ADR 0006)."""
from __future__ import annotations

import json
import logging
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .archive import safe_dirname
from .exporter import collect_tickets
from .middleware import EMAIL_RE, PHONE_RE, PSEUDONYM_RE, Middleware
from .zammad import ZammadClient

log = logging.getLogger(__name__)


class FieldStats:
    def __init__(self) -> None:
        self.total = 0
        self.present: Counter = Counter()
        self.filled: Counter = Counter()
        self.values: Dict[str, Counter] = defaultdict(Counter)
        self.pii: Dict[str, set] = defaultdict(set)

    def add(self, obj: Dict[str, Any], skip: tuple = ()) -> None:
        self.total += 1
        for key, value in obj.items():
            if key in skip:
                continue
            self.present[key] += 1
            if value in (None, "", [], {}, "-"):
                continue
            self.filled[key] += 1
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            self.values[key][text[:80]] += 1
            if EMAIL_RE.search(text):
                self.pii[key].add("mail")
            if PHONE_RE.search(text):
                self.pii[key].add("phone")

    def table(self, kept: List[str]) -> str:
        rows = ["| field | kept | filled | distinct | personal data? | samples |",
                "|---|---|---|---|---|---|"]
        for key in sorted(self.present, key=lambda k: (-self.filled[k], k)):
            samples = [s.replace("|", "\\|").replace("\n", " ") for s, _ in self.values[key].most_common(3)]
            rows.append("| `{}` | {} | {}/{} | {} | {} | {} |".format(
                key, "✅" if key in kept else "–", self.filled[key], self.total,
                len(self.values[key]), " ".join(f"⚠ {p}" for p in sorted(self.pii[key])),
                "<br>".join(f"`{s}`" for s in samples)))
        return "\n".join(rows)


def render_text(bundle: Dict[str, Any]) -> str:
    t = bundle["ticket"]
    lines = [f"# {t.get('number')} – {t.get('title')}", ""]
    for key, value in t.items():
        if key not in ("title", "number"):
            lines.append(f"{key}: {value}")
    for a in bundle["articles"]:
        lines += ["", "-" * 72,
                  f"{a.get('created_at')}  {a.get('sender')}/{a.get('type')}"
                  f"{'  [internal]' if a.get('internal') else ''}",
                  f"from: {a.get('from')}   to: {a.get('to')}   cc: {a.get('cc')}",
                  f"subject: {a.get('subject')}", ""]
        lines.append(a.get("body") or "")
        for att in a.get("attachments") or []:
            lines.append(f"[attachment] {att['filename']} ({att['size']} bytes, {att['content_type']})")
    return "\n".join(lines) + "\n"


def _leftovers(bundle: Dict[str, Any], external_terms: set) -> List[str]:
    texts = [str(bundle["ticket"].get("title") or "")]
    for a in bundle["articles"]:
        texts += [str(a.get(f) or "") for f in ("from", "to", "cc", "subject", "body")]
        texts += [att["filename"] for att in a.get("attachments") or []]
    blob = "\n".join(texts)
    found = []
    for m in EMAIL_RE.finditer(blob):
        found.append(f"mail address `{m.group(0)}`")
    for m in PHONE_RE.finditer(blob):
        found.append(f"phone-like `{m.group(0)}`")
    lowered = PSEUDONYM_RE.sub("", blob).lower()
    for term in sorted(external_terms):
        if "@" not in term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", lowered):
            found.append(f"name `{term}`")
    return found


def run_inspect(cfg: Dict[str, Any], client: ZammadClient, mw: Middleware, out: Path,
                limit: int, query: Optional[str] = None) -> Path:
    queries = [{"name": "adhoc", "query": query}] if query else cfg["queries"]
    if not queries:
        raise ValueError("no queries configured and no --query given")
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    found = collect_tickets(client, queries, incremental_days=None, max_per_query=limit)

    tstats, astats = FieldStats(), FieldStats()
    kinds: Counter = Counter()
    content_types: Counter = Counter()
    body_raw: List[int] = []
    body_kept: List[int] = []
    att_types: Counter = Counter()
    att_sizes: List[int] = []
    att_kept = att_inline = 0
    leftovers: Dict[str, List[str]] = {}
    per_query: Counter = Counter()

    for ticket, names in found.values():
        number = safe_dirname(ticket.get("number") or ticket.get("id"))
        for n in names:
            per_query[n] += 1
        raw = {"ticket": ticket, "articles": client.articles(ticket["id"]),
               "tags": client.tags(ticket["id"]) if cfg["zammad"]["fetch_tags"] else []}
        (out / "raw" / f"{number}.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tstats.add(dict(ticket, tags=raw["tags"]))
        for a in raw["articles"]:
            astats.add(a, skip=("body",))
            kinds[f"{a.get('sender')}/{a.get('type')}{' (internal)' if a.get('internal') else ''}"] += 1
            content_types[a.get("content_type") or "?"] += 1
            body_raw.append(len(a.get("body") or ""))
            for att in a.get("attachments") or []:
                prefs = att.get("preferences") or {}
                att_types[prefs.get("Content-Type") or prefs.get("Mime-Type") or "?"] += 1
                att_sizes.append(int(att.get("size") or 0))
                disp = str(prefs.get("Content-Disposition") or "").lower()
                att_inline += disp.startswith("inline") or bool(prefs.get("Content-ID"))
                att_kept += mw.keep_attachment(att)

        result = mw.process(raw)
        if result.bundle is None:
            (out / "preview" / f"{number}.txt").write_text("(dropped by middleware)\n", encoding="utf-8")
            continue
        body_kept += [len(a.get("body") or "") for a in result.bundle["articles"]]
        (out / "preview" / f"{number}.json").write_text(
            json.dumps(result.bundle, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (out / "preview" / f"{number}.txt").write_text(render_text(result.bundle), encoding="utf-8")
        left = _leftovers(result.bundle, result.ctx.external_terms)
        if left:
            leftovers[number] = left

    def _dist(values: List[int]) -> str:
        if not values:
            return "–"
        return f"min {min(values)}, median {int(statistics.median(values))}, max {max(values)}, sum {sum(values)}"

    p = cfg["privacy"]
    report = [
        "# Zammad inspect report",
        "",
        "> ⚠ This folder contains **unprocessed personal data** (`raw/`). Delete it when you are done.",
        "",
        f"Tickets: **{len(found)}**, articles: **{astats.total}**",
        "",
        "Tickets per query: " + ", ".join(f"`{k}`: {v}" for k, v in per_query.most_common()),
        "",
        "## Ticket fields",
        "",
        "`kept` = in `privacy.ticket_fields`. Fields not kept are dropped from the archive.",
        "",
        tstats.table(p["ticket_fields"]),
        "",
        "## Article fields (body excluded)",
        "",
        astats.table(p["article_fields"]),
        "",
        "## Article kinds (sender/type)",
        "",
        "\n".join(f"- {k}: {v}" for k, v in kinds.most_common()),
        "",
        "Body content types: " + ", ".join(f"`{k}` {v}" for k, v in content_types.most_common()),
        "",
        f"- body length raw (chars, incl. HTML): {_dist(body_raw)}",
        f"- body length after middleware: {_dist(body_kept)}",
        "",
        "## Attachments",
        "",
        f"- count: {len(att_sizes)}, inline (signature images etc.): {att_inline}, "
        f"would be kept: {att_kept}",
        f"- size (bytes): {_dist(att_sizes)}",
        "",
        "\n".join(f"- `{k}`: {v}" for k, v in att_types.most_common()) or "- none",
        "",
        "## Possible personal data left after the middleware",
        "",
        "Check these in `preview/<number>.txt`. Mail addresses of colleagues (internal_domains) are",
        "fine if you chose `agent_display: login`. Names are only detected if they appear in a mail header.",
        "",
    ]
    if leftovers:
        for number, items in sorted(leftovers.items()):
            report.append(f"- **{number}**: " + "; ".join(sorted(set(items))[:15]))
    else:
        report.append("Nothing found. 🎉 (Still read a few previews yourself.)")
    report_path = out / "report.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    return report_path
