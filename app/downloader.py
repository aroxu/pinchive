"""gallery-dl wrapper: the download engine.

We shell out to gallery-dl rather than importing it: process isolation keeps a
crashing extractor from taking down the worker, and the CLI's stdout gives us a
line-per-file stream we can turn into live progress.

Output contract (default verbosity, stderr merged into stdout):
  * `<path>`        a file that was just downloaded
  * `# <path>`      a file skipped (already present / in the archive)
  * `[scope][lvl] …`  a log line (warnings, errors) — collected, not counted
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import dedup

# Emits to stdout (see tasks._startup) so `docker compose logs -f worker` shows
# per-file download activity: successes, skips (with reason), and errors.
logger = logging.getLogger("pinchive.download")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS


@dataclass
class Progress:
    downloaded: int = 0
    skipped: int = 0
    errors: int = 0
    last_line: str = ""


@dataclass
class DownloadResult:
    downloaded: int = 0
    skipped: int = 0
    errors: int = 0
    returncode: int = 0
    dest: Path | None = None
    log_tail: str = ""
    media: list["MediaItem"] = field(default_factory=list)
    board_name: str | None = None  # real Pinterest board name from metadata

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.errors == 0

    @property
    def partial(self) -> bool:
        return self.downloaded > 0 and (self.errors > 0 or self.returncode != 0)


@dataclass
class MediaItem:
    filename: str
    rel_path: str
    media_type: str
    pinterest_id: str | None = None
    width: int | None = None
    height: int | None = None
    source_url: str | None = None
    title: str | None = None
    description: str | None = None
    content_sha256: str | None = None
    phash: str | None = None
    file_size: int | None = None


def build_command(
    url: str,
    dest: Path,
    *,
    cookies_file: Path | None,
    archive_file: Path | None,
    sleep: float,
) -> list[str]:
    # Invoke via the current interpreter so we don't depend on gallery-dl being
    # on PATH (venv on Windows, /usr/local/bin in the container).
    cmd: list[str] = [
        sys.executable, "-m", "gallery_dl",
        "--directory", str(dest),          # flatten everything into one board dir
        "--write-metadata",                # sidecar .json per file (archival)
        "--retries", "3",
        "-o", "extractor.pinterest.videos=true",
    ]
    if sleep > 0:
        cmd += ["-o", f"extractor.pinterest.sleep-request={sleep}"]
    if cookies_file is not None:
        cmd += ["--cookies", str(cookies_file)]
    if archive_file is not None:
        # Skip pins already recorded → cheap incremental re-download / refresh.
        cmd += ["--download-archive", str(archive_file)]
    cmd.append(url)
    return cmd


def run_download(
    url: str,
    dest: Path,
    *,
    cookies_file: Path | None = None,
    archive_file: Path | None = None,
    sleep: float = 0.8,
    on_progress: Callable[[Progress], None] | None = None,
    progress_every: int = 5,
    log_maxlen: int = 60,
) -> DownloadResult:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = build_command(
        url, dest, cookies_file=cookies_file, archive_file=archive_file, sleep=sleep
    )

    prog = Progress()
    log: deque[str] = deque(maxlen=log_maxlen)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )

    assert proc.stdout is not None
    since_flush = 0
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("# "):
            prog.skipped += 1
            logger.info("skip · %s (already downloaded)", line[2:].strip())
        elif line.startswith("["):
            log.append(line)
            low = line.lower()
            if "[error]" in low:
                prog.errors += 1
                logger.warning("%s", line)
            else:
                logger.info("%s", line)
        else:
            prog.downloaded += 1
            prog.last_line = line
            logger.info("ok   · %s", line)
        since_flush += 1
        if on_progress and since_flush >= progress_every:
            since_flush = 0
            on_progress(Progress(prog.downloaded, prog.skipped, prog.errors, prog.last_line))

    returncode = proc.wait()
    if on_progress:
        on_progress(Progress(prog.downloaded, prog.skipped, prog.errors, prog.last_line))

    media = scan_media(dest)

    return DownloadResult(
        downloaded=prog.downloaded,
        skipped=prog.skipped,
        errors=prog.errors,
        returncode=returncode,
        dest=dest,
        log_tail="\n".join(log),
        media=media,
        board_name=extract_board_name(dest),
    )


def extract_board_name(dest: Path) -> str | None:
    """Read the real Pinterest board name from the first sidecar that has it."""
    if not dest.exists():
        return None
    for sidecar in sorted(dest.rglob("*.json")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        board = meta.get("board")
        if isinstance(board, dict):
            name = board.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def scan_media(
    dest: Path, *, with_hashes: bool = True, with_sidecar: bool = True
) -> list[MediaItem]:
    """Walk a board dir and build MediaItem rows.

    The full scan (default) enriches from sidecar JSON and computes hashes. A
    light scan (both flags False) just lists media files — used mid-download to
    surface partial results cheaply, before sidecars/hashes are worth computing.
    """
    items: list[MediaItem] = []
    if not dest.exists():
        return items
    for path in sorted(dest.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in MEDIA_EXTS:
            continue
        media_type = "video" if ext in VIDEO_EXTS else "image"
        rel = path.relative_to(dest).as_posix()
        item = MediaItem(filename=path.name, rel_path=rel, media_type=media_type)
        if with_sidecar:
            _enrich_from_sidecar(path, item)
        if with_hashes:
            h = dedup.compute(path, is_image=(media_type == "image"))
            item.content_sha256 = h.sha256
            item.phash = h.phash
            item.file_size = h.size
        items.append(item)
    return items


def _enrich_from_sidecar(media_path: Path, item: MediaItem) -> None:
    sidecar = media_path.with_suffix(media_path.suffix + ".json")
    if not sidecar.exists():
        return
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    pid = meta.get("id") or meta.get("pin_id")
    item.pinterest_id = str(pid) if pid is not None else None
    item.width = _as_int(meta.get("width"))
    item.height = _as_int(meta.get("height"))
    item.source_url = (
        meta.get("url")
        or meta.get("link")
        or (meta.get("images", {}) or {}).get("orig", {}).get("url")
    )
    item.title = _clean_text(meta.get("title") or meta.get("grid_title"))
    item.description = _clean_text(meta.get("description"))


def _clean_text(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v[:1000] or None


def _as_int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
