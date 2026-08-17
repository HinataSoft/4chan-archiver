import sqlite3
from datetime import datetime

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
        where.append("LOWER(COALESCE(subject, '')) LIKE ?")
        params.append(f"%{q.lower()}%")
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
