"""JSON API tests via TestClient (no redis; queue degraded)."""

import re
import shutil

import pytest

from app.core.config import get_settings
from app.models import Board, BoardStatus, Pin, PinTagLink, Tag
from app.services import dedup
from app.services.dedup_service import recompute

settings = get_settings()


def _mk_board(title="Board", slug="slug", url="https://www.pinterest.com/u/b/",
              status=BoardStatus.done, pin_count=0):
    return Board.create(
        title=title, slug=slug, url=url, status=status.value, pin_count=pin_count,
    ).id


def _mk_pin(board_id, rel_path, sha=None, ph=None, media="image", **kw):
    return Pin.create(
        board=board_id, rel_path=rel_path, filename=rel_path.split("/")[-1],
        media_type=media, content_sha256=sha, phash=ph, **kw,
    ).id


class FakePool:
    """Stands in for the arq pool so queue behaviour is observable."""

    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args):
        self.jobs.append((name, args))


# --------------------------------------------------------------------------- #
# shell + health
# --------------------------------------------------------------------------- #
def test_spa_shell_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<div id="root">' in r.text


def test_client_side_route_falls_back_to_shell(client):
    """A deep link must reach the SPA, not 404 on the server."""
    r = client.get("/boards/123")
    assert r.status_code == 200
    assert '<div id="root">' in r.text


def test_shell_is_revalidated_but_assets_are_immutable(client):
    """A cached index.html would pin the browser to the previous build.

    index.html names the hashed bundles, so it must revalidate every load, while
    the fingerprinted assets under /assets can be cached indefinitely.
    """
    shell = client.get("/")
    assert shell.headers["cache-control"] == "no-cache"

    # Find whatever bundle this build produced rather than hard-coding a hash.
    match = re.search(r'src="(/assets/[^"]+\.js)"', shell.text)
    if match is None:
        pytest.skip("front end not built in this environment")
    asset = client.get(match.group(1))
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]


def test_healthz_queue_disabled(client):
    body = client.get("/api/healthz").json()
    assert body["status"] == "ok" and body["queue"] is False


def test_stats_and_sidebar_counts(client):
    b = _mk_board(slug="s1")
    _mk_pin(b, "s1/a.jpg")
    _mk_pin(b, "s1/b.jpg")
    stats = client.get("/api/stats").json()
    assert stats == {"boards": 1, "pins": 2, "sessions": 0}
    side = client.get("/api/sidebar").json()
    assert side["boards"] == 1 and side["removable"] == 0


# --------------------------------------------------------------------------- #
# boards
# --------------------------------------------------------------------------- #
def test_add_board_without_queue_becomes_pending(client):
    r = client.post("/api/boards", json={"url": "https://www.pinterest.com/u/b/"})
    assert r.status_code == 201
    # Queue is down -> parked as pending with a note, not stuck on "queued".
    assert r.json()["status"] == BoardStatus.pending.value
    assert Board.select().count() == 1


def test_add_board_rejects_non_pinterest(client):
    r = client.post("/api/boards", json={"url": "https://example.com/x"})
    assert r.status_code == 400


def test_board_detail_and_404(client):
    bid = _mk_board(title="Alpha", slug="alpha")
    body = client.get(f"/api/boards/{bid}").json()
    assert body["title"] == "Alpha" and body["slug"] == "alpha"
    assert client.get("/api/boards/99999").status_code == 404


def test_processed_count_is_work_done_not_a_percentage(client):
    """Regression: a percentage over pin_count read as 100% mid-download.

    gallery-dl never announces a board total, and pin_count is what is already
    stored — so the card reports processed work instead of a fake completion bar.
    """
    bid = _mk_board(slug="prog", status=BoardStatus.downloading, pin_count=5)
    Board.update(downloaded_count=11, skipped_count=3, error_count=2).where(
        Board.id == bid
    ).execute()
    body = client.get(f"/api/boards/{bid}").json()
    assert "progress_pct" not in body
    # Work done can legitimately exceed what is stored (skips, re-downloads).
    assert body["processed_count"] == 14
    assert body["pin_count"] == 5
    assert body["error_count"] == 2
    assert body["is_active"] is True


