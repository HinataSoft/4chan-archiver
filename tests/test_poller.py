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
