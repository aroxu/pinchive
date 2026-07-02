"""Slug + per-board folder derivation."""

from app.tasks import board_folder, derive_slug


def test_derive_slug_board_url():
    assert derive_slug("https://www.pinterest.com/john/cool-board/") == "john__cool-board"


def test_derive_slug_pin_url():
    assert derive_slug("https://www.pinterest.com/pin/12345/") == "pin__12345"


def test_derive_slug_sanitizes():
    s = derive_slug("https://www.pinterest.com/a b/c!d/")
    assert " " not in s and "!" not in s


def test_derive_slug_fallback():
    assert derive_slug("https://www.pinterest.com/") == "board"


def test_same_url_boards_get_distinct_folders():
    # Regression: two boards with the same URL derive the same slug but must NOT
    # share a directory (else they clobber each other's files).
    slug = derive_slug("https://www.pinterest.com/pin/424605071112831904/")
    assert board_folder(slug, 1) != board_folder(slug, 2)
    assert board_folder(slug, 1).endswith("-1")
