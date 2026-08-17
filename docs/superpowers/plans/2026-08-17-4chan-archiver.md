# 4chan Archiver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Služba, která trvale archivuje vybrané 4chan thready (ručně vložené i automaticky nalezené podle klíčových slov) a umožňuje je prohlížet offline v UI podobném 4chanu.

**Architecture:** Jeden Python balík, dva entrypointy: `app.web` (FastAPI, jen CRUD nad SQLite) a `app.worker` (asyncio smyčka — poller, scanner, stahovač médií; jediná komponenta chodící na síť). Obsah threadů leží na disku jako `data/archive/<board>/<no>/thread.json` + média a servíruje ho nginx (nebo v devu `StaticFiles` na stejných cestách). SQLite drží jen provozní stav a je z archivu regenerovatelná.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, httpx (async, `MockTransport` v testech), stdlib `sqlite3` (WAL), pytest + pytest-asyncio, vanilla JS bez build stepu, nginx, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-17-4chan-archiver-design.md`

## Global Constraints

- Python 3.12. Závislosti jen: `fastapi`, `uvicorn[standard]`, `httpx`, `pytest`, `pytest-asyncio`. Nic dalšího nepřidávej.
- Rate limit na `a.4cdn.org` je **1 request/s** (vyžadují to API pravidla 4chanu). Na `i.4cdn.org` výchozí 4 req/s.
- Každý JSON požadavek posílá `If-Modified-Since` z uloženého `last_modified` a rozlišuje `200` / `304` / `404`.
- **Posty se merguje, nikdy nepřepisují.** Post, který zmizel z API odpovědi, zůstává v `thread.json` a dostane `"_deleted": true`.
- Obsah postů se **nikdy** neukládá do SQLite. Jediný zdroj pravdy je `thread.json`.
- Všechny zápisy na disk jsou atomické: `thread.json.tmp` → rename, `{tim}{ext}.part` → rename.
- Média se na disk ukládají pod jménem `{tim}{ext}` (thumb `{tim}s.jpg`), **nikdy** pod původním `filename` z postu.
- Testy nesmí dělat žádný skutečný síťový provoz. Veškerá HTTP komunikace jde přes `httpx.AsyncClient` s injektovaným transportem.
- Klient je vanilla JS bez build stepu. Žádné npm, žádný bundler.
- Čas se do funkcí vždy **předává jako parametr** `now: datetime` (nikdy `datetime.now()` uvnitř logiky) — jinak nejdou testovat backoffy.
- TDD: každý task začíná failujícím testem a končí commitem.

## File Structure

```
pyproject.toml               závislosti, pytest config
Dockerfile                   jeden image pro app i worker
docker-compose.yml           nginx + app + worker + volume
nginx/nginx.conf             alias /archive, proxy /api, auth_basic
scripts/make_fixture.py      vygeneruje ukázkový archiv pro ruční ověření klienta

app/config.py                Config dataclass + load_config(env)
app/urls.py                  parser 4chan URL → ThreadRef
app/text.py                  HTML → plain text, keyword matching
app/db.py                    connect() + init_schema()
app/repo.py                  všechny SQL dotazy (threads, rules, media)
app/archive.py               cesty, atomický zápis, merge postů, mazání
app/fourchan.py              RateLimiter + FourchanClient (JSON i download)
app/poller.py                poll jednoho threadu + výběr due threadů
app/scanner.py               scan boardu podle pravidel
app/media.py                 stahování pending médií
app/worker.py                asyncio smyčka spojující poller/scanner/media
app/web.py                   create_app() + všechny HTTP endpointy

static/index.html            dashboard + vložení URL + seznam threadů
static/rules.html            správa pravidel
static/thread.html           prohlížeč threadu
static/css/style.css         4chan-like styl
static/js/api.js             fetch wrappery nad /api
static/js/dashboard.js       logika index.html
static/js/rules.js           logika rules.html
static/js/comment.js         bezpečný render 4chan HTML komentáře
static/js/thread.js          render threadu, quotelinky, backlinky, média

tests/conftest.py            fixtures: cfg, conn, Fake4chan
tests/fake4chan.py           fake 4chan přes httpx.MockTransport
tests/test_urls.py           tests/test_text.py       tests/test_archive.py
tests/test_repo.py           tests/test_fourchan.py   tests/test_poller.py
tests/test_scanner.py        tests/test_media.py      tests/test_web.py
tests/test_integration.py    end-to-end přes fake 4chan
```

Dělení je podle odpovědnosti, ne podle vrstvy: `archive.py` vlastní všechno, co se dotýká souborů threadu; `repo.py` vlastní všechen SQL; `fourchan.py` vlastní všechno, co jde ven na síť. Poller/scanner/media jsou tenké orchestrace nad těmito třemi.

---

### Task 1: Skeleton projektu a konfigurace

**Files:**
- Create: `pyproject.toml`, `app/__init__.py`, `app/config.py`, `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nic
- Produces: `Config` (frozen dataclass) s atributy `data_dir: Path`, `db_path: Path`, `archive_dir: Path`, `serve_static: bool`, `poll_min_interval: int`, `poll_max_interval: int`, `scan_interval: int`, `api_rate: float`, `media_rate: float`, `log_level: str`; funkce `load_config(env: Mapping[str, str] | None = None) -> Config`

- [ ] **Step 1: Vytvoř `pyproject.toml`**

```toml
[project]
name = "fourchan-archiver"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "httpx",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["app*"]
```

- [ ] **Step 2: Nainstaluj závislosti do venv**

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
```

Na Linuxu `.venv/bin/python`. Dál v plánu se `pytest` spouští z tohoto venv.

- [ ] **Step 3: Napiš failující test**

Vytvoř `tests/test_config.py`:

```python
from pathlib import Path

from app.config import load_config


def test_defaults():
    cfg = load_config({})
    assert cfg.data_dir == Path("/data")
    assert cfg.db_path == Path("/data/app.db")
    assert cfg.archive_dir == Path("/data/archive")
    assert cfg.serve_static is False
    assert cfg.poll_min_interval == 60
    assert cfg.poll_max_interval == 600
    assert cfg.scan_interval == 300
    assert cfg.api_rate == 1.0
    assert cfg.media_rate == 4.0


def test_env_overrides():
    cfg = load_config({
        "DATA_DIR": "/tmp/x",
        "SERVE_STATIC": "1",
        "POLL_MIN_INTERVAL": "30",
        "API_RATE": "0.5",
    })
    assert cfg.data_dir == Path("/tmp/x")
    assert cfg.db_path == Path("/tmp/x/app.db")
    assert cfg.serve_static is True
    assert cfg.poll_min_interval == 30
    assert cfg.api_rate == 0.5
```

- [ ] **Step 4: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 5: Implementuj `app/config.py`**

Vytvoř prázdný `app/__init__.py` a `tests/__init__.py`, pak:

```python
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    serve_static: bool
    poll_min_interval: int
    poll_max_interval: int
    scan_interval: int
    api_rate: float
    media_rate: float
    log_level: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
    return Config(
        data_dir=Path(e.get("DATA_DIR", "/data")),
        serve_static=e.get("SERVE_STATIC", "0") == "1",
        poll_min_interval=int(e.get("POLL_MIN_INTERVAL", "60")),
        poll_max_interval=int(e.get("POLL_MAX_INTERVAL", "600")),
        scan_interval=int(e.get("SCAN_INTERVAL", "300")),
        api_rate=float(e.get("API_RATE", "1")),
        media_rate=float(e.get("MEDIA_RATE", "4")),
        log_level=e.get("LOG_LEVEL", "INFO"),
    )
```

`db_path` a `archive_dir` jsou property, ne pole — nemohou se rozejít s `data_dir`.

- [ ] **Step 6: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: PASS (2 testy)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml app tests && git commit -m "feat: project skeleton and config loading"
```

---

### Task 2: Parser 4chan URL

**Files:**
- Create: `app/urls.py`, `tests/test_urls.py`

**Interfaces:**
- Consumes: nic
- Produces: `ThreadRef` (frozen dataclass, pole `board: str`, `no: int`); `parse_thread_url(raw: str) -> ThreadRef` — vyhazuje `ValueError` na neplatný vstup

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_urls.py`:

```python
import pytest

from app.urls import ThreadRef, parse_thread_url


@pytest.mark.parametrize("raw", [
    "https://boards.4chan.org/g/thread/12345678",
    "https://boards.4channel.org/g/thread/12345678",
    "http://boards.4chan.org/g/thread/12345678/some-slug-text",
    "https://boards.4chan.org/g/thread/12345678#p12345690",
    "boards.4chan.org/g/thread/12345678",
    "  https://boards.4chan.org/g/thread/12345678/  ",
    "g/12345678",
    "/g/thread/12345678",
])
def test_accepts_known_forms(raw):
    assert parse_thread_url(raw) == ThreadRef(board="g", no=12345678)


def test_board_with_digits():
    assert parse_thread_url("https://boards.4chan.org/vr/thread/999") == ThreadRef("vr", 999)


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "https://example.com/g/thread/123",
    "https://boards.4chan.org/g/catalog",
    "https://boards.4chan.org/g/thread/abc",
    "g/",
    "12345678",
    "https://boards.4chan.org/thread/12345678",
])
def test_rejects_garbage(raw):
    with pytest.raises(ValueError):
        parse_thread_url(raw)
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_urls.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.urls'`

- [ ] **Step 3: Implementuj `app/urls.py`**

```python
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
        raise ValueError("prázdný vstup")
    for pattern in (_FULL, _PATH, _SHORT):
        m = pattern.match(text)
        if m:
            return ThreadRef(board=m.group("board").lower(), no=int(m.group("no")))
    raise ValueError(f"nerozpoznané URL threadu: {raw!r}")
```

Pozor na pořadí: `_FULL` musí být první, jinak by `_SHORT` chytil část hostname. `_HOSTS` je jen dokumentační konstanta pro čtenáře — omezení hostitele je zapečeno v `_FULL`.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_urls.py -v`
Expected: PASS (11 testů)

- [ ] **Step 5: Commit**

```bash
git add app/urls.py tests/test_urls.py && git commit -m "feat: tolerant 4chan thread URL parser"
```

---

### Task 3: Převod HTML komentáře na text a keyword matching

**Files:**
- Create: `app/text.py`, `tests/test_text.py`

**Interfaces:**
- Consumes: nic
- Produces: `html_to_text(html: str) -> str`; `op_search_text(post: dict) -> str`; `matches_keywords(haystack: str, keywords: Sequence[str]) -> bool`

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_text.py`:

```python
from app.text import html_to_text, matches_keywords, op_search_text


def test_strips_tags_and_unescapes_entities():
    raw = '<a href="#p1" class="quotelink">&gt;&gt;1</a><br>Rust &amp; C++ <b>rocks</b>'
    assert html_to_text(raw) == ">>1 Rust & C++ rocks"


def test_br_becomes_space_not_glue():
    assert html_to_text("foo<br>bar") == "foo bar"


def test_handles_empty_and_none():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""


def test_op_search_text_joins_subject_and_comment():
    post = {"sub": "Daily Programming", "com": "post your <b>setup</b>"}
    assert op_search_text(post) == "Daily Programming post your setup"


def test_op_search_text_survives_missing_fields():
    assert op_search_text({}) == ""
    assert op_search_text({"com": "only comment"}) == "only comment"


def test_matching_is_case_insensitive_substring():
    assert matches_keywords("Daily Programming Thread", ["programming"]) is True
    assert matches_keywords("Daily Programming Thread", ["RUST", "daily"]) is True
    assert matches_keywords("Daily Programming Thread", ["rust"]) is False


def test_entity_encoded_text_is_matchable():
    text = html_to_text("looking for &gt;&gt;&gt; deals &amp; steals")
    assert matches_keywords(text, ["& steals"]) is True


def test_empty_keywords_never_match():
    assert matches_keywords("anything", []) is False
    assert matches_keywords("anything", ["", "  "]) is False
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.text'`

- [ ] **Step 3: Implementuj `app/text.py`**

```python
import html
import re
from collections.abc import Sequence

_BREAK = re.compile(r"<br\s*/?>|</p>|</div>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = _BREAK.sub(" ", raw)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip()


def op_search_text(post: dict) -> str:
    parts = [html_to_text(post.get("sub")), html_to_text(post.get("com"))]
    return " ".join(p for p in parts if p)


def matches_keywords(haystack: str, keywords: Sequence[str]) -> bool:
    hay = haystack.lower()
    return any(k.strip() and k.strip().lower() in hay for k in keywords)
```

Pořadí operací je podstatné: nejdřív odstranit tagy, **až potom** rozbalit entity. Opačně by `&lt;b&gt;` po unescape vzniklo jako `<b>` a regulár na tagy by ho smazal jako značku, i když to byl doslovný text uživatele.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_text.py -v`
Expected: PASS (8 testů)

- [ ] **Step 5: Commit**

```bash
git add app/text.py tests/test_text.py && git commit -m "feat: HTML-to-text conversion and keyword matching"
```

---

### Task 4: SQLite schéma a připojení

**Files:**
- Create: `app/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: nic
- Produces: `connect(db_path: Path) -> sqlite3.Connection` (WAL, `busy_timeout=5000`, `row_factory=sqlite3.Row`, zapnuté foreign keys, schéma vytvořené); `init_schema(conn: sqlite3.Connection) -> None`

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Implementuj `app/db.py`**

```python
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


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    init_schema(conn)
    return conn
```

