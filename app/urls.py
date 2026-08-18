import re
from dataclasses import dataclass

_HOSTS = {"boards.4chan.org", "boards.4channel.org"}

_FULL = re.compile(
    r"^(?:https?://)?(?P<host>boards\.4chan(?:nel)?\.org)"
    r"/(?P<board>[a-z0-9]+)/thread/(?P<no>\d+)(?:/[^#?]*)?(?:[#?].*)?$",
    re.IGNORECASE,
)
_PATH = re.compile(r"^/?(?P<board>[a-z0-9]+)/thread/(?P<no>\d+)/?$", re.IGNORECASE)
_SHORT = re.compile(r"^/?(?P<board>[a-z0-9]+)/(?P<no>\d+)/?$", re.IGNORECASE)


@dataclass(frozen=True)
class ThreadRef:
    board: str
    no: int


def parse_thread_url(raw: str) -> ThreadRef:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty input")
    for pattern in (_FULL, _PATH, _SHORT):
        m = pattern.match(text)
        if m:
            return ThreadRef(board=m.group("board").lower(), no=int(m.group("no")))
    raise ValueError(f"unrecognised thread URL: {raw!r}")
