"""Content hashing + duplicate grouping.

Two signals per image:
  * content_sha256 — exact byte-for-byte identity.
  * phash — a 64-bit perceptual dHash (as 16 hex chars). Survives re-encoding /
    resizing, so the *same picture* pinned to different pins (different pin ids,
    possibly different files) still collides within a small Hamming distance.

dHash is computed with Pillow only (no numpy): shrink to 9x8 grayscale and emit
one bit per horizontal adjacent-pixel comparison.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
    _HAVE_PIL = True
except ImportError:  # Pillow optional at import time; hashing degrades to sha256
    _HAVE_PIL = False

_HASH_SIZE = 16                       # -> 256-bit dHash (fine, discriminative)
PHASH_HEX_LEN = _HASH_SIZE * _HASH_SIZE // 4  # 64 hex chars

# We only want to flag the SAME image at a different resolution — not merely
# "similar-looking". A resized copy has a tiny 256-bit dHash distance (typically
# 0-8) and the SAME aspect ratio; genuinely different images sit ~128 apart. So:
# a strict Hamming cap AND a matching aspect ratio are both required.
NEAR_THRESHOLD = 10                   # out of 256 (~4%)
ASPECT_TOL = 0.04                     # aspect ratios must match within 4%


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def dhash(path: Path, hash_size: int = _HASH_SIZE) -> str | None:
    if not _HAVE_PIL:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("L").resize(
                (hash_size + 1, hash_size), Image.Resampling.LANCZOS
            )
            px = im.tobytes()  # row-major, one byte per pixel in mode "L"
    except Exception:  # noqa: BLE001 — unreadable/corrupt image -> no phash
        return None
    w = hash_size + 1
    bits = 0
    for row in range(hash_size):
        base = row * w
        for col in range(hash_size):
            bits <<= 1
            if px[base + col] < px[base + col + 1]:
                bits |= 1
    nbits = hash_size * hash_size
    # A flat / near-flat image (solid colour, blank thumbnail) yields an all-0 or
    # all-1 hash and carries no perceptual signal — treat it as "no phash" so
    # unrelated blank images don't collide as near-duplicates. Exact sha still
    # catches genuinely identical files.
    if bits == 0 or bits == (1 << nbits) - 1:
        return None
    return f"{bits:0{nbits // 4}x}"


@dataclass
class Hashes:
    sha256: str | None
    phash: str | None
    size: int | None


def compute(path: Path, *, is_image: bool) -> Hashes:
    size = None
    try:
        size = path.stat().st_size
    except OSError:
        pass
    sha = None
    try:
        sha = sha256_file(path)
    except OSError:
        pass
    ph = dhash(path) if is_image else None
    return Hashes(sha256=sha, phash=ph, size=size)


def hamming(a: str, b: str) -> int:
    """Hamming distance between two equal-length hex phashes. Different lengths
    (e.g. a stale short hash from an older version) are treated as far apart."""
    if not a or not b or len(a) != len(b):
        return 1 << 20
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 1 << 20


def _aspect(it: dict) -> float | None:
    w, h = it.get("width"), it.get("height")
    if not w or not h:
        return None
    return w / h


def _aspect_match(a: dict, b: dict) -> bool:
    ra, rb = _aspect(a), _aspect(b)
    if ra is None or rb is None:
        return True  # unknown dimensions -> don't block (sha/phash still decide)
    return abs(ra - rb) <= ASPECT_TOL * max(ra, rb)


# --------------------------------------------------------------------------- #
# grouping (union-find over exact sha + near phash)
# --------------------------------------------------------------------------- #
def group_duplicates(
    items: list[dict], *, near_threshold: int = NEAR_THRESHOLD
) -> list[list[dict]]:
    """Cluster items sharing an exact sha256 or a near phash.

    Each item is a dict with at least 'id', 'content_sha256', 'phash'.
    Returns groups (size >= 2), largest first. O(n^2) on phash pairs — fine for
    a self-hosted archive; swap in a BK-tree if it ever gets huge.
    """
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # exact sha buckets
    by_sha: dict[str, int] = {}
    for i, it in enumerate(items):
        s = it.get("content_sha256")
        if s:
            if s in by_sha:
                union(by_sha[s], i)
            else:
                by_sha[s] = i

    # near phash pairs — same image, different resolution: tiny Hamming distance
    # AND a matching aspect ratio. Both guards keep genuinely different images
    # (and coarse-hash collisions) out of the same group.
    with_ph = [(i, items[i]) for i in range(len(items)) if items[i].get("phash")]
    for x in range(len(with_ph)):
        ix, a = with_ph[x]
        for y in range(x + 1, len(with_ph)):
            iy, b = with_ph[y]
            if find(ix) == find(iy):
                continue
            if hamming(a["phash"], b["phash"]) <= near_threshold and _aspect_match(a, b):
                union(ix, iy)

    clusters: dict[int, list[dict]] = {}
    for i, it in enumerate(items):
        clusters.setdefault(find(i), []).append(it)

    groups = [g for g in clusters.values() if len(g) >= 2]
    groups.sort(key=len, reverse=True)
    return groups
