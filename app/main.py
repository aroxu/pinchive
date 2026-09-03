"""FastAPI application factory.

The app is an API server plus a static host for the built React client. All
behaviour lives in ``app.api`` (HTTP) and ``app.services`` (logic); this module
only wires them together and manages process-level lifecycle.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import boards, credentials, duplicates, pins, system
from app.core.config import get_settings
from app.core.i18n import LocaleMiddleware
from app.models import init_db

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


class _ImmutableStatic(StaticFiles):
    """StaticFiles for content-hashed filenames: cache hard, never revalidate."""

    def file_response(self, *args, **kwargs):  # noqa: ANN002, ANN003
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    try:
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        # Fail fast if Redis is absent so the UI still comes up read-only; under
        # compose, depends_on: service_healthy guarantees Redis starts first.
        redis_settings.conn_retries = 2
        redis_settings.conn_retry_delay = 1
        app.state.arq = await create_pool(redis_settings)
    except Exception:  # noqa: BLE001 — app still serves read-only if Redis is down
        app.state.arq = None
    yield
    if app.state.arq is not None:
        await app.state.arq.close()


def create_app() -> FastAPI:
    # StaticFiles refuses to mount a missing directory, and on a fresh install
    # the data volume is empty until the worker runs — so create it up front.
    settings.ensure_dirs()
    app = FastAPI(title="Pinchive", lifespan=lifespan)
    app.add_middleware(LocaleMiddleware)

    app.include_router(boards.router)
    app.include_router(pins.router)
    app.include_router(duplicates.router)
    app.include_router(credentials.router)
    app.include_router(system.router)

    # Downloaded media, served read-only from the data volume.
    app.mount(
        "/media",
        StaticFiles(directory=str(settings.boards_dir)),
        name="media",
    )

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React client, falling back to index.html for client-side
    routes (deep links like /boards/3 must not 404)."""
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        # Vite fingerprints these filenames, so a changed bundle is a new URL and
        # the old one can be cached forever.
        app.mount(
            "/assets",
            _ImmutableStatic(directory=str(assets)),
            name="assets",
        )

    index = FRONTEND_DIST / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request):  # noqa: ARG001
        if not index.exists():
            raise HTTPException(
                503,
                "front end not built — run `npm ci && npm run build` in frontend/",
            )
        # A real file (favicon, manifest, …) wins over the SPA fallback.
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        # index.html names the hashed bundles, so it must never be served from
        # cache without revalidating: a stale copy pins the browser to the
        # previous build and an update looks like it did nothing.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
