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


async def test_concurrent_requests_all_succeed(api, cfg, now):
    """C1: FastAPI dispatches the sync dependency and the sync endpoint body
    through separate threadpool hops, so a connection without
    check_same_thread=False blows up under concurrency."""
    import asyncio

    from app.db import connect
    conn = connect(cfg.db_path)
    repo.add_thread(conn, "g", 1, "manual", now)
    conn.close()

    responses = await asyncio.gather(
        *(api.get("/api/threads") for _ in range(16)), return_exceptions=True)
    statuses = [r if isinstance(r, BaseException) else r.status_code for r in responses]
    assert statuses == [200] * 16


async def test_thread_list_reports_failed_media_count(api, cfg, now):
    """I2: selhání médií nikdy nesáhne na threads.last_error, takže UI potřebuje
    vlastní počítadlo, jinak nemá jak nabídnout retry."""
    from app.db import connect
    await api.post("/api/threads", json={"url": "g/12345678"})
    conn = connect(cfg.db_path)
    tid = repo.find_thread(conn, "g", 12345678)["id"]
    repo.add_media(conn, tid, 111, ".jpg", "file")
    repo.add_media(conn, tid, 111, ".jpg", "thumb")
    for _ in range(3):
        repo.mark_media_failed(conn, tid, 111, "file", "HTTP 404")
    conn.close()

    row = (await api.get("/api/threads")).json()["threads"][0]
    assert row["media_failed"] == 1
    assert row["last_error"] is None      # thread je z pohledu polleru zdravý


async def test_retry_clears_the_failed_media_count(api, cfg, now):
    """I2: round-trip — po retry musí počítadlo spadnout na nulu a tlačítko zmizet."""
    from app.db import connect
    await api.post("/api/threads", json={"url": "g/12345678"})
    conn = connect(cfg.db_path)
    tid = repo.find_thread(conn, "g", 12345678)["id"]
    repo.add_media(conn, tid, 111, ".jpg", "file")
    for _ in range(3):
        repo.mark_media_failed(conn, tid, 111, "file", "HTTP 404")
    conn.close()

    assert (await api.get("/api/threads")).json()["threads"][0]["media_failed"] == 1
    assert (await api.post(f"/api/threads/{tid}/retry")).json() == {"requeued": 1}
    assert (await api.get("/api/threads")).json()["threads"][0]["media_failed"] == 0


async def test_failed_media_count_is_per_thread(api, cfg, now):
    from app.db import connect
    await api.post("/api/threads", json={"url": "g/1"})
    await api.post("/api/threads", json={"url": "g/2"})
    conn = connect(cfg.db_path)
    broken = repo.find_thread(conn, "g", 1)["id"]
    healthy = repo.find_thread(conn, "g", 2)["id"]
    repo.add_media(conn, broken, 111, ".jpg", "file")
    repo.add_media(conn, healthy, 222, ".jpg", "file")
    for _ in range(3):
        repo.mark_media_failed(conn, broken, 111, "file", "HTTP 404")
    conn.close()

    rows = {r["id"]: r["media_failed"]
            for r in (await api.get("/api/threads")).json()["threads"]}
    assert rows == {broken: 1, healthy: 0}


async def test_schema_is_created_once_not_on_every_request(cfg, monkeypatch):
    """Minor: connect() per request dřív pouštěl executescript(SCHEMA), tedy
    6 DDL příkazů při každém HTTP requestu."""
    from app import db

    calls = []
    real_init = db.init_schema
    monkeypatch.setattr(db, "init_schema",
                        lambda conn: (calls.append(1), real_init(conn))[-1])

    app = create_app(cfg)
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                            base_url="http://test")
    for _ in range(3):
        assert (await api.get("/api/threads")).status_code == 200
    assert calls == [1]


async def test_static_serving_does_not_depend_on_the_working_directory(cfg, tmp_path,
                                                                      monkeypatch):
    """Minor: StaticFiles(directory="static") bylo relativní k CWD, takže
    SERVE_STATIC=1 spadlo všude mimo kořen repa."""
    import dataclasses

    monkeypatch.chdir(tmp_path)
    app = create_app(dataclasses.replace(cfg, serve_static=True))
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                            base_url="http://test")
    resp = await api.get("/index.html")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()
