"""FastAPI application: routes, HTMX partials, static + media serving."""

from __future__ import annotations

from contextlib import asynccontextmanager
from math import ceil
from pathlib import Path
from urllib.parse import urlencode

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import MutableHeaders
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app import appsettings, auth, i18n
from app.config import get_settings
from app.db import get_session, init_db
from app.models import (
    Board,
    BoardStatus,
    BoardTagLink,
    Credential,
    CredentialStatus,
    Pin,
    Tag,
)
from app.tasks import derive_slug

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent.parent

ACTIVE_STATUSES = {BoardStatus.pending, BoardStatus.queued, BoardStatus.downloading}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    try:
        rs = RedisSettings.from_dsn(settings.redis_url)
        # Fail fast if redis is absent so the UI still comes up read-only;
        # under compose, depends_on: service_healthy guarantees redis first.
        rs.conn_retries = 2
        rs.conn_retry_delay = 1
        app.state.arq = await create_pool(rs)
    except Exception:  # noqa: BLE001 — app still serves read-only if redis is down
        app.state.arq = None
    yield
    if app.state.arq is not None:
        await app.state.arq.close()


class LocaleMiddleware:
    """Pure ASGI middleware (NOT BaseHTTPMiddleware) so the locale ContextVar it
    sets is visible to the endpoint + template render — BaseHTTPMiddleware runs
    the endpoint in a separate task and would drop the ContextVar. Persists an
    explicit `?lang=` choice as a cookie."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        loc = i18n.resolve_locale(request)
        i18n.set_locale(loc)
        if request.query_params.get("lang") in i18n.SUPPORTED:
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(raw=message.setdefault("headers", []))
                    headers.append(
                        "set-cookie",
                        f"lang={loc}; Max-Age=31536000; Path=/; SameSite=Lax",
                    )
                await send(message)

            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)


app = FastAPI(title="Pinchive", lifespan=lifespan)
app.add_middleware(LocaleMiddleware)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["BoardStatus"] = BoardStatus
templates.env.globals["CredentialStatus"] = CredentialStatus
templates.env.globals["t"] = i18n.t
templates.env.globals["get_locale"] = i18n.get_locale
templates.env.globals["SUPPORTED_LANGS"] = i18n.SUPPORTED
templates.env.globals["LANG_NAMES"] = i18n.LANG_NAMES

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
# Downloaded media, served read-only from the data volume.
app.mount("/media", StaticFiles(directory=str(settings.boards_dir)), name="media")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def enqueue_download(request: Request, board_id: int) -> bool:
    pool: ArqRedis | None = request.app.state.arq
    if pool is None:
        return False
    try:
        await pool.enqueue_job("download_board", board_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _count(session: Session, stmt) -> int:
    return session.exec(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).one()


def _paginate(session: Session, stmt, page: int, per_page: int):
    """Return (items, page, pages, total) for a select statement."""
    total = _count(session, stmt)
    pages = max(1, ceil(total / per_page))
    page = min(max(1, page), pages)
    items = session.exec(
        stmt.offset((page - 1) * per_page).limit(per_page)
    ).all()
    return items, page, pages, total


def _hero_stats(session: Session) -> dict:
    """Archive-wide totals for the live hero stats (not page-limited)."""
    n = lambda model: session.exec(select(func.count()).select_from(model)).one()  # noqa: E731
    return {"stat_boards": n(Board), "stat_pins": n(Pin), "stat_sessions": n(Credential)}


def _paginate_list(rows: list, page: int, per_page: int):
    total = len(rows)
    pages = max(1, ceil(total / per_page)) if total else 1
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return rows[start:start + per_page], page, pages, total


def _base_qs(**params) -> str:
    """Query string (trailing '&') of the active filters, minus `page`."""
    clean = {k: v for k, v in params.items() if v not in ("", None, False)}
    s = urlencode(clean)
    return f"{s}&" if s else ""


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    tag: str = Query(default=""),
    page: int = Query(default=1),
    session: Session = Depends(get_session),
):
    stmt = select(Board)
    q = q.strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Board.title.ilike(like), Board.url.ilike(like), Board.slug.ilike(like))
        )
    if status and status in BoardStatus.__members__:
        stmt = stmt.where(Board.status == BoardStatus(status))
    tag = tag.strip()
    if tag:
        stmt = (
            stmt.join(BoardTagLink, BoardTagLink.board_id == Board.id)
            .join(Tag, Tag.id == BoardTagLink.tag_id)
            .where(Tag.name == tag)
        )
    stmt = stmt.order_by(Board.created_at.desc())
    boards, page, pages, total = _paginate(
        session, stmt, page, appsettings.get("per_page_boards")
    )

    creds = session.exec(select(Credential).order_by(Credential.name)).all()
    all_tags = session.exec(select(Tag).order_by(Tag.name)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "boards": boards,
            "credentials": creds,
            "any_active": _any_active(boards),
            "all_tags": all_tags,
            "q": q,
            "active_status": status,
            "active_tag": tag,
            "statuses": list(BoardStatus.__members__.keys()),
            "page": page,
            "pages": pages,
            "total": total,
            "base_qs": _base_qs(q=q, status=status, tag=tag),
            **_hero_stats(session),
        },
    )


@app.get("/stats", response_class=HTMLResponse)
def hero_stats(request: Request, session: Session = Depends(get_session)):
    """HTMX poll target: the live archive-wide counters on the home hero."""
    return templates.TemplateResponse(
        request, "partials/hero_stats.html", _hero_stats(session)
    )


# Pin sort options -> ORDER BY clauses. `_area` orders by pixel resolution.
_area = Pin.width * Pin.height
PIN_SORTS = {
    "new": [Pin.id.desc()],
    "old": [Pin.id.asc()],
    "big": [_area.desc(), Pin.id.desc()],
    "small": [_area.asc(), Pin.id.asc()],
    "name": [Pin.filename.asc(), Pin.id.asc()],
}
PIN_VIEWS = {"s", "m", "l"}


@app.get("/boards/{board_id}", response_class=HTMLResponse)
def board_detail(
    request: Request,
    board_id: int,
    q: str = Query(default=""),
    type: str = Query(default=""),       # image | video
    dupes: str = Query(default=""),      # "1" -> only pins that have duplicates
    sort: str = Query(default="new"),
    view: str = Query(default="m"),      # s | m | l  (thumbnail size)
    page: int = Query(default=1),
    session: Session = Depends(get_session),
):
    board = session.get(Board, board_id)
    if board is None:
        raise HTTPException(404, "board not found")

    stmt = select(Pin).where(Pin.board_id == board_id)
    q = q.strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Pin.filename.ilike(like),
                Pin.title.ilike(like),
                Pin.description.ilike(like),
                Pin.source_url.ilike(like),
            )
        )
    if type in ("image", "video"):
        stmt = stmt.where(Pin.media_type == type)
    if dupes == "1":
        # Push the (in-memory) duplicate set into SQL so pagination is correct.
        dup_ids = _duplicate_pin_ids(session) or {-1}
        stmt = stmt.where(Pin.id.in_(dup_ids))

    sort = sort if sort in PIN_SORTS else "new"
    view = view if view in PIN_VIEWS else "m"
    stmt = stmt.order_by(*PIN_SORTS[sort])
    pins, page, pages, total = _paginate(
        session, stmt, page, appsettings.get("per_page_pins")
    )

    all_tags = session.exec(select(Tag).order_by(Tag.name)).all()
    return templates.TemplateResponse(
        request,
        "board_detail.html",
        {
            "board": board,
            "pins": pins,
            "all_tags": all_tags,
            "q": q,
            "active_type": type,
            "dupes": dupes == "1",
            "active_sort": sort,
            "active_view": view,
            "page": page,
            "pages": pages,
            "total": total,
            "base_qs": _base_qs(q=q, type=type, dupes=dupes, sort=sort, view=view),
        },
    )


@app.get("/credentials")
def credentials_page(request: Request):
    # Credentials live under the consolidated Settings page now.
    return RedirectResponse("/settings", status_code=307)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    synced: str = Query(default=""),
    saved: str = Query(default=""),
    session: Session = Depends(get_session),
):
    creds = session.exec(select(Credential).order_by(Credential.name)).all()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "credentials": creds,
            "cfg": settings,
            "eff": appsettings.effective(),
            "queue_up": request.app.state.arq is not None,
            "synced": synced == "1",
            "saved": saved == "1",
        },
    )


@app.post("/settings/save")
async def settings_save(request: Request):
    form = await request.form()
    appsettings.save(dict(form))
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/sync-all")
async def settings_sync_all(request: Request):
    """Manually enqueue an auto-resync of all opted-in boards."""
    pool: ArqRedis | None = request.app.state.arq
    if pool is not None:
        try:
            await pool.enqueue_job("resync_all_boards")
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse("/settings?synced=1", status_code=303)


# --------------------------------------------------------------------------- #
# board actions
# --------------------------------------------------------------------------- #
@app.post("/boards", response_class=HTMLResponse)
async def add_board(
    request: Request,
    url: str = Form(...),
    credential_id: str = Form(default=""),
    session: Session = Depends(get_session),
):
    url = url.strip()
    if "pinterest." not in url:
        raise HTTPException(400, "not a Pinterest URL")

    cred_id = int(credential_id) if credential_id.strip().isdigit() else None
    board = Board(
        url=url,
        slug=derive_slug(url),
        status=BoardStatus.queued,
        credential_id=cred_id,
    )
    session.add(board)
    session.commit()
    session.refresh(board)

    if not await enqueue_download(request, board.id):
        board.status = BoardStatus.pending
        board.last_error = "queue unavailable — press retry once the worker is up"
        session.add(board)
        session.commit()
        session.refresh(board)

    return templates.TemplateResponse(
        request, "partials/board_card.html", {"board": board}
    )


@app.post("/boards/{board_id}/redownload", response_class=HTMLResponse)
async def redownload_board(
    request: Request, board_id: int, session: Session = Depends(get_session)
):
    board = session.get(Board, board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    board.status = BoardStatus.queued
    board.last_error = None
    session.add(board)
    session.commit()
    if not await enqueue_download(request, board_id):
        board.status = BoardStatus.pending
        board.last_error = "queue unavailable"
        session.add(board)
        session.commit()
    session.refresh(board)
    return templates.TemplateResponse(
        request, "partials/board_card.html", {"board": board}
    )


@app.get("/boards/{board_id}/card", response_class=HTMLResponse)
def board_card(
    request: Request, board_id: int, session: Session = Depends(get_session)
):
    """HTMX poll target: returns the (possibly still-polling) card fragment."""
    board = session.get(Board, board_id)
    if board is None:
        return HTMLResponse("", status_code=200)
    return templates.TemplateResponse(
        request, "partials/board_card.html", {"board": board}
    )


@app.post("/boards/{board_id}/toggle-resync", response_class=HTMLResponse)
async def toggle_resync(
    request: Request, board_id: int, session: Session = Depends(get_session)
):
    board = session.get(Board, board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    board.auto_resync = not board.auto_resync
    session.add(board)
    session.commit()
    session.refresh(board)
    return templates.TemplateResponse(
        request, "partials/board_card.html", {"board": board}
    )


@app.post("/boards/{board_id}/delete")
async def delete_board(
    board_id: int,
    purge: str = Form(default=""),
    session: Session = Depends(get_session),
):
    board = session.get(Board, board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    if purge and board.dest_path:
        _rmtree_safe(Path(board.dest_path))
    session.delete(board)
    session.commit()
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------- #
# credential actions
# --------------------------------------------------------------------------- #
@app.post("/credentials")
async def add_credential(
    request: Request,
    name: str = Form(...),
    account: str = Form(default=""),
    cookies: str = Form(...),
    session: Session = Depends(get_session),
):
    cred = Credential(name=name.strip() or "unnamed", account=account.strip() or None)
    session.add(cred)
    session.commit()
    session.refresh(cred)
    try:
        auth.save_cookies_text(cred.id, cookies)
    except ValueError as e:
        session.delete(cred)
        session.commit()
        raise HTTPException(400, f"cookie parse error: {e}") from e

    # Immediately keep-alive so rotation starts from registration.
    from app.tasks import _now

    res = auth.refresh_session(auth.cookies_path(cred.id))
    cred.status = (
        CredentialStatus.active if res.active else CredentialStatus.expired
    )
    cred.last_checked_at = _now()
    cred.last_error = None if res.active else res.message
    session.add(cred)
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/credentials/{cred_id}/validate", response_class=HTMLResponse)
async def validate_credential(
    request: Request, cred_id: int, session: Session = Depends(get_session)
):
    cred = session.get(Credential, cred_id)
    if cred is None:
        raise HTTPException(404, "credential not found")
    # Manual "Validate" also does a keep-alive: rotate + persist the cookie.
    res = auth.refresh_session(auth.cookies_path(cred_id))
    from app.tasks import _now

    cred.status = CredentialStatus.active if res.active else CredentialStatus.expired
    cred.last_checked_at = _now()
    cred.last_error = None if res.active else res.message
    session.add(cred)
    session.commit()
    session.refresh(cred)
    return templates.TemplateResponse(
        request, "partials/credential_row.html", {"cred": cred}
    )


@app.post("/credentials/{cred_id}/delete")
async def delete_credential(cred_id: int, session: Session = Depends(get_session)):
    cred = session.get(Credential, cred_id)
    if cred is None:
        raise HTTPException(404, "credential not found")
    p = auth.cookies_path(cred_id)
    if p.exists():
        p.unlink(missing_ok=True)
    session.delete(cred)
    session.commit()
    return RedirectResponse("/settings", status_code=303)


# --------------------------------------------------------------------------- #
# tags
# --------------------------------------------------------------------------- #
def _get_or_create_tag(session: Session, name: str) -> Tag | None:
    name = name.strip().lower()
    if not name:
        return None
    tag = session.exec(select(Tag).where(Tag.name == name)).first()
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.commit()
        session.refresh(tag)
    return tag


def _redirect_back(request: Request, fallback: str) -> RedirectResponse:
    return RedirectResponse(request.headers.get("referer") or fallback, status_code=303)


@app.post("/boards/{board_id}/tag")
async def tag_board(
    request: Request,
    board_id: int,
    name: str = Form(...),
    session: Session = Depends(get_session),
):
    board = session.get(Board, board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    tag = _get_or_create_tag(session, name)
    if tag and tag not in board.tags:
        board.tags.append(tag)
        session.add(board)
        session.commit()
    return _redirect_back(request, f"/boards/{board_id}")


@app.post("/boards/{board_id}/untag/{tag_id}")
async def untag_board(
    request: Request,
    board_id: int,
    tag_id: int,
    session: Session = Depends(get_session),
):
    board = session.get(Board, board_id)
    tag = session.get(Tag, tag_id)
    if board and tag and tag in board.tags:
        board.tags.remove(tag)
        session.add(board)
        session.commit()
    return _redirect_back(request, f"/boards/{board_id}")


@app.post("/pins/{pin_id}/tag")
async def tag_pin(
    request: Request,
    pin_id: int,
    name: str = Form(...),
    session: Session = Depends(get_session),
):
    pin = session.get(Pin, pin_id)
    if pin is None:
        raise HTTPException(404, "pin not found")
    tag = _get_or_create_tag(session, name)
    if tag and tag not in pin.tags:
        pin.tags.append(tag)
        session.add(pin)
        session.commit()
    return _redirect_back(request, f"/boards/{pin.board_id}")


@app.post("/pins/{pin_id}/untag/{tag_id}")
async def untag_pin(
    request: Request,
    pin_id: int,
    tag_id: int,
    session: Session = Depends(get_session),
):
    pin = session.get(Pin, pin_id)
    tag = session.get(Tag, tag_id)
    if pin and tag and tag in pin.tags:
        pin.tags.remove(tag)
        session.add(pin)
        session.commit()
    return _redirect_back(request, f"/boards/{pin.board_id}" if pin else "/")


@app.post("/pins/bulk-tag")
async def bulk_tag_pins(
    request: Request,
    pin_ids: list[int] = Form(default=[]),
    name: str = Form(default=""),
    action: str = Form(default="add"),   # add | remove | delete
    session: Session = Depends(get_session),
):
    """Apply/remove a tag on many pins at once, or bulk-delete them."""
    if not pin_ids:
        return _redirect_back(request, "/")

    if action == "delete":
        affected: set[int] = set()
        for pid in pin_ids:
            pin = session.get(Pin, pid)
            if pin is None:
                continue
            affected.add(pin.board_id)
            _delete_pin_files(pin)
            session.delete(pin)
        session.commit()
        for bid in affected:
            _reconcile_board_counts(session, bid)
        session.commit()
        return _redirect_back(request, "/")

    tag = _get_or_create_tag(session, name)
    if tag is None:
        return _redirect_back(request, "/")
    for pid in pin_ids:
        pin = session.get(Pin, pid)
        if pin is None:
            continue
        if action == "remove":
            if tag in pin.tags:
                pin.tags.remove(tag)
        elif tag not in pin.tags:
            pin.tags.append(tag)
        session.add(pin)
    session.commit()
    return _redirect_back(request, "/")


# --------------------------------------------------------------------------- #
# duplicates  (read precomputed groups; see tasks.recompute_duplicates)
# --------------------------------------------------------------------------- #
def _duplicate_pin_ids(session: Session) -> set[int]:
    rows = session.exec(select(Pin.id).where(Pin.dup_group.is_not(None))).all()
    return set(rows)


def _stored_dup_groups(session: Session) -> list[list[Pin]]:
    """Group image pins by their precomputed dup_group, highest-res copy first."""
    rows = session.exec(
        select(Pin).where(Pin.dup_group.is_not(None)).order_by(Pin.dup_group)
    ).all()
    by_group: dict[int, list[Pin]] = {}
    for p in rows:
        by_group.setdefault(p.dup_group, []).append(p)
    groups = [g for g in by_group.values() if len(g) >= 2]
    for g in groups:
        g.sort(key=lambda p: (p.width or 0) * (p.height or 0), reverse=True)
    groups.sort(key=len, reverse=True)
    return groups


@app.get("/duplicates", response_class=HTMLResponse)
def duplicates_page(
    request: Request,
    page: int = Query(default=1),
    rescanned: str = Query(default=""),
    session: Session = Depends(get_session),
):
    groups = _stored_dup_groups(session)
    total_extra = sum(len(g) - 1 for g in groups)
    page_groups, page, pages, total = _paginate_list(
        groups, page, appsettings.get("per_page_dupes")
    )
    return templates.TemplateResponse(
        request,
        "duplicates.html",
        {
            "groups": page_groups,
            "total_extra": total_extra,
            "total_groups": total,
            "page": page,
            "pages": pages,
            "base_qs": "",
            "rescanned": rescanned == "1",
        },
    )


@app.post("/duplicates/resolve")
async def resolve_duplicates(
    request: Request,
    pin_ids: list[int] = Form(default=[]),
    session: Session = Depends(get_session),
):
    affected: set[int] = set()
    for pid in pin_ids:
        pin = session.get(Pin, pid)
        if pin is None:
            continue
        affected.add(pin.board_id)
        _delete_pin_files(pin)
        session.delete(pin)
    session.commit()
    for bid in affected:
        _reconcile_board_counts(session, bid)
    session.commit()
    return RedirectResponse("/duplicates", status_code=303)


@app.post("/duplicates/rescan")
async def rescan_duplicates(request: Request):
    """Manually trigger the (re)hash + regroup pass; results are stored in DB."""
    pool: ArqRedis | None = request.app.state.arq
    if pool is not None:
        try:
            await pool.enqueue_job("recompute_duplicates")
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse("/duplicates?rescanned=1", status_code=303)


@app.get("/healthz")
def healthz(request: Request):
    return {"status": "ok", "queue": request.app.state.arq is not None}


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #
def _any_active(boards: list[Board]) -> bool:
    return any(b.status in ACTIVE_STATUSES for b in boards)


def _reconcile_board_counts(session: Session, board_id: int) -> None:
    """After deleting pins, keep the board's counters honest with disk."""
    board = session.get(Board, board_id)
    if board is None:
        return
    remaining = len(session.exec(select(Pin).where(Pin.board_id == board_id)).all())
    board.pin_count = remaining
    board.downloaded_count = min(board.downloaded_count, remaining)
    session.add(board)


def _rmtree_safe(path: Path) -> None:
    import shutil

    try:
        resolved = path.resolve()
        if settings.boards_dir.resolve() in resolved.parents:
            shutil.rmtree(resolved, ignore_errors=True)
    except OSError:
        pass


def _delete_pin_files(pin: Pin) -> None:
    """Remove a pin's media file and its sidecar, guarded to boards_dir."""
    try:
        media = (settings.boards_dir / pin.rel_path).resolve()
        if settings.boards_dir.resolve() not in media.parents:
            return
        media.unlink(missing_ok=True)
        sidecar = media.with_suffix(media.suffix + ".json")
        sidecar.unlink(missing_ok=True)
    except OSError:
        pass
