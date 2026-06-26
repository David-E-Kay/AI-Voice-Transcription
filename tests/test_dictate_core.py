from dictate_core import clean_text


def test_clean_text_strips_and_collapses():
    assert clean_text("  hello   world  ") == "hello world"


def test_clean_text_empty_is_empty():
    assert clean_text("   ") == ""


def test_clean_text_collapses_newlines():
    assert clean_text("line one\n  line two") == "line one line two"
