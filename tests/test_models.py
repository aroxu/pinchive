"""Board display-name derivation (Korean/non-ASCII URL decoding)."""

from app.models import Board, readable_name_from_url


def test_readable_name_decodes_percent_encoded_korean():
    url = "https://kr.pinterest.com/aroxu02/%EA%B7%B8%EB%A6%BC-%EC%97%B0%EC%8A%B5%EC%9A%A9/"
    assert readable_name_from_url(url) == "그림 연습용"


def test_readable_name_plain_ascii():
    assert readable_name_from_url("https://www.pinterest.com/user/cool-board/") == "cool board"


def test_readable_name_pin_url():
    # single-segment path -> uses it
    assert readable_name_from_url("https://www.pinterest.com/pin/") == "pin"


def test_display_title_prefers_real_title():
    b = Board(url="https://kr.pinterest.com/u/%EA%B7%B8%EB%A6%BC/", title="Real", slug="s")
    assert b.display_title == "Real"


def test_display_title_falls_back_to_decoded_url():
    b = Board(url="https://kr.pinterest.com/aroxu02/%EA%B7%B8%EB%A6%BC/",
              slug="aroxu02__-EA-B7-B8")
    assert b.display_title == "그림"


def test_display_title_last_resort_slug():
    b = Board(url="", slug="fallback-slug")
    assert b.display_title == "fallback-slug"
