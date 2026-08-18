import httpx
import pytest

from app.fourchan import FourchanClient, RateLimiter


async def test_rate_limiter_spaces_calls_out():
    stamps = []
    clock = [0.0]

    async def fake_sleep(seconds):
        clock[0] += seconds

    limiter = RateLimiter(2.0, clock=lambda: clock[0], sleep=fake_sleep)
    for _ in range(3):
        await limiter.acquire()
        stamps.append(clock[0])
    assert stamps == [0.0, 0.5, 1.0]


async def test_fetch_thread_returns_posts(client, fake):
    fake.set_thread("g", 123, [{"no": 123, "com": "hi"}])
    resp = await client.fetch_thread("g", 123, None)
    assert resp.status == 200
    assert resp.data["posts"][0]["no"] == 123
    assert resp.last_modified == fake.last_modified


async def test_fetch_thread_sends_if_modified_since(client, fake):
    fake.set_thread("g", 123, [{"no": 123}])
    resp = await client.fetch_thread("g", 123, fake.last_modified)
    assert resp.status == 304
    assert resp.data is None
    assert fake.requests[-1].headers["If-Modified-Since"] == fake.last_modified


async def test_fetch_thread_reports_404(client, fake):
    fake.set_thread("g", 123, None)
    assert (await client.fetch_thread("g", 123, None)).status == 404


async def test_fetch_thread_hits_correct_url(client, fake):
    fake.set_thread("g", 123, [])
    await client.fetch_thread("g", 123, None)
    assert str(fake.requests[-1].url) == "https://a.4cdn.org/g/thread/123.json"


async def test_fetch_catalog_flattens_pages(client, fake):
    fake.set_catalog("g", [{"no": 1, "sub": "a"}, {"no": 2, "sub": "b"}])
    resp = await client.fetch_catalog("g")
    assert [op["no"] for op in resp.data] == [1, 2]


def test_media_urls():
    http = httpx.AsyncClient()
    c = FourchanClient(http)
    assert c.media_url("g", 111, ".webm", "file") == "https://i.4cdn.org/g/111.webm"
    assert c.media_url("g", 111, ".webm", "thumb") == "https://i.4cdn.org/g/111s.jpg"


async def test_download_writes_file_atomically(client, fake, tmp_path):
    fake.set_file("g", "111.webm", b"video-bytes")
    dest = tmp_path / "111.webm"
    size = await client.download("https://i.4cdn.org/g/111.webm", dest)
    assert size == len(b"video-bytes")
    assert dest.read_bytes() == b"video-bytes"
    assert not (tmp_path / "111.webm.part").exists()


async def test_download_raises_on_missing_file(client, tmp_path):
    with pytest.raises(httpx.HTTPStatusError):
        await client.download("https://i.4cdn.org/g/nope.jpg", tmp_path / "nope.jpg")
    assert not (tmp_path / "nope.jpg").exists()
    assert not (tmp_path / "nope.jpg.part").exists()


async def test_fake_tracks_last_modified_per_thread(client, fake):
    fake.set_thread("g", 1, [{"no": 1}])
    fake.set_thread("g", 2, [{"no": 2}])
    resp1 = await client.fetch_thread("g", 1, None)
    resp2 = await client.fetch_thread("g", 2, None)

    fake.set_thread("g", 1, [{"no": 1}, {"no": 3, "com": "new"}])

    changed = await client.fetch_thread("g", 1, resp1.last_modified)
    unchanged = await client.fetch_thread("g", 2, resp2.last_modified)
    assert changed.status == 200
    assert unchanged.status == 304


def _fake_clock():
    """Falešné hodiny, které se posouvají jen tím, že limiter uspí."""
    clock = [0.0]

    async def sleeper(seconds):
        clock[0] += seconds

    return clock, sleeper


def _client(fake, clock, sleeper, *, api_rate, media_rate):
    http = httpx.AsyncClient(transport=fake.transport())
    return FourchanClient(http, api_rate=api_rate, media_rate=media_rate,
                          clock=lambda: clock[0], sleep=sleeper)


async def test_api_rate_of_one_paces_thread_fetches(fake):
    """I5: 1 req/s na a.4cdn.org vyžadují pravidla API. Bez tohoto testu by
    refactor, který acquire() z _get_json vypustí, prošel celou sadou."""
    clock, sleeper = _fake_clock()
    client = _client(fake, clock, sleeper, api_rate=1, media_rate=1000)
    fake.set_thread("g", 123, [{"no": 123}])

    for _ in range(3):
        await client.fetch_thread("g", 123, None)
    assert clock[0] >= 2.0
    assert len(fake.requests) == 3


async def test_api_rate_of_one_paces_catalog_fetches_too(fake):
    clock, sleeper = _fake_clock()
    client = _client(fake, clock, sleeper, api_rate=1, media_rate=1000)
    fake.set_catalog("g", [{"no": 1}])

    for _ in range(3):
        await client.fetch_catalog("g")
    assert clock[0] >= 2.0


async def test_media_downloads_use_their_own_limiter(fake, tmp_path):
    """I5: média nesmí viset na API limiteru — a API nesmí jet rychlostí médií."""
    clock, sleeper = _fake_clock()
    client = _client(fake, clock, sleeper, api_rate=1000, media_rate=1)
    fake.set_file("g", "111.jpg", b"x")

    for i in range(3):
        await client.download("https://i.4cdn.org/g/111.jpg", tmp_path / f"{i}.jpg")
    assert clock[0] >= 2.0


async def test_media_traffic_is_not_slowed_down_by_the_api_limiter(fake, tmp_path):
    clock, sleeper = _fake_clock()
    client = _client(fake, clock, sleeper, api_rate=1, media_rate=1000)
    fake.set_file("g", "111.jpg", b"x")

    for i in range(3):
        await client.download("https://i.4cdn.org/g/111.jpg", tmp_path / f"{i}.jpg")
    assert clock[0] < 0.1
