from datetime import timedelta

import httpx
import pytest

from app import archive, repo, worker
from app.db import connect
from app.web import create_app


@pytest.fixture
def api(cfg, now):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(cfg, now_fn=lambda: now)),
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

    directory = archive.thread_dir(cfg.archive_dir, "g", 9)
    assert (directory / "thread.json").exists()
    assert (directory / "222.webm").read_bytes() == b"video"
    assert (directory / "222s.jpg").read_bytes() == b"thumb"

    assert (await api.delete(f"/api/threads/{tid}")).status_code == 204
    assert not archive.thread_dir(cfg.archive_dir, "g", 9).exists()