`isolation_level=None` = autocommit; každý dotaz v `repo.py` je jedna krátká transakce, což je přesně to, co WAL potřebuje, aby si `app` a `worker` nešlapaly po zápisech.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: PASS (5 testů)

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py && git commit -m "feat: SQLite schema and connection setup"
```

---

### Task 5: Repository — thready

**Files:**
- Create: `app/repo.py`, `tests/conftest.py`, `tests/test_repo.py`

**Interfaces:**
- Consumes: `app.db.connect`
- Produces (všechny berou `conn` jako první argument, `now` je `datetime`):
  - `add_thread(conn, board: str, no: int, source: str, now: datetime) -> int | None` — `None` když už existuje
  - `find_thread(conn, board: str, no: int) -> sqlite3.Row | None`
  - `get_thread(conn, thread_id: int) -> sqlite3.Row | None`
  - `list_threads(conn, *, status=None, board=None, q=None, limit=100, offset=0) -> list[sqlite3.Row]`
  - `due_threads(conn, now: datetime, limit: int) -> list[sqlite3.Row]`
  - `mark_polled(conn, thread_id, *, now, next_poll_at, poll_interval, last_modified, post_count, subject) -> None`
  - `mark_unchanged(conn, thread_id, *, now, next_poll_at, poll_interval) -> None`
  - `mark_dead(conn, thread_id, now: datetime) -> None`
  - `mark_failure(conn, thread_id, *, now, error: str, next_poll_at, poll_interval) -> None`
  - `delete_thread(conn, thread_id) -> None`
  - `iso(dt: datetime) -> str`

- [ ] **Step 1: Vytvoř sdílené fixtures**

Vytvoř `tests/conftest.py`:

```python
from datetime import datetime, timezone

import pytest

from app.config import load_config
from app.db import connect


@pytest.fixture
def cfg(tmp_path):
    return load_config({"DATA_DIR": str(tmp_path)})


@pytest.fixture
def conn(cfg):
    c = connect(cfg.db_path)
    yield c
    c.close()


@pytest.fixture
def now():
    return datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: Napiš failující test**

Vytvoř `tests/test_repo.py`:

```python
from datetime import timedelta

from app import repo


def test_add_thread_returns_id_then_none_on_duplicate(conn, now):
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    assert isinstance(tid, int)
    assert repo.add_thread(conn, "g", 123, "rule:1", now) is None


def test_new_thread_is_live_and_due_immediately(conn, now):
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    row = repo.get_thread(conn, tid)
    assert row["status"] == "live"
    assert row["source"] == "manual"
    assert [r["id"] for r in repo.due_threads(conn, now, 10)] == [tid]


def test_due_threads_respects_next_poll_at(conn, now):
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    repo.mark_unchanged(conn, tid, now=now,
                        next_poll_at=now + timedelta(seconds=60), poll_interval=60)
    assert repo.due_threads(conn, now, 10) == []
    assert len(repo.due_threads(conn, now + timedelta(seconds=61), 10)) == 1


def test_due_threads_includes_error_but_not_dead(conn, now):
    live = repo.add_thread(conn, "g", 1, "manual", now)
    dead = repo.add_thread(conn, "g", 2, "manual", now)
    errored = repo.add_thread(conn, "g", 3, "manual", now)
    repo.mark_dead(conn, dead, now)
    repo.mark_failure(conn, errored, now=now, error="boom",
                      next_poll_at=now, poll_interval=600)
    for _ in range(9):
        repo.mark_failure(conn, errored, now=now, error="boom",
                          next_poll_at=now, poll_interval=600)
    assert repo.get_thread(conn, errored)["status"] == "error"
    ids = {r["id"] for r in repo.due_threads(conn, now, 10)}
    assert ids == {live, errored}


def test_mark_polled_updates_metadata_and_clears_failures(conn, now):
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    repo.mark_failure(conn, tid, now=now, error="boom",
                      next_poll_at=now, poll_interval=90)
    repo.mark_polled(conn, tid, now=now, next_poll_at=now + timedelta(seconds=60),
                     poll_interval=60, last_modified="Mon, 17 Aug 2026 12:00:00 GMT",
                     post_count=7, subject="Daily thread")
    row = repo.get_thread(conn, tid)
    assert row["post_count"] == 7
    assert row["subject"] == "Daily thread"
    assert row["last_modified"] == "Mon, 17 Aug 2026 12:00:00 GMT"
    assert row["fail_count"] == 0
    assert row["last_error"] is None
    assert row["status"] == "live"


def test_mark_dead_stops_polling(conn, now):
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    repo.mark_dead(conn, tid, now)
    row = repo.get_thread(conn, tid)
    assert row["status"] == "dead"
    assert row["died_at"] == repo.iso(now)
    assert repo.due_threads(conn, now, 10) == []


def test_error_status_only_after_ten_failures(conn, now):
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    for i in range(9):
        repo.mark_failure(conn, tid, now=now, error="boom",
                          next_poll_at=now, poll_interval=600)
        assert repo.get_thread(conn, tid)["status"] == "live"
    repo.mark_failure(conn, tid, now=now, error="boom",
                      next_poll_at=now, poll_interval=600)
    assert repo.get_thread(conn, tid)["status"] == "error"


def test_list_threads_filters(conn, now):
    a = repo.add_thread(conn, "g", 1, "manual", now)
    repo.add_thread(conn, "b", 2, "manual", now)
    repo.mark_polled(conn, a, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject="Rust general")
    assert len(repo.list_threads(conn, board="g")) == 1
    assert len(repo.list_threads(conn, q="rust")) == 1
    assert len(repo.list_threads(conn, q="python")) == 0
    assert len(repo.list_threads(conn, status="live")) == 2


def test_delete_thread(conn, now):
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    repo.delete_thread(conn, tid)
    assert repo.get_thread(conn, tid) is None
```

- [ ] **Step 3: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.repo'`

- [ ] **Step 4: Implementuj `app/repo.py`**

```python
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
```

Poznámka k `mark_failure`: stav se počítá v SQL z `fail_count + 1`, ne v Pythonu — jinak by dva procesy mohly přečíst starou hodnotu a přepsat si výsledek. `iso()` ukládá ISO 8601 s timezone, takže řetězcové porovnání `next_poll_at <= ?` odpovídá chronologickému pořadí (všechny časy jsou UTC).

- [ ] **Step 5: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_repo.py -v`
Expected: PASS (9 testů)

- [ ] **Step 6: Commit**

```bash
git add app/repo.py tests/conftest.py tests/test_repo.py && git commit -m "feat: thread repository queries"
```

---

### Task 6: Repository — pravidla, média a statistiky

**Files:**
- Modify: `app/repo.py` (přidat funkce na konec)
- Modify: `tests/test_repo.py` (přidat testy na konec)

**Interfaces:**
- Consumes: `app.repo` z Tasku 5
- Produces:
  - `add_rule(conn, board: str, keywords: list[str], now: datetime) -> int`
  - `list_rules(conn) -> list[sqlite3.Row]`, `get_rule(conn, rule_id) -> sqlite3.Row | None`
  - `update_rule(conn, rule_id, *, keywords: list[str] | None = None, enabled: bool | None = None) -> None`
  - `delete_rule(conn, rule_id) -> None`
  - `due_rules(conn, now: datetime, scan_interval: int) -> list[sqlite3.Row]`
  - `mark_rule_scanned(conn, rule_id, now: datetime, error: str | None = None) -> None`
  - `rule_keywords(row) -> list[str]`
  - `add_media(conn, thread_id, tim: int, ext: str, kind: str) -> None`
  - `pending_media(conn, limit: int) -> list[sqlite3.Row]`
  - `mark_media_ok(conn, thread_id, tim, kind, size: int) -> None`
  - `mark_media_failed(conn, thread_id, tim, kind, error: str) -> None`
  - `retry_failed_media(conn, thread_id) -> int`
  - `recompute_thread_bytes(conn, thread_id) -> None`
  - `stats(conn) -> dict`

- [ ] **Step 1: Napiš failující testy**

Přidej na konec `tests/test_repo.py`:

```python
def test_rule_roundtrip(conn, now):
    rid = repo.add_rule(conn, "g", ["rust", "daily programming"], now)
    row = repo.get_rule(conn, rid)
    assert row["board"] == "g"
    assert repo.rule_keywords(row) == ["rust", "daily programming"]
    assert row["enabled"] == 1


def test_update_rule_changes_keywords_and_enabled(conn, now):
    rid = repo.add_rule(conn, "g", ["rust"], now)
    repo.update_rule(conn, rid, keywords=["zig"], enabled=False)
    row = repo.get_rule(conn, rid)
    assert repo.rule_keywords(row) == ["zig"]
    assert row["enabled"] == 0


def test_due_rules_skips_disabled_and_recently_scanned(conn, now):
    from datetime import timedelta
    a = repo.add_rule(conn, "g", ["rust"], now)
    b = repo.add_rule(conn, "b", ["cats"], now)
    repo.update_rule(conn, b, enabled=False)
    assert [r["id"] for r in repo.due_rules(conn, now, 300)] == [a]
    repo.mark_rule_scanned(conn, a, now)
    assert repo.due_rules(conn, now + timedelta(seconds=299), 300) == []
    assert len(repo.due_rules(conn, now + timedelta(seconds=301), 300)) == 1


def test_media_lifecycle(conn, now):
    tid = repo.add_thread(conn, "g", 1, "manual", now)
    repo.add_media(conn, tid, 1699887766543, ".webm", "file")
    repo.add_media(conn, tid, 1699887766543, ".webm", "file")  # idempotent
    assert len(repo.pending_media(conn, 10)) == 1
    repo.mark_media_ok(conn, tid, 1699887766543, "file", 4192304)
    assert repo.pending_media(conn, 10) == []
    repo.recompute_thread_bytes(conn, tid)
    assert repo.get_thread(conn, tid)["bytes"] == 4192304


def test_failed_media_can_be_retried(conn, now):
    tid = repo.add_thread(conn, "g", 1, "manual", now)
    repo.add_media(conn, tid, 111, ".jpg", "thumb")
    for _ in range(3):
        repo.mark_media_failed(conn, tid, 111, "thumb", "timeout")
    row = conn.execute("SELECT * FROM media").fetchone()
    assert row["status"] == "failed"
    assert row["fail_count"] == 3
    assert repo.pending_media(conn, 10) == []
    assert repo.retry_failed_media(conn, tid) == 1
    assert len(repo.pending_media(conn, 10)) == 1


def test_media_rows_die_with_their_thread(conn, now):
    tid = repo.add_thread(conn, "g", 1, "manual", now)
    repo.add_media(conn, tid, 111, ".jpg", "thumb")
    repo.delete_thread(conn, tid)
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0


def test_stats_counts_by_status(conn, now):
    live = repo.add_thread(conn, "g", 1, "manual", now)
    dead = repo.add_thread(conn, "g", 2, "rule:1", now)
    repo.mark_dead(conn, dead, now)
    repo.add_media(conn, live, 111, ".jpg", "thumb")
    repo.mark_media_ok(conn, live, 111, "thumb", 5000)
    s = repo.stats(conn)
    assert s["threads"]["live"] == 1
    assert s["threads"]["dead"] == 1
    assert s["threads"]["error"] == 0
    assert s["media_bytes"] == 5000
    assert s["media_failed"] == 0
```

- [ ] **Step 2: Spusť testy a ověř, že selžou**

Run: `.venv/Scripts/python -m pytest tests/test_repo.py -v`
Expected: FAIL — `AttributeError: module 'app.repo' has no attribute 'add_rule'`

- [ ] **Step 3: Doplň `app/repo.py`**

Přidej na začátek souboru `import json` a na konec:

```python
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
    counts = {"live": 0, "dead": 0, "error": 0}
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
```

Uprav import data na začátku souboru: `from datetime import datetime, timedelta`.

- [ ] **Step 4: Spusť testy a ověř, že projdou**

Run: `.venv/Scripts/python -m pytest tests/test_repo.py -v`
Expected: PASS (16 testů)

- [ ] **Step 5: Commit**

```bash
git add app/repo.py tests/test_repo.py && git commit -m "feat: rule, media and stats repository queries"
```

---

### Task 7: Archiv na disku — cesty, merge postů, atomický zápis

**Files:**
- Create: `app/archive.py`, `tests/test_archive.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `thread_dir(archive_dir: Path, board: str, no: int) -> Path`
  - `media_filename(tim: int, ext: str, kind: str) -> str` — `kind` je `"file"` nebo `"thumb"`
  - `media_path(archive_dir, board, no, tim, ext, kind) -> Path`
  - `new_document(board: str, no: int, now: datetime) -> dict`
  - `load_thread(archive_dir, board, no) -> dict | None`
  - `save_thread(archive_dir, doc: dict) -> None` — atomicky
  - `merge_posts(old: list[dict], new: list[dict]) -> list[dict]`
  - `media_entries(posts: list[dict]) -> list[tuple[int, str]]` — `(tim, ext)` postů s přílohou
  - `delete_thread_dir(archive_dir, board, no) -> None`

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_archive.py`:

```python
from app import archive


def test_media_filenames_follow_4chan_convention():
    assert archive.media_filename(1699887766543, ".webm", "file") == "1699887766543.webm"
    assert archive.media_filename(1699887766543, ".webm", "thumb") == "1699887766543s.jpg"
    assert archive.media_filename(1699887766543, ".png", "thumb") == "1699887766543s.jpg"


def test_thread_dir_layout(cfg):
    assert archive.thread_dir(cfg.archive_dir, "g", 123) == cfg.archive_dir / "g" / "123"


def test_save_and_load_roundtrip(cfg, now):
    doc = archive.new_document("g", 123, now)
    doc["posts"] = [{"no": 1, "com": "hello"}]
    archive.save_thread(cfg.archive_dir, doc)
    assert archive.load_thread(cfg.archive_dir, "g", 123) == doc


def test_load_missing_thread_returns_none(cfg):
    assert archive.load_thread(cfg.archive_dir, "g", 999) is None


def test_save_leaves_no_tmp_file(cfg, now):
    archive.save_thread(cfg.archive_dir, archive.new_document("g", 123, now))
    files = {p.name for p in archive.thread_dir(cfg.archive_dir, "g", 123).iterdir()}
    assert files == {"thread.json"}


def test_merge_appends_new_posts():
    old = [{"no": 1, "com": "a"}]
    new = [{"no": 1, "com": "a"}, {"no": 2, "com": "b"}]
    assert [p["no"] for p in archive.merge_posts(old, new)] == [1, 2]


def test_merge_flags_disappeared_post_as_deleted():
    old = [{"no": 1, "com": "a"}, {"no": 2, "com": "b"}]
    new = [{"no": 1, "com": "a"}]
    merged = archive.merge_posts(old, new)
    assert [p["no"] for p in merged] == [1, 2]
    assert merged[1]["_deleted"] is True
    assert merged[0].get("_deleted", False) is False


