from datetime import timedelta

import httpx

from app import repo, scanner


async def test_matches_subject(conn, client, cfg, fake, now):
    fake.set_catalog("g", [
        {"no": 1, "sub": "Daily Programming Thread", "com": "post setups"},
        {"no": 2, "sub": "Sticky", "com": "rules"},
    ])
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["daily programming"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 1
    assert repo.find_thread(conn, "g", 1) is not None
    assert repo.find_thread(conn, "g", 2) is None


async def test_matches_op_comment_when_subject_missing(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 7, "com": "anyone here running <b>Zig</b> in prod?"}])
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["zig"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 1


async def test_html_entities_do_not_break_matching(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 7, "com": "cats &amp; dogs<br>thread"}])
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["cats & dogs"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 1


async def test_matched_thread_is_marked_with_rule_source(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    rid = repo.add_rule(conn, "g", ["rust"], now)
    await scanner.scan_rule(conn, client, repo.get_rule(conn, rid), now)
    assert repo.find_thread(conn, "g", 1)["source"] == f"rule:{rid}"


async def test_already_tracked_thread_is_not_duplicated(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    repo.add_thread(conn, "g", 1, "manual", now)
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["rust"], now))
    assert await scanner.scan_rule(conn, client, rule, now) == 0
    assert repo.find_thread(conn, "g", 1)["source"] == "manual"


async def test_scan_failure_is_recorded_not_raised(conn, cfg, now):
    from app.fourchan import FourchanClient

    def explode(request):
        raise httpx.ConnectError("network down")

    broken = FourchanClient(httpx.AsyncClient(transport=httpx.MockTransport(explode)),
                            api_rate=1000, media_rate=1000)
    rid = repo.add_rule(conn, "g", ["rust"], now)
    assert await scanner.scan_rule(conn, broken, repo.get_rule(conn, rid), now) == 0
    assert "network down" in repo.get_rule(conn, rid)["last_error"]


async def test_scan_due_skips_disabled_and_updates_timestamp(conn, client, cfg, fake, now):
    fake.set_catalog("g", [{"no": 1, "sub": "rust general"}])
    fake.set_catalog("b", [{"no": 2, "sub": "rust general"}])
    repo.add_rule(conn, "g", ["rust"], now)
    disabled = repo.add_rule(conn, "b", ["rust"], now)
    repo.update_rule(conn, disabled, enabled=False)

    assert await scanner.scan_due(conn, client, cfg, now) == 1
    assert repo.find_thread(conn, "b", 2) is None
    assert await scanner.scan_due(conn, client, cfg, now + timedelta(seconds=10)) == 0


async def test_nonexistent_board_returns_404(conn, client, cfg, fake, now):
    rid = repo.add_rule(conn, "z", ["test"], now)
    result = await scanner.scan_rule(conn, client, repo.get_rule(conn, rid), now)
    assert result == 0
    rule = repo.get_rule(conn, rid)
    assert rule["last_scan_at"] is not None
    assert "404" in rule["last_error"]


async def test_skips_malformed_entries(conn, client, cfg, fake, now):
    fake.set_catalog("g", [
        {"no": 1, "sub": "rust general", "com": ""},
        {"sub": "rust malformed - no 'no' key", "com": ""},
    ])
    rule = repo.get_rule(conn, repo.add_rule(conn, "g", ["rust"], now))
    result = await scanner.scan_rule(conn, client, rule, now)
    assert result == 1
    assert repo.find_thread(conn, "g", 1) is not None
