"""FastAPI application: routes, HTMX partials, static + media serving."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app import auth
from app.config import get_settings
from app.db import get_session, init_db
from app.models import Board, BoardStatus, Credential, CredentialStatus, Pin
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
def index(request: Request, session: Session = Depends(get_session)):
    boards = session.exec(select(Board).order_by(Board.created_at.desc())).all()
    creds = session.exec(select(Credential).order_by(Credential.name)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"boards": boards, "credentials": creds, "any_active": _any_active(boards)},
    )


@app.get("/boards/{board_id}", response_class=HTMLResponse)
def board_detail(
    request: Request, board_id: int, session: Session = Depends(get_session)
):
    board = session.get(Board, board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    pins = session.exec(
        select(Pin).where(Pin.board_id == board_id).order_by(Pin.id)
    ).all()
    return templates.TemplateResponse(
        request, "board_detail.html", {"board": board, "pins": pins}
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
