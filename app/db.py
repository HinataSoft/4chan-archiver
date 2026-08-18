import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
  id            INTEGER PRIMARY KEY,
  board         TEXT NOT NULL,
  no            INTEGER NOT NULL,
  subject       TEXT,
  status        TEXT NOT NULL,
  source        TEXT NOT NULL,
  first_seen    TEXT NOT NULL,
  last_polled   TEXT,
  next_poll_at  TEXT NOT NULL,
  poll_interval INTEGER NOT NULL,
  last_modified TEXT,
  post_count    INTEGER NOT NULL DEFAULT 0,
  bytes         INTEGER NOT NULL DEFAULT 0,
  fail_count    INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  died_at       TEXT,
  UNIQUE (board, no)
);

CREATE INDEX IF NOT EXISTS idx_threads_due ON threads (next_poll_at)
  WHERE status IN ('live', 'error');

CREATE TABLE IF NOT EXISTS rules (
  id           INTEGER PRIMARY KEY,
  board        TEXT NOT NULL,
  keywords     TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL,
  last_scan_at TEXT,
  last_error   TEXT
);

CREATE TABLE IF NOT EXISTS media (
  thread_id  INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  tim        INTEGER NOT NULL,
  ext        TEXT NOT NULL,
  kind       TEXT NOT NULL,
  status     TEXT NOT NULL,
  bytes      INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  PRIMARY KEY (thread_id, tim, kind)
);

CREATE INDEX IF NOT EXISTS idx_media_pending ON media (status)
  WHERE status = 'pending';
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def connect(db_path: Path, *, create_schema: bool = True) -> sqlite3.Connection:
    """Otevře databázi. create_schema=False přeskočí DDL — web ho pouští
    jednou při startu, ne při každém requestu."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI dispatchuje sync dependency a sync handler
    # přes samostatné threadpool hopy bez thread affinity, takže spojení vzniklé
    # v get_conn se běžně používá a zavírá na jiném vlákně. Každý request má
    # vlastní spojení a v jednu chvíli se ho dotýká jen jedno vlákno.
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    if create_schema:
        init_schema(conn)
    return conn
