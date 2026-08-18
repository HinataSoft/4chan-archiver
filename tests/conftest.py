from datetime import datetime, timezone

import httpx
import pytest

from app.config import load_config
from app.db import connect
from tests.fake4chan import Fake4chan


@pytest.fixture
def cfg(tmp_path):
    return load_config({"DATA_DIR": str(tmp_path)})


@pytest.fixture
def conn(cfg):
    c = connect(cfg.db_path)
    yield c
    c.close()


@pytest.fixture
def now():
    return datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake():
    return Fake4chan()


@pytest.fixture
def client(fake):
    from app.fourchan import FourchanClient
    http = httpx.AsyncClient(transport=fake.transport())
    yield FourchanClient(http, api_rate=1000, media_rate=1000)
