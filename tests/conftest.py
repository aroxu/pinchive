"""Test fixtures. A throwaway data dir + sqlite is wired up *before* app import
so the real engine binds to it; redis points at a closed port so the app boots
degraded (queue disabled) without a broker.
"""

import os
import tempfile
from pathlib import Path

# Must be set before importing anything under app.*
_TMP = Path(tempfile.mkdtemp(prefix="pinchive-test-"))
os.environ["PINCHIVE_DATA_DIR"] = str(_TMP)
os.environ["PINCHIVE_REDIS_URL"] = "redis://127.0.0.1:6399"  # closed -> fast refuse

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import main  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402

settings = get_settings()


@pytest.fixture(autouse=True)
def fresh_db():
    """Drop + recreate all tables around every test for isolation."""
    SQLModel.metadata.drop_all(engine)
    init_db()
    # clean any files a previous test wrote
    if settings.boards_dir.exists():
        for p in settings.boards_dir.glob("**/*"):
            if p.is_file():
                p.unlink()
    yield
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def make_image(tmp_path):
    """Factory: write a JPEG and return its path. Optionally derived from a base
    image (resized/re-encoded) to simulate a near-duplicate."""
    counter = {"n": 0}

    def _make(color=(120, 60, 200), size=(400, 300), base: Path | None = None,
              quality=90, seed=0) -> Path:
        counter["n"] += 1
        out = tmp_path / f"img_{counter['n']}.jpg"
        if base is not None:
            im = Image.open(base).resize(size)
        else:
            # A smooth low-frequency gradient: non-degenerate dHash that also
            # survives resizing (mimics a real photo, not a striped test pattern).
            w, h = size
            im = Image.new("RGB", size)
            r0, g0, b0 = color
            for x in range(w):
                gx = x * 255 // max(w - 1, 1)
                for y in range(h):
                    gy = y * 255 // max(h - 1, 1)
                    im.putpixel((x, y),
                                ((gx + r0 + seed) % 256, (gy + g0) % 256, b0 % 256))
        im.save(out, "JPEG", quality=quality)
        return out

    return _make
