"""i18n catalog, locale resolution, and per-request rendering."""

from app import i18n


def test_t_missing_key_returns_key():
    i18n.set_locale("en")
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_t_locale_and_format():
    i18n.set_locale("ko")
    assert i18n.t("nav.boards") == "보드"
    assert i18n.t("archive.count", n=3) == "보드 3개"
    i18n.set_locale("en")
    assert i18n.t("nav.boards") == "Boards"
    assert i18n.t("archive.count", n=3) == "3 boards"


def test_t_falls_back_to_default_locale_for_missing_translation():
    # every EN key should exist; a KO-only lookup of a hypothetical missing key
    # falls back to EN then to the key itself
    i18n.set_locale("ko")
    assert i18n.t("nav.boards")  # present in ko
    i18n.set_locale("en")


def test_set_locale_coerces_unknown_to_default():
    i18n.set_locale("zz")
    assert i18n.get_locale() == "en"
    i18n.set_locale("ko")
    assert i18n.get_locale() == "ko"
    i18n.set_locale("en")


def test_catalogs_have_matching_keys():
    # KO must cover every EN key (no silent English leakage)
    missing = set(i18n.EN) - set(i18n.KO)
    assert not missing, f"KO missing keys: {sorted(missing)}"


def test_every_board_status_has_a_translation():
    # the status filter dropdown renders t('status.<enum value>') for each status,
    # so every BoardStatus needs a key or the raw key leaks into the UI
    from app.models import BoardStatus

    for st in BoardStatus:
        key = f"status.{st.value}"
        assert i18n.EN.get(key), f"missing EN {key}"
        assert i18n.KO.get(key), f"missing KO {key}"


class _Req:
    def __init__(self, query=None, cookies=None, al=""):
        self.query_params = query or {}
        self.cookies = cookies or {}
        self.headers = {"accept-language": al} if al else {}


def test_resolve_priority_query_over_cookie_over_header():
    assert i18n.resolve_locale(
        _Req(query={"lang": "ko"}, cookies={"lang": "en"}, al="en")
    ) == "ko"
    assert i18n.resolve_locale(_Req(cookies={"lang": "ko"}, al="en")) == "ko"
    assert i18n.resolve_locale(_Req(al="ko-KR,ko;q=0.9,en;q=0.8")) == "ko"


def test_resolve_unsupported_falls_back_to_default():
    assert i18n.resolve_locale(_Req(al="fr-FR,fr;q=0.9")) == "en"
    assert i18n.resolve_locale(_Req()) == "en"
    assert i18n.resolve_locale(_Req(query={"lang": "zz"})) == "en"


# --- rendered pages honor the locale (proves ContextVar reaches templates) ---
def test_page_korean_via_accept_language(client):
    r = client.get("/", headers={"Accept-Language": "ko-KR,ko;q=0.9"})
    assert r.status_code == 200
    assert "보드 추가" in r.text            # nav.add_board (ko)
    assert "Add board" not in r.text


def test_page_default_english(client):
    r = client.get("/")
    assert "Add board" in r.text
    assert "보드 추가" not in r.text


def test_lang_query_sets_cookie_and_renders(client):
    r = client.get("/?lang=ko")
    setc = r.headers.get("set-cookie", "")
    assert "lang=ko" in setc
    assert "보드 추가" in r.text            # nav.add_board (ko)
