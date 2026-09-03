"""Server-side locale resolution + the storage abstraction."""

from pathlib import Path

from app.core import i18n
from app.services.storage import LocalStorage


class _Req:
    """Minimal stand-in for a Starlette request."""

    def __init__(self, query=None, cookies=None, headers=None):
        self.query_params = query or {}
        self.cookies = cookies or {}
        self.headers = headers or {}


def test_query_param_wins():
    req = _Req(query={"lang": "ko"}, cookies={"lang": "en"},
               headers={"accept-language": "en-US"})
    assert i18n.resolve_locale(req) == "ko"


def test_cookie_beats_accept_language():
    req = _Req(cookies={"lang": "ko"}, headers={"accept-language": "en-US,en"})
    assert i18n.resolve_locale(req) == "ko"


def test_accept_language_fallback():
    req = _Req(headers={"accept-language": "ko-KR,ko;q=0.9,en;q=0.8"})
    assert i18n.resolve_locale(req) == "ko"


def test_unsupported_falls_back_to_default():
    req = _Req(query={"lang": "fr"}, headers={"accept-language": "fr-FR"})
    assert i18n.resolve_locale(req) == i18n.DEFAULT


def test_lang_cookie_is_persisted_for_explicit_choice(client):
    r = client.get("/api/stats?lang=ko")
    assert "lang=ko" in r.headers.get("set-cookie", "")


def test_no_cookie_written_without_explicit_choice(client):
    r = client.get("/api/stats")
    assert "lang=" not in r.headers.get("set-cookie", "")


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #
def test_local_storage_roundtrip(tmp_path):
    store = LocalStorage(tmp_path)
    (tmp_path / "board").mkdir()
    target = tmp_path / "board" / "pin.jpg"
    target.write_bytes(b"hello")
    (tmp_path / "board" / "pin.jpg.json").write_text("{}")

    assert store.exists("board/pin.jpg") is True
    assert store.size("board/pin.jpg") == 5
    assert store.url_for("board/pin.jpg") == "/media/board/pin.jpg"
    assert store.local_path("board/pin.jpg") == target.resolve()

    store.delete("board/pin.jpg")
    assert not target.exists()
    # The metadata sidecar goes with the media file.
    assert not (tmp_path / "board" / "pin.jpg.json").exists()


def test_local_storage_refuses_path_traversal(tmp_path):
    """A crafted rel_path must never reach outside the archive root."""
    store = LocalStorage(tmp_path / "root")
    (tmp_path / "root").mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("keep me")

    assert store.local_path("../secret.txt") is None
    assert store.exists("../secret.txt") is False
    store.delete("../secret.txt")
    assert outside.exists()  # untouched


def test_local_storage_delete_prefix_removes_board_folder(tmp_path):
    store = LocalStorage(tmp_path)
    folder = tmp_path / "board-1"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"a")
    store.delete_prefix("board-1")
    assert not folder.exists()


def test_local_storage_never_wipes_the_archive_root(tmp_path):
    """delete_prefix("") must not empty the whole data volume."""
    store = LocalStorage(tmp_path)
    keep = tmp_path / "keep.txt"
    keep.write_text("x")
    store.delete_prefix("")
    assert tmp_path.exists() and keep.exists()


def test_local_storage_missing_file_is_safe(tmp_path):
    store = LocalStorage(tmp_path)
    assert store.exists("nope.jpg") is False
    assert store.size("nope.jpg") is None
    store.delete("nope.jpg")  # must not raise


def test_storage_root_follows_settings(monkeypatch):
    """The default backend resolves its root lazily from settings."""
    from app.services.storage import get_storage

    store = get_storage()
    assert isinstance(store.local_path("x/y.jpg"), Path)
    assert store.name == "local"
