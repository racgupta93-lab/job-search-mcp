"""Make untrusted posting text safe to hand to an AI.

Job postings are written by strangers. They may contain HTML, hidden
characters, or sentences aimed at an AI ("ignore your instructions..."). We
cannot make such sentences harmless by rewording them, so the defence is:
return plain text only, strip hidden characters, cap every length, and keep
posting text in clearly named data fields (never in the tool's instructions).
"""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "template", "noscript", "iframe", "object"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
}  # fmt: skip
_BLANKS = re.compile(r"[ \t\f\v\u00a0]+")
_NEWLINES = re.compile(r"\n\s*\n+")
_ELLIPSIS = "…"


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + _ELLIPSIS


def _strip_hidden(text: str) -> str:
    """Drop control and invisible formatting characters (zero-width, bidi...)."""
    kept = []
    for ch in text:
        if ch in "\n\t":
            kept.append(ch)
        elif unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            continue
        else:
            kept.append(ch)
    return "".join(kept)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def clean_text(raw: object, max_chars: int) -> str:
    """For short plain fields (title, company, location)."""
    if not isinstance(raw, str):
        return ""
    text = _strip_hidden(raw)
    text = " ".join(text.split())
    return truncate(text, max_chars)


def html_to_text(raw: object, max_chars: int) -> str:
    """Turn an HTML (or HTML-escaped HTML) description into plain text."""
    if not isinstance(raw, str) or not raw:
        return ""
    # Some boards (Greenhouse) deliver HTML that is itself entity-escaped
    # ("&lt;div&gt;"), so unescape once before parsing the tags.
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # malformed HTML must never crash a search
        return clean_text(re.sub(r"<[^>]*>", " ", raw), max_chars)
    text = _strip_hidden("".join(parser.parts))
    lines = [_BLANKS.sub(" ", line).strip() for line in text.split("\n")]
    text = _NEWLINES.sub("\n\n", "\n".join(lines)).strip()
    return truncate(text, max_chars)
