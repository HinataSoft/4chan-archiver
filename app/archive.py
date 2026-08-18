import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# `tim` je int a `ext` jde do jména souboru na disku i do URL, kterou
# nginx servíruje bez jakékoli sanitizace. Jediná obrana je tenhle
# whitelist na hranici: cokoli mimo tvar ".webm" se zahodí.
EXT_RE = re.compile(r"\.[a-z0-9]{1,5}")


def thread_dir(archive_dir: Path, board: str, no: int) -> Path:
    return Path(archive_dir) / board / str(no)


def media_filename(tim: int, ext: str, kind: str) -> str:
    if kind == "thumb":
        return f"{tim}s.jpg"
    return f"{tim}{ext}"


def media_path(archive_dir: Path, board: str, no: int,
               tim: int, ext: str, kind: str) -> Path:
    return thread_dir(archive_dir, board, no) / media_filename(tim, ext, kind)


def new_document(board: str, no: int, now: datetime) -> dict:
    stamp = now.isoformat()
    return {
        "board": board,
        "no": no,
        "status": "live",
        "first_seen": stamp,
        "last_updated": stamp,
        "died_at": None,
        "posts": [],
        "media": {},
    }


def load_thread(archive_dir: Path, board: str, no: int) -> dict | None:
    path = thread_dir(archive_dir, board, no) / "thread.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_thread(archive_dir: Path, doc: dict) -> None:
    directory = thread_dir(archive_dir, doc["board"], doc["no"])
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "thread.json"
    tmp = directory / "thread.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def merge_posts(old: list[dict], new: list[dict]) -> list[dict]:
    merged: dict[int, dict] = {}
    for post in old:
        copy = dict(post)
        copy.setdefault("_deleted", False)
        merged[post["no"]] = copy
    for post in new:
        existing = merged.get(post["no"], {})
        combined = {**existing, **post}
        combined["_deleted"] = False
        merged[post["no"]] = combined
    live_numbers = {p["no"] for p in new}
    for number, post in merged.items():
        if number not in live_numbers:
            post["_deleted"] = True
    return [merged[n] for n in sorted(merged)]


def valid_ext(ext) -> bool:
    return isinstance(ext, str) and EXT_RE.fullmatch(ext) is not None


def media_entries(posts: list[dict]) -> list[tuple[int, str]]:
    out = []
    for post in posts:
        if not post.get("tim") or not post.get("ext") or post.get("filedeleted"):
            continue
        ext = post["ext"]
        if not valid_ext(ext):
            log.warning("post %s: podezřelé ext %r, médium přeskočeno",
                        post.get("no"), ext)
            continue
        out.append((int(post["tim"]), ext))
    return out


def delete_thread_dir(archive_dir: Path, board: str, no: int) -> None:
    try:
        shutil.rmtree(thread_dir(archive_dir, board, no))
    except FileNotFoundError:
        pass
