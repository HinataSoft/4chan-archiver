from app.text import html_to_text, matches_keywords, op_search_text


def test_strips_tags_and_unescapes_entities():
    raw = '<a href="#p1" class="quotelink">&gt;&gt;1</a><br>Rust &amp; C++ <b>rocks</b>'
    assert html_to_text(raw) == ">>1 Rust & C++ rocks"


def test_br_becomes_space_not_glue():
    assert html_to_text("foo<br>bar") == "foo bar"


def test_handles_empty_and_none():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""


def test_op_search_text_joins_subject_and_comment():
    post = {"sub": "Daily Programming", "com": "post your <b>setup</b>"}
    assert op_search_text(post) == "Daily Programming post your setup"


def test_op_search_text_survives_missing_fields():
    assert op_search_text({}) == ""
    assert op_search_text({"com": "only comment"}) == "only comment"


def test_matching_is_case_insensitive_substring():
    assert matches_keywords("Daily Programming Thread", ["programming"]) is True
    assert matches_keywords("Daily Programming Thread", ["RUST", "daily"]) is True
    assert matches_keywords("Daily Programming Thread", ["rust"]) is False


def test_entity_encoded_text_is_matchable():
    text = html_to_text("looking for &gt;&gt;&gt; deals &amp; steals")
    assert matches_keywords(text, ["& steals"]) is True


def test_empty_keywords_never_match():
    assert matches_keywords("anything", []) is False
    assert matches_keywords("anything", ["", "  "]) is False