def test_merge_never_drops_content_of_deleted_post():
    old = [{"no": 1, "com": "a"}, {"no": 2, "com": "important", "tim": 42, "ext": ".jpg"}]
    merged = archive.merge_posts(old, [{"no": 1, "com": "a"}])
    assert merged[1]["com"] == "important"
    assert merged[1]["tim"] == 42


def test_merge_updates_fields_of_surviving_post():
    old = [{"no": 1, "com": "a", "closed": 0}]
    new = [{"no": 1, "com": "a", "closed": 1, "sticky": 1}]
    merged = archive.merge_posts(old, new)
    assert merged[0]["closed"] == 1
    assert merged[0]["sticky"] == 1
    assert merged[0]["_deleted"] is False


def test_merge_resurrects_post_that_reappears():
    old = [{"no": 1, "com": "a"}, {"no": 2, "com": "b", "_deleted": True}]
    merged = archive.merge_posts(old, [{"no": 1, "com": "a"}, {"no": 2, "com": "b"}])
    assert merged[1]["_deleted"] is False


def test_merge_keeps_chronological_order_by_post_number():
    old = [{"no": 5, "com": "e"}]
    new = [{"no": 5, "com": "e"}, {"no": 3, "com": "c"}]
    assert [p["no"] for p in archive.merge_posts(old, new)] == [3, 5]


def test_media_entries_skips_posts_without_files():
    posts = [
        {"no": 1, "tim": 111, "ext": ".jpg"},
        {"no": 2, "com": "text only"},
        {"no": 3, "tim": 222, "ext": ".webm"},
        {"no": 4, "tim": 333, "ext": ".jpg", "filedeleted": 1},
    ]
    assert archive.media_entries(posts) == [(111, ".jpg"), (222, ".webm")]


def test_delete_thread_dir_removes_everything(cfg, now):
    archive.save_thread(cfg.archive_dir, archive.new_document("g", 123, now))
    (archive.thread_dir(cfg.archive_dir, "g", 123) / "111.jpg").write_bytes(b"x")
    archive.delete_thread_dir(cfg.archive_dir, "g", 123)
    assert not archive.thread_dir(cfg.archive_dir, "g", 123).exists()


def test_delete_missing_dir_is_noop(cfg):
    archive.delete_thread_dir(cfg.archive_dir, "g", 404)
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_archive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.archive'`

- [ ] **Step 3: Implementuj `app/archive.py`**

```python
import json
import os
import shutil
from datetime import datetime
from pathlib import Path


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


def media_entries(posts: list[dict]) -> list[tuple[int, str]]:
    out = []
    for post in posts:
        if post.get("tim") and post.get("ext") and not post.get("filedeleted"):
            out.append((int(post["tim"]), post["ext"]))
    return out


def delete_thread_dir(archive_dir: Path, board: str, no: int) -> None:
    shutil.rmtree(thread_dir(archive_dir, board, no), ignore_errors=True)
```

Klíčové vlastnosti `merge_posts`: `{**existing, **post}` zachová naše pole (`_deleted`) i pole, která 4chan v novější odpovědi vynechal, a zároveň přepíše to, co se změnilo (`closed`, `sticky`, `replies`). Post, který v nové odpovědi chybí, dostane `_deleted = True`, ale **jeho obsah zůstává** — to je celý smysl archivu. Thumbnaily jsou na 4chanu vždy `.jpg` bez ohledu na příponu originálu, proto to `media_filename` zapisuje natvrdo.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_archive.py -v`
Expected: PASS (14 testů)

- [ ] **Step 5: Commit**

```bash
git add app/archive.py tests/test_archive.py && git commit -m "feat: on-disk archive with post merging and atomic writes"
```

---

### Task 8: HTTP klient pro 4chan a rate limiting

**Files:**
- Create: `app/fourchan.py`, `tests/fake4chan.py`, `tests/test_fourchan.py`
- Modify: `tests/conftest.py` (přidat fixture `fake`)

**Interfaces:**
- Consumes: nic
- Produces:
  - `RateLimiter(rate: float)` s `async acquire() -> None`
  - `JsonResponse` (frozen dataclass): `status: int`, `data: Any | None`, `last_modified: str | None`
  - `FourchanClient(client: httpx.AsyncClient, *, api_rate: float = 1.0, media_rate: float = 4.0)`
    - `async fetch_thread(board: str, no: int, last_modified: str | None) -> JsonResponse`
    - `async fetch_catalog(board: str) -> JsonResponse`
    - `media_url(board: str, tim: int, ext: str, kind: str) -> str`
    - `async download(url: str, dest: Path) -> int` — vrací počet bajtů, píše přes `.part`
- Test helper: `tests/fake4chan.py` → `Fake4chan` s `threads: dict[tuple[str, int], dict | None]`, `catalogs: dict[str, list[dict]]`, `files: dict[str, bytes]`, `requests: list[httpx.Request]`, `transport() -> httpx.MockTransport`

- [ ] **Step 1: Napiš fake 4chan server**

Vytvoř `tests/fake4chan.py`:

```python
import json

import httpx

LAST_MODIFIED = "Mon, 17 Aug 2026 12:00:00 GMT"


