import json

import httpx

LAST_MODIFIED = "Mon, 17 Aug 2026 12:00:00 GMT"


class Fake4chan:
    """Fake 4chan API + CDN nad httpx.MockTransport. Žádný skutečný socket."""

    def __init__(self):
        self.threads: dict[tuple[str, int], dict | None] = {}
        self.catalogs: dict[str, list[dict]] = {}
        self.files: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.last_modified = LAST_MODIFIED

    def set_thread(self, board: str, no: int, posts: list[dict] | None) -> None:
        """posts=None znamená 404 (thread smazán)."""
        self.threads[(board, no)] = None if posts is None else {"posts": posts}

    def set_catalog(self, board: str, ops: list[dict]) -> None:
        self.catalogs[board] = ops

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
            if request.headers.get("If-Modified-Since") == self.last_modified:
                return httpx.Response(304)
            return httpx.Response(
                200, json=self.threads[key],
                headers={"Last-Modified": self.last_modified})
        if len(parts) == 2 and parts[1] == "catalog.json":
            ops = self.catalogs.get(parts[0])
            if ops is None:
                return httpx.Response(404)
            pages = [{"page": 1, "threads": ops}]
            return httpx.Response(200, content=json.dumps(pages),
                                  headers={"Content-Type": "application/json",
                                           "Last-Modified": self.last_modified})
        return httpx.Response(404)
