"""HTML to plain text, quoted-reply stripping and signature cutting."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional, Pattern, Sequence

# Line inserted where Zammad's signature detection marked the start of a signature.
SIGNATURE_SENTINEL = "⁣ZAMMAD_SIGNATURE⁣"

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "pre", "hr", "blockquote", "section", "article", "header", "footer", "dd", "dt",
}
_SKIP_TAGS = {"script", "style", "head", "title", "template", "noscript"}
_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "wbr"}


class _TextExtractor(HTMLParser):
    def __init__(self, drop_blockquotes: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.drop_blockquotes = drop_blockquotes
        self.parts: List[str] = []
        self.skip_depth = 0
        self.quote_depth = 0
        self.pre_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "blockquote":
            self.quote_depth += 1
        if tag == "pre":
            self.pre_depth += 1
        if "js-signatureMarker" in (attrs_d.get("class") or ""):
            self.parts.append("\n" + SIGNATURE_SENTINEL + "\n")
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag in ("td", "th"):
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "blockquote":
            self.quote_depth = max(0, self.quote_depth - 1)
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth or (self.drop_blockquotes and self.quote_depth):
            return
        if self.pre_depth:
            data = data.replace(" ", "\xa0")  # protect indentation from line stripping
        else:
            data = re.sub(r"[ \t\r\n\f\v]+", " ", data)
        self.parts.append(data)


def normalise_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(html: str, drop_blockquotes: bool = False) -> str:
    parser = _TextExtractor(drop_blockquotes)
    parser.feed(html or "")
    parser.close()
    text = "".join(parser.parts)
    text = "\n".join(line.strip(" ") for line in text.split("\n"))
    return normalise_whitespace(text)


def compile_markers(patterns: Sequence[str]) -> List[Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_OUTLOOK_FROM = re.compile(r"^\**\s*(von|from)\s*:\**\s", re.IGNORECASE)
_OUTLOOK_NEXT = re.compile(r"^\**\s*(gesendet|sent|datum|date|an|to|betreff|subject)\s*:", re.IGNORECASE)


def _find_quote_start(lines: List[str], markers: Sequence[Pattern[str]]) -> Optional[int]:
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(m.search(stripped) for m in markers):
            return i
        if _OUTLOOK_FROM.search(stripped):
            following = [l.strip() for l in lines[i + 1:i + 4]]
            if any(_OUTLOOK_NEXT.search(l) for l in following):
                return i
    return None


def strip_quoted(text: str, markers: Sequence[Pattern[str]]) -> str:
    """Remove '>' quoted lines and everything after a reply/forward header.

    If cutting would leave (almost) nothing, e.g. a pure forward, the text after
    the header is kept (only '>' lines are removed).
    """
    lines = [l for l in text.split("\n") if not l.lstrip().startswith(">")]
    start = _find_quote_start(lines, markers)
    if start is not None:
        head = "\n".join(lines[:start]).strip()
        if len(head) >= 20:
            lines = lines[:start]
    return normalise_whitespace("\n".join(lines))


def cut_signature(text: str, markers: Sequence[Pattern[str]], keep_lines: int = 1) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == SIGNATURE_SENTINEL:
            return normalise_whitespace("\n".join(lines[:i]))
        if i > 0 and any(m.search(stripped) for m in markers):
            keep = keep_lines if not re.match(r"^--\s*$", stripped) else 0
            return normalise_whitespace("\n".join(lines[:i + keep]))
    return normalise_whitespace("\n".join(l for l in lines if l.strip() != SIGNATURE_SENTINEL))
