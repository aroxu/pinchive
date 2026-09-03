"""Media storage abstraction.

Every read/write of archived media goes through a ``StorageBackend`` so the
object-storage work described in ``docs/storage-backends.md`` can be added
without touching services or the API. Only :class:`LocalStorage` is implemented
today; ``Pin.storage_backend`` records which backend owns each file.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings


class Storage(ABC):
    """Content-addressed-ish media store keyed by ``rel_path``.

    ``rel_path`` is always POSIX-style and relative to the archive root
    (``<board-folder>/<file>``), which is what the DB stores and what URLs are
    built from — so the same key works for a local disk and an S3 bucket.
    """

    name: str

    @abstractmethod
    def exists(self, rel_path: str) -> bool: ...

    @abstractmethod
    def size(self, rel_path: str) -> int | None: ...

    @abstractmethod
    def delete(self, rel_path: str) -> None:
        """Remove a media file and its metadata sidecar. Never raises."""

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Remove everything under a board folder. Never raises."""

    @abstractmethod
    def local_path(self, rel_path: str) -> Path | None:
        """A real filesystem path, when the backend has one (local only).

        Hashing and ffmpeg need a real file; a remote backend returns None and
        callers fall back to :meth:`open` / a staged temp copy.
        """

    @abstractmethod
    def url_for(self, rel_path: str) -> str:
        """Browser-facing URL for a stored object."""


class LocalStorage(Storage):
    """Files on the data volume, served by the app under ``/media``."""

    name = "local"

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        # Resolved lazily so tests can repoint PINCHIVE_DATA_DIR after import.
        return self._root if self._root is not None else get_settings().boards_dir

    def _resolve(self, rel_path: str) -> Path | None:
        """Resolve a key inside the archive root, or None if it escapes it.

        Guards against ``..`` traversal in a DB value or a crafted request.
        """
        try:
            root = self.root.resolve()
            target = (root / rel_path).resolve()
        except OSError:
            return None
        if target != root and root not in target.parents:
            return None
        return target

    def exists(self, rel_path: str) -> bool:
        p = self._resolve(rel_path)
        return bool(p and p.exists())

    def size(self, rel_path: str) -> int | None:
        p = self._resolve(rel_path)
        try:
            return p.stat().st_size if p else None
        except OSError:
            return None

    def delete(self, rel_path: str) -> None:
        p = self._resolve(rel_path)
        if p is None:
            return
        try:
            p.unlink(missing_ok=True)
            p.with_suffix(p.suffix + ".json").unlink(missing_ok=True)
        except OSError:
            pass

    def delete_prefix(self, prefix: str) -> None:
        p = self._resolve(prefix)
        # Refuse to wipe the archive root itself.
        if p is None or p == self.root.resolve():
            return
        shutil.rmtree(p, ignore_errors=True)

    def local_path(self, rel_path: str) -> Path | None:
        return self._resolve(rel_path)

    def url_for(self, rel_path: str) -> str:
        return f"/media/{rel_path}"


_default: Storage | None = None


def get_storage() -> Storage:
    """The configured backend. Local today; see docs/storage-backends.md."""
    global _default
    if _default is None:
        _default = LocalStorage()
    return _default