class Fake4chan:
    """Fake 4chan API + CDN nad httpx.MockTransport. Žádný skutečný socket."""

    def __init__(self):
        self.threads: dict[tuple[str, int], dict | None] = {}
        self.catalogs: dict[str, list[dict]] = {}
        self.files: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.last_modified = LAST_MODIFIED

    def set_thread(self, board: str, no: int, posts: list[dict] | None) -> None:
        """posts=None znamená 404 (thread smazán)."""
        self.threads[(board, no)] = None if posts is None else {"posts": posts}

    def set_catalog(self, board: str, ops: list[dict]) -> None:
        self.catalogs[board] = ops

    def set_file(self, board: str, name: str, payload: bytes) -> None:
        self.files[f"/{board}/{name}"] = payload

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "i.4cdn.org":
            payload = self.files.get(path)
            if payload is None:
                return httpx.Response(404)
            return httpx.Response(200, content=payload)
        if host == "a.4cdn.org":
            return self._handle_api(request, path)
        return httpx.Response(404)

    def _handle_api(self, request: httpx.Request, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[1] == "thread":
            key = (parts[0], int(parts[2].removesuffix(".json")))
            if key not in self.threads or self.threads[key] is None:
                return httpx.Response(404)
            if request.headers.get("If-Modified-Since") == self.last_modified:
                return httpx.Response(304)
            return httpx.Response(
                200, json=self.threads[key],
                headers={"Last-Modified": self.last_modified})
        if len(parts) == 2 and parts[1] == "catalog.json":
            ops = self.catalogs.get(parts[0])
            if ops is None:
                return httpx.Response(404)
            pages = [{"page": 1, "threads": ops}]
            return httpx.Response(200, content=json.dumps(pages),
                                  headers={"Content-Type": "application/json",
                                           "Last-Modified": self.last_modified})
        return httpx.Response(404)
```

Přidej do `tests/conftest.py`:

```python
import httpx

from tests.fake4chan import Fake4chan


@pytest.fixture
def fake():
    return Fake4chan()


@pytest.fixture
def client(fake):
    from app.fourchan import FourchanClient
    http = httpx.AsyncClient(transport=fake.transport())
    yield FourchanClient(http, api_rate=1000, media_rate=1000)
```

Rate 1000 req/s v testech znamená, že limiter nikdo nečeká — testuje se zvlášť v Kroku 2.

- [ ] **Step 2: Napiš failující test**

Vytvoř `tests/test_fourchan.py`:

```python
import httpx
import pytest

from app.fourchan import FourchanClient, RateLimiter


async def test_rate_limiter_spaces_calls_out():
    stamps = []
    clock = [0.0]

    async def fake_sleep(seconds):
        clock[0] += seconds

    limiter = RateLimiter(2.0, clock=lambda: clock[0], sleep=fake_sleep)
    for _ in range(3):
        await limiter.acquire()
        stamps.append(clock[0])
    assert stamps == [0.0, 0.5, 1.0]


async def test_fetch_thread_returns_posts(client, fake):
    fake.set_thread("g", 123, [{"no": 123, "com": "hi"}])
    resp = await client.fetch_thread("g", 123, None)
    assert resp.status == 200
    assert resp.data["posts"][0]["no"] == 123
    assert resp.last_modified == fake.last_modified


async def test_fetch_thread_sends_if_modified_since(client, fake):
    fake.set_thread("g", 123, [{"no": 123}])
    resp = await client.fetch_thread("g", 123, fake.last_modified)
    assert resp.status == 304
    assert resp.data is None
    assert fake.requests[-1].headers["If-Modified-Since"] == fake.last_modified


async def test_fetch_thread_reports_404(client, fake):
    fake.set_thread("g", 123, None)
    assert (await client.fetch_thread("g", 123, None)).status == 404


async def test_fetch_thread_hits_correct_url(client, fake):
    fake.set_thread("g", 123, [])
    await client.fetch_thread("g", 123, None)
    assert str(fake.requests[-1].url) == "https://a.4cdn.org/g/thread/123.json"


async def test_fetch_catalog_flattens_pages(client, fake):
    fake.set_catalog("g", [{"no": 1, "sub": "a"}, {"no": 2, "sub": "b"}])
    resp = await client.fetch_catalog("g")
    assert [op["no"] for op in resp.data] == [1, 2]


def test_media_urls():
    http = httpx.AsyncClient()
    c = FourchanClient(http)
    assert c.media_url("g", 111, ".webm", "file") == "https://i.4cdn.org/g/111.webm"
    assert c.media_url("g", 111, ".webm", "thumb") == "https://i.4cdn.org/g/111s.jpg"


async def test_download_writes_file_atomically(client, fake, tmp_path):
    fake.set_file("g", "111.webm", b"video-bytes")
    dest = tmp_path / "111.webm"
    size = await client.download("https://i.4cdn.org/g/111.webm", dest)
    assert size == len(b"video-bytes")
    assert dest.read_bytes() == b"video-bytes"
    assert not (tmp_path / "111.webm.part").exists()


async def test_download_raises_on_missing_file(client, tmp_path):
    with pytest.raises(httpx.HTTPStatusError):
        await client.download("https://i.4cdn.org/g/nope.jpg", tmp_path / "nope.jpg")
    assert not (tmp_path / "nope.jpg").exists()
    assert not (tmp_path / "nope.jpg.part").exists()
```

- [ ] **Step 3: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_fourchan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.fourchan'`

- [ ] **Step 4: Implementuj `app/fourchan.py`**

```python
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://a.4cdn.org"
MEDIA_BASE = "https://i.4cdn.org"


class RateLimiter:
    """Nejvýše `rate` požadavků za sekundu, sériově."""

    def __init__(self, rate: float, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._next_at - now
            if wait > 0:
                await self._sleep(wait)
                now = self._clock()
            self._next_at = now + self._interval


@dataclass(frozen=True)
class JsonResponse:
    status: int
    data: Any | None
    last_modified: str | None


class FourchanClient:
    def __init__(self, client: httpx.AsyncClient, *,
                 api_rate: float = 1.0, media_rate: float = 4.0):
        self._http = client
        self._api = RateLimiter(api_rate)
        self._media = RateLimiter(media_rate)

    async def _get_json(self, url: str, last_modified: str | None) -> JsonResponse:
        headers = {"If-Modified-Since": last_modified} if last_modified else {}
        await self._api.acquire()
        resp = await self._http.get(url, headers=headers, timeout=30.0)
        if resp.status_code in (304, 404):
            return JsonResponse(resp.status_code, None, last_modified)
        resp.raise_for_status()
        return JsonResponse(200, resp.json(), resp.headers.get("Last-Modified"))

    async def fetch_thread(self, board: str, no: int,
                           last_modified: str | None) -> JsonResponse:
        return await self._get_json(f"{API_BASE}/{board}/thread/{no}.json",
                                    last_modified)

    async def fetch_catalog(self, board: str) -> JsonResponse:
        resp = await self._get_json(f"{API_BASE}/{board}/catalog.json", None)
        if resp.status != 200:
            return resp
        ops = [op for page in resp.data for op in page.get("threads", [])]
        return JsonResponse(200, ops, resp.last_modified)

    def media_url(self, board: str, tim: int, ext: str, kind: str) -> str:
        name = f"{tim}s.jpg" if kind == "thumb" else f"{tim}{ext}"
        return f"{MEDIA_BASE}/{board}/{name}"

    async def download(self, url: str, dest: Path) -> int:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        await self._media.acquire()
        try:
            written = 0
            async with self._http.stream("GET", url, timeout=120.0) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as handle:
                    async for chunk in resp.aiter_bytes(65536):
                        handle.write(chunk)
                        written += len(chunk)
            tmp.replace(dest)
            return written
        finally:
            tmp.unlink(missing_ok=True)
```

`tmp.replace(dest)` uspěje jen po kompletním stažení; `finally` pak uklidí `.part` po výjimce. Po úspěchu už `.part` neexistuje, takže `unlink(missing_ok=True)` je no-op. Katalog se plochá hned v klientovi, aby poller ani scanner nemusely znát stránkovací formát 4chanu.

- [ ] **Step 5: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_fourchan.py -v`
Expected: PASS (9 testů)

- [ ] **Step 6: Commit**

```bash
git add app/fourchan.py tests/fake4chan.py tests/conftest.py tests/test_fourchan.py && git commit -m "feat: rate-limited 4chan HTTP client with fake server for tests"
```

---

### Task 9: Poller

**Files:**
- Create: `app/poller.py`, `tests/test_poller.py`

**Interfaces:**
- Consumes: `app.repo`, `app.archive`, `app.fourchan.FourchanClient`, `app.text.html_to_text`, `app.config.Config`
- Produces:
  - `next_interval(cfg: Config, current: int, changed: bool) -> int`
  - `async poll_thread(conn, client, cfg, row, now: datetime) -> str` — vrací `"updated"` / `"unchanged"` / `"dead"` / `"error"`
  - `async poll_due(conn, client, cfg, now: datetime, limit: int = 50) -> dict[str, int]` — počty výsledků podle druhu

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_poller.py`:

```python
from datetime import timedelta

import httpx
import pytest

from app import archive, poller, repo


async def test_first_poll_stores_posts_and_metadata(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [
        {"no": 123, "sub": "Daily Programming", "com": "hello", "tim": 111, "ext": ".jpg"},
        {"no": 124, "com": "reply"},
    ])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    assert await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now) == "updated"

    doc = archive.load_thread(cfg.archive_dir, "g", 123)
    assert [p["no"] for p in doc["posts"]] == [123, 124]
    row = repo.get_thread(conn, tid)
    assert row["post_count"] == 2
    assert row["subject"] == "Daily Programming"
    assert row["last_modified"] == fake.last_modified


async def test_poll_queues_media_for_download(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123, "tim": 111, "ext": ".jpg"}])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    kinds = {(r["tim"], r["kind"]) for r in repo.pending_media(conn, 10)}
    assert kinds == {(111, "file"), (111, "thumb")}


async def test_unchanged_thread_backs_off(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123}])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    later = now + timedelta(seconds=60)
    assert await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), later) == "unchanged"
    row = repo.get_thread(conn, tid)
    assert row["poll_interval"] == 90
    assert row["next_poll_at"] == repo.iso(later + timedelta(seconds=90))


async def test_backoff_is_capped_at_max(cfg):
    interval = cfg.poll_min_interval
    for _ in range(50):
        interval = poller.next_interval(cfg, interval, changed=False)
    assert interval == cfg.poll_max_interval


async def test_change_resets_interval_to_minimum(cfg):
    assert poller.next_interval(cfg, 600, changed=True) == cfg.poll_min_interval


async def test_new_reply_is_appended_on_second_poll(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123, "com": "op"}])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)

    fake.set_thread("g", 123, [{"no": 123, "com": "op"}, {"no": 124, "com": "new"}])
    fake.last_modified = "Mon, 17 Aug 2026 13:00:00 GMT"
    later = now + timedelta(seconds=60)
    assert await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), later) == "updated"
    doc = archive.load_thread(cfg.archive_dir, "g", 123)
    assert [p["no"] for p in doc["posts"]] == [123, 124]


async def test_moderator_deleted_post_survives_in_archive(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123, "com": "op"}, {"no": 124, "com": "doomed"}])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)

    fake.set_thread("g", 123, [{"no": 123, "com": "op"}])
    fake.last_modified = "Mon, 17 Aug 2026 13:00:00 GMT"
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now + timedelta(seconds=60))

    doc = archive.load_thread(cfg.archive_dir, "g", 123)
    assert doc["posts"][1]["com"] == "doomed"
    assert doc["posts"][1]["_deleted"] is True


async def test_404_marks_thread_dead_everywhere(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123}])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)

    fake.set_thread("g", 123, None)
    later = now + timedelta(seconds=60)
    assert await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), later) == "dead"

    row = repo.get_thread(conn, tid)
    assert row["status"] == "dead"
    doc = archive.load_thread(cfg.archive_dir, "g", 123)
    assert doc["status"] == "dead"
    assert doc["died_at"] == repo.iso(later)
    assert repo.due_threads(conn, later + timedelta(days=30), 10) == []


async def test_404_on_never_polled_thread_is_not_a_crash(conn, client, cfg, fake, now):
    fake.set_thread("g", 999, None)
    tid = repo.add_thread(conn, "g", 999, "manual", now)
    assert await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now) == "dead"
    assert repo.get_thread(conn, tid)["status"] == "dead"


async def test_network_error_records_failure_without_raising(conn, cfg, now):
    from app.fourchan import FourchanClient

    def explode(request):
        raise httpx.ConnectError("network down")

    broken = FourchanClient(httpx.AsyncClient(transport=httpx.MockTransport(explode)),
                            api_rate=1000, media_rate=1000)
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    assert await poller.poll_thread(conn, broken, cfg, repo.get_thread(conn, tid), now) == "error"
    row = repo.get_thread(conn, tid)
    assert row["fail_count"] == 1
    assert "network down" in row["last_error"]
    assert row["poll_interval"] == cfg.poll_max_interval


async def test_poll_due_processes_all_due_threads(conn, client, cfg, fake, now):
    for no in (1, 2, 3):
        fake.set_thread("g", no, [{"no": no}])
        repo.add_thread(conn, "g", no, "manual", now)
    assert await poller.poll_due(conn, client, cfg, now) == {
        "updated": 3, "unchanged": 0, "dead": 0, "error": 0}
    assert repo.due_threads(conn, now, 10) == []
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_poller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.poller'`

- [ ] **Step 3: Implementuj `app/poller.py`**

```python
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
```

Zachytávat holé `Exception` je tady záměrné a je to jediné takové místo v kódu: worker nesmí spadnout kvůli jednomu rozbitému threadu. Chyba jde do `last_error` a na dashboard.

Média se odvozují z **mergnutých** postů, ne z odpovědi — soubor smazaného postu se tak doarchivuje, pokud jsme ho ještě nestihli stáhnout.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_poller.py -v`
Expected: PASS (11 testů)

- [ ] **Step 5: Commit**

```bash
git add app/poller.py tests/test_poller.py && git commit -m "feat: thread poller with adaptive backoff and death detection"
```

---

### Task 10: Scanner boardů

**Files:**
- Create: `app/scanner.py`, `tests/test_scanner.py`

**Interfaces:**
- Consumes: `app.repo`, `app.text.{op_search_text, matches_keywords}`, `app.fourchan.FourchanClient`
- Produces:
  - `async scan_rule(conn, client, rule, now: datetime) -> int` — počet nově zařazených threadů
  - `async scan_due(conn, client, cfg, now: datetime) -> int` — součet přes všechna due pravidla

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_scanner.py`:

```python
from datetime import timedelta

import httpx

from app import repo, scanner


async def test_matches_subject(conn, client, cfg, fake, now):
    fake.set_catalog("g", [
        {"no": 1, "sub": "Daily Programming Thread", "com": "post setups"},
        {"no": 2, "sub": "Sticky", "com": "rules"},
    ])
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["daily programming"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 1
    assert repo.find_thread(conn, "g", 1) is not None
    assert repo.find_thread(conn, "g", 2) is None


async def test_matches_op_comment_when_subject_missing(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 7, "com": "anyone here running <b>Zig</b> in prod?"}])
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["zig"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 1


async def test_html_entities_do_not_break_matching(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 7, "com": "cats &amp; dogs<br>thread"}])
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["cats & dogs"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 1


async def test_matched_thread_is_marked_with_rule_source(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    rid = repo.add_rule(conn, "g", ["rust"], now)
    await scanner.scan_rule(conn, client, repo.get_rule(conn, rid), now)
    assert repo.find_thread(conn, "g", 1)["source"] == f"rule:{rid}"


async def test_already_tracked_thread_is_not_duplicated(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    repo.add_thread(conn, "g", 1, "manual", now)
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["rust"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 0
    assert repo.find_thread(conn, "g", 1)["source"] == "manual"


async def test_scan_failure_is_recorded_not_raised(conn, cfg, now):
    from app.fourchan import FourchanClient

    def explode(request):
        raise httpx.ConnectError("network down")

    broken = FourchanClient(httpx.AsyncClient(transport=httpx.MockTransport(explode)),
                            api_rate=1000, media_rate=1000)
    rid = repo.add_rule(conn, "g", ["rust"], now)
    assert await scanner.scan_rule(conn, broken, repo.get_rule(conn, rid), now) == 0
    assert "network down" in repo.get_rule(conn, rid)["last_error"]


async def test_scan_due_skips_disabled_and_updates_timestamp(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    fake.set_catalog("b", [{"no": 2, "sub": "rust general"}])
    repo.add_rule(conn, "g", ["rust"], now)
    disabled = repo.add_rule(conn, "b", ["rust"], now)
    repo.update_rule(conn, disabled, enabled=False)

    assert await scanner.scan_due(conn, client, cfg, now) == 1
    assert repo.find_thread(conn, "b", 2) is None
    assert await scanner.scan_due(conn, client, cfg, now + timedelta(seconds=10)) == 0
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.scanner'`

- [ ] **Step 3: Implementuj `app/scanner.py`**

```python
import logging
from datetime import datetime

from app import repo
from app.config import Config
from app.text import matches_keywords, op_search_text

log = logging.getLogger(__name__)


async def scan_rule(conn, client, rule, now: datetime) -> int:
    board = rule["board"]
    keywords = repo.rule_keywords(rule)
    try:
        resp = await client.fetch_catalog(board)
        if resp.status != 200:
            raise RuntimeError(f"catalog vrátil {resp.status}")
    except Exception as exc:
        log.warning("scan /%s/ selhal: %s", board, exc)
        repo.mark_rule_scanned(conn, rule["id"], now, error=f"{type(exc).__name__}: {exc}")
        return 0

    added = 0
    for op in resp.data:
        if not matches_keywords(op_search_text(op), keywords):
            continue
        if repo.add_thread(conn, board, int(op["no"]), f"rule:{rule['id']}", now):
            added += 1
    repo.mark_rule_scanned(conn, rule["id"], now)
    log.info("scan /%s/: %s nových threadů", board, added)
    return added


async def scan_due(conn, client, cfg: Config, now: datetime) -> int:
    total = 0
    for rule in repo.due_rules(conn, now, cfg.scan_interval):
        total += await scan_rule(conn, client, rule, now)
    return total
```

Nově zařazený thread má `next_poll_at = now`, takže ho poller sebere hned v následujícím tiku a stáhne obsah — scanner sám žádné posty neukládá.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_scanner.py -v`
Expected: PASS (7 testů)

- [ ] **Step 5: Commit**

```bash
git add app/scanner.py tests/test_scanner.py && git commit -m "feat: keyword-based board scanner"
```

---

### Task 11: Stahovač médií

**Files:**
- Create: `app/media.py`, `tests/test_media.py`

**Interfaces:**
- Consumes: `app.repo`, `app.archive`, `app.fourchan.FourchanClient`
- Produces:
  - `async download_pending(conn, client, cfg, limit: int = 20) -> dict[str, int]` — `{"ok": n, "failed": n}`
  - `sync_media_doc(conn, cfg, thread_id: int) -> None` — přepíše `doc["media"]` v `thread.json` podle DB

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_media.py`:

```python
from app import archive, media, poller, repo


async def _seed(conn, client, cfg, fake, now, ext=".webm"):
    fake.set_thread("g", 123, [{"no": 123, "tim": 111, "ext": ext}])
    fake.set_file("g", f"111{ext}", b"video-bytes")
    fake.set_file("g", "111s.jpg", b"thumb-bytes")
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    return tid


async def test_downloads_file_and_thumbnail(conn, client, cfg, fake, now):
    tid = await _seed(conn, client, cfg, fake, now)
    assert await media.download_pending(conn, client, cfg) == {"ok": 2, "failed": 0}

    directory = archive.thread_dir(cfg.archive_dir, "g", 123)
    assert (directory / "111.webm").read_bytes() == b"video-bytes"
    assert (directory / "111s.jpg").read_bytes() == b"thumb-bytes"
    assert repo.pending_media(conn, 10) == []


async def test_records_sizes_and_updates_thread_bytes(conn, client, cfg, fake, now):
    tid = await _seed(conn, client, cfg, fake, now)
    await media.download_pending(conn, client, cfg)
    assert repo.get_thread(conn, tid)["bytes"] == len(b"video-bytes") + len(b"thumb-bytes")


async def test_writes_media_map_into_thread_json(conn, client, cfg, fake, now):
    await _seed(conn, client, cfg, fake, now)
    await media.download_pending(conn, client, cfg)
    doc = archive.load_thread(cfg.archive_dir, "g", 123)
    assert doc["media"]["111"] == {
        "ext": ".webm", "file": "ok", "thumb": "ok",
        "bytes": len(b"video-bytes") + len(b"thumb-bytes")}


async def test_missing_file_is_retried_three_times_then_failed(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123, "tim": 111, "ext": ".jpg"}])
    fake.set_file("g", "111s.jpg", b"thumb")          # full soubor na CDN chybí
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)

    for _ in range(3):
        await media.download_pending(conn, client, cfg)
    row = conn.execute(
        "SELECT * FROM media WHERE kind='file'").fetchone()
    assert row["status"] == "failed"
    assert row["fail_count"] == 3
    doc = archive.load_thread(cfg.archive_dir, "g", 123)
    assert doc["media"]["111"]["file"] == "failed"
    assert doc["media"]["111"]["thumb"] == "ok"


async def test_no_partial_file_survives_failure(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123, "tim": 111, "ext": ".jpg"}])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    await media.download_pending(conn, client, cfg)
    directory = archive.thread_dir(cfg.archive_dir, "g", 123)
    assert {p.name for p in directory.iterdir()} == {"thread.json"}


async def test_already_downloaded_file_is_not_fetched_again(conn, client, cfg, fake, now):
    await _seed(conn, client, cfg, fake, now)
    await media.download_pending(conn, client, cfg)
    before = len(fake.requests)
    assert await media.download_pending(conn, client, cfg) == {"ok": 0, "failed": 0}
    assert len(fake.requests) == before


async def test_retry_reschedules_failed_media(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123, "tim": 111, "ext": ".jpg"}])
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    for _ in range(3):
        await media.download_pending(conn, client, cfg)

    fake.set_file("g", "111.jpg", b"now-it-exists")
    fake.set_file("g", "111s.jpg", b"thumb")
    assert repo.retry_failed_media(conn, tid) == 2
    assert await media.download_pending(conn, client, cfg) == {"ok": 2, "failed": 0}
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_media.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.media'`

- [ ] **Step 3: Doplň `app/repo.py` o dotaz na média threadu**

Všechen SQL patří do `repo.py` — `media.py` nesmí sahat na tabulky přímo. Přidej na konec:

```python
def media_for_thread(conn, thread_id) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT tim, ext, kind, status, bytes FROM media WHERE thread_id = ?"
        " ORDER BY tim, kind", (thread_id,)).fetchall()
```

- [ ] **Step 4: Implementuj `app/media.py`**

```python
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
```

`dest.exists()` je obrana proti restartu: existující soubor se jen zaeviduje, nestahuje znovu. Stav médií se propisuje i do `thread.json`, aby klient poznal, co má k dispozici, aniž by se ptal API.

Plný disk sem spadne jako `OSError` ze zápisu — skončí v `mark_media_failed` a na dashboardu jako `media_failed`. Pollování JSONu tím není dotčené a běží dál: text je levný a je to ta hodnotnější část archivu. Až se místo uvolní, `POST /api/threads/{id}/retry` média vrátí do fronty.

- [ ] **Step 5: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_media.py -v`
Expected: PASS (7 testů)

- [ ] **Step 6: Commit**

```bash
git add app/repo.py app/media.py tests/test_media.py && git commit -m "feat: media downloader with retries and archive metadata sync"
```

---

### Task 12: Worker smyčka

**Files:**
- Create: `app/worker.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: `app.config.load_config`, `app.db.connect`, `app.fourchan.FourchanClient`, `app.poller.poll_due`, `app.scanner.scan_due`, `app.media.download_pending`
- Produces:
  - `async tick(conn, client, cfg, now: datetime) -> dict` — jedna iterace: scan → poll → média
  - `async run(cfg, *, iterations: int | None = None) -> None` — smyčka s `asyncio.sleep(TICK_SECONDS)`
  - `TICK_SECONDS = 5`
  - `python -m app.worker` spouští `run(load_config())`

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_worker.py`:

```python
from app import repo, worker


async def test_tick_scans_polls_and_downloads_in_one_pass(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    fake.set_thread("g", 1, [{"no": 1, "sub": "rust general", "tim": 111, "ext": ".jpg"}])
    fake.set_file("g", "111.jpg", b"img")
    fake.set_file("g", "111s.jpg", b"thumb")
    repo.add_rule(conn, "g", ["rust"], now)

    result = await worker.tick(conn, client, cfg, now)

    assert result["scanned"] == 1
    assert result["poll"]["updated"] == 1
    assert result["media"]["ok"] == 2
    assert repo.get_thread(conn, repo.find_thread(conn, "g", 1)["id"])["post_count"] == 1


async def test_tick_survives_when_everything_is_empty(conn, client, cfg, now):
    result = await worker.tick(conn, client, cfg, now)
    assert result == {"scanned": 0,
                      "poll": {"updated": 0, "unchanged": 0, "dead": 0, "error": 0},
                      "media": {"ok": 0, "failed": 0}}


async def test_run_stops_after_requested_iterations(cfg, fake, monkeypatch):
    import httpx

    from app import fourchan

    monkeypatch.setattr(worker.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(worker, "_make_client", lambda cfg: fourchan.FourchanClient(
        httpx.AsyncClient(transport=fake.transport()), api_rate=1000, media_rate=1000))
    await worker.run(cfg, iterations=2)


async def _no_sleep(seconds):
    return None
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.worker'`

- [ ] **Step 3: Implementuj `app/worker.py`**

```python
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app import media, poller, scanner
from app.config import Config, load_config
from app.db import connect
from app.fourchan import FourchanClient

log = logging.getLogger(__name__)

TICK_SECONDS = 5
MEDIA_BATCH = 20


def _make_client(cfg: Config) -> FourchanClient:
    http = httpx.AsyncClient(
        headers={"User-Agent": "fourchan-archiver/0.1 (personal archive)"},
        follow_redirects=True)
    return FourchanClient(http, api_rate=cfg.api_rate, media_rate=cfg.media_rate)


async def tick(conn, client, cfg: Config, now: datetime) -> dict:
    scanned = await scanner.scan_due(conn, client, cfg, now)
    poll = await poller.poll_due(conn, client, cfg, now)
    downloaded = await media.download_pending(conn, client, cfg, MEDIA_BATCH)
    return {"scanned": scanned, "poll": poll, "media": downloaded}


async def run(cfg: Config, *, iterations: int | None = None) -> None:
    logging.basicConfig(level=cfg.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    conn = connect(cfg.db_path)
    client = _make_client(cfg)
    log.info("worker běží, data v %s", cfg.data_dir)
    done = 0
    try:
        while iterations is None or done < iterations:
            try:
                result = await tick(conn, client, cfg, datetime.now(timezone.utc))
                if result["scanned"] or result["poll"]["updated"] or result["media"]["ok"]:
                    log.info("tick: %s", result)
            except Exception:
                log.exception("tick selhal, pokračuji")
            done += 1
            await asyncio.sleep(TICK_SECONDS)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(run(load_config()))
```

Pořadí uvnitř ticku není náhodné: scan první, aby nově nalezený thread stihl ve stejném ticku i první poll; média poslední, protože jsou nejpomalejší a nesmí zdržet detekci nových postů.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_worker.py -v`
Expected: PASS (3 testy)

- [ ] **Step 5: Ověř, že worker nastartuje i doopravdy**

Run: `DATA_DIR=./data-dev .venv/Scripts/python -m app.worker` (PowerShell: `$env:DATA_DIR="./data-dev"; .venv/Scripts/python -m app.worker`)
Expected: vypíše `worker běží, data v data-dev`, vytvoří `data-dev/app.db` a nic dalšího nedělá (žádná pravidla, žádné thready). Ukonči `Ctrl+C`.

- [ ] **Step 6: Commit**

```bash
git add app/worker.py tests/test_worker.py && git commit -m "feat: worker loop tying scanner, poller and media downloader together"
```

---

### Task 13: HTTP API — thready

**Files:**
- Create: `app/web.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `app.config.Config`, `app.db.connect`, `app.repo`, `app.archive`, `app.urls.parse_thread_url`
- Produces: `create_app(cfg: Config) -> FastAPI` s endpointy `POST /api/threads`, `GET /api/threads`, `DELETE /api/threads/{id}`, `POST /api/threads/{id}/retry`; každý thread se serializuje funkcí `thread_json(row) -> dict` (klíče `id, board, no, subject, status, source, first_seen, last_polled, next_poll_at, post_count, bytes, fail_count, last_error, died_at, url`, kde `url` je `/archive/{board}/{no}/thread.json`)

- [ ] **Step 1: Napiš failující test**

Vytvoř `tests/test_web.py`:

```python
import httpx
import pytest

from app import archive, repo
from app.web import create_app


@pytest.fixture
def api(cfg):
    app = create_app(cfg)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_add_thread_by_url(api, cfg):
    resp = await api.post("/api/threads",
                          json={"url": "https://boards.4chan.org/g/thread/12345678"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["board"] == "g"
    assert body["no"] == 12345678
    assert body["status"] == "live"
    assert body["source"] == "manual"
    assert body["url"] == "/archive/g/12345678/thread.json"


async def test_add_thread_rejects_garbage_url(api):
    resp = await api.post("/api/threads", json={"url": "https://example.com/nope"})
    assert resp.status_code == 400
    assert "detail" in resp.json()


async def test_add_thread_reports_duplicate(api):
    url = "https://boards.4chan.org/g/thread/12345678"
    await api.post("/api/threads", json={"url": url})
    resp = await api.post("/api/threads", json={"url": url})
    assert resp.status_code == 409


async def test_list_threads_with_filters(api, cfg, now):
    from app.db import connect
    conn = connect(cfg.db_path)
    a = repo.add_thread(conn, "g", 1, "manual", now)
    repo.add_thread(conn, "b", 2, "manual", now)
    repo.mark_polled(conn, a, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=3, subject="Rust general")
    conn.close()

    assert len((await api.get("/api/threads")).json()["threads"]) == 2
    assert len((await api.get("/api/threads?board=g")).json()["threads"]) == 1
    assert len((await api.get("/api/threads?q=rust")).json()["threads"]) == 1
    assert len((await api.get("/api/threads?status=dead")).json()["threads"]) == 0


async def test_delete_thread_removes_files_too(api, cfg, now):
    await api.post("/api/threads", json={"url": "g/12345678"})
    tid = (await api.get("/api/threads")).json()["threads"][0]["id"]
    archive.save_thread(cfg.archive_dir, archive.new_document("g", 12345678, now))
    directory = archive.thread_dir(cfg.archive_dir, "g", 12345678)
    (directory / "111.jpg").write_bytes(b"x")

    assert (await api.delete(f"/api/threads/{tid}")).status_code == 204
    assert not directory.exists()
    assert (await api.get("/api/threads")).json()["threads"] == []


async def test_delete_unknown_thread_is_404(api):
    assert (await api.delete("/api/threads/999")).status_code == 404


async def test_retry_requeues_failed_media(api, cfg, now):
    from app.db import connect
    await api.post("/api/threads", json={"url": "g/12345678"})
    conn = connect(cfg.db_path)
    tid = repo.find_thread(conn, "g", 12345678)["id"]
    repo.add_media(conn, tid, 111, ".jpg", "file")
    for _ in range(3):
        repo.mark_media_failed(conn, tid, 111, "file", "boom")
    conn.close()

    resp = await api.post(f"/api/threads/{tid}/retry")
    assert resp.status_code == 200
    assert resp.json() == {"requeued": 1}
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/Scripts/python -m pytest tests/test_web.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.web'`

- [ ] **Step 3: Implementuj `app/web.py`**

```python
import sqlite3
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel

from app import archive, repo
from app.config import Config, load_config
from app.db import connect
from app.urls import parse_thread_url


class ThreadIn(BaseModel):
    url: str


def thread_json(row: sqlite3.Row) -> dict:
    data = {k: row[k] for k in (
        "id", "board", "no", "subject", "status", "source", "first_seen",
        "last_polled", "next_poll_at", "post_count", "bytes", "fail_count",
        "last_error", "died_at")}
    data["url"] = f"/archive/{row['board']}/{row['no']}/thread.json"
    return data


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="4chan archiver")
    app.state.cfg = cfg

    def get_conn():
        conn = connect(cfg.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def now() -> datetime:
        return datetime.now(timezone.utc)

    @app.post("/api/threads", status_code=201)
    def add_thread(payload: ThreadIn, conn=Depends(get_conn)):
        try:
            ref = parse_thread_url(payload.url)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        thread_id = repo.add_thread(conn, ref.board, ref.no, "manual", now())
        if thread_id is None:
            raise HTTPException(409, f"thread {ref.board}/{ref.no} už je sledován")
        return thread_json(repo.get_thread(conn, thread_id))

    @app.get("/api/threads")
    def list_threads(status: str | None = None, board: str | None = None,
                     q: str | None = None, limit: int = 100, offset: int = 0,
                     conn=Depends(get_conn)):
        rows = repo.list_threads(conn, status=status, board=board, q=q,
                                 limit=min(limit, 500), offset=offset)
        return {"threads": [thread_json(r) for r in rows]}

    @app.delete("/api/threads/{thread_id}", status_code=204)
    def delete_thread(thread_id: int, conn=Depends(get_conn)):
        row = repo.get_thread(conn, thread_id)
        if row is None:
            raise HTTPException(404, "thread neexistuje")
        archive.delete_thread_dir(cfg.archive_dir, row["board"], row["no"])
        repo.delete_thread(conn, thread_id)
        return Response(status_code=204)

    @app.post("/api/threads/{thread_id}/retry")
    def retry_media(thread_id: int, conn=Depends(get_conn)):
        if repo.get_thread(conn, thread_id) is None:
            raise HTTPException(404, "thread neexistuje")
        return {"requeued": repo.retry_failed_media(conn, thread_id)}

    return app


app = create_app(load_config())
```

Endpointy jsou `def`, ne `async def` — FastAPI je pak pouští v threadpoolu a blokující `sqlite3` nikomu nedrží event loop. Spojení se otevírá per-request přes `Depends`, protože `sqlite3.Connection` není bezpečné sdílet mezi vlákny.

- [ ] **Step 4: Spusť test a ověř, že projde**

Run: `.venv/Scripts/python -m pytest tests/test_web.py -v`
Expected: PASS (7 testů)

- [ ] **Step 5: Commit**

```bash
git add app/web.py tests/test_web.py && git commit -m "feat: thread management HTTP API"
```

---

### Task 14: HTTP API — pravidla, statistiky a servírování statiky

**Files:**
- Modify: `app/web.py`
- Modify: `tests/test_web.py` (přidat testy na konec)

**Interfaces:**
- Consumes: `app.repo` (rules, stats)
- Produces: endpointy `GET/POST /api/rules`, `PATCH/DELETE /api/rules/{id}`, `GET /api/stats`; při `cfg.serve_static` navíc mount `/archive` a `/`; `python -m app.web` spustí uvicorn

- [ ] **Step 1: Napiš failující testy**

Přidej na konec `tests/test_web.py`:

```python
async def test_create_and_list_rule(api):
    resp = await api.post("/api/rules",
                          json={"board": "g", "keywords": ["rust", "zig"]})
    assert resp.status_code == 201
    assert resp.json()["keywords"] == ["rust", "zig"]
    assert resp.json()["enabled"] is True

    listed = (await api.get("/api/rules")).json()["rules"]
    assert len(listed) == 1
    assert listed[0]["board"] == "g"


async def test_rule_requires_at_least_one_keyword(api):
    resp = await api.post("/api/rules", json={"board": "g", "keywords": []})
    assert resp.status_code == 400


async def test_rule_rejects_bad_board_name(api):
    resp = await api.post("/api/rules", json={"board": "../etc", "keywords": ["x"]})
    assert resp.status_code == 400


async def test_patch_rule(api):
    rid = (await api.post("/api/rules",
                          json={"board": "g", "keywords": ["rust"]})).json()["id"]
    resp = await api.patch(f"/api/rules/{rid}",
                           json={"keywords": ["zig"], "enabled": False})
    assert resp.status_code == 200
    assert resp.json()["keywords"] == ["zig"]
    assert resp.json()["enabled"] is False


async def test_delete_rule_keeps_downloaded_threads(api, cfg, now):
    from app.db import connect
    rid = (await api.post("/api/rules",
                          json={"board": "g", "keywords": ["rust"]})).json()["id"]
    conn = connect(cfg.db_path)
    repo.add_thread(conn, "g", 1, f"rule:{rid}", now)
    conn.close()

    assert (await api.delete(f"/api/rules/{rid}")).status_code == 204
    assert (await api.get("/api/rules")).json()["rules"] == []
    assert len((await api.get("/api/threads")).json()["threads"]) == 1


async def test_delete_unknown_rule_is_404(api):
    assert (await api.delete("/api/rules/999")).status_code == 404


async def test_stats_shape(api, cfg, now):
    from app.db import connect
    await api.post("/api/threads", json={"url": "g/1"})
    conn = connect(cfg.db_path)
    tid = repo.find_thread(conn, "g", 1)["id"]
    repo.add_media(conn, tid, 111, ".jpg", "file")
    repo.mark_media_ok(conn, tid, 111, "file", 1234)
    conn.close()

    body = (await api.get("/api/stats")).json()
    assert body["threads"] == {"live": 1, "dead": 0, "error": 0}
    assert body["media_bytes"] == 1234
    assert body["media_pending"] == 0
    assert body["recent_errors"] == []


async def test_static_serving_is_off_by_default(api):
    assert (await api.get("/archive/g/1/thread.json")).status_code == 404
```

- [ ] **Step 2: Spusť testy a ověř, že selžou**

Run: `.venv/Scripts/python -m pytest tests/test_web.py -v`
Expected: FAIL — `assert 404 == 201` na `test_create_and_list_rule` (endpoint neexistuje)

- [ ] **Step 3: Doplň `app/web.py`**

Nad `create_app` přidej:

```python
import re

BOARD_RE = re.compile(r"^[a-z0-9]{1,10}$")


class RuleIn(BaseModel):
    board: str
    keywords: list[str]


class RulePatch(BaseModel):
    keywords: list[str] | None = None
    enabled: bool | None = None


def rule_json(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "board": row["board"],
        "keywords": repo.rule_keywords(row),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "last_scan_at": row["last_scan_at"],
        "last_error": row["last_error"],
    }


def _clean_keywords(raw: list[str]) -> list[str]:
    return [k.strip() for k in raw if k.strip()]
```

Dovnitř `create_app`, před `return app`:

```python
    @app.get("/api/rules")
    def list_rules(conn=Depends(get_conn)):
        return {"rules": [rule_json(r) for r in repo.list_rules(conn)]}

    @app.post("/api/rules", status_code=201)
    def create_rule(payload: RuleIn, conn=Depends(get_conn)):
        board = payload.board.strip().strip("/").lower()
        if not BOARD_RE.match(board):
            raise HTTPException(400, f"neplatné jméno boardu: {payload.board!r}")
        keywords = _clean_keywords(payload.keywords)
        if not keywords:
            raise HTTPException(400, "pravidlo potřebuje aspoň jedno klíčové slovo")
        rule_id = repo.add_rule(conn, board, keywords, now())
        return rule_json(repo.get_rule(conn, rule_id))

    @app.patch("/api/rules/{rule_id}")
    def patch_rule(rule_id: int, payload: RulePatch, conn=Depends(get_conn)):
        if repo.get_rule(conn, rule_id) is None:
            raise HTTPException(404, "pravidlo neexistuje")
        keywords = None
        if payload.keywords is not None:
            keywords = _clean_keywords(payload.keywords)
            if not keywords:
                raise HTTPException(400, "pravidlo potřebuje aspoň jedno klíčové slovo")
        repo.update_rule(conn, rule_id, keywords=keywords, enabled=payload.enabled)
        return rule_json(repo.get_rule(conn, rule_id))

    @app.delete("/api/rules/{rule_id}", status_code=204)
    def delete_rule(rule_id: int, conn=Depends(get_conn)):
        if repo.get_rule(conn, rule_id) is None:
            raise HTTPException(404, "pravidlo neexistuje")
        repo.delete_rule(conn, rule_id)
        return Response(status_code=204)

    @app.get("/api/stats")
    def stats(conn=Depends(get_conn)):
        return repo.stats(conn)

    if cfg.serve_static:
        from fastapi.staticfiles import StaticFiles
        cfg.archive_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/archive", StaticFiles(directory=cfg.archive_dir), name="archive")
        app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

Mounty musí být až **za** všemi `/api` routami, jinak by `StaticFiles` na `/` pohltil i API. Validace jména boardu je bezpečnostní, ne kosmetická: board se stává částí cesty na disku, takže `../etc` se musí odmítnout dřív, než dorazí do `archive.thread_dir`.

Na konec souboru přidej:

```python
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
```

- [ ] **Step 4: Spusť testy a ověř, že projdou**

Run: `.venv/Scripts/python -m pytest tests/test_web.py -v`
Expected: PASS (15 testů)

- [ ] **Step 5: Ověř dev režim se statikou**

Vytvoř dočasně `static/index.html` s obsahem `<h1>ok</h1>`, pak:

Run (PowerShell): `$env:DATA_DIR="./data-dev"; $env:SERVE_STATIC="1"; .venv/Scripts/python -m app.web`
Expected: `http://localhost:8000/` vrátí `ok`, `http://localhost:8000/api/stats` vrátí JSON se statistikami. Ukonči `Ctrl+C`. Soubor `static/index.html` nech — přepíše se v Tasku 16.

- [ ] **Step 6: Commit**

```bash
git add app/web.py tests/test_web.py static/index.html && git commit -m "feat: rule and stats API, optional static serving"
```

---

### Task 15: Integrační test celého životního cyklu

**Files:**
- Create: `tests/test_integration.py`

**Interfaces:**
- Consumes: všechno z Tasků 1–14. Nic nového neprodukuje — je to síť pojistek proti regresi na švech mezi moduly.

- [ ] **Step 1: Napiš test**

Vytvoř `tests/test_integration.py`:

```python
from datetime import timedelta

import httpx
import pytest

from app import archive, repo, worker
from app.db import connect
from app.web import create_app


@pytest.fixture
def api(cfg):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(cfg)),
                             base_url="http://test")


async def test_manual_thread_from_url_to_dead_archive(api, client, cfg, fake, now):
    fake.set_thread("g", 12345678, [
        {"no": 12345678, "sub": "Daily Programming", "com": "post setups",
         "tim": 111, "ext": ".jpg", "filename": "my vacation photo"},
    ])
    fake.set_file("g", "111.jpg", b"full-image")
    fake.set_file("g", "111s.jpg", b"thumbnail")

    resp = await api.post("/api/threads",
                          json={"url": "https://boards.4chan.org/g/thread/12345678"})
    assert resp.status_code == 201

    conn = connect(cfg.db_path)
    await worker.tick(conn, client, cfg, now)

    directory = archive.thread_dir(cfg.archive_dir, "g", 12345678)
    assert (directory / "thread.json").exists()
    assert (directory / "111.jpg").read_bytes() == b"full-image"
    assert (directory / "111s.jpg").read_bytes() == b"thumbnail"

    doc = archive.load_thread(cfg.archive_dir, "g", 12345678)
    assert doc["posts"][0]["filename"] == "my vacation photo"
    assert doc["media"]["111"]["file"] == "ok"

    listed = (await api.get("/api/threads")).json()["threads"][0]
    assert listed["subject"] == "Daily Programming"
    assert listed["post_count"] == 1
    assert listed["bytes"] == len(b"full-image") + len(b"thumbnail")

    fake.set_thread("g", 12345678, None)
    await worker.tick(conn, client, cfg, now + timedelta(seconds=3600))

    assert (await api.get("/api/threads")).json()["threads"][0]["status"] == "dead"
    assert archive.load_thread(cfg.archive_dir, "g", 12345678)["status"] == "dead"
    assert (directory / "111.jpg").exists()
    conn.close()


async def test_rule_finds_thread_and_archives_it(api, client, cfg, fake, now):
    fake.set_catalog("g", [
        {"no": 1, "sub": "Rust General", "com": "memory safety"},
        {"no": 2, "sub": "Sticky", "com": "read the rules"},
    ])
    fake.set_thread("g", 1, [{"no": 1, "sub": "Rust General", "com": "memory safety"}])

    await api.post("/api/rules", json={"board": "g", "keywords": ["rust general"]})
    conn = connect(cfg.db_path)
    await worker.tick(conn, client, cfg, now)

    threads = (await api.get("/api/threads")).json()["threads"]
    assert [t["no"] for t in threads] == [1]
    assert threads[0]["source"].startswith("rule:")
    assert archive.load_thread(cfg.archive_dir, "g", 1)["posts"][0]["com"] == "memory safety"
    conn.close()


async def test_deleted_post_stays_in_archive_across_ticks(api, client, cfg, fake, now):
    fake.set_thread("g", 5, [{"no": 5, "com": "op"}, {"no": 6, "com": "will be nuked"}])
    await api.post("/api/threads", json={"url": "g/5"})
    conn = connect(cfg.db_path)
    await worker.tick(conn, client, cfg, now)

    fake.set_thread("g", 5, [{"no": 5, "com": "op"}])
    fake.last_modified = "Tue, 18 Aug 2026 12:00:00 GMT"
    await worker.tick(conn, client, cfg, now + timedelta(seconds=3600))

    posts = archive.load_thread(cfg.archive_dir, "g", 5)["posts"]
    assert [p["no"] for p in posts] == [5, 6]
    assert posts[1]["com"] == "will be nuked"
    assert posts[1]["_deleted"] is True
    conn.close()


async def test_delete_from_api_removes_media_from_disk(api, client, cfg, fake, now):
    fake.set_thread("g", 9, [{"no": 9, "tim": 222, "ext": ".webm"}])
    fake.set_file("g", "222.webm", b"video")
    fake.set_file("g", "222s.jpg", b"thumb")
    await api.post("/api/threads", json={"url": "g/9"})
    conn = connect(cfg.db_path)
    await worker.tick(conn, client, cfg, now)
    tid = repo.find_thread(conn, "g", 9)["id"]
    conn.close()

    assert (await api.delete(f"/api/threads/{tid}")).status_code == 204
    assert not archive.thread_dir(cfg.archive_dir, "g", 9).exists()
```

- [ ] **Step 2: Spusť test**

Run: `.venv/Scripts/python -m pytest tests/test_integration.py -v`
Expected: PASS (4 testy). Pokud něco selže, je chyba na švu mezi moduly — oprav modul, ne test.

- [ ] **Step 3: Spusť celou sadu**

Run: `.venv/Scripts/python -m pytest -v`
Expected: PASS, 0 failed. Zapiš si celkový počet testů.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py && git commit -m "test: end-to-end lifecycle from URL to dead archive"
```

---

### Task 16: Statický klient — dashboard a pravidla

**Files:**
- Create: `scripts/make_fixture.py`, `static/css/style.css`, `static/js/api.js`, `static/js/dashboard.js`, `static/js/rules.js`, `static/rules.html`
- Modify: `static/index.html` (nahradit placeholder z Tasku 14)

**Interfaces:**
- Consumes: `/api/threads`, `/api/rules`, `/api/stats` z Tasků 13–14
- Produces: `static/js/api.js` exportuje `getJSON(path)`, `postJSON(path, body)`, `patchJSON(path, body)`, `del(path)`, `formatBytes(n)`, `formatTime(iso)`; `scripts/make_fixture.py` naplní `DATA_DIR` ukázkovým archivem pro ruční ověření

Klient nemá build step ani testovací runner, proto se ověřuje ručně proti fixture datům. Kroky ověření jsou konkrétní a musí projít doslova.

- [ ] **Step 1: Napiš generátor fixture dat**

Vytvoř `scripts/make_fixture.py`:

```python
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


def main() -> None:
    cfg = load_config()
    conn = connect(cfg.db_path)
    now = datetime.now(timezone.utc)

    tid = repo.add_thread(conn, "g", 12345678, "manual", now)
    rid = repo.add_rule(conn, "g", ["daily programming", "rust"], now)
    repo.add_thread(conn, "g", 12345999, f"rule:{rid}", now)

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
```

Run (PowerShell): `$env:DATA_DIR="./data-dev"; .venv/Scripts/python scripts/make_fixture.py`
Expected: vypíše `fixture zapsána do data-dev`

- [ ] **Step 2: Napiš `static/js/api.js`**

```javascript
async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (e) { /* odpověď bez JSON těla */ }
    throw new Error(detail);
  }
  return resp.status === 204 ? null : resp.json();
}

export const getJSON = (path) => request("GET", path);
export const postJSON = (path, body) => request("POST", path, body ?? {});
export const patchJSON = (path, body) => request("PATCH", path, body);
export const del = (path) => request("DELETE", path);

export function formatBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = n, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
```

- [ ] **Step 3: Napiš `static/css/style.css`**

```css
:root {
  --bg: #eef2ff; --panel: #d6daf0; --border: #b7c5d9;
  --text: #0f0c5d; --muted: #707070; --link: #34345c;
  --greentext: #789922; --subject: #0f0c5d; --name: #117743; --dead: #d00;
}
body { background: var(--bg); color: var(--text); font: 13px/1.5 arial, helvetica, sans-serif;
       margin: 0; padding: 12px 16px; }
a { color: var(--link); }
h1 { font-size: 20px; margin: 0 0 12px; }
nav { margin-bottom: 12px; }
nav a { margin-right: 12px; }
.panel { background: var(--panel); border: 1px solid var(--border);
         padding: 10px; margin-bottom: 14px; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
input[type=text] { flex: 1 1 320px; padding: 4px 6px; border: 1px solid var(--border); }
button { padding: 4px 10px; cursor: pointer; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid var(--border); padding: 4px 6px; text-align: left;
         vertical-align: top; }
th { background: var(--panel); }
.status-dead { color: var(--dead); font-weight: bold; }
.status-error { color: #b8860b; font-weight: bold; }
.error { color: var(--dead); margin: 6px 0; min-height: 18px; }
.stats span { margin-right: 18px; }

/* prohlížeč threadu */
.post { background: var(--panel); border: 1px solid var(--border);
        padding: 6px 8px; margin: 0 0 6px; display: inline-block;
        max-width: 100%; box-sizing: border-box; }
.post.op { background: transparent; border: none; display: block; }
.post:target, .post.flash { background: #f0c0b4; border-color: #d99f91; }
.post.deleted { border-left: 4px solid var(--dead); }
.deleted-note { color: var(--dead); font-weight: bold; margin-left: 6px; }
.post-header { margin-bottom: 4px; }
.post-subject { color: var(--subject); font-weight: bold; }
.post-name { color: var(--name); font-weight: bold; }
.post-no { color: var(--muted); }
.backlinks { font-size: 11px; color: var(--muted); }
.backlinks a { margin-right: 4px; }
.comment { margin: 6px 0 0; white-space: pre-wrap; word-wrap: break-word; }
.comment .quote { color: var(--greentext); }
.comment .quotelink { color: #d00060; }
.comment .quotelink.dead { color: var(--muted); text-decoration: line-through; }
.file-info { font-size: 11px; color: var(--muted); margin-bottom: 4px; }
.media img, .media video { max-width: 100%; display: block; }
.media img.thumb { max-width: 250px; max-height: 250px; cursor: pointer; }
#preview { position: absolute; z-index: 50; max-width: 500px;
           box-shadow: 0 2px 8px rgba(0,0,0,.4); pointer-events: none; }
```

- [ ] **Step 4: Napiš `static/index.html`**

```html
<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <title>4chan archiv</title>
  <link rel="stylesheet" href="/css/style.css">
</head>
<body>
  <h1>4chan archiv</h1>
  <nav><a href="/">Thready</a><a href="/rules.html">Pravidla</a></nav>

  <div class="panel stats" id="stats">načítám…</div>

  <div class="panel">
    <form class="row" id="add-form">
      <input type="text" id="url" placeholder="https://boards.4chan.org/g/thread/12345678"
             autocomplete="off" required>
      <button type="submit">Stáhnout</button>
    </form>
    <div class="error" id="add-error"></div>
  </div>

  <div class="panel row">
    <input type="text" id="filter-q" placeholder="hledat v názvu">
    <select id="filter-status">
      <option value="">všechny stavy</option>
      <option value="live">live</option>
      <option value="dead">dead</option>
      <option value="error">error</option>
    </select>
    <button id="refresh">Obnovit</button>
  </div>

  <table>
    <thead><tr>
      <th>Board</th><th>Thread</th><th>Název</th><th>Stav</th><th>Postů</th>
      <th>Velikost</th><th>Poslední poll</th><th>Zdroj</th><th></th>
    </tr></thead>
    <tbody id="threads"></tbody>
  </table>

  <script type="module" src="/js/dashboard.js"></script>
</body>
</html>
```

- [ ] **Step 5: Napiš `static/js/dashboard.js`**

```javascript
import { del, formatBytes, formatTime, getJSON, postJSON } from "./api.js";

const tbody = document.getElementById("threads");
const addError = document.getElementById("add-error");

async function loadStats() {
  const s = await getJSON("/api/stats");
  document.getElementById("stats").innerHTML = "";
  const items = [
    `live: <b>${s.threads.live}</b>`,
    `dead: <b>${s.threads.dead}</b>`,
    `error: <b>${s.threads.error}</b>`,
    `média: <b>${formatBytes(s.media_bytes)}</b>`,
    `ke stažení: <b>${s.media_pending}</b>`,
    `selhalo: <b>${s.media_failed}</b>`,
    `poslední poll: <b>${formatTime(s.last_polled)}</b>`,
  ];
  for (const html of items) {
    const span = document.createElement("span");
    span.innerHTML = html;
    document.getElementById("stats").appendChild(span);
  }
}

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
  return td;
}

function renderThread(t) {
  const tr = document.createElement("tr");
  cell(tr, t.board);
  const link = document.createElement("a");
  link.href = `/thread.html?b=${encodeURIComponent(t.board)}&no=${t.no}`;
  link.textContent = t.no;
  cell(tr, "").appendChild(link);
  cell(tr, t.subject || "—");
  cell(tr, t.status).className = `status-${t.status}`;
  cell(tr, t.post_count);
  cell(tr, formatBytes(t.bytes));
  cell(tr, formatTime(t.last_polled));
  cell(tr, t.source);

  const actions = cell(tr, "");
  const remove = document.createElement("button");
  remove.textContent = "Smazat";
  remove.onclick = async () => {
    if (!confirm(`Smazat ${t.board}/${t.no} včetně médií?`)) return;
    await del(`/api/threads/${t.id}`);
    await refresh();
  };
  actions.appendChild(remove);
  if (t.last_error) {
    const retry = document.createElement("button");
    retry.textContent = "Retry médií";
    retry.title = t.last_error;
    retry.onclick = async () => {
      await postJSON(`/api/threads/${t.id}/retry`);
      await refresh();
    };
    actions.appendChild(retry);
  }
  return tr;
}

async function refresh() {
  const params = new URLSearchParams();
  const q = document.getElementById("filter-q").value.trim();
  const status = document.getElementById("filter-status").value;
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  const { threads } = await getJSON(`/api/threads?${params}`);
  tbody.replaceChildren(...threads.map(renderThread));
  await loadStats();
}

document.getElementById("add-form").onsubmit = async (event) => {
  event.preventDefault();
  addError.textContent = "";
  const input = document.getElementById("url");
  try {
    await postJSON("/api/threads", { url: input.value });
    input.value = "";
    await refresh();
  } catch (err) {
    addError.textContent = err.message;
  }
};

document.getElementById("refresh").onclick = refresh;
document.getElementById("filter-q").oninput = refresh;
document.getElementById("filter-status").onchange = refresh;
refresh();
```

- [ ] **Step 6: Napiš `static/rules.html` a `static/js/rules.js`**

`static/rules.html`:

```html
<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <title>Pravidla — 4chan archiv</title>
  <link rel="stylesheet" href="/css/style.css">
</head>
<body>
  <h1>Pravidla automatického stahování</h1>
  <nav><a href="/">Thready</a><a href="/rules.html">Pravidla</a></nav>

  <div class="panel">
    <form class="row" id="add-form">
      <input type="text" id="board" placeholder="board (např. g)" size="8" required>
      <input type="text" id="keywords"
             placeholder="klíčová slova oddělená čárkou" required>
      <button type="submit">Přidat pravidlo</button>
    </form>
    <div class="error" id="add-error"></div>
    <p>Thread se zařadí, když se některé klíčové slovo objeví v názvu nebo
       v textu prvního postu (nezáleží na velikosti písmen).</p>
  </div>

  <table>
    <thead><tr>
      <th>Board</th><th>Klíčová slova</th><th>Aktivní</th>
      <th>Poslední sken</th><th>Chyba</th><th></th>
    </tr></thead>
    <tbody id="rules"></tbody>
  </table>

  <script type="module" src="/js/rules.js"></script>
</body>
</html>
```

`static/js/rules.js`:

```javascript
import { del, formatTime, getJSON, patchJSON, postJSON } from "./api.js";

const tbody = document.getElementById("rules");
const addError = document.getElementById("add-error");

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
  return td;
}

function renderRule(rule) {
  const tr = document.createElement("tr");
  cell(tr, rule.board);

  const keywords = cell(tr, "");
  const input = document.createElement("input");
  input.type = "text";
  input.value = rule.keywords.join(", ");
  input.onchange = async () => {
    const list = input.value.split(",").map((k) => k.trim()).filter(Boolean);
    try {
      await patchJSON(`/api/rules/${rule.id}`, { keywords: list });
    } catch (err) {
      alert(err.message);
    }
    await refresh();
  };
  keywords.appendChild(input);

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = rule.enabled;
  toggle.onchange = async () => {
    await patchJSON(`/api/rules/${rule.id}`, { enabled: toggle.checked });
    await refresh();
  };
  cell(tr, "").appendChild(toggle);

  cell(tr, formatTime(rule.last_scan_at));
  cell(tr, rule.last_error || "—");

  const remove = document.createElement("button");
  remove.textContent = "Smazat";
  remove.onclick = async () => {
    if (!confirm(`Smazat pravidlo pro /${rule.board}/?`)) return;
    await del(`/api/rules/${rule.id}`);
    await refresh();
  };
  cell(tr, "").appendChild(remove);
  return tr;
}

async function refresh() {
  const { rules } = await getJSON("/api/rules");
  tbody.replaceChildren(...rules.map(renderRule));
}

document.getElementById("add-form").onsubmit = async (event) => {
  event.preventDefault();
  addError.textContent = "";
  const board = document.getElementById("board");
  const keywords = document.getElementById("keywords");
  try {
    await postJSON("/api/rules", {
      board: board.value,
      keywords: keywords.value.split(",").map((k) => k.trim()).filter(Boolean),
    });
    board.value = "";
    keywords.value = "";
    await refresh();
  } catch (err) {
    addError.textContent = err.message;
  }
};

refresh();
```

- [ ] **Step 7: Ověř ručně proti fixture datům**

Run (PowerShell): `$env:DATA_DIR="./data-dev"; $env:SERVE_STATIC="1"; .venv/Scripts/python -m app.web`

Otevři `http://localhost:8000/` a ověř všechny body:

1. Panel se statistikami ukazuje `live: 2`, `dead: 0`, `média: 128 B` (nebo podobnou nenulovou hodnotu).
2. Tabulka má dva řádky; `12345678` má název „Daily Programming Thread" a zdroj `manual`, `12345999` má zdroj `rule:1`.
3. Vložení nesmyslu (`abc`) do pole URL vypíše červenou hlášku, nikoli prázdnou obrazovku.
4. Vložení `g/999` přidá řádek a tabulka se obnoví bez reloadu stránky.
5. Napsání `daily` do filtru zúží tabulku na jeden řádek; smazání textu ji vrátí.
6. Tlačítko „Smazat" u `g/999` po potvrzení řádek odstraní.
7. Na `/rules.html` je jedno pravidlo pro `/g/` s klíčovými slovy `daily programming, rust`; přepnutí checkboxu ho vypne a po reloadu zůstane vypnuté.
8. Konzole prohlížeče je bez chyb.

Ukonči `Ctrl+C`.

- [ ] **Step 8: Commit**

```bash
git add scripts static && git commit -m "feat: dashboard and rules web UI"
```

---

### Task 17: Statický klient — prohlížeč threadu

**Files:**
- Create: `static/thread.html`, `static/js/comment.js`, `static/js/thread.js`

**Interfaces:**
- Consumes: `/archive/{board}/{no}/thread.json` (statický soubor), `static/js/api.js`
- Produces:
  - `comment.js`: `renderComment(html: string, knownPosts: Set<number>) -> DocumentFragment`, `quotedNumbers(html: string) -> number[]`
  - `thread.js`: bez exportů, spouští se z `thread.html`

- [ ] **Step 1: Napiš `static/js/comment.js`**

```javascript
// 4chan posílá v poli `com` HTML. Nikdy ho nevkládáme přes innerHTML —
// procházíme parsovaný strom a stavíme vlastní z whitelistu značek.

const PARSER = new DOMParser();

export function quotedNumbers(html) {
  const doc = PARSER.parseFromString(html || "", "text/html");
  const out = [];
  for (const a of doc.querySelectorAll("a.quotelink")) {
    const m = /#p(\d+)/.exec(a.getAttribute("href") || "");
    if (m) out.push(Number(m[1]));
  }
  return out;
}

function convert(node, knownPosts, out) {
  if (node.nodeType === Node.TEXT_NODE) {
    out.appendChild(document.createTextNode(node.nodeValue));
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;

  const tag = node.tagName.toLowerCase();
  const classes = node.className || "";

  if (tag === "br") {
    out.appendChild(document.createElement("br"));
    return;
  }

  if (tag === "a" && classes.includes("quotelink")) {
    const m = /#p(\d+)/.exec(node.getAttribute("href") || "");
    const target = m ? Number(m[1]) : null;
    const link = document.createElement("a");
    link.className = "quotelink";
    link.textContent = node.textContent;
    if (target !== null && knownPosts.has(target)) {
      link.href = `#p${target}`;
      link.dataset.target = String(target);
    } else {
      link.classList.add("dead");        // odkaz mimo tento thread
      link.title = "post není v tomto threadu";
    }
    out.appendChild(link);
    return;
  }

  let wrapper = null;
  if (tag === "span" && classes.includes("quote")) {
    wrapper = document.createElement("span");
    wrapper.className = "quote";
  } else if (tag === "s" || classes.includes("spoiler")) {
    wrapper = document.createElement("span");
    wrapper.className = "spoiler";
  } else if (tag === "pre") {
    wrapper = document.createElement("pre");
  } else if (tag === "b" || tag === "strong" || tag === "i" || tag === "em"
             || tag === "u") {
    wrapper = document.createElement(tag);
  }

  const sink = wrapper || out;
  for (const child of node.childNodes) convert(child, knownPosts, sink);
  if (wrapper) out.appendChild(wrapper);
}

