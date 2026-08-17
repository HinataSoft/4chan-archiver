import logging
from datetime import datetime

from app import repo
from app.config import Config
from app.text import matches_keywords, op_search_text

log = logging.getLogger(__name__)


async def scan_rule(conn, client, rule, now: datetime) -> int:
    board = rule["board"]
    keywords = repo.rule_keywords(rule)
    try:
        resp = await client.fetch_catalog(board)
        if resp.status != 200:
            raise RuntimeError(f"catalog vrátil {resp.status}")
    except Exception as exc:
        log.warning("scan /%s/ selhal: %s", board, exc)
        repo.mark_rule_scanned(conn, rule["id"], now, error=f"{type(exc).__name__}: {exc}")
        return 0

    added = 0
    for op in resp.data:
        if not matches_keywords(op_search_text(op), keywords):
            continue
        if repo.add_thread(conn, board, int(op["no"]), f"rule:{rule['id']}", now):
            added += 1
    repo.mark_rule_scanned(conn, rule["id"], now)
    log.info("scan /%s/: %s nových threadů", board, added)
    return added


async def scan_due(conn, client, cfg: Config, now: datetime) -> int:
    total = 0
    for rule in repo.due_rules(conn, now, cfg.scan_interval):
        total += await scan_rule(conn, client, rule, now)
    return total