def test_board_search_and_status_filter(client):
    _mk_board(title="Interior Ideas", slug="interior", status=BoardStatus.done)
    _mk_board(title="Workshop Refs", slug="workshop", status=BoardStatus.downloading)

    def titles(query: str) -> list[str]:
        return [b["title"] for b in client.get(f"/api/boards{query}").json()["items"]]

    assert titles("?q=interior") == ["Interior Ideas"]
    # Two user-facing states: working (active) vs done (finished, incl. error).
    assert titles("?status=working") == ["Workshop Refs"]
    assert titles("?status=done") == ["Interior Ideas"]


def test_board_pagination_and_total(client):
    per = settings.per_page_boards
    for i in range(per + 5):
        _mk_board(title=f"BRD{i:03d}", slug=f"s{i}")
    page1 = client.get("/api/boards").json()
    assert page1["meta"] == {"page": 1, "pages": 2, "total": per + 5}
    # Newest first -> the oldest is pushed to page 2.
    assert "BRD000" not in [b["title"] for b in page1["items"]]
    page2 = client.get("/api/boards?page=2").json()
    assert "BRD000" in [b["title"] for b in page2["items"]]


def test_settings_change_pagination_live(client):
    per = settings.per_page_boards
    for i in range(per + 5):
        _mk_board(title=f"P{i:03d}", slug=f"ps{i}")
    assert client.get("/api/boards").json()["meta"]["pages"] == 2
    client.put("/api/settings", json={"per_page_boards": str(per + 50)})
    assert client.get("/api/boards").json()["meta"]["pages"] == 1


def test_toggle_auto_resync(client):
    bid = _mk_board(slug="tr")
    assert Board.get_by_id(bid).auto_resync is True  # default on
    assert client.post(f"/api/boards/{bid}/auto-resync").json()["auto_resync"] is False
    assert client.post(f"/api/boards/{bid}/auto-resync").json()["auto_resync"] is True


def test_incremental_vs_forced_resync_enqueue(client):
    normal_id = _mk_board(slug="inc")
    Board.update(force_full_sync=True).where(Board.id == normal_id).execute()
    full_id = _mk_board(slug="full")
    pool = FakePool()
    original = client.app.state.arq
    client.app.state.arq = pool
    try:
        assert client.post(f"/api/boards/{normal_id}/resync").status_code == 200
        assert client.post(f"/api/boards/{full_id}/resync?full=true").status_code == 200
    finally:
        client.app.state.arq = original

    assert pool.jobs == [
        ("download_board", (normal_id,)),
        ("download_board", (full_id, True)),
    ]
    # The forced flag is persisted so a restart resumes the full scan.
    assert Board.get_by_id(normal_id).force_full_sync is False
    assert Board.get_by_id(full_id).force_full_sync is True


def test_resync_skips_board_already_in_flight(client):
    bid = _mk_board(slug="busy", status=BoardStatus.downloading)
    pool = FakePool()
    original = client.app.state.arq
    client.app.state.arq = pool
    try:
        assert client.post(f"/api/boards/{bid}/resync").status_code == 200
    finally:
        client.app.state.arq = original
    assert pool.jobs == []  # never double-queue


def test_delete_board_removes_row_and_files(client):
    bid = _mk_board(slug="del")
    folder = settings.boards_dir / f"del-{bid}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pin.jpg").write_bytes(b"x")
    (folder / "pin.jpg.json").write_text("{}")
    _mk_pin(bid, f"del-{bid}/pin.jpg")
    assert client.delete(f"/api/boards/{bid}").status_code == 204
    assert Board.get_or_none(Board.id == bid) is None
    assert Pin.select().count() == 0  # cascade
    assert not folder.exists()        # media folder wiped from disk too


