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


def test_delete_thread_dir_propagates_real_errors(cfg, now, monkeypatch):
    import shutil
    archive.save_thread(cfg.archive_dir, archive.new_document("g", 123, now))

    calls = []

    def fake_rmtree(path, ignore_errors=False):
        calls.append({"path": path, "ignore_errors": ignore_errors})
        if ignore_errors:
            return
        raise PermissionError("File locked by antivirus")

    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)

    try:
        archive.delete_thread_dir(cfg.archive_dir, "g", 123)
        assert False, "Expected PermissionError to propagate"
    except PermissionError:
        pass

    assert len(calls) == 1, f"Expected rmtree to be called once, got {len(calls)} calls"
    assert calls[0]["ignore_errors"] is False, \
        f"Expected rmtree called with ignore_errors=False, got {calls[0]}"


def test_media_entries_accepts_normal_extensions():
    posts = [{"no": 1, "tim": 111, "ext": ".webm"}, {"no": 2, "tim": 222, "ext": ".jpg"}]
    assert archive.media_entries(posts) == [(111, ".webm"), (222, ".jpg")]


def test_media_entries_rejects_path_traversal_in_ext():
    """I1: `ext` jde přímo do jména souboru na disku. Nepřátelský ext by zapsal
    mimo archiv — spec staví bezpečnost médií právě na tom, že cesta je
    odvozená z čísel, což platí jen když je i ext validovaný."""
    hostile = [
        {"no": 1, "tim": 111, "ext": "/../../../../../pwned.txt"},
        {"no": 2, "tim": 222, "ext": "../evil.sh"},
        {"no": 3, "tim": 333, "ext": ".jpg/../../x"},
        {"no": 4, "tim": 444, "ext": "jpg"},
        {"no": 5, "tim": 555, "ext": ".JPG" + chr(92) + ".." + chr(92) + "x"},
        {"no": 6, "tim": 666, "ext": 123},
        {"no": 7, "tim": 777, "ext": ".toolongext"},
    ]
    assert archive.media_entries(hostile) == []


# ── Archiv jednotlivých příspěvků ────────────────────────────────────────────

def _source_thread(cfg, now, posts, media_files=()):
    doc = archive.new_document("g", 123, now)
    doc["posts"] = posts
    archive.save_thread(cfg.archive_dir, doc)
    directory = archive.thread_dir(cfg.archive_dir, "g", 123)
    for name, payload in media_files:
        (directory / name).write_bytes(payload)
    return doc


def test_archiving_a_post_copies_it_into_the_board_archive(cfg, now):
    _source_thread(cfg, now, [{"no": 1, "com": "op"}, {"no": 2, "com": "keep me"}])

    assert archive.archive_post(cfg.archive_dir, "g", 123, {"no": 2, "com": "keep me"},
                                source_subject="Some Thread", now=now) is True

    doc = archive.load_thread(cfg.archive_dir, "g", archive.ARCHIVE_NO)
    assert [p["no"] for p in doc["posts"]] == [2]
    assert doc["posts"][0]["com"] == "keep me"


def test_archived_post_records_where_it_came_from(cfg, now):
    _source_thread(cfg, now, [{"no": 2, "com": "keep me"}])
    archive.archive_post(cfg.archive_dir, "g", 123, {"no": 2, "com": "keep me"},
                         source_subject="Daily Programming", now=now)

    post = archive.load_thread(cfg.archive_dir, "g", archive.ARCHIVE_NO)["posts"][0]
    assert post["_source_thread"] == 123
    assert post["_source_subject"] == "Daily Programming"
    assert post["_archived_at"] == now.isoformat()


def test_media_are_copied_so_the_archive_survives_deleting_the_thread(cfg, now):
    _source_thread(cfg, now, [{"no": 2, "tim": 555, "ext": ".jpg"}],
                   media_files=[("555.jpg", b"full"), ("555s.jpg", b"thumb")])

    archive.archive_post(cfg.archive_dir, "g", 123, {"no": 2, "tim": 555, "ext": ".jpg"},
                         source_subject=None, now=now)
    archive.delete_thread_dir(cfg.archive_dir, "g", 123)

    directory = archive.thread_dir(cfg.archive_dir, "g", archive.ARCHIVE_NO)
    assert (directory / "555.jpg").read_bytes() == b"full"
    assert (directory / "555s.jpg").read_bytes() == b"thumb"


def test_archiving_the_same_post_twice_changes_nothing(cfg, now):
    _source_thread(cfg, now, [{"no": 2, "com": "keep me"}])
    post = {"no": 2, "com": "keep me"}

    assert archive.archive_post(cfg.archive_dir, "g", 123, post,
                                source_subject=None, now=now) is True
    assert archive.archive_post(cfg.archive_dir, "g", 123, post,
                                source_subject=None, now=now) is False
    assert len(archive.load_thread(cfg.archive_dir, "g", archive.ARCHIVE_NO)["posts"]) == 1


def test_archive_is_ordered_by_the_original_post_date(cfg, now):
    _source_thread(cfg, now, [])
    for no, time in [(30, 300), (10, 100), (20, 200)]:
        archive.archive_post(cfg.archive_dir, "g", 123, {"no": no, "time": time},
                             source_subject=None, now=now)

    posts = archive.load_thread(cfg.archive_dir, "g", archive.ARCHIVE_NO)["posts"]
    assert [p["time"] for p in posts] == [100, 200, 300]


def test_removing_an_archived_post_takes_its_media_with_it(cfg, now):
    _source_thread(cfg, now, [{"no": 2, "tim": 555, "ext": ".jpg"}],
                   media_files=[("555.jpg", b"full"), ("555s.jpg", b"thumb")])
    archive.archive_post(cfg.archive_dir, "g", 123, {"no": 2, "tim": 555, "ext": ".jpg"},
                         source_subject=None, now=now)

    assert archive.unarchive_post(cfg.archive_dir, "g", 2) is True
    directory = archive.thread_dir(cfg.archive_dir, "g", archive.ARCHIVE_NO)
    assert not (directory / "555.jpg").exists()
    assert not (directory / "555s.jpg").exists()
    assert archive.load_thread(cfg.archive_dir, "g", archive.ARCHIVE_NO)["posts"] == []


def test_removing_a_post_that_is_not_archived_reports_it(cfg, now):
    _source_thread(cfg, now, [])
    assert archive.unarchive_post(cfg.archive_dir, "g", 999) is False


def test_archived_boards_lists_what_has_something_in_it(cfg, now):
    _source_thread(cfg, now, [])
    assert archive.archived_boards(cfg.archive_dir) == []

    archive.archive_post(cfg.archive_dir, "g", 123, {"no": 2, "time": 1},
                         source_subject=None, now=now)
    archive.archive_post(cfg.archive_dir, "g", 123, {"no": 3, "time": 2},
                         source_subject=None, now=now)
    assert archive.archived_boards(cfg.archive_dir) == [{"board": "g", "posts": 2}]
