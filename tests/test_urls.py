import pytest

from app.urls import ThreadRef, parse_thread_url


@pytest.mark.parametrize("raw", [
    "https://boards.4chan.org/g/thread/12345678",
    "https://boards.4channel.org/g/thread/12345678",
    "http://boards.4chan.org/g/thread/12345678/some-slug-text",
    "https://boards.4chan.org/g/thread/12345678#p12345690",
    "boards.4chan.org/g/thread/12345678",
    "  https://boards.4chan.org/g/thread/12345678/  ",
    "g/12345678",
    "/g/thread/12345678",
])
def test_accepts_known_forms(raw):
    assert parse_thread_url(raw) == ThreadRef(board="g", no=12345678)


def test_board_with_digits():
    assert parse_thread_url("https://boards.4chan.org/vr/thread/999") == ThreadRef("vr", 999)


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "https://example.com/g/thread/123",
    "https://boards.4chan.org/g/catalog",
    "https://boards.4chan.org/g/thread/abc",
    "g/",
    "12345678",
    "https://boards.4chan.org/thread/12345678",
])
def test_rejects_garbage(raw):
    with pytest.raises(ValueError):
        parse_thread_url(raw)
