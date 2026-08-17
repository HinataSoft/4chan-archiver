import logging
from datetime import datetime, timedelta

from app import archive, repo
from app.config import Config
from app.text import html_to_text

log = logging.getLogger(__name__)


def next_interval(cfg: Config, current: int, changed: bool) -> int:
    if changed:
        return cfg.poll_min_interval
    return min(int(current * 1.5), cfg.poll_max_interval)


def _subject(posts: list[dict]) -> str | None:
    if not posts:
        return None
    return html_to_text(posts[0].get("sub")) or None


async def poll_thread(conn, client, cfg: Config, row, now: datetime) -> str:
    board, no = row["board"], row["no"]
    try:
        resp = await client.fetch_thread(board, no, row["last_modified"])
    except Exception as exc:  # síť, timeout, rozbité JSON
        log.warning("poll %s/%s selhal: %s", board, no, exc)
        repo.mark_failure(conn, row["id"], now=now, error=f"{type(exc).__name__}: {exc}",
                          next_poll_at=now + timedelta(seconds=cfg.poll_max_interval),
                          poll_interval=cfg.poll_max_interval)
        return "error"

    if resp.status == 404:
        doc = archive.load_thread(cfg.archive_dir, board, no)
        if doc is not None:
            doc["status"] = "dead"
            doc["died_at"] = repo.iso(now)
            archive.save_thread(cfg.archive_dir, doc)
        repo.mark_dead(conn, row["id"], now)
        return "dead"

    if resp.status == 304:
        interval = next_interval(cfg, row["poll_interval"], changed=False)
        repo.mark_unchanged(conn, row["id"], now=now,
                            next_poll_at=now + timedelta(seconds=interval),
                            poll_interval=interval)
        return "unchanged"

    doc = archive.load_thread(cfg.archive_dir, board, no) \
        or archive.new_document(board, no, now)
    doc["posts"] = archive.merge_posts(doc.get("posts", []), resp.data.get("posts", []))
    doc["last_updated"] = repo.iso(now)
    archive.save_thread(cfg.archive_dir, doc)

    for tim, ext in archive.media_entries(doc["posts"]):
        repo.add_media(conn, row["id"], tim, ext, "file")
        repo.add_media(conn, row["id"], tim, ext, "thumb")

    interval = next_interval(cfg, row["poll_interval"], changed=True)
    repo.mark_polled(conn, row["id"], now=now,
                     next_poll_at=now + timedelta(seconds=interval),
                     poll_interval=interval, last_modified=resp.last_modified,
                     post_count=len(doc["posts"]), subject=_subject(doc["posts"]))
    return "updated"


async def poll_due(conn, client, cfg: Config, now: datetime,
                   limit: int = 50) -> dict[str, int]:
    counts = {"updated": 0, "unchanged": 0, "dead": 0, "error": 0}
    for row in repo.due_threads(conn, now, limit):
        counts[await poll_thread(conn, client, cfg, row, now)] += 1
    return counts
