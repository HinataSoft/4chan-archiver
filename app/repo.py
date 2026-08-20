import json
import sqlite3
from datetime import datetime, timedelta

ERROR_THRESHOLD = 10


def iso(dt: datetime) -> str:
    return dt.isoformat()


def add_thread(conn, board: str, no: int, source: str, now: datetime) -> int | None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO threads"
        " (board, no, status, source, first_seen, next_poll_at, poll_interval)"
        " VALUES (?, ?, 'live', ?, ?, ?, 60)",
        (board, no, source, iso(now), iso(now)),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


def find_thread(conn, board: str, no: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM threads WHERE board = ? AND no = ?", (board, no)).fetchone()


def get_thread(conn, thread_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()


def list_threads(conn, *, status=None, board=None, q=None,
                 limit=100, offset=0) -> list[sqlite3.Row]:
    where, params = ["1=1"], []
    if status:
        where.append("status = ?")
        params.append(status)
    if board:
        where.append("board = ?")
        params.append(board)
    if q:
        escaped_q = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("LOWER(COALESCE(subject, '')) LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped_q}%")
    params.extend([limit, offset])
    return conn.execute(
        f"SELECT * FROM threads WHERE {' AND '.join(where)}"
        " ORDER BY first_seen DESC, id DESC LIMIT ? OFFSET ?", params).fetchall()


def due_threads(conn, now: datetime, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM threads WHERE status IN ('live', 'error')"
        " AND next_poll_at <= ? ORDER BY next_poll_at LIMIT ?",
        (iso(now), limit)).fetchall()


def mark_polled(conn, thread_id, *, now, next_poll_at, poll_interval,
                last_modified, post_count, subject) -> None:
    conn.execute(
        "UPDATE threads SET status='live', last_polled=?, next_poll_at=?,"
        " poll_interval=?, last_modified=?, post_count=?, subject=?,"
        " fail_count=0, last_error=NULL WHERE id=?",
        (iso(now), iso(next_poll_at), poll_interval, last_modified,
         post_count, subject, thread_id),
    )


def mark_unchanged(conn, thread_id, *, now, next_poll_at, poll_interval) -> None:
    conn.execute(
        "UPDATE threads SET status='live', last_polled=?, next_poll_at=?,"
        " poll_interval=?, fail_count=0, last_error=NULL WHERE id=?",
        (iso(now), iso(next_poll_at), poll_interval, thread_id),
    )


def mark_dead(conn, thread_id, now: datetime) -> None:
    conn.execute(
        "UPDATE threads SET status='dead', died_at=?, last_polled=? WHERE id=?",
        (iso(now), iso(now), thread_id),
    )


def set_thread_enabled(conn, thread_id, *, enabled: bool, now: datetime,
                       poll_interval: int) -> bool:
    """Pozastaví nebo obnoví pollování threadu; archiv zůstává nedotčený.

    Přechod je povolen jen mezi 'disabled' a živými stavy — mrtvý thread
    oživit nelze, 4chan ho už nevrací. Vrací False, když byl thread ve stavu,
    ze kterého přechod nedává smysl; podmínka je součástí UPDATE, takže dva
    souběžné požadavky nemůžou stav přepsat jeden druhému.
    """
    if enabled:
        # Obnovení je čistý restart: fail_count i chyba z minula jdou pryč,
        # ať se thread rovnou zkusí a nezdědí starý backoff.
        cur = conn.execute(
            "UPDATE threads SET status='live', next_poll_at=?, poll_interval=?,"
            " fail_count=0, last_error=NULL WHERE id=? AND status='disabled'",
            (iso(now), poll_interval, thread_id))
    else:
        cur = conn.execute(
            "UPDATE threads SET status='disabled' WHERE id=?"
            " AND status IN ('live', 'error')", (thread_id,))
    return cur.rowcount > 0


def mark_failure(conn, thread_id, *, now, error, next_poll_at, poll_interval) -> None:
    conn.execute(
        "UPDATE threads SET fail_count = fail_count + 1, last_error=?,"
        " last_polled=?, next_poll_at=?, poll_interval=?,"
        " status = CASE WHEN fail_count + 1 >= ? THEN 'error' ELSE status END"
        " WHERE id=?",
        (error[:500], iso(now), iso(next_poll_at), poll_interval,
         ERROR_THRESHOLD, thread_id),
    )


def delete_thread(conn, thread_id) -> None:
    conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))


MEDIA_FAIL_THRESHOLD = 3


def add_rule(conn, board: str, keywords: list[str], now: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO rules (board, keywords, enabled, created_at)"
        " VALUES (?, ?, 1, ?)",
        (board, json.dumps(keywords), iso(now)),
    )
    return cur.lastrowid


