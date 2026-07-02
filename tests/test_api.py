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
