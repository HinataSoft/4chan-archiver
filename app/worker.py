import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app import media, poller, scanner
from app.config import Config, load_config
from app.db import connect
from app.fourchan import FourchanClient

log = logging.getLogger(__name__)

TICK_SECONDS = 5
MEDIA_BATCH = 20


def _make_client(cfg: Config) -> FourchanClient:
    http = httpx.AsyncClient(
        headers={"User-Agent": "fourchan-archiver/0.1 (personal archive)"},
        follow_redirects=True)
    return FourchanClient(http, api_rate=cfg.api_rate, media_rate=cfg.media_rate)


async def tick(conn, client, cfg: Config, now: datetime) -> dict:
    scanned = await scanner.scan_due(conn, client, cfg, now)
    poll = await poller.poll_due(conn, client, cfg, now)
    downloaded = await media.download_pending(conn, client, cfg, MEDIA_BATCH)
    return {"scanned": scanned, "poll": poll, "media": downloaded}


async def run(cfg: Config, *, iterations: int | None = None) -> None:
    logging.basicConfig(level=cfg.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    conn = connect(cfg.db_path)
    client = _make_client(cfg)
    log.info("worker běží, data v %s", cfg.data_dir)
    # Jednorázově dorovná názvy threadů stažených dřív, než se odvozovaly
    # z textu OP; poll sám by se k nim nedostal, dokud se thread nezmění.
    poller.backfill_missing_subjects(conn, cfg)
    done = 0
    try:
        while iterations is None or done < iterations:
            try:
                result = await tick(conn, client, cfg, datetime.now(timezone.utc))
                if result["scanned"] or result["poll"]["updated"] or result["media"]["ok"]:
                    log.info("tick: %s", result)
            except Exception:
                log.exception("tick selhal, pokračuji")
            done += 1
            if iterations is None or done < iterations:
                await asyncio.sleep(TICK_SECONDS)
    finally:
        try:
            await client.aclose()
        except Exception:
            log.exception("chyba při zavírání klienta")
        try:
            conn.close()
        except Exception:
            log.exception("chyba při zavírání databáze")


if __name__ == "__main__":
    asyncio.run(run(load_config()))