# --------------------------------------------------------------------------- #
# pins
# --------------------------------------------------------------------------- #
def test_pin_listing_and_type_filter(client):
    bid = _mk_board(slug="mix")
    _mk_pin(bid, "mix/a.jpg", media="image")
    _mk_pin(bid, "mix/b.mp4", media="video")

    def names(query: str) -> list[str]:
        url = f"/api/boards/{bid}/pins{query}"
        return [p["filename"] for p in client.get(url).json()["items"]]

    assert names("?type=image") == ["a.jpg"]
    assert names("?type=video") == ["b.mp4"]
    assert sorted(names("")) == ["a.jpg", "b.mp4"]


def test_pin_url_points_at_media_mount(client):
    bid = _mk_board(slug="u")
    _mk_pin(bid, "u/a.jpg")
    pin = client.get(f"/api/boards/{bid}/pins").json()["items"][0]
    assert pin["url"] == "/media/u/a.jpg"


def test_pin_pagination(client):
    bid = _mk_board(slug="big")
    for i in range(settings.per_page_pins + 3):
        _mk_pin(bid, f"big/p{i:04d}.jpg")
    meta = client.get(f"/api/boards/{bid}/pins").json()["meta"]
    assert meta["pages"] == 2 and meta["total"] == settings.per_page_pins + 3


def test_pin_sort_by_size(client):
    bid = _mk_board(slug="srt")
    _mk_pin(bid, "srt/big.jpg", width=1000, height=1000)
    _mk_pin(bid, "srt/small.jpg", width=100, height=100)

    def names(sort: str) -> list[str]:
        url = f"/api/boards/{bid}/pins?sort={sort}"
        return [p["filename"] for p in client.get(url).json()["items"]]

    assert names("big") == ["big.jpg", "small.jpg"]
    assert names("small") == ["small.jpg", "big.jpg"]


def test_pin_sort_by_name_and_recency(client):
    bid = _mk_board(slug="srt2")
    _mk_pin(bid, "srt2/aaa.jpg")
    _mk_pin(bid, "srt2/zzz.jpg")

    def names(sort: str) -> list[str]:
        url = f"/api/boards/{bid}/pins?sort={sort}"
        return [p["filename"] for p in client.get(url).json()["items"]]

    assert names("name") == ["aaa.jpg", "zzz.jpg"]
    # Recency = Pinterest pin id (embedded in the filename); higher = newer.
    assert names("new") == ["zzz.jpg", "aaa.jpg"]
    assert names("old") == ["aaa.jpg", "zzz.jpg"]


def test_pin_dupes_only_filter(client):
    bid = _mk_board(slug="dp")
    other_bid = _mk_board(slug="dp-other")
    _mk_pin(bid, "dp/plain.jpg")
    _mk_pin(bid, "dp/dupe.jpg", dup_group=7)
    _mk_pin(other_bid, "dp-other/dupe.jpg", dup_group=7)
    url = f"/api/boards/{bid}/pins?dupes=true"
    assert [p["filename"] for p in client.get(url).json()["items"]] == ["dupe.jpg"]


def test_pin_tagging(client):
    bid = _mk_board(slug="pt")
    pid = _mk_pin(bid, "pt/a.jpg")
    body = client.post(f"/api/pins/{pid}/tags", json={"name": "Wood"}).json()
    assert body["tags"] == ["wood"]  # normalised to lowercase
    tag_id = Tag.get(Tag.name == "wood").id
    body = client.delete(f"/api/pins/{pid}/tags/{tag_id}").json()
    assert body["tags"] == []


def test_board_tagging_and_filter(client):
    bid = _mk_board(title="Alpha", slug="alpha")
    body = client.post(f"/api/boards/{bid}/tags", json={"name": "DIY"}).json()
    assert body["tags"] == ["diy"]
    listed = client.get("/api/boards?tag=diy").json()["items"]
    assert [b["title"] for b in listed] == ["Alpha"]
    assert client.get("/api/boards?tag=nope").json()["items"] == []
    tag_id = Tag.get(Tag.name == "diy").id
    assert client.delete(f"/api/boards/{bid}/tags/{tag_id}").json()["tags"] == []


