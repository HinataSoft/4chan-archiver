from app.db import connect


def test_creates_schema_and_pragmas(tmp_path):
    conn = connect(tmp_path / "app.db")
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"threads", "rules", "media"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_is_idempotent(tmp_path):
    path = tmp_path / "app.db"
    connect(path).close()
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 0


def test_creates_parent_directory(tmp_path):
    conn = connect(tmp_path / "nested" / "deeper" / "app.db")
    assert (tmp_path / "nested" / "deeper" / "app.db").exists()
    conn.close()


def test_rows_are_mappings(tmp_path):
    conn = connect(tmp_path / "app.db")
    conn.execute(
        "INSERT INTO rules (board, keywords, enabled, created_at)"
        " VALUES ('g', '[\"x\"]', 1, 'now')")
    row = conn.execute("SELECT * FROM rules").fetchone()
    assert row["board"] == "g"


def test_thread_uniqueness_is_enforced(tmp_path):
    import sqlite3
    import pytest
    conn = connect(tmp_path / "app.db")
    sql = ("INSERT INTO threads (board, no, status, source, first_seen,"
           " next_poll_at, poll_interval) VALUES ('g', 1, 'live', 'manual', 't', 't', 60)")
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
