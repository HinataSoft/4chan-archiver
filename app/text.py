import html
import re
from collections.abc import Sequence

_BREAK = re.compile(r"<br\s*/?>|</p>|</div>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = _BREAK.sub(" ", raw)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip()


def op_search_text(post: dict) -> str:
    parts = [html_to_text(post.get("sub")), html_to_text(post.get("com"))]
    return " ".join(p for p in parts if p)


def matches_keywords(haystack: str, keywords: Sequence[str]) -> bool:
    hay = haystack.lower()
    return any(k.strip() and k.strip().lower() in hay for k in keywords)