export function renderComment(html, knownPosts) {
  const doc = PARSER.parseFromString(html || "", "text/html");
  const fragment = document.createDocumentFragment();
  for (const child of doc.body.childNodes) convert(child, knownPosts, fragment);
  return fragment;
}
```

Cokoli mimo whitelist (`<script>`, `<img>`, `onerror=…`) se zahodí a projde jen jeho textový obsah. `DOMParser` navíc parsuje do odpojeného dokumentu, takže se nic nespustí ani během parsování.

- [ ] **Step 2: Napiš `static/thread.html`**

```html
<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <title>Thread — 4chan archiv</title>
  <link rel="stylesheet" href="/css/style.css">
</head>
<body>
  <nav><a href="/">← zpět na seznam</a></nav>
  <h1 id="title">načítám…</h1>
  <div class="error" id="error"></div>
  <div id="posts"></div>
  <div id="preview" hidden></div>
  <script type="module" src="/js/thread.js"></script>
</body>
</html>
```

- [ ] **Step 3: Napiš `static/js/thread.js`**

```javascript
import { formatBytes } from "./api.js";
import { quotedNumbers, renderComment } from "./comment.js";

const params = new URLSearchParams(location.search);
const board = params.get("b");
const no = Number(params.get("no"));
const postsEl = document.getElementById("posts");
const previewEl = document.getElementById("preview");