def get_rule(conn, rule_id) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()


def list_rules(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM rules ORDER BY board, id").fetchall()


def rule_keywords(row) -> list[str]:
    return json.loads(row["keywords"])


def update_rule(conn, rule_id, *, keywords=None, enabled=None) -> None:
    sets, params = [], []
    if keywords is not None:
        sets.append("keywords = ?")
        params.append(json.dumps(keywords))
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not sets:
        return
    params.append(rule_id)
    conn.execute(f"UPDATE rules SET {', '.join(sets)} WHERE id = ?", params)


def delete_rule(conn, rule_id) -> None:
    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def due_rules(conn, now: datetime, scan_interval: int) -> list[sqlite3.Row]:
    cutoff = iso(now - timedelta(seconds=scan_interval))
    return conn.execute(
        "SELECT * FROM rules WHERE enabled = 1"
        " AND (last_scan_at IS NULL OR last_scan_at <= ?) ORDER BY id",
        (cutoff,)).fetchall()


def mark_rule_scanned(conn, rule_id, now: datetime, error: str | None = None) -> None:
    conn.execute(
        "UPDATE rules SET last_scan_at = ?, last_error = ? WHERE id = ?",
        (iso(now), error[:500] if error else None, rule_id))


def add_media(conn, thread_id, tim: int, ext: str, kind: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO media (thread_id, tim, ext, kind, status)"
        " VALUES (?, ?, ?, ?, 'pending')", (thread_id, tim, ext, kind))


def pending_media(conn, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT m.*, t.board, t.no FROM media m JOIN threads t ON t.id = m.thread_id"
        " WHERE m.status = 'pending' ORDER BY m.thread_id, m.tim LIMIT ?",
        (limit,)).fetchall()


def mark_media_ok(conn, thread_id, tim, kind, size: int) -> None:
    conn.execute(
        "UPDATE media SET status='ok', bytes=?, last_error=NULL"
        " WHERE thread_id=? AND tim=? AND kind=?", (size, thread_id, tim, kind))


def mark_media_failed(conn, thread_id, tim, kind, error: str) -> None:
    conn.execute(
        "UPDATE media SET fail_count = fail_count + 1, last_error = ?,"
        " status = CASE WHEN fail_count + 1 >= ? THEN 'failed' ELSE 'pending' END"
        " WHERE thread_id=? AND tim=? AND kind=?",
        (error[:500], MEDIA_FAIL_THRESHOLD, thread_id, tim, kind))


def retry_failed_media(conn, thread_id) -> int:
    cur = conn.execute(
        "UPDATE media SET status='pending', fail_count=0, last_error=NULL"
        " WHERE thread_id = ? AND status = 'failed'", (thread_id,))
    return cur.rowcount


def recompute_thread_bytes(conn, thread_id) -> None:
    conn.execute(
        "UPDATE threads SET bytes = COALESCE("
        " (SELECT SUM(bytes) FROM media WHERE thread_id = ?), 0) WHERE id = ?",
        (thread_id, thread_id))


def stats(conn) -> dict:
    counts = {"live": 0, "dead": 0, "error": 0, "disabled": 0}
    for row in conn.execute("SELECT status, COUNT(*) c FROM threads GROUP BY status"):
        counts[row["status"]] = row["c"]
    row = conn.execute(
        "SELECT COALESCE(SUM(bytes), 0) b,"
        " SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) f,"
        " SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) p FROM media").fetchone()
    last = conn.execute("SELECT MAX(last_polled) m FROM threads").fetchone()["m"]
    errors = conn.execute(
        "SELECT id, board, no, last_error FROM threads"
        " WHERE last_error IS NOT NULL ORDER BY last_polled DESC LIMIT 10").fetchall()
    return {
        "threads": counts,
        "media_bytes": row["b"],
        "media_failed": row["f"] or 0,
        "media_pending": row["p"] or 0,
        "last_polled": last,
        "recent_errors": [dict(e) for e in errors],
    }


def failed_media_counts(conn, thread_ids) -> dict[int, int]:
    """Počet médií se status='failed' pro dané thready. Selhání médií se nikdy
    nepropíše do threads.last_error, takže bez tohohle čísla nemá UI jak zjistit,
    že je co retryovat."""
    ids = [int(i) for i in thread_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT thread_id, COUNT(*) c FROM media WHERE status = 'failed'"
        f" AND thread_id IN ({placeholders}) GROUP BY thread_id", ids)
    return {row["thread_id"]: row["c"] for row in rows}


def media_for_thread(conn, thread_id) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT tim, ext, kind, status, bytes FROM media WHERE thread_id = ?"
        " ORDER BY tim, kind", (thread_id,)).fetchall()
