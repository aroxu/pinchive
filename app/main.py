"""FastAPI application: routes, HTMX partials, static + media serving."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlmodel import Session, select

from app import auth, dedup
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


app = FastAPI(title="Pinchive", lifespan=lifespan)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["BoardStatus"] = BoardStatus
templates.env.globals["CredentialStatus"] = CredentialStatus

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


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    tag: str = Query(default=""),
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
    if tag.strip():
        stmt = (
            stmt.join(BoardTagLink, BoardTagLink.board_id == Board.id)
            .join(Tag, Tag.id == BoardTagLink.tag_id)
            .where(Tag.name == tag.strip())
        )
    boards = session.exec(stmt.order_by(Board.created_at.desc())).all()

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
            "active_tag": tag.strip(),
            "statuses": list(BoardStatus.__members__.keys()),
        },
    )


@app.get("/boards/{board_id}", response_class=HTMLResponse)
def board_detail(
    request: Request,
    board_id: int,
    q: str = Query(default=""),
    type: str = Query(default=""),       # image | video
    dupes: str = Query(default=""),      # "1" -> only pins that have duplicates
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
    pins = session.exec(stmt.order_by(Pin.id)).all()

    if dupes == "1":
        dup_ids = _duplicate_pin_ids(session)
        pins = [p for p in pins if p.id in dup_ids]

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
        },
    )


@app.get("/credentials", response_class=HTMLResponse)
def credentials_page(request: Request, session: Session = Depends(get_session)):
    creds = session.exec(select(Credential).order_by(Credential.name)).all()
    return templates.TemplateResponse(
        request, "credentials.html", {"credentials": creds}
    )


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
    return RedirectResponse("/credentials", status_code=303)


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
    return RedirectResponse("/credentials", status_code=303)


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


# --------------------------------------------------------------------------- #
# duplicates
# --------------------------------------------------------------------------- #
def _image_pin_rows(session: Session) -> list[dict]:
    rows = session.exec(
        select(Pin).where(Pin.media_type == "image")
    ).all()
    return [
        {
            "id": p.id,
            "content_sha256": p.content_sha256,
            "phash": p.phash,
            "pin": p,
        }
        for p in rows
    ]


def _duplicate_pin_ids(session: Session) -> set[int]:
    groups = dedup.group_duplicates(_image_pin_rows(session))
    ids: set[int] = set()
    for g in groups:
        for it in g:
            ids.add(it["id"])
    return ids


@app.get("/duplicates", response_class=HTMLResponse)
def duplicates_page(request: Request, session: Session = Depends(get_session)):
    rows = _image_pin_rows(session)
    raw_groups = dedup.group_duplicates(rows)
    # Build view groups: keep the largest-resolution pin as the "keep" suggestion.
    groups = []
    for g in raw_groups:
        pins = [it["pin"] for it in g]
        pins.sort(key=lambda p: (p.width or 0) * (p.height or 0), reverse=True)
        groups.append(pins)
    total_extra = sum(len(g) - 1 for g in groups)
    return templates.TemplateResponse(
        request,
        "duplicates.html",
        {"groups": groups, "total_extra": total_extra},
    )


@app.post("/duplicates/resolve")
async def resolve_duplicates(
    request: Request,
    pin_ids: list[int] = Form(default=[]),
    session: Session = Depends(get_session),
):
    for pid in pin_ids:
        pin = session.get(Pin, pid)
        if pin is None:
            continue
        _delete_pin_files(pin)
        session.delete(pin)
    session.commit()
    return RedirectResponse("/duplicates", status_code=303)


@app.get("/healthz")
def healthz(request: Request):
    return {"status": "ok", "queue": request.app.state.arq is not None}


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #
def _any_active(boards: list[Board]) -> bool:
    return any(b.status in ACTIVE_STATUSES for b in boards)


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