const VIDEO = new Set([".webm", ".mp4"]);

function mediaBase(post) {
  return `/archive/${board}/${no}/${post.tim}`;
}

function fileInfo(post) {
  const name = `${post.filename ?? "file"}${post.ext}`;
  const size = post.fsize ? formatBytes(post.fsize) : "?";
  const dims = post.w && post.h ? `, ${post.w}x${post.h}` : "";
  const info = document.createElement("div");
  info.className = "file-info";
  info.append("Soubor: ");
  const link = document.createElement("a");
  link.href = `${mediaBase(post)}${post.ext}`;
  link.download = name;
  link.textContent = name;
  info.appendChild(link);
  info.append(` (${size}${dims})`);
  return info;
}

function renderMedia(post, mediaState) {
  const box = document.createElement("div");
  box.className = "media";
  const state = mediaState[String(post.tim)] || {};
  if (state.file === "failed" && state.thumb === "failed") {
    box.textContent = "[médium se nepodařilo stáhnout]";
    return box;
  }

  const thumb = document.createElement("img");
  thumb.className = "thumb";
  thumb.src = `${mediaBase(post)}s.jpg`;
  thumb.alt = post.filename || "";
  thumb.loading = "lazy";
  box.appendChild(thumb);

  if (state.file === "failed") return box;   // originál nemáme, expandovat není co

  thumb.onclick = () => {
    if (VIDEO.has(post.ext)) {
      const video = document.createElement("video");
      video.src = `${mediaBase(post)}${post.ext}`;
      video.controls = true;
      video.loop = true;
      video.autoplay = true;
      video.onclick = (e) => { e.stopPropagation(); };
      video.ondblclick = () => box.replaceChildren(thumb);
      box.replaceChildren(video);
    } else {
      const full = document.createElement("img");
      full.src = `${mediaBase(post)}${post.ext}`;
      full.onclick = () => box.replaceChildren(thumb);
      box.replaceChildren(full);
    }
  };
  return box;
}

