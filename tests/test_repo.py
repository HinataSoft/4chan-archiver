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


def test_list_threads_escapes_like_percent_metacharacter(conn, now):
    with_percent = repo.add_thread(conn, "g", 1, "manual", now)
    without_percent = repo.add_thread(conn, "g", 2, "manual", now)
    repo.mark_polled(conn, with_percent, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject="Sales 50%")
    repo.mark_polled(conn, without_percent, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject="Sales 500")
    results = repo.list_threads(conn, q="50%")
    assert len(results) == 1
    assert results[0]["id"] == with_percent


def test_list_threads_escapes_like_underscore_metacharacter(conn, now):
    with_underscore = repo.add_thread(conn, "g", 1, "manual", now)
    without_underscore = repo.add_thread(conn, "g", 2, "manual", now)
    repo.mark_polled(conn, with_underscore, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject="foo_bar")
    repo.mark_polled(conn, without_underscore, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject="fooXbar")
    results = repo.list_threads(conn, q="foo_bar")
    assert len(results) == 1
    assert results[0]["id"] == with_underscore


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
