import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

from app import archive, repo
from app.config import Config, load_config
from app.db import connect
from app.urls import parse_thread_url

# Statika se hledá relativně k tomuhle souboru, ne k CWD — jinak SERVE_STATIC=1
# spadne všude, kde se app nespouští z kořene repa.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ThreadIn(BaseModel):
    url: str


def thread_json(row: sqlite3.Row, media_failed: int = 0) -> dict:
    data = {k: row[k] for k in (
        "id", "board", "no", "subject", "status", "source", "first_seen",
        "last_polled", "next_poll_at", "post_count", "bytes", "fail_count",
        "last_error", "died_at")}
    data["url"] = f"/archive/{row['board']}/{row['no']}/thread.json"
    data["media_failed"] = media_failed
    return data


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


def create_app(cfg: Config, now_fn=None) -> FastAPI:
    app = FastAPI(title="4chan archiver")
    app.state.cfg = cfg

    # Schéma je jednorázový startovní krok; per-request connect() jen otevře
    # databázi, místo aby pouštěl executescript(SCHEMA) při každém HTTP requestu.
    # Není to lifespan handler proto, že httpx.ASGITransport lifespan nespouští,
    # takže by testy běžely jinou cestou než produkce.
    schema_ready = False

    def get_conn():
        nonlocal schema_ready
        conn = connect(cfg.db_path, create_schema=not schema_ready)
        schema_ready = True
        try:
            yield conn
        finally:
            conn.close()

    if now_fn is None:
        now_fn = lambda: datetime.now(timezone.utc)

    def now() -> datetime:
        return now_fn()

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
                     q: str | None = None, limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0),
                     conn=Depends(get_conn)):
        rows = repo.list_threads(conn, status=status, board=board, q=q,
                                 limit=limit, offset=offset)
        failed = repo.failed_media_counts(conn, [r["id"] for r in rows])
        return {"threads": [thread_json(r, failed.get(r["id"], 0)) for r in rows]}

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
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app(load_config())

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