def test_bulk_tag_add_and_remove(client):
    bid = _mk_board(slug="blk")
    p1 = _mk_pin(bid, "blk/1.jpg")
    p2 = _mk_pin(bid, "blk/2.jpg")
    p3 = _mk_pin(bid, "blk/3.jpg")
    client.post("/api/pins/bulk", json={"pin_ids": [p1, p2], "name": "Sky", "action": "add"})
    tagged = {link.pin_id for link in PinTagLink.select()}
    assert tagged == {p1, p2} and p3 not in tagged
    client.post("/api/pins/bulk", json={"pin_ids": [p1], "name": "sky", "action": "remove"})
    assert {link.pin_id for link in PinTagLink.select()} == {p2}


def test_bulk_tag_is_idempotent(client):
    bid = _mk_board(slug="idem")
    pid = _mk_pin(bid, "idem/1.jpg")
    client.post("/api/pins/bulk", json={"pin_ids": [pid], "name": "x", "action": "add"})
    # Re-tagging an already-tagged pin must not raise a constraint error.
    r = client.post("/api/pins/bulk", json={"pin_ids": [pid], "name": "x", "action": "add"})
    assert r.status_code == 200
    assert PinTagLink.select().count() == 1


def test_bulk_delete_pins_reconciles_counts(client):
    bid = _mk_board(slug="bd", pin_count=2)
    p1 = _mk_pin(bid, "bd/1.jpg")
    _mk_pin(bid, "bd/2.jpg")
    client.post("/api/pins/bulk", json={"pin_ids": [p1], "action": "delete"})
    assert Pin.get_or_none(Pin.id == p1) is None
    assert Board.get_by_id(bid).pin_count == 1


def test_bulk_empty_selection_noop(client):
    r = client.post("/api/pins/bulk", json={"pin_ids": [], "action": "add"})
    assert r.status_code == 200 and r.json()["affected"] == 0


def test_duplicates_filter_ignores_orphaned_stored_group(client):
    """Legacy stale dup_group metadata must not make one pin a duplicate."""
    bid = _mk_board(slug="orphan", pin_count=1)
    _mk_pin(bid, "orphan/only.jpg", dup_group=77)

    body = client.get(f"/api/boards/{bid}/pins?dupes=true").json()

    assert body["items"] == []
    assert body["meta"]["total"] == 0


# --------------------------------------------------------------------------- #
# duplicates
# --------------------------------------------------------------------------- #
def test_duplicates_detect_and_resolve(client, make_image):
    # Two boards, each with its own physical copy of the same image.
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

    # Groups are precomputed and stored, not computed per request.
    recompute()

    groups = client.get("/api/duplicates").json()["items"]
    assert len(groups) == 1 and len(groups[0]["pins"]) == 2
    assert client.get("/api/duplicates/status").json()["removable"] == 1

    # Delete the second copy.
    assert (settings.boards_dir / "d2" / "pin.jpg").exists()
    r = client.post("/api/duplicates/resolve", json={"pin_ids": [p2]})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    # File removed, kept copy intact.
    assert not (settings.boards_dir / "d2" / "pin.jpg").exists()
    assert (settings.boards_dir / "d1" / "pin.jpg").exists()
    # Counters reconciled and the group is gone.
    assert Pin.get_or_none(Pin.id == p2) is None
    assert Board.get_by_id(b2).pin_count == 0
    assert client.get("/api/duplicates").json()["items"] == []
    # The surviving keeper must disappear from a board's "duplicates only"
    # filter as soon as the group collapses to one pin.
    survivor = Pin.get(Pin.board == b1)
    assert survivor.dup_group is None
    filtered = client.get(f"/api/boards/{b1}/pins?dupes=true").json()
    assert filtered["items"] == []