function renderPost(post, index, knownPosts, backlinks, mediaState) {
  const el = document.createElement("div");
  el.className = index === 0 ? "post op" : "post";
  if (post._deleted) el.classList.add("deleted");
  el.id = `p${post.no}`;

  const header = document.createElement("div");
  header.className = "post-header";
  if (post.sub) {
    const sub = document.createElement("span");
    sub.className = "post-subject";
    sub.textContent = post.sub;
    header.append(sub, " ");
  }
  const name = document.createElement("span");
  name.className = "post-name";
  name.textContent = post.name || "Anonymous";
  header.append(name, " ");
  header.append(new Date((post.time || 0) * 1000).toLocaleString(), " ");
  const number = document.createElement("span");
  number.className = "post-no";
  number.textContent = `No.${post.no}`;
  header.appendChild(number);
  if (post._deleted) {
    const note = document.createElement("span");
    note.className = "deleted-note";
    note.textContent = "[smazáno moderátorem]";
    header.appendChild(note);
  }
  el.appendChild(header);

  const replies = backlinks.get(post.no);
  if (replies && replies.length) {
    const box = document.createElement("div");
    box.className = "backlinks";
    box.append("Odpovědi: ");
    for (const target of replies) {
      const link = document.createElement("a");
      link.href = `#p${target}`;
      link.dataset.target = String(target);
      link.textContent = `>>${target}`;
      box.appendChild(link);
    }
    el.appendChild(box);
  }

  if (post.tim && post.ext && !post.filedeleted) {
    el.appendChild(fileInfo(post));
    el.appendChild(renderMedia(post, mediaState));
  }

  const comment = document.createElement("blockquote");
  comment.className = "comment";
  comment.appendChild(renderComment(post.com || "", knownPosts));
  el.appendChild(comment);
  return el;
}

