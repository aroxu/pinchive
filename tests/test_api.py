"""Route-level tests via TestClient (no redis; queue degraded)."""

import shutil

from sqlmodel import select

from app import dedup
from app.config import get_settings
from app.db import session_scope
from app.models import Board, BoardStatus, Pin, Tag

settings = get_settings()


def _mk_board(title="Board", slug="slug", url="https://www.pinterest.com/u/b/",
              status=BoardStatus.done, pin_count=0):
    with session_scope() as s:
        b = Board(title=title, slug=slug, url=url, status=status, pin_count=pin_count)
        s.add(b)
        s.flush()
        return b.id


def _mk_pin(board_id, rel_path, sha=None, ph=None, media="image", **kw):
    with session_scope() as s:
        p = Pin(board_id=board_id, rel_path=rel_path, filename=rel_path.split("/")[-1],
                media_type=media, content_sha256=sha, phash=ph, **kw)
        s.add(p)
        s.flush()
        return p.id


# --------------------------------------------------------------------------- #
def test_index_ok(client):
    assert client.get("/").status_code == 200


def test_index_has_live_stats(client):
    r = client.get("/")
    assert 'id="hero-stats"' in r.text
    assert 'hx-get="/stats"' in r.text
    assert 'every 5s' in r.text


def test_stats_endpoint_counts_and_polls(client):
    b = _mk_board(slug="s1")
    _mk_pin(b, "s1/a.jpg")
    _mk_pin(b, "s1/b.jpg")
    r = client.get("/stats")
    assert r.status_code == 200
    # keeps polling itself
    assert 'hx-get="/stats"' in r.text
    # counts present (1 board, 2 pins, 0 sessions)
    assert ">1<" in r.text and ">2<" in r.text


def test_healthz_queue_disabled(client):
    j = client.get("/healthz").json()
    assert j["status"] == "ok" and j["queue"] is False


def test_add_board_without_queue_becomes_pending(client):
    r = client.post("/boards", data={"url": "https://www.pinterest.com/u/b/",
                                     "credential_id": ""})
    assert r.status_code == 200
    with session_scope() as s:
        boards = s.exec(select(Board)).all()
        assert len(boards) == 1
        # queue is down -> should be pending with a note, not stuck "queued"
        assert boards[0].status == BoardStatus.pending


def test_add_board_rejects_non_pinterest(client):
    r = client.post("/boards", data={"url": "https://example.com/x", "credential_id": ""})
    assert r.status_code == 400


def test_tag_board_and_filter(client):
    bid = _mk_board(title="Alpha", slug="alpha")
    client.post(f"/boards/{bid}/tag", data={"name": "DIY"}, follow_redirects=False)
    # tag normalized to lowercase, board shows under it
    with session_scope() as s:
        tags = s.exec(select(Tag)).all()
        assert [t.name for t in tags] == ["diy"]
    assert "Alpha" in client.get("/?tag=diy").text
    assert "Alpha" not in client.get("/?tag=nope").text


def test_untag_board(client):
    bid = _mk_board(title="Alpha", slug="alpha")
    client.post(f"/boards/{bid}/tag", data={"name": "keep"}, follow_redirects=False)
    with session_scope() as s:
        tid = s.exec(select(Tag)).first().id
    client.post(f"/boards/{bid}/untag/{tid}", follow_redirects=False)
    with session_scope() as s:
        b = s.get(Board, bid)
        assert b.tags == []


def test_search_and_status_filter(client):
    _mk_board(title="Interior Ideas", slug="interior", status=BoardStatus.done)
    _mk_board(title="Workshop Refs", slug="workshop", status=BoardStatus.error)
    assert "Interior Ideas" in client.get("/?q=interior").text
    assert "Workshop Refs" not in client.get("/?q=interior").text
    assert "Workshop Refs" in client.get("/?status=error").text
    assert "Interior Ideas" not in client.get("/?status=error").text