def test_resolve_all_keeps_highest_resolution(client, make_image):
    img = make_image(size=(600, 400))
    sha = dedup.sha256_file(img)
    ph = dedup.dhash(img)
    bdir = settings.boards_dir / "ra"
    bdir.mkdir(parents=True, exist_ok=True)
    for name in ("big.jpg", "small.jpg"):
        shutil.copy(img, bdir / name)

    bid = _mk_board(slug="ra", pin_count=2)
    keep = _mk_pin(bid, "ra/big.jpg", sha=sha, ph=ph, width=600, height=400)
    drop = _mk_pin(bid, "ra/small.jpg", sha=sha, ph=ph, width=300, height=200)
    recompute()

    r = client.post("/api/duplicates/resolve", json={"all": True})
    assert r.json()["deleted"] == 1
    assert Pin.get_or_none(Pin.id == keep) is not None   # highest-res survives
    assert Pin.get_or_none(Pin.id == drop) is None


def test_deleted_duplicate_is_tombstoned(client, make_image):
    """A resolved duplicate must never come back on the next board sync."""
    from app.models import DeletedPin

    img = make_image(size=(500, 400))
    sha = dedup.sha256_file(img)
    ph = dedup.dhash(img)
    bdir = settings.boards_dir / "tomb"
    bdir.mkdir(parents=True, exist_ok=True)
    for name in ("a.jpg", "b.jpg"):
        shutil.copy(img, bdir / name)
    bid = _mk_board(slug="tomb", pin_count=2)
    _mk_pin(bid, "tomb/a.jpg", sha=sha, ph=ph, width=500, height=400)
    drop = _mk_pin(bid, "tomb/b.jpg", sha=sha, ph=ph, width=250, height=200)
    recompute()

    client.post("/api/duplicates/resolve", json={"pin_ids": [drop]})
    assert DeletedPin.get_or_none(DeletedPin.rel_path == "tomb/b.jpg") is not None


def test_rescan_without_queue_reports_not_queued(client):
    body = client.post("/api/duplicates/rescan").json()
    assert body["queued"] is False


def test_rescan_enqueues_job(client):
    pool = FakePool()
    original = client.app.state.arq
    client.app.state.arq = pool
    try:
        assert client.post("/api/duplicates/rescan").json()["queued"] is True
    finally:
        client.app.state.arq = original
    assert pool.jobs == [("recompute_duplicates", ())]


# --------------------------------------------------------------------------- #
# settings + credentials
# --------------------------------------------------------------------------- #
def test_settings_read_and_write(client):
    body = client.get("/api/settings").json()
    assert "per_page_pins" in body["values"]
    assert body["max_concurrency"] == settings.max_concurrency
    assert body["queue_up"] is False
    saved = client.put("/api/settings", json={"per_page_pins": "42"}).json()
    assert saved["values"]["per_page_pins"] == 42


def test_settings_rejects_bad_cron_and_keeps_previous(client):
    client.put("/api/settings", json={"resync_cron": "15 2 * * *"})
    client.put("/api/settings", json={"resync_cron": "not a cron"})
    assert client.get("/api/settings").json()["values"]["resync_cron"] == "15 2 * * *"


def test_sync_all_without_queue(client):
    assert client.post("/api/settings/sync-all").json()["queued"] is False


def test_credential_rejects_unparseable_cookies(client):
    from app.models import Credential

    r = client.post("/api/credentials", json={"name": "x", "cookies": "garbage"})
    assert r.status_code == 400
    # A failed parse must not leave a dangling credential row behind.
    assert Credential.select().count() == 0


def test_credential_missing_404s(client):
    assert client.post("/api/credentials/999/validate").status_code == 404
    assert client.delete("/api/credentials/999").status_code == 404


def test_tags_endpoint(client):
    bid = _mk_board(slug="tg")
    client.post(f"/api/boards/{bid}/tags", json={"name": "alpha"})
    assert [t["name"] for t in client.get("/api/tags").json()] == ["alpha"]
