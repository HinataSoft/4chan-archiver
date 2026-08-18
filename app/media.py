import logging

from app import archive, repo
from app.config import Config

log = logging.getLogger(__name__)


def sync_media_doc(conn, cfg: Config, thread_id: int) -> None:
    thread = repo.get_thread(conn, thread_id)
    if thread is None:
        return
    doc = archive.load_thread(cfg.archive_dir, thread["board"], thread["no"])
    if doc is None:
        return
    entries: dict[str, dict] = {}
    for row in repo.media_for_thread(conn, thread_id):
        entry = entries.setdefault(str(row["tim"]), {"ext": row["ext"], "bytes": 0})
        entry[row["kind"]] = row["status"]
        entry["bytes"] += row["bytes"]
    doc["media"] = entries
    archive.save_thread(cfg.archive_dir, doc)


async def download_pending(conn, client, cfg: Config, limit: int = 20) -> dict[str, int]:
    rows = repo.pending_media(conn, limit)
    counts = {"ok": 0, "failed": 0}
    touched: set[int] = set()

    for row in rows:
        dest = archive.media_path(cfg.archive_dir, row["board"], row["no"],
                                  row["tim"], row["ext"], row["kind"])
        touched.add(row["thread_id"])
        if dest.exists():
            repo.mark_media_ok(conn, row["thread_id"], row["tim"], row["kind"],
                               dest.stat().st_size)
            counts["ok"] += 1
            continue
        url = client.media_url(row["board"], row["tim"], row["ext"], row["kind"])
        try:
            size = await client.download(url, dest)
        except Exception as exc:
            log.warning("stažení %s selhalo: %s", url, exc)
            repo.mark_media_failed(conn, row["thread_id"], row["tim"], row["kind"],
                                   f"{type(exc).__name__}: {exc}")
            counts["failed"] += 1
            continue
        repo.mark_media_ok(conn, row["thread_id"], row["tim"], row["kind"], size)
        counts["ok"] += 1

    for thread_id in touched:
        repo.recompute_thread_bytes(conn, thread_id)
        sync_media_doc(conn, cfg, thread_id)
    return counts
