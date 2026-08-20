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


async def test_disk_failure_does_not_stall_the_poll_queue(conn, client, cfg, fake, now,
                                                          monkeypatch):
    """C2: plný disk u jednoho threadu nesmí shodit dávku ani zablokovat frontu.
    next_poll_at se musí posunout, jinak thread trvale sedí v čele due fronty."""
    fake.set_thread("g", 111, [{"no": 111, "com": "poisoned"}])
    fake.set_thread("g", 222, [{"no": 222, "com": "healthy"}])
    bad = repo.add_thread(conn, "g", 111, "manual", now)
    good = repo.add_thread(conn, "g", 222, "manual", now)

    real_save = archive.save_thread

    def flaky_save(archive_dir, doc):
        if doc["no"] == 111:
            raise OSError(28, "No space left on device")
        real_save(archive_dir, doc)

    monkeypatch.setattr(archive, "save_thread", flaky_save)

    counts = await poller.poll_due(conn, client, cfg, now)
    assert counts == {"updated": 1, "unchanged": 0, "dead": 0, "error": 1}

    bad_row = repo.get_thread(conn, bad)
    assert bad_row["fail_count"] == 1
    assert bad_row["next_poll_at"] > repo.iso(now)
    assert "No space left on device" in bad_row["last_error"]

    good_row = repo.get_thread(conn, good)
    assert good_row["post_count"] == 1
    assert good_row["last_error"] is None
    assert archive.load_thread(cfg.archive_dir, "g", 222)["posts"][0]["no"] == 222

    # Otrávený thread už nesmí blokovat frontu v témže okamžiku.
    assert [r["id"] for r in repo.due_threads(conn, now, 10)] == []


async def test_subject_falls_back_to_the_start_of_the_op_text(conn, client, cfg, fake, now):
    fake.set_thread("g", 500, [{"no": 500, "com": "anyone here running Zig in prod?"}])
    tid = repo.add_thread(conn, "g", 500, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    assert repo.get_thread(conn, tid)["subject"] == "anyone here running Zig in prod?"


async def test_subject_prefers_the_real_subject_over_the_text(conn, client, cfg, fake, now):
    fake.set_thread("g", 501, [{"no": 501, "sub": "Rust General", "com": "memory safety"}])
    tid = repo.add_thread(conn, "g", 501, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    assert repo.get_thread(conn, tid)["subject"] == "Rust General"


async def test_long_op_text_is_cut_on_a_word_boundary(conn, client, cfg, fake, now):
    long_text = "the quick brown fox jumps over the lazy dog while everyone watches it happen"
    fake.set_thread("g", 502, [{"no": 502, "com": long_text}])
    tid = repo.add_thread(conn, "g", 502, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)

    subject = repo.get_thread(conn, tid)["subject"]
    assert subject.endswith("…")
    assert len(subject) <= poller.SUBJECT_FALLBACK_CHARS + 1
    assert long_text.startswith(subject[:-1])   # jen zkráceno, nic přepsáno
    assert not subject[:-1].endswith(" ")       # neseklo uprostřed slova


async def test_html_and_quotelinks_do_not_leak_into_the_subject(conn, client, cfg, fake, now):
    fake.set_thread("g", 503, [{
        "no": 503,
        "com": '<a href="#p1" class="quotelink">&gt;&gt;1</a><br>cats &amp; dogs',
    }])
    tid = repo.add_thread(conn, "g", 503, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    assert repo.get_thread(conn, tid)["subject"] == ">>1 cats & dogs"


async def test_text_only_op_with_no_subject_and_no_text_stays_empty(conn, client, cfg, fake, now):
    fake.set_thread("g", 504, [{"no": 504}])
    tid = repo.add_thread(conn, "g", 504, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)
    assert repo.get_thread(conn, tid)["subject"] is None


def _archived(cfg, board, no, posts, now):
    doc = archive.new_document(board, no, now)
    doc["posts"] = posts
    archive.save_thread(cfg.archive_dir, doc)


async def test_backfill_fills_subjects_from_the_archive(conn, cfg, now):
    """Thready stažené dřív, než fallback existoval, mají subject prázdný.
    Beze změny obsahu vrací 4chan 304, takže by ho poll nikdy nedoplnil —
    ale posty leží v archivu a dají se z nich odvodit."""
    tid = repo.add_thread(conn, "g", 700, "manual", now)
    _archived(cfg, "g", 700, [{"no": 700, "com": "anyone here running Zig in prod?"}], now)
    repo.mark_polled(conn, tid, now=now, next_poll_at=now, poll_interval=60,
                     last_modified="Mon, 17 Aug 2026 12:00:00 GMT", post_count=1,
                     subject=None)
    assert repo.get_thread(conn, tid)["subject"] is None

    assert poller.backfill_missing_subjects(conn, cfg) == 1
    assert repo.get_thread(conn, tid)["subject"] == "anyone here running Zig in prod?"


async def test_backfill_reaches_dead_threads_too(conn, cfg, now):
    """Mrtvý thread se nepolluje, takže tudy je jediná cesta, jak ho dorovnat."""
    tid = repo.add_thread(conn, "g", 701, "manual", now)
    _archived(cfg, "g", 701, [{"no": 701, "com": "thread that got nuked"}], now)
    repo.mark_polled(conn, tid, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject=None)
    repo.mark_dead(conn, tid, now)

    assert poller.backfill_missing_subjects(conn, cfg) == 1
    assert repo.get_thread(conn, tid)["subject"] == "thread that got nuked"


async def test_backfill_leaves_an_existing_subject_alone(conn, cfg, now):
    tid = repo.add_thread(conn, "g", 702, "manual", now)
    _archived(cfg, "g", 702, [{"no": 702, "sub": "Real Subject", "com": "text"}], now)
    repo.mark_polled(conn, tid, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject="Real Subject")

    assert poller.backfill_missing_subjects(conn, cfg) == 0
    assert repo.get_thread(conn, tid)["subject"] == "Real Subject"


async def test_backfill_survives_a_missing_archive(conn, cfg, now):
    """Thread zavedený, ale ještě nikdy nestažený — na disku nic není."""
    repo.add_thread(conn, "g", 703, "manual", now)
    assert poller.backfill_missing_subjects(conn, cfg) == 0


async def test_backfill_is_idempotent(conn, cfg, now):
    tid = repo.add_thread(conn, "g", 704, "manual", now)
    _archived(cfg, "g", 704, [{"no": 704, "com": "some text"}], now)
    repo.mark_polled(conn, tid, now=now, next_poll_at=now, poll_interval=60,
                     last_modified=None, post_count=1, subject=None)

    assert poller.backfill_missing_subjects(conn, cfg) == 1
    assert poller.backfill_missing_subjects(conn, cfg) == 0
