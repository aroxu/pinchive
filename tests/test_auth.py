"""Cookie parsing + liveness classification."""

import time

import httpx

from app import auth


def _cookie_path(tmp_path):
    return tmp_path / "c.txt"


def test_parse_netscape_and_detect_session(tmp_path):
    p = _cookie_path(tmp_path)
    exp = int(time.time()) + 99999
    raw = f".pinterest.com\tTRUE\t/\tTRUE\t{exp}\t_pinterest_sess\tABC\n"
    # save via the public API (writes to cookies_dir); emulate by writing file
    n = _save(p, raw)
    assert n == 1
    res = auth.validate_cookies(p, network=False)
    assert res.active and "present" in res.message


def test_parse_json_cookies(tmp_path):
    p = _cookie_path(tmp_path)
    raw = (
        '[{"name":"_pinterest_sess","value":"XYZ","domain":"pinterest.com",'
        '"path":"/","secure":true,"expirationDate":9999999999}]'
    )
    n = _save(p, raw)
    assert n == 1
    assert auth.validate_cookies(p, network=False).active


def test_missing_session_cookie(tmp_path):
    p = _cookie_path(tmp_path)
    _save(p, ".pinterest.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tzzz\n")
    res = auth.validate_cookies(p, network=False)
    assert not res.active and "_pinterest_sess" in res.message


def test_expired_by_date(tmp_path):
    p = _cookie_path(tmp_path)
    past = int(time.time()) - 10
    _save(p, f".pinterest.com\tTRUE\t/\tTRUE\t{past}\t_pinterest_sess\tABC\n")
    res = auth.validate_cookies(p, network=False)
    assert not res.active and "expired" in res.message


def test_empty_payload_raises(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        auth.save_cookies_text_to(_cookie_path(tmp_path), "   ")


# --- _classify: the core liveness heuristic ---
def _resp(status=200, text="", url="https://www.pinterest.com/settings/"):
    req = httpx.Request("GET", url)
    return httpx.Response(status_code=status, text=text, request=req)


def test_classify_authenticated():
    r = _resp(text='window.__X = {"is_authenticated": true, "id": 1};')
    assert auth._classify(r).active


def test_classify_not_authenticated():
    r = _resp(text='{"is_authenticated": false, "unauth_id": "x"}',
              url="https://www.pinterest.com/")
    assert not auth._classify(r).active


def test_classify_forbidden():
    assert not auth._classify(_resp(status=403)).active


def test_classify_login_redirect_without_flag():
    r = _resp(text="<html>login</html>", url="https://www.pinterest.com/login/")
    assert not auth._classify(r).active


def test_classify_settings_reachable_fallback():
    # 200, still on /settings/, no explicit flag -> treated as live
    r = _resp(text="<html>ok</html>", url="https://www.pinterest.com/settings/")
    assert auth._classify(r).active


# ---- helpers ----
def _save(path, raw):
    """Mirror save_cookies_text but to an arbitrary path (tests avoid cookies_dir)."""
    return auth.save_cookies_text_to(path, raw)
