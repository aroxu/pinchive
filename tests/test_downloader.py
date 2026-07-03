"""gallery-dl command build + on-disk scan."""

import json
import sys
from pathlib import Path

from app import downloader


def test_build_command_minimal():
    cmd = downloader.build_command(
        "https://www.pinterest.com/u/b/", Path("/dest"),
        cookies_file=None, archive_file=None, sleep=0,
    )
    assert cmd[:3] == [sys.executable, "-m", "gallery_dl"]
    assert "--cookies" not in cmd
    assert "--download-archive" not in cmd
    assert "sleep-request" not in " ".join(cmd)
    assert cmd[-1].endswith("/b/")


def test_build_command_full():
    cmd = downloader.build_command(
        "https://p/", Path("/dest"),
        cookies_file=Path("/c.txt"), archive_file=Path("/a.db"), sleep=0.8,
        stall_timeout=600,
    )
    j = " ".join(cmd)
    assert "--cookies" in cmd and "--download-archive" in cmd
    assert "extractor.pinterest.sleep-request=0.8" in j
    assert "extractor.pinterest.videos=true" in j
    assert "downloader.http.timeout=600" in j  # per-pin stall guard


def test_scan_media_image_with_sidecar_and_hashes(tmp_path, make_image):
    import shutil

    dest = tmp_path / "board"
    dest.mkdir()
    shutil.copy(make_image(size=(300, 200)), dest / "123.jpg")
    (dest / "123.jpg.json").write_text(
        json.dumps({"id": 123, "width": 300, "height": 200,
                    "title": "hello", "description": "world",
                    "url": "https://src/x"}),
        encoding="utf-8",
    )
    items = downloader.scan_media(dest)
    assert len(items) == 1
    m = items[0]
    assert m.media_type == "image"
    assert m.pinterest_id == "123"
    assert m.width == 300 and m.height == 200
    assert m.title == "hello" and m.description == "world"
    assert m.source_url == "https://src/x"
    assert m.content_sha256 and len(m.content_sha256) == 64
    assert m.phash and len(m.phash) == 16
    assert m.file_size and m.file_size > 0


def test_scan_media_video_has_sha_no_phash(tmp_path):
    dest = tmp_path / "board"
    dest.mkdir()
    (dest / "clip.mp4").write_bytes(b"\x00\x01\x02video-bytes")
    items = downloader.scan_media(dest)
    assert len(items) == 1
    assert items[0].media_type == "video"
    assert items[0].content_sha256 is not None
    assert items[0].phash is None


def test_scan_media_ignores_non_media(tmp_path):
    dest = tmp_path / "board"
    dest.mkdir()
    (dest / "notes.txt").write_text("x")
    (dest / "meta.json").write_text("{}")
    assert downloader.scan_media(dest) == []


def test_scan_media_missing_dir(tmp_path):
    assert downloader.scan_media(tmp_path / "nope") == []


def test_extract_board_name_from_sidecar(tmp_path):
    dest = tmp_path / "b"
    dest.mkdir()
    (dest / "1.jpg.json").write_text(
        json.dumps({"id": 1, "board": {"name": "내 보드", "id": 9}}),
        encoding="utf-8",
    )
    assert downloader.extract_board_name(dest) == "내 보드"


def test_extract_board_name_absent(tmp_path):
    dest = tmp_path / "b"
    dest.mkdir()
    (dest / "1.jpg.json").write_text(json.dumps({"id": 1}), encoding="utf-8")
    assert downloader.extract_board_name(dest) is None


def test_scan_media_light_skips_hashes_and_sidecar(tmp_path, make_image):
    import shutil
    dest = tmp_path / "b"
    dest.mkdir()
    shutil.copy(make_image(), dest / "x.jpg")
    (dest / "x.jpg.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
    items = downloader.scan_media(dest, with_hashes=False, with_sidecar=False)
    assert len(items) == 1
    m = items[0]
    assert m.media_type == "image" and m.filename == "x.jpg"
    assert m.content_sha256 is None and m.phash is None
    assert m.title is None  # sidecar skipped
