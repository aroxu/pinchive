"""Duplicate-detection logic."""

import shutil

from app import dedup


def _row(i, sha=None, ph=None):
    return {"id": i, "content_sha256": sha, "phash": ph}


def test_sha256_identical(make_image):
    a = make_image()
    b = a.parent / "copy.jpg"
    shutil.copy(a, b)
    assert dedup.sha256_file(a) == dedup.sha256_file(b)


def test_sha256_differs(make_image):
    a = make_image(color=(10, 10, 10))
    b = make_image(color=(200, 200, 200))
    assert dedup.sha256_file(a) != dedup.sha256_file(b)


def test_dhash_identical(make_image):
    a = make_image()
    b = a.parent / "copy.jpg"
    shutil.copy(a, b)
    assert dedup.dhash(a) == dedup.dhash(b)


def test_dhash_near_after_resize_and_reencode(make_image):
    a = make_image(size=(800, 600), quality=95)
    near = make_image(size=(400, 300), base=a, quality=60)
    d = dedup.hamming(dedup.dhash(a), dedup.dhash(near))
    assert d <= dedup.NEAR_THRESHOLD, f"resized copy should be near, got {d}"


def test_dhash_far_for_unrelated(tmp_path):
    from PIL import Image

    grad = tmp_path / "grad.jpg"
    im = Image.new("RGB", (400, 300))
    for x in range(400):
        for y in range(300):
            im.putpixel((x, y), (x * 255 // 399, y * 255 // 299, 100))
    im.save(grad, "JPEG", quality=90)

    checker = tmp_path / "checker.jpg"
    im2 = Image.new("RGB", (400, 300))
    for x in range(400):
        for y in range(300):
            v = 255 if ((x // 40) + (y // 40)) % 2 == 0 else 0
            im2.putpixel((x, y), (v, v, v))
    im2.save(checker, "JPEG", quality=90)

    d = dedup.hamming(dedup.dhash(grad), dedup.dhash(checker))
    assert d > dedup.NEAR_THRESHOLD


def test_dhash_flat_image_has_no_phash(tmp_path):
    from PIL import Image

    # solid colour -> degenerate hash -> None (won't false-match other blanks)
    solid = tmp_path / "solid.jpg"
    Image.new("RGB", (200, 200), (30, 90, 160)).save(solid, "JPEG", quality=95)
    assert dedup.dhash(solid) is None


def test_dhash_corrupt_returns_none(tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image")
    assert dedup.dhash(bad) is None


def test_hamming_length_and_bad_input():
    assert dedup.hamming("00", "01") == 1
    assert dedup.hamming("ff", "00") == 8
    # junk / mismatched lengths -> "far apart" sentinel, never a small distance
    assert dedup.hamming(None, "00") > 64
    assert dedup.hamming("zz", "00") > 64
    assert dedup.hamming("0000", "00") > 64   # different lengths


def test_group_exact_only():
    rows = [_row(1, sha="aaa"), _row(2, sha="aaa"), _row(3, sha="bbb")]
    groups = dedup.group_duplicates(rows)
    assert len(groups) == 1
    assert {r["id"] for r in groups[0]} == {1, 2}


def test_group_near_only():
    rows = [_row(1, ph="0000000000000000"), _row(2, ph="0000000000000001")]
    groups = dedup.group_duplicates(rows, near_threshold=6)
    assert len(groups) == 1 and len(groups[0]) == 2


def test_group_transitive_chain():
    # A~B (dist 1), B~C (dist 1), A..C (dist 2) — all one cluster via union-find.
    rows = [
        _row(1, ph="0000000000000000"),
        _row(2, ph="0000000000000001"),
        _row(3, ph="0000000000000003"),
    ]
    groups = dedup.group_duplicates(rows, near_threshold=1)
    assert len(groups) == 1
    assert {r["id"] for r in groups[0]} == {1, 2, 3}


def test_group_singletons_excluded():
    rows = [_row(1, sha="x"), _row(2, sha="y"), _row(3, sha="z")]
    assert dedup.group_duplicates(rows) == []


def test_group_empty():
    assert dedup.group_duplicates([]) == []


def test_group_by_sha_when_no_phash():
    # videos have sha but no phash — still dedupe by exact bytes
    rows = [_row(1, sha="v", ph=None), _row(2, sha="v", ph=None)]
    groups = dedup.group_duplicates(rows)
    assert len(groups) == 1 and len(groups[0]) == 2


def test_group_none_hashes_never_match():
    rows = [_row(1), _row(2), _row(3)]
    assert dedup.group_duplicates(rows) == []


def test_group_sorted_largest_first():
    rows = [
        _row(1, sha="a"), _row(2, sha="a"),          # group of 2
        _row(3, sha="b"), _row(4, sha="b"), _row(5, sha="b"),  # group of 3
    ]
    groups = dedup.group_duplicates(rows)
    assert [len(g) for g in groups] == [3, 2]
