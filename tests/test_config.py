from pathlib import Path

from app.config import load_config


def test_defaults():
    cfg = load_config({})
    assert cfg.data_dir == Path("/data")
    assert cfg.db_path == Path("/data/app.db")
    assert cfg.archive_dir == Path("/data/archive")
    assert cfg.serve_static is False
    assert cfg.poll_min_interval == 60
    assert cfg.poll_max_interval == 600
    assert cfg.scan_interval == 300
    assert cfg.api_rate == 1.0
    assert cfg.media_rate == 4.0


def test_env_overrides():
    cfg = load_config({
        "DATA_DIR": "/tmp/x",
        "SERVE_STATIC": "1",
        "POLL_MIN_INTERVAL": "30",
        "API_RATE": "0.5",
    })
    assert cfg.data_dir == Path("/tmp/x")
    assert cfg.db_path == Path("/tmp/x/app.db")
    assert cfg.serve_static is True
    assert cfg.poll_min_interval == 30
    assert cfg.api_rate == 0.5