function flash(target) {
  target.classList.add("flash");
  setTimeout(() => target.classList.remove("flash"), 1200);
}

function wireQuoteLinks(byNumber) {
  postsEl.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-target]");
    if (!link) return;
    const target = document.getElementById(`p${link.dataset.target}`);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    flash(target);
  });

  postsEl.addEventListener("mouseover", (event) => {
    const link = event.target.closest("a[data-target]");
    if (!link) return;
    const source = document.getElementById(`p${link.dataset.target}`);
    if (!source) return;
    previewEl.replaceChildren(source.cloneNode(true));
    previewEl.hidden = false;
    const rect = link.getBoundingClientRect();
    previewEl.style.left = `${window.scrollX + rect.left}px`;
    previewEl.style.top = `${window.scrollY + rect.bottom + 4}px`;
  });

  postsEl.addEventListener("mouseout", (event) => {
    if (event.target.closest("a[data-target]")) previewEl.hidden = true;
  });
}

async function main() {
  if (!board || !Number.isInteger(no)) {
    document.getElementById("error").textContent = "chybí parametry ?b= a ?no=";
    return;
  }
  let doc;
  try {
    const resp = await fetch(`/archive/${board}/${no}/thread.json`);
    if (!resp.ok) throw new Error(`thread není v archivu (HTTP ${resp.status})`);
    doc = await resp.json();
  } catch (err) {
    document.getElementById("error").textContent = err.message;
    return;
  }

  const posts = doc.posts || [];
  const knownPosts = new Set(posts.map((p) => p.no));
  const backlinks = new Map();
  for (const post of posts) {
    for (const target of quotedNumbers(post.com || "")) {
      if (!knownPosts.has(target)) continue;
      if (!backlinks.has(target)) backlinks.set(target, []);
      backlinks.get(target).push(post.no);
    }
  }

  const subject = posts[0]?.sub || `/${board}/ ${no}`;
  document.title = `${subject} — 4chan archiv`;
  document.getElementById("title").textContent =
    `${subject} (/${board}/${no}, ${posts.length} postů${doc.status === "dead" ? ", smazán" : ""})`;

  const mediaState = doc.media || {};
  postsEl.replaceChildren(
    ...posts.map((p, i) => renderPost(p, i, knownPosts, backlinks, mediaState)));
  wireQuoteLinks();

  if (location.hash) {
    const target = document.querySelector(location.hash);
    if (target) { target.scrollIntoView({ block: "center" }); flash(target); }
  }
}

main();
```

- [ ] **Step 4: Ověř ručně proti fixture datům**

Run (PowerShell): `$env:DATA_DIR="./data-dev"; $env:SERVE_STATIC="1"; .venv/Scripts/python -m app.web`

Otevři `http://localhost:8000/thread.html?b=g&no=12345678` a ověř všechny body:

1. Nadpis obsahuje „Daily Programming Thread" a „4 postů".
2. OP post ukazuje „Soubor: my screenshot.png (…)" — tedy **původní** jméno, ne `1699887766543.png` — a odkaz má atribut `download` s tímto jménem (ověř přes pravý klik → uložit, nebo v devtools).
3. Řádek `>inb4 electron` je zelený (greentext).
4. Post `12345680` má nahoře odkaz `>>12345678`; kliknutí odscrolluje na OP a ten na chvíli zežloutne/zčervená.
5. Najetí myší na `>>12345678` zobrazí plovoucí náhled OP postu; odjetí ho schová.
6. OP post má pod hlavičkou „Odpovědi: >>12345680" (backlink), post `12345680` má „Odpovědi: >>12345682".
7. Post `12345682` má červený pruh vlevo a v hlavičce „[smazáno moderátorem]", ale jeho text je vidět.
8. Post `12345684` má odkaz `>>99999999` přeškrtnutý a šedý (deadlink), kliknutí nedělá nic.
9. Klik na thumbnail v OP postu ho zvětší na originál, další klik zmenší zpět.
10. `http://localhost:8000/thread.html?b=g&no=999` zobrazí červenou hlášku „thread není v archivu", ne prázdnou stránku.
11. Konzole prohlížeče je bez chyb.

Pro ověření videa dočasně nahraď v `data-dev/archive/g/12345678/thread.json` u OP postu `"ext": ".png"` za `"ext": ".webm"`, vedle vlož jakýkoli malý `.webm` jako `1699887766543.webm` a znovu načti stránku: klik na thumbnail musí přehrát video s ovládacími prvky. Poté fixture vygeneruj znovu (`scripts/make_fixture.py`).

Ukonči `Ctrl+C`.

- [ ] **Step 5: Commit**

```bash
git add static/thread.html static/js/comment.js static/js/thread.js && git commit -m "feat: 4chan-like thread viewer with quotelinks, backlinks and inline video"
```

---

### Task 18: Docker, nginx a README

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `nginx/nginx.conf`, `README.md`

**Interfaces:**
- Consumes: `app.web:app` (uvicorn ASGI), `python -m app.worker`
- Produces: běžící stack `nginx` + `app` + `worker` nad jedním volume; nginx servíruje `/` ze `static/`, `/archive/` z volume a proxuje `/api/`

- [ ] **Step 1: Napiš `Dockerfile` a `.dockerignore`**

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
```

`.dockerignore`:

```
.venv
data
data-dev
docs
tests
scripts
.git
__pycache__
*.pyc
```

- [ ] **Step 2: Napiš `nginx/nginx.conf`**

```nginx
server {
    listen 80;
    server_name _;
    client_max_body_size 1m;

    auth_basic "4chan archiv";
    auth_basic_user_file /etc/nginx/htpasswd;

    location /api/ {
        proxy_pass http://app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /archive/ {
        alias /data/archive/;
        autoindex off;
        add_header Cache-Control "public, max-age=31536000, immutable";
        types {
            application/json json;
            image/jpeg       jpg jpeg;
            image/png        png;
            image/gif        gif;
            image/webp       webp;
            video/webm       webm;
            video/mp4        mp4;
        }
    }

    location = /archive/ { return 404; }

    location / {
        root /srv/static;
        try_files $uri $uri/ =404;
    }
}
```

Cache hlavička je agresivní schválně: média pod jménem `{tim}{ext}` jsou neměnná. `thread.json` se ale mění — proto ho klient tahá s `cache: "no-cache"`. Uprav v `static/js/thread.js` volání fetch na:

```javascript
    const resp = await fetch(`/archive/${board}/${no}/thread.json`, { cache: "no-cache" });
```

- [ ] **Step 3: Napiš `docker-compose.yml`**

```yaml
services:
  app:
    build: .
    environment:
      DATA_DIR: /data
      LOG_LEVEL: INFO
    volumes:
      - archive:/data
    restart: unless-stopped

  worker:
    build: .
    command: ["python", "-m", "app.worker"]
    environment:
      DATA_DIR: /data
      LOG_LEVEL: INFO
      API_RATE: "1"
      MEDIA_RATE: "4"
    volumes:
      - archive:/data
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "8080:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./nginx/htpasswd:/etc/nginx/htpasswd:ro
      - ./static:/srv/static:ro
      - archive:/data:ro
    depends_on:
      - app
    restart: unless-stopped

volumes:
  archive:
```

Statika se do nginx mountuje z repozitáře, ne z image — klient nemá build step, takže není co kopírovat a úprava JS se projeví po reloadu.

- [ ] **Step 4: Vytvoř htpasswd a spusť stack**

```bash
docker run --rm httpd:alpine htpasswd -nbB admin 'zvol-si-heslo' > nginx/htpasswd
```

```bash
docker compose up --build -d
```

- [ ] **Step 5: Ověř běžící stack**

1. `http://localhost:8080/` si vyžádá jméno a heslo; po zadání se zobrazí dashboard.
2. Špatné heslo vrátí 401 a dashboard se nezobrazí.
3. Přidej thread nějakého skutečně živého threadu z 4chanu a počkej ~30 s.
4. `docker compose logs worker` ukazuje řádek `tick: {...}` s `updated: 1`.
5. Řádek v tabulce má nenulový počet postů a nenulovou velikost médií.
6. Klik na číslo threadu otevře prohlížeč s obrázky; ve vývojářské konzoli mají requesty na `/archive/...` odpověď od nginx (hlavička `Server: nginx`), ne od uvicornu.
7. `docker compose restart worker` a po minutě `docker compose logs worker` — stahování pokračuje, žádné `.part` soubory nezůstaly: `docker compose exec app find /data/archive -name '*.part'` nevypíše nic.

- [ ] **Step 6: Napiš `README.md`**

```markdown
# 4chan archiver

Archivuje vybrané 4chan thready (ručně vložené i automaticky nalezené podle
klíčových slov) a umožňuje je prohlížet offline v UI podobném 4chanu.

## Provoz

```bash
docker run --rm httpd:alpine htpasswd -nbB admin 'heslo' > nginx/htpasswd
docker compose up --build -d
```

Web běží na `http://localhost:8080/`. Data (SQLite + archiv) jsou ve volume
`archive`, struktura `archive/<board>/<thread id>/`.

## Vývoj bez Dockeru

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
DATA_DIR=./data-dev SERVE_STATIC=1 .venv/bin/python -m app.web
DATA_DIR=./data-dev .venv/bin/python -m app.worker
```

`SERVE_STATIC=1` nechá FastAPI servírovat `/` a `/archive/` na stejných
cestách, jaké v produkci obsluhuje nginx.

Testy: `.venv/bin/python -m pytest`. Ukázková data pro klienta:
`DATA_DIR=./data-dev python scripts/make_fixture.py`.

## Konfigurace

| proměnná | výchozí | význam |
|---|---|---|
| `DATA_DIR` | `/data` | kořen dat |
| `SERVE_STATIC` | `0` | FastAPI servíruje statiku a `/archive` |
| `POLL_MIN_INTERVAL` | `60` | interval pollu po změně (s) |
| `POLL_MAX_INTERVAL` | `600` | strop backoffu (s) |
| `SCAN_INTERVAL` | `300` | jak často scanner čte katalogy (s) |
| `API_RATE` | `1` | req/s na `a.4cdn.org` (nezvyšuj — pravidla API) |
| `MEDIA_RATE` | `4` | req/s na `i.4cdn.org` |
| `LOG_LEVEL` | `INFO` | |

## Jak to funguje

Worker každých 5 s: projde due pravidla (katalog boardu → shoda klíčových slov
v názvu nebo textu OP → zařazení threadu), pak due thready (`If-Modified-Since`,
merge postů do `thread.json`), pak frontu médií. Thread se polluje, dokud API
nevrátí 404; pak dostane stav `dead` a zůstává v archivu.

Post smazaný moderátorem se z archivu **nemaže** — dostane `"_deleted": true`
a v prohlížeči je označený.
```

- [ ] **Step 7: Finální kontrola a commit**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS, 0 failed

```bash
git add Dockerfile .dockerignore docker-compose.yml nginx README.md static/js/thread.js && git commit -m "feat: docker compose stack with nginx and documentation"
```

---

## Poznámky k pořadí

Tasky 1–15 jsou striktně sekvenční (každý staví na typech předchozího). Tasky 16 a 17 (klient) jsou na sobě nezávislé kromě `api.js` a `style.css` z Tasku 16 — dají se dělat paralelně, pokud Task 16 skončí první. Task 18 vyžaduje všechno ostatní hotové.







