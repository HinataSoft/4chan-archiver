"""Naplní DATA_DIR ukázkovým archivem pro ruční ověření klienta.

Spuštění:  DATA_DIR=./data-dev python scripts/make_fixture.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import archive, repo  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import connect  # noqa: E402

PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082")


def get_or_create_thread(conn, board, no, source, now):
    """add_thread vrací None, pokud (board, no) už existuje — najdi existující id."""
    thread_id = repo.add_thread(conn, board, no, source, now)
    if thread_id is None:
        thread_id = repo.find_thread(conn, board, no)["id"]
    return thread_id


def get_or_create_rule(conn, board, keywords, now):
    """add_rule vždy vloží nový řádek — dohledej existující pravidlo se stejnými
    klíčovými slovy, aby opakované spuštění nevytvářelo duplicity."""
    for row in repo.list_rules(conn):
        if row["board"] == board and repo.rule_keywords(row) == keywords:
            return row["id"]
    return repo.add_rule(conn, board, keywords, now)


def main() -> None:
    cfg = load_config()
    conn = connect(cfg.db_path)
    now = datetime.now(timezone.utc)

    tid = get_or_create_thread(conn, "g", 12345678, "manual", now)
    rid = get_or_create_rule(conn, "g", ["daily programming", "rust"], now)
    get_or_create_thread(conn, "g", 12345999, f"rule:{rid}", now)

    doc = archive.new_document("g", 12345678, now)
    doc["posts"] = [
        {"no": 12345678, "sub": "Daily Programming Thread",
         "name": "Anonymous", "time": int(now.timestamp()),
         "com": "What are you working on?<br><span class=\"quote\">&gt;inb4 electron</span>",
         "tim": 1699887766543, "ext": ".png", "filename": "my screenshot",
         "fsize": len(PIXEL), "w": 1, "h": 1, "_deleted": False},
        {"no": 12345680, "name": "Anonymous",
         "time": int((now + timedelta(minutes=3)).timestamp()),
         "com": '<a href="#p12345678" class="quotelink">&gt;&gt;12345678</a>'
                '<br>rewriting it in <s>Rust</s> Zig',
         "_deleted": False},
        {"no": 12345682, "name": "Anonymous",
         "time": int((now + timedelta(minutes=5)).timestamp()),
         "com": '<a href="#p12345680" class="quotelink">&gt;&gt;12345680</a>'
                '<br>this post was deleted by a moderator',
         "_deleted": True},
        {"no": 12345684, "name": "Anonymous",
         "time": int((now + timedelta(minutes=9)).timestamp()),
         "com": '<a href="#p99999999" class="quotelink">&gt;&gt;99999999</a>'
                '<br>odkaz mimo thread', "_deleted": False},
    ]
    doc["media"] = {"1699887766543": {"ext": ".png", "file": "ok",
                                      "thumb": "ok", "bytes": 2 * len(PIXEL)}}
    archive.save_thread(cfg.archive_dir, doc)

    directory = archive.thread_dir(cfg.archive_dir, "g", 12345678)
    (directory / "1699887766543.png").write_bytes(PIXEL)
    (directory / "1699887766543s.jpg").write_bytes(PIXEL)

    repo.add_media(conn, tid, 1699887766543, ".png", "file")
    repo.add_media(conn, tid, 1699887766543, ".png", "thumb")
    repo.mark_media_ok(conn, tid, 1699887766543, "file", len(PIXEL))
    repo.mark_media_ok(conn, tid, 1699887766543, "thumb", len(PIXEL))
    repo.mark_polled(conn, tid, now=now, next_poll_at=now + timedelta(seconds=60),
                     poll_interval=60, last_modified=None, post_count=4,
                     subject="Daily Programming Thread")
    repo.recompute_thread_bytes(conn, tid)
    conn.close()
    print(f"fixture zapsána do {cfg.data_dir}")


if __name__ == "__main__":
    main()
