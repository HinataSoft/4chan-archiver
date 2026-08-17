import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://a.4cdn.org"
MEDIA_BASE = "https://i.4cdn.org"


class RateLimiter:
    """Nejvýše `rate` požadavků za sekundu, sériově."""

    def __init__(self, rate: float, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            wait = self._next_at - now
            if wait > 0:
                await self._sleep(wait)
                now = self._clock()
            self._next_at = now + self._interval


@dataclass(frozen=True)
class JsonResponse:
    status: int
    data: Any | None
    last_modified: str | None


class FourchanClient:
    def __init__(self, client: httpx.AsyncClient, *,
                 api_rate: float = 1.0, media_rate: float = 4.0):
        self._http = client
        self._api = RateLimiter(api_rate)
        self._media = RateLimiter(media_rate)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get_json(self, url: str, last_modified: str | None) -> JsonResponse:
        headers = {"If-Modified-Since": last_modified} if last_modified else {}
        await self._api.acquire()
        resp = await self._http.get(url, headers=headers, timeout=30.0)
        if resp.status_code in (304, 404):
            return JsonResponse(resp.status_code, None, last_modified)
        resp.raise_for_status()
        return JsonResponse(200, resp.json(), resp.headers.get("Last-Modified"))

    async def fetch_thread(self, board: str, no: int,
                           last_modified: str | None) -> JsonResponse:
        return await self._get_json(f"{API_BASE}/{board}/thread/{no}.json",
                                    last_modified)

    async def fetch_catalog(self, board: str) -> JsonResponse:
        resp = await self._get_json(f"{API_BASE}/{board}/catalog.json", None)
        if resp.status != 200:
            return resp
        ops = [op for page in resp.data for op in page.get("threads", [])]
        return JsonResponse(200, ops, resp.last_modified)

    def media_url(self, board: str, tim: int, ext: str, kind: str) -> str:
        name = f"{tim}s.jpg" if kind == "thumb" else f"{tim}{ext}"
        return f"{MEDIA_BASE}/{board}/{name}"

    async def download(self, url: str, dest: Path) -> int:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        await self._media.acquire()
        try:
            written = 0
            async with self._http.stream("GET", url, timeout=120.0) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as handle:
                    async for chunk in resp.aiter_bytes(65536):
                        handle.write(chunk)
                        written += len(chunk)
            tmp.replace(dest)
            return written
        finally:
            tmp.unlink(missing_ok=True)
