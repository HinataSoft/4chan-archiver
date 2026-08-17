from app import archive


def test_media_filenames_follow_4chan_convention():
    assert archive.media_filename(1699887766543, ".webm", "file") == "1699887766543.webm"
    assert archive.media_filename(1699887766543, ".webm", "thumb") == "1699887766543s.jpg"
    assert archive.media_filename(1699887766543, ".png", "thumb") == "1699887766543s.jpg"


def test_thread_dir_layout(cfg):
    assert archive.thread_dir(cfg.archive_dir, "g", 123) == cfg.archive_dir / "g" / "123"


def test_save_and_load_roundtrip(cfg, now):
    doc = archive.new_document("g", 123, now)
    doc["posts"] = [{"no": 1, "com": "hello"}]
    archive.save_thread(cfg.archive_dir, doc)
    assert archive.load_thread(cfg.archive_dir, "g", 123) == doc


def test_load_missing_thread_returns_none(cfg):
    assert archive.load_thread(cfg.archive_dir, "g", 999) is None


def test_save_leaves_no_tmp_file(cfg, now):
    archive.save_thread(cfg.archive_dir, archive.new_document("g", 123, now))
    files = {p.name for p in archive.thread_dir(cfg.archive_dir, "g", 123).iterdir()}
    assert files == {"thread.json"}


def test_merge_appends_new_posts():
    old = [{"no": 1, "com": "a"}]
    new = [{"no": 1, "com": "a"}, {"no": 2, "com": "b"}]
    assert [p["no"] for p in archive.merge_posts(old, new)] == [1, 2]


def test_merge_flags_disappeared_post_as_deleted():
    old = [{"no": 1, "com": "a"}, {"no": 2, "com": "b"}]
    new = [{"no": 1, "com": "a"}]
    merged = archive.merge_posts(old, new)
    assert [p["no"] for p in merged] == [1, 2]
    assert merged[1]["_deleted"] is True
    assert merged[0].get("_deleted", False) is False


def test_merge_never_drops_content_of_deleted_post():
    old = [{"no": 1, "com": "a"}, {"no": 2, "com": "important", "tim": 42, "ext": ".jpg"}]
    merged = archive.merge_posts(old, [{"no": 1, "com": "a"}])
    assert merged[1]["com"] == "important"
    assert merged[1]["tim"] == 42


def test_merge_updates_fields_of_surviving_post():
    old = [{"no": 1, "com": "a", "closed": 0}]
    new = [{"no": 1, "com": "a", "closed": 1, "sticky": 1}]
    merged = archive.merge_posts(old, new)
    assert merged[0]["closed"] == 1
    assert merged[0]["sticky"] == 1
    assert merged[0]["_deleted"] is False


def test_merge_resurrects_post_that_reappears():
    old = [{"no": 1, "com": "a"}, {"no": 2, "com": "b", "_deleted": True}]
    merged = archive.merge_posts(old, [{"no": 1, "com": "a"}, {"no": 2, "com": "b"}])
    assert merged[1]["_deleted"] is False


def test_merge_keeps_chronological_order_by_post_number():
    old = [{"no": 5, "com": "e"}]
    new = [{"no": 5, "com": "e"}, {"no": 3, "com": "c"}]
    assert [p["no"] for p in archive.merge_posts(old, new)] == [3, 5]


def test_media_entries_skips_posts_without_files():
    posts = [
        {"no": 1, "tim": 111, "ext": ".jpg"},
        {"no": 2, "com": "text only"},
        {"no": 3, "tim": 222, "ext": ".webm"},
        {"no": 4, "tim": 333, "ext": ".jpg", "filedeleted": 1},
    ]
    assert archive.media_entries(posts) == [(111, ".jpg"), (222, ".webm")]


def test_delete_thread_dir_removes_everything(cfg, now):
    archive.save_thread(cfg.archive_dir, archive.new_document("g", 123, now))
    (archive.thread_dir(cfg.archive_dir, "g", 123) / "111.jpg").write_bytes(b"x")
    archive.delete_thread_dir(cfg.archive_dir, "g", 123)
    assert not archive.thread_dir(cfg.archive_dir, "g", 123).exists()


def test_delete_missing_dir_is_noop(cfg):
    archive.delete_thread_dir(cfg.archive_dir, "g", 404)
