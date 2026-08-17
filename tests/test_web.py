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


async def test_list_threads_rejects_negative_limit(api):
    resp = await api.get("/api/threads?limit=-1")
    assert resp.status_code == 422


async def test_list_threads_rejects_zero_limit(api):
    resp = await api.get("/api/threads?limit=0")
    assert resp.status_code == 422


async def test_list_threads_rejects_too_large_limit(api):
    resp = await api.get("/api/threads?limit=501")
    assert resp.status_code == 422


async def test_list_threads_rejects_negative_offset(api):
    resp = await api.get("/api/threads?offset=-1")
    assert resp.status_code == 422


async def test_list_threads_pagination(api, cfg, now):
    from app.db import connect
    conn = connect(cfg.db_path)
    repo.add_thread(conn, "g", 1, "manual", now)
    repo.add_thread(conn, "b", 2, "manual", now)
    conn.close()

    first = (await api.get("/api/threads?limit=1")).json()["threads"]
    assert len(first) == 1
    second = (await api.get("/api/threads?limit=1&offset=1")).json()["threads"]
    assert len(second) == 1
    assert first[0]["id"] != second[0]["id"]


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