def test_board_detail_polls_while_downloading(client):
    bid = _mk_board(slug="dl", status=BoardStatus.downloading)
    r = client.get(f"/boards/{bid}").text
    assert 'id="pins-live"' in r
    assert "every 3s" in r  # live-updates the grid while downloading


def test_board_detail_no_poll_when_done(client):
    bid = _mk_board(slug="dn", status=BoardStatus.done)
    r = client.get(f"/boards/{bid}").text
    assert 'id="pins-live"' in r
    assert "every 3s" not in r  # settled -> no polling


def test_board_detail_pin_type_filter(client):
    bid = _mk_board(slug="mix")
    _mk_pin(bid, "mix/a.jpg", media="image")
    _mk_pin(bid, "mix/b.mp4", media="video")
    assert "a.jpg" in client.get(f"/boards/{bid}?type=image").text
    assert "a.jpg" not in client.get(f"/boards/{bid}?type=video").text
    assert "No matching pins" in client.get(f"/boards/{bid}?type=video").text or \
           "b.mp4" in client.get(f"/boards/{bid}?type=video").text


def test_pin_tag(client):
    bid = _mk_board(slug="pt")
    pid = _mk_pin(bid, "pt/a.jpg")
    client.post(f"/pins/{pid}/tag", data={"name": "wood"}, follow_redirects=False)
    with session_scope() as s:
        p = s.get(Pin, pid)
        assert [t.name for t in p.tags] == ["wood"]


def test_duplicates_detect_and_resolve(client, make_image):
    # two boards, each with its own physical copy of the same image
    img = make_image(size=(500, 400))
    sha = dedup.sha256_file(img)
    ph = dedup.dhash(img)

    for slug in ("d1", "d2"):
        bdir = settings.boards_dir / slug
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(img, bdir / "pin.jpg")

    b1 = _mk_board(slug="d1", pin_count=1)
    b2 = _mk_board(slug="d2", pin_count=1)
    _mk_pin(b1, "d1/pin.jpg", sha=sha, ph=ph, width=500, height=400)
    p2 = _mk_pin(b2, "d2/pin.jpg", sha=sha, ph=ph, width=500, height=400)

    page = client.get("/duplicates").text
    assert "copies" in page  # a group rendered

    # delete the second copy
    assert (settings.boards_dir / "d2" / "pin.jpg").exists()
    r = client.post("/duplicates/resolve", data={"pin_ids": [p2]},
                    follow_redirects=False)
    assert r.status_code == 303
    # file removed, kept copy intact
    assert not (settings.boards_dir / "d2" / "pin.jpg").exists()
    assert (settings.boards_dir / "d1" / "pin.jpg").exists()
    # counters reconciled + no more duplicate group
    with session_scope() as s:
        assert s.get(Pin, p2) is None
        assert s.get(Board, b2).pin_count == 0
    assert "No duplicates found" in client.get("/duplicates").text


def test_delete_board_removes_row(client):
    bid = _mk_board(slug="del")
    client.post(f"/boards/{bid}/delete", data={"purge": ""}, follow_redirects=False)
    with session_scope() as s:
        assert s.get(Board, bid) is None


def test_board_pagination(client):
    per = settings.per_page_boards
    for i in range(per + 5):  # one full page + overflow
        _mk_board(title=f"BRD{i:03d}", slug=f"s{i}")
    p1 = client.get("/").text
    assert "Page 1 / 2" in p1
    # newest first -> the oldest (BRD000) is pushed to page 2
    assert "BRD000" not in p1
    assert "BRD000" in client.get("/?page=2").text
    assert f"{per + 5} boards" in p1  # total, not page size


def test_pin_pagination(client):
    bid = _mk_board(slug="big")
    per = settings.per_page_pins
    for i in range(per + 3):
        _mk_pin(bid, f"big/p{i:04d}.jpg")
    page1 = client.get(f"/boards/{bid}").text
    assert "Page 1 / 2" in page1
    page2 = client.get(f"/boards/{bid}?page=2").text
    assert "Page 2 / 2" in page2


