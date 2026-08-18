import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    serve_static: bool
    poll_min_interval: int
    poll_max_interval: int
    scan_interval: int
    api_rate: float
    media_rate: float
    log_level: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
    return Config(
        data_dir=Path(e.get("DATA_DIR", "/data")),
        serve_static=e.get("SERVE_STATIC", "0") == "1",
        poll_min_interval=int(e.get("POLL_MIN_INTERVAL", "60")),
        poll_max_interval=int(e.get("POLL_MAX_INTERVAL", "600")),
        scan_interval=int(e.get("SCAN_INTERVAL", "300")),
        api_rate=float(e.get("API_RATE", "1")),
        media_rate=float(e.get("MEDIA_RATE", "4")),
        log_level=e.get("LOG_LEVEL", "INFO"),
    )
