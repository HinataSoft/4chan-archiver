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


async def test_existing_file_on_disk_is_recorded_not_redownloaded(conn, client, cfg, fake, now):
    fake.set_thread("g", 123, [{"no": 123, "tim": 111, "ext": ".webm"}])
    fake.set_file("g", "111.webm", b"cdn-bytes-should-not-be-fetched")
    fake.set_file("g", "111s.jpg", b"thumb-bytes")
    tid = repo.add_thread(conn, "g", 123, "manual", now)
    await poller.poll_thread(conn, client, cfg, repo.get_thread(conn, tid), now)

    dest = archive.media_path(cfg.archive_dir, "g", 123, 111, ".webm", "file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"already-on-disk")

    file_requests_before = [r for r in fake.requests if r.url.path == "/g/111.webm"]
    result = await media.download_pending(conn, client, cfg)
    file_requests_after = [r for r in fake.requests if r.url.path == "/g/111.webm"]

    assert len(file_requests_after) == len(file_requests_before)
    assert result == {"ok": 2, "failed": 0}

    row = conn.execute(
        "SELECT * FROM media WHERE tim=111 AND kind='file'").fetchone()
    assert row["status"] == "ok"
    assert row["bytes"] == len(b"already-on-disk")
    assert dest.read_bytes() == b"already-on-disk"


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
