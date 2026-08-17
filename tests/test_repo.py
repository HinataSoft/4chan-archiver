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
