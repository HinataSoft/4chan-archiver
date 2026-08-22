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


# ── Archiv jednotlivých příspěvků ────────────────────────────────────────────
#
# Vybrané posty se kopírují do pseudo-threadu <board>/0/. Nula proto, že
# skutečná 4chan ID jsou velká čísla, takže nemůže kolidovat — a zároveň projde
# všude, kde se číslo threadu očekává: v URL klienta, v nginx regexu i ve
# schématu. Kontejner záměrně není řádkem v tabulce threads: nepolluje se,
# nemá stav a nemá co dělat v seznamu sledovaných threadů.

ARCHIVE_NO = 0


def _archive_media_names(post: dict) -> list[str]:
    if not post.get("tim") or not post.get("ext"):
        return []
    tim, ext = int(post["tim"]), post["ext"]
    return [media_filename(tim, ext, "file"), media_filename(tim, ext, "thumb")]


def archive_post(archive_dir: Path, board: str, source_no: int, post: dict,
                 *, source_subject: str | None, now: datetime) -> bool:
    """Zkopíruje post i s médii do archivu boardu.

    Kopie, ne odkaz — archiv musí přežít smazání původního threadu, což je
    hlavní důvod, proč vůbec existuje. Vrací False, když už tam post je.
    """
    doc = load_thread(archive_dir, board, ARCHIVE_NO)
    if doc is None:
        doc = new_document(board, ARCHIVE_NO, now)
        doc["status"] = "archive"
    if any(p["no"] == post["no"] for p in doc["posts"]):
        return False

    source = thread_dir(archive_dir, board, source_no)
    target = thread_dir(archive_dir, board, ARCHIVE_NO)
    target.mkdir(parents=True, exist_ok=True)
    for name in _archive_media_names(post):
        origin = source / name
        if origin.exists():
            shutil.copy2(origin, target / name)

    entry = dict(post)
    entry["_archived_at"] = now.isoformat()
    entry["_source_thread"] = source_no
    entry["_source_subject"] = source_subject
    doc["posts"].append(entry)
    # Řadí se podle data původního příspěvku, ne podle pořadí archivace.
    doc["posts"].sort(key=lambda p: (p.get("time") or 0, p["no"]))
    doc["last_updated"] = now.isoformat()
    save_thread(archive_dir, doc)
    return True


def unarchive_post(archive_dir: Path, board: str, post_no: int) -> bool:
    """Odstraní post z archivu boardu i s jeho zkopírovanými médii."""
    doc = load_thread(archive_dir, board, ARCHIVE_NO)
    if doc is None:
        return False
    keep = [p for p in doc["posts"] if p["no"] != post_no]
    if len(keep) == len(doc["posts"]):
        return False

    gone = next(p for p in doc["posts"] if p["no"] == post_no)
    directory = thread_dir(archive_dir, board, ARCHIVE_NO)
    for name in _archive_media_names(gone):
        (directory / name).unlink(missing_ok=True)

    doc["posts"] = keep
    save_thread(archive_dir, doc)
    return True


def archived_boards(archive_dir: Path) -> list[dict]:
    """Boardy, které mají něco v archivu, s počtem příspěvků."""
    root = Path(archive_dir)
    if not root.exists():
        return []
    out = []
    for board_dir in sorted(root.iterdir()):
        doc = load_thread(archive_dir, board_dir.name, ARCHIVE_NO)
        if doc and doc["posts"]:
            out.append({"board": board_dir.name, "posts": len(doc["posts"])})
    return out