def test_pin_sort_by_size(client):
    bid = _mk_board(slug="srt")
    _mk_pin(bid, "srt/big.jpg", width=1000, height=1000)
    _mk_pin(bid, "srt/small.jpg", width=100, height=100)
    big = client.get(f"/boards/{bid}?sort=big").text
    assert big.index("big.jpg") < big.index("small.jpg")
    small = client.get(f"/boards/{bid}?sort=small").text
    assert small.index("small.jpg") < small.index("big.jpg")


def test_pin_sort_by_name_and_recency(client):
    bid = _mk_board(slug="srt2")
    a = _mk_pin(bid, "srt2/aaa.jpg")   # lower id
    z = _mk_pin(bid, "srt2/zzz.jpg")   # higher id
    name = client.get(f"/boards/{bid}?sort=name").text
    assert name.index("aaa.jpg") < name.index("zzz.jpg")
    new = client.get(f"/boards/{bid}?sort=new").text
    assert new.index("zzz.jpg") < new.index("aaa.jpg")  # newest (higher id) first
    old = client.get(f"/boards/{bid}?sort=old").text
    assert old.index("aaa.jpg") < old.index("zzz.jpg")
    assert a < z


def test_pin_view_class(client):
    bid = _mk_board(slug="vw")
    _mk_pin(bid, "vw/a.jpg")
    assert "pin-grid view-l" in client.get(f"/boards/{bid}?view=l").text
    assert "pin-grid view-s" in client.get(f"/boards/{bid}?view=s").text
    assert "pin-grid view-m" in client.get(f"/boards/{bid}").text  # default


def test_bulk_tag_add_and_remove(client):
    bid = _mk_board(slug="blk")
    p1 = _mk_pin(bid, "blk/1.jpg")
    p2 = _mk_pin(bid, "blk/2.jpg")
    p3 = _mk_pin(bid, "blk/3.jpg")
    client.post("/pins/bulk-tag",
                data={"pin_ids": [p1, p2], "name": "Sky", "action": "add"},
                follow_redirects=False)
    with session_scope() as s:
        assert "sky" in [t.name for t in s.get(Pin, p1).tags]
        assert "sky" in [t.name for t in s.get(Pin, p2).tags]
        assert s.get(Pin, p3).tags == []
    client.post("/pins/bulk-tag",
                data={"pin_ids": [p1], "name": "sky", "action": "remove"},
                follow_redirects=False)
    with session_scope() as s:
        assert s.get(Pin, p1).tags == []
        assert "sky" in [t.name for t in s.get(Pin, p2).tags]


def test_bulk_delete_pins(client):
    bid = _mk_board(slug="bd", pin_count=2)
    p1 = _mk_pin(bid, "bd/1.jpg")
    _mk_pin(bid, "bd/2.jpg")
    client.post("/pins/bulk-tag",
                data={"pin_ids": [p1], "action": "delete"},
                follow_redirects=False)
    with session_scope() as s:
        assert s.get(Pin, p1) is None
        assert s.get(Board, bid).pin_count == 1


def test_bulk_tag_empty_selection_noop(client):
    r = client.post("/pins/bulk-tag", data={"name": "x", "action": "add"},
                    follow_redirects=False)
    assert r.status_code in (303, 307)  # just redirects, no error


def test_toggle_resync(client):
    bid = _mk_board(slug="tr")
    with session_scope() as s:
        assert s.get(Board, bid).auto_resync is True  # default on
    r = client.post(f"/boards/{bid}/toggle-resync")
    assert r.status_code == 200
    with session_scope() as s:
        assert s.get(Board, bid).auto_resync is False
    client.post(f"/boards/{bid}/toggle-resync")
    with session_scope() as s:
        assert s.get(Board, bid).auto_resync is True
