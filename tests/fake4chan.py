import json
from datetime import datetime, timedelta, timezone

import httpx

LAST_MODIFIED = "Mon, 17 Aug 2026 12:00:00 GMT"

_EPOCH = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


class Fake4chan:
    """Fake 4chan API + CDN nad httpx.MockTransport. Žádný skutečný socket."""

    def __init__(self):
        self.threads: dict[tuple[str, int], dict | None] = {}
        self.catalogs: dict[str, list[dict]] = {}
        self.files: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self._thread_last_modified: dict[tuple[str, int], str] = {}
        self._catalog_last_modified: dict[str, str] = {}
        self._counter = 0
        self._current_last_modified = LAST_MODIFIED

    def _next_last_modified(self) -> str:
        """Vrátí nový, unikátní a monotónně rostoucí HTTP-date (RFC 7231)."""
        self._counter += 1
        stamp = _EPOCH + timedelta(seconds=self._counter)
        value = stamp.strftime("%a, %d %b %Y %H:%M:%S GMT")
        self._current_last_modified = value
        return value

    @property
    def last_modified(self) -> str:
        """Naposledy vydaná hodnota Last-Modified (napříč všemi zdroji)."""
        return self._current_last_modified

    @last_modified.setter
    def last_modified(self, value: str) -> None:
        """Zpětná kompatibilita: přepíše Last-Modified všech aktuálně
        nastavených threadů a katalogů (a stane se novou "aktuální"
        hodnotou), stejně jako dřívější sdílený scalar."""
        self._current_last_modified = value
        for key, posts in self.threads.items():
            if posts is not None:
                self._thread_last_modified[key] = value
        for board in self.catalogs:
            self._catalog_last_modified[board] = value

    def set_thread(self, board: str, no: int, posts: list[dict] | None) -> None:
        """posts=None znamená 404 (thread smazán)."""
        self.threads[(board, no)] = None if posts is None else {"posts": posts}
        self._thread_last_modified[(board, no)] = self._next_last_modified()

    def set_catalog(self, board: str, ops: list[dict]) -> None:
        self.catalogs[board] = ops
        self._catalog_last_modified[board] = self._next_last_modified()

    def set_file(self, board: str, name: str, payload: bytes) -> None:
        self.files[f"/{board}/{name}"] = payload

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "i.4cdn.org":
            payload = self.files.get(path)
            if payload is None:
                return httpx.Response(404)
            return httpx.Response(200, content=payload)
        if host == "a.4cdn.org":
            return self._handle_api(request, path)
        return httpx.Response(404)

    def _handle_api(self, request: httpx.Request, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[1] == "thread":
            key = (parts[0], int(parts[2].removesuffix(".json")))
            if key not in self.threads or self.threads[key] is None:
                return httpx.Response(404)
            stamp = self._thread_last_modified[key]
            if request.headers.get("If-Modified-Since") == stamp:
                return httpx.Response(304)
            return httpx.Response(
                200, json=self.threads[key],
                headers={"Last-Modified": stamp})
        if len(parts) == 2 and parts[1] == "catalog.json":
            ops = self.catalogs.get(parts[0])
            if ops is None:
                return httpx.Response(404)
            pages = [{"page": 1, "threads": ops}]
            stamp = self._catalog_last_modified[parts[0]]
            return httpx.Response(200, content=json.dumps(pages),
                                  headers={"Content-Type": "application/json",
                                           "Last-Modified": stamp})
        return httpx.Response(404)
