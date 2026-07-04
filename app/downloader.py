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
import re
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app import dedup

# Emits to stdout (see tasks._startup) so `docker compose logs -f worker` shows
# per-file download activity: successes, skips (with reason), and errors.
logger = logging.getLogger("pinchive.download")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Pinterest returns 403 for some `i.pinimg.com/originals/…` images (originals are
# selectively blocked) while serving the SAME picture at a sized CDN path. When
# gallery-dl exhausts its fallbacks and gives up, we recover the pin by fetching
# a large sized variant ourselves. These need no auth (a UA is enough).
_RE_403 = re.compile(r"for '(https?://i\.pinimg\.com/originals/\S+?)'")
_RE_FAILED = re.compile(r"Failed to download (\S+)")
_SIZED_FALLBACKS = ("736x", "564x")
_PINIMG_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _sized_url(orig_url: str, size: str) -> str | None:
    """Rewrite an `…/originals/AA/BB/CC/HASH.ext` URL to a sized-variant path.
    Sized variants are always served as .jpg regardless of the original type."""
    m = re.search(r"/originals/(.+)$", orig_url)
    if not m:
        return None
    tail = re.sub(r"\.\w+$", ".jpg", m.group(1))
    return f"https://i.pinimg.com/{size}/{tail}"


def _fetch_sized_fallback(dest: Path, fname: str, orig_url: str) -> bool:
    """Try to save a sized variant of a 403'd original. Returns True on success."""
    out = (dest / fname).with_suffix(".jpg")  # sized variants are jpg
    for size in _SIZED_FALLBACKS:
        url = _sized_url(orig_url, size)
        if not url:
            return False
        try:
            with httpx.stream(
                "GET", url, headers={"User-Agent": _PINIMG_UA},
                timeout=30, follow_redirects=True,
            ) as r:
                if r.status_code != 200:
                    continue
                with out.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            return True
        except (httpx.HTTPError, OSError):
            continue
    return False


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
    stall_timeout: float = 600.0,
) -> list[str]:
    # Invoke via the current interpreter so we don't depend on gallery-dl being
    # on PATH (venv on Windows, /usr/local/bin in the container).
    cmd: list[str] = [
        sys.executable, "-m", "gallery_dl",
        "--directory", str(dest),          # flatten everything into one board dir
        "--write-metadata",                # sidecar .json per file (archival)
        "--retries", "3",
        "-o", "extractor.pinterest.videos=true",
        # Pinterest videos are HLS (m3u8). yt-dlp's *native* HLS downloader
        # fetches each segment to a `.part-FragN` temp file and merges them; under
        # a bulk board run the signed segment URLs expire / rate-limit mid-way,
        # leaving a fragment missing -> "No such file or directory: …part-FragN"
        # and the whole video fails. Handing HLS to ffmpeg streams the manifest in
        # one process (no per-fragment temp files), which is far more resilient;
        # fragment_retries adds headroom for the occasional flaky segment.
        "-o", "downloader.ytdl.raw-options.hls_prefer_native=false",
        "-o", "downloader.ytdl.raw-options.fragment_retries=20",
        # If a single file stalls (no data) this long, abort just that file and
        # move on to the next pin — no overall board timeout.
        "-o", f"downloader.http.timeout={stall_timeout}",
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
    stall_timeout: float = 600.0,
    on_progress: Callable[[Progress], None] | None = None,
    progress_every: int = 5,
    log_maxlen: int = 60,
    blocked: set[str] | None = None,
) -> DownloadResult:
    dest.mkdir(parents=True, exist_ok=True)
    # A container restart mid-download can leave yt-dlp/gallery-dl temp files
    # (`*.part`, HLS `*.part-FragN`, `*.ytdl`). gallery-dl skips a pin whose final
    # file already exists but a leftover *part* can wedge a resumed download, so
    # clear them before we (re)start — the archive still skips completed pins.
    _clean_partials(dest)
    cmd = build_command(
        url, dest, cookies_file=cookies_file, archive_file=archive_file,
        sleep=sleep, stall_timeout=stall_timeout,
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
    pending_403: list[str] = []                 # originals URLs 403'd for cur file
    fallback_tasks: list[tuple[str, str]] = []  # (filename, orig_url) to recover
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("# "):
            prog.skipped += 1
            pending_403 = []
            logger.info("skip · %s (already downloaded)", line[2:].strip())
        elif line.startswith("["):
            low = line.lower()
            m403 = _RE_403.search(line)
            if m403:
                pending_403.append(m403.group(1))
            if "[error]" in low:
                mfail = _RE_FAILED.search(line)
                name = mfail.group(1) if mfail else None
                rel = f"{dest.name}/{name}" if name else None
                if rel and blocked and rel in blocked:
                    # A pin the user deleted (tombstoned) that Pinterest still
                    # offers — usually a 403 on the now-blocked original. It's meant
                    # to be gone, so skip it silently rather than counting an error
                    # or trying to recover it.
                    prog.skipped += 1
                    logger.info("skip · %s (deleted, not re-downloaded)", name)
                else:
                    log.append(line)
                    prog.errors += 1
                    logger.warning("%s", line)
                    if name and pending_403 and Path(name).suffix.lower() in IMAGE_EXTS:
                        fallback_tasks.append((name, pending_403[-1]))
                if name:
                    pending_403 = []
            else:
                log.append(line)
                logger.info("%s", line)
        else:
            prog.downloaded += 1
            prog.last_line = line
            pending_403 = []
            logger.info("ok   · %s", line)
        since_flush += 1
        if on_progress and since_flush >= progress_every:
            since_flush = 0
            on_progress(Progress(prog.downloaded, prog.skipped, prog.errors, prog.last_line))

    returncode = proc.wait()

    # Recover images Pinterest blocked at `originals/` by fetching a sized variant
    # of the same picture. Runs after the download so it never slows the main pass.
    if fallback_tasks:
        recovered = 0
        logger.info("↻ recovering %s blocked image(s) via sized fallback", len(fallback_tasks))
        for name, orig_url in fallback_tasks:
            if _fetch_sized_fallback(dest, name, orig_url):
                recovered += 1
                logger.info("ok   · %s (sized fallback)", name)
                if sleep > 0:
                    time.sleep(sleep)
        if recovered:
            prog.downloaded += recovered
            prog.errors = max(0, prog.errors - recovered)
            logger.info("↻ recovered %s/%s blocked image(s)", recovered, len(fallback_tasks))

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


def _clean_partials(dest: Path) -> None:
    """Delete leftover download temp files from an interrupted run."""
    if not dest.exists():
        return
    for pat in ("*.part", "*.part-Frag*", "*.ytdl"):
        for f in dest.rglob(pat):
            try:
                f.unlink()
            except OSError:
                pass


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
