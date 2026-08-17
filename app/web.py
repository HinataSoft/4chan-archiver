import sqlite3
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Response
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
                     q: str | None = None, limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0),
                     conn=Depends(get_conn)):
        rows = repo.list_threads(conn, status=status, board=board, q=q,
                                 limit=limit, offset=offset)
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
