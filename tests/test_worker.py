from app import repo, worker


async def test_tick_scans_polls_and_downloads_in_one_pass(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    fake.set_thread("g", 1, [{"no": 1, "sub": "rust general", "tim": 111, "ext": ".jpg"}])
    fake.set_file("g", "111.jpg", b"img")
    fake.set_file("g", "111s.jpg", b"thumb")
    repo.add_rule(conn, "g", ["rust"], now)

    result = await worker.tick(conn, client, cfg, now)

    assert result["scanned"] == 1
    assert result["poll"]["updated"] == 1
    assert result["media"]["ok"] == 2
    assert repo.get_thread(conn, repo.find_thread(conn, "g", 1)["id"])["post_count"] == 1


async def test_tick_survives_when_everything_is_empty(conn, client, cfg, now):
    result = await worker.tick(conn, client, cfg, now)
    assert result == {"scanned": 0,
                      "poll": {"updated": 0, "unchanged": 0, "dead": 0, "error": 0},
                      "media": {"ok": 0, "failed": 0}}


async def test_run_stops_after_requested_iterations(cfg, fake, monkeypatch):
    import httpx

    from app import fourchan

    monkeypatch.setattr(worker.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(worker, "_make_client", lambda cfg: fourchan.FourchanClient(
        httpx.AsyncClient(transport=fake.transport()), api_rate=1000, media_rate=1000))
    await worker.run(cfg, iterations=2)


async def test_run_closes_the_client(cfg, fake, monkeypatch):
    import httpx

    from app import fourchan

    http = httpx.AsyncClient(transport=fake.transport())
    monkeypatch.setattr(worker.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(worker, "_make_client", lambda cfg: fourchan.FourchanClient(
        http, api_rate=1000, media_rate=1000))

    await worker.run(cfg, iterations=1)

    assert http.is_closed


async def test_run_sleeps_only_between_iterations(cfg, fake, monkeypatch):
    import httpx

    from app import fourchan

    sleeps = []

    async def counting_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(worker.asyncio, "sleep", counting_sleep)
    monkeypatch.setattr(worker, "_make_client", lambda cfg: fourchan.FourchanClient(
        httpx.AsyncClient(transport=fake.transport()), api_rate=1000, media_rate=1000))

    await worker.run(cfg, iterations=2)

    assert sleeps == [worker.TICK_SECONDS]


async def _no_sleep(seconds):
    return None
