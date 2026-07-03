# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What Pinchive is

A self-hosted **Pinterest board archiver**, inspired by TubeArchivist. It downloads
public *and* private boards (your own private boards, via session cookies), stores the
media locally, and serves a dark web UI to browse/search/dedupe them. Designed to run
as a small Docker stack on modest hardware.

## Stack (and why)

- **Python 3.12**, **FastAPI + HTMX + Jinja2** — server-rendered pages, no SPA/Node build.
  HTMX gives live polling (download progress, stats) without a frontend framework.
- **gallery-dl** (`python -m gallery_dl`) is the download engine; **yt-dlp** + **ffmpeg**
  handle video/story pins.
- **SQLite (WAL)** via **SQLModel** for metadata; **arq + Redis** for the async worker and
  gated hourly crons.
- **Pillow** for perceptual-hash duplicate detection (no numpy).
- **Playwright** is an *optional* headless re-login fallback (heavy ~300MB), shipped as a
  separate image variant — off by default.
- Hand-written CSS design tokens (no Tailwind). See "Design" below.

Rationale lives in memory `pinchive-stack.md`.

## Layout

```
app/
  main.py          FastAPI app, routes, pure-ASGI LocaleMiddleware (i18n)
  config.py        pydantic-settings, env prefix PINCHIVE_ (see env vars below)
  appsettings.py   runtime-editable settings: Setting DB table over config merge
  models.py        SQLModel tables (NO `from __future__ import annotations` — see gotchas)
  db.py            engine/session
  auth.py          cookie ingest (Netscape/JSON -> Netscape file), liveness heuristic
  tasks.py         arq worker: download_board, refresh_credential, recompute_duplicates,
                   resync_all_boards + gated crons + resume-on-startup
  downloader.py    build_command / run_download (line parsing, per-pin stall guard),
                   scan_media, extract_board_name
  dedup.py         sha256 + 256-bit dHash + banded-LSH grouping
  refresh_browser.py  Playwright relogin(cred_id) with success verification
  i18n.py          EN/KO catalogs, t(), locale resolution
templates/         base, index, board_detail, duplicates, settings + partials/
scripts/           deploy-dev.sh, fetch_assets.{sh,ps1}
tests/             pytest (dedup, i18n coverage, etc.)
```

## Running

```bash
# local dev (needs Redis running)
uvicorn app.main:app --reload          # web
arq app.tasks.WorkerSettings           # worker

# tests / lint
pytest -q
ruff check .

# full stack
docker compose up --build
```

App serves on `:8000`; data (db, boards, cookies) lives under `PINCHIVE_DATA_DIR` (`./data`).

## Environment variables (`PINCHIVE_*`)

Defined in `app/config.py`. Many are also **runtime-editable** from the Settings page
(persisted in the `Setting` table via `app/appsettings.py`, which overrides env at runtime).

| Var | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` | root for `pinchive.db`, `boards/`, `cookies/` |
| `REDIS_URL` | `redis://localhost:6379` | arq broker |
| `MAX_CONCURRENCY` | 2 | worker parallelism |
| `DL_SLEEP` | 0.8 | polite delay between requests (editable) |
| `PIN_STALL_TIMEOUT` | 600 | per-pin no-data abort in seconds; **no overall board timeout** (editable) |
| `REFRESH_EVERY_HOURS` | 6 | cookie keep-alive cron cadence (0 = once daily at REFRESH_HOUR) (editable) |
| `USE_PLAYWRIGHT_FALLBACK` | false | try headless re-login when a session is truly dead (editable) |
| `RESYNC_EVERY_HOURS` | 24 | board auto-resync cadence; 0 disables (editable) |
| `DEDUP_EVERY_HOURS` | 6 | periodic duplicate recompute; 0 disables (editable) |
| `PER_PAGE_BOARDS/PINS/DUPES` | 24/60/20 | UI page sizes (editable) |

## Key behaviors

- **Credential keep-alive**: a cron re-hits Pinterest and persists the rotated
  `Set-Cookie` so a registered credential stays alive. Liveness is heuristic
  (`"is_authenticated"` page flag). Optional Playwright fallback mints fresh cookies
  when the session is truly dead.
- **No board timeout**: `job_timeout = 7 days`. Instead each file has a per-pin stall
  guard (`downloader.http.timeout`). Interrupted downloads (status downloading/queued/
  pending) are **re-enqueued on worker startup** (`_resume_interrupted`).
- **Duplicate detection**: exact `content_sha256` + 256-bit perceptual dHash. Grouping
  uses union-find over sha + **banded LSH** (pigeonhole) with an **aspect-ratio guard** —
  intent is "same image, resolution-only", not "looks similar". Groups are **precomputed
  and stored** in `Pin.dup_group` (not computed per view). `NEAR_THRESHOLD=10` bits /256,
  `ASPECT_TOL=0.04`. Flat/blank images yield no phash (avoids false collisions). Recompute
  parallelizes hashing with `ProcessPoolExecutor`; the Settings page has a manual Rescan.
- **i18n**: EN/KO catalogs + `ContextVar` + pure-ASGI `LocaleMiddleware` (a
  `BaseHTTPMiddleware` drops the ContextVar). `?lang=` persists via cookie. KO must cover
  every EN key (enforced by test). Korean UI uses `word-break: keep-all`.
- **Board names**: `Board.display_title` / `readable_name_from_url` un-percent-encode the
  URL name segment so Korean board names don't render as punycode/%-escapes.

## Design tokens (ClickHouse-inspired)

Near-black canvas `#0a0a0a`, electric teal `#2e999c`, dark cards `#1a1a1a`, **no shadows**,
96px section rhythm, radii 8/12px. Inter (700/600/400) for UI, JetBrains Mono for code.
Korean: **Noto Sans KR (본고딕)** primary, **Pretendard** fallback.

## Gotchas (learned the hard way — full list in memory `pinchive-gotchas.md`)

- `app/models.py` must **not** use `from __future__ import annotations` — SQLModel needs
  real types for `List["X"]` relationships (else `InvalidRequestError`).
- In-container templates need `ENV PYTHONPATH=/app`.
- Bind-mounted `/data` is host-owned; entrypoint runs as root, `chown`s `/data`, then
  `gosu pinchive` drops privileges. Do **not** add `USER pinchive` to the Dockerfile.
- Board folder is `{slug}-{board_id}` — plain slug collides when two boards share a URL.
- `select(Pin.rel_path)` yields scalars in SQLModel — don't unpack `for (r,) in ...`.
- i18n must use the pure-ASGI middleware, not `BaseHTTPMiddleware`.

## Docker / deploy

- Multi-stage Dockerfile (assets fetch + runtime), tini → entrypoint → gosu.
- **Two image variants** via `docker-bake.hcl`: `slim` (`:latest`, no browser) and
  `playwright` (`:playwright`, INSTALL_PLAYWRIGHT=true). Published to
  `ghcr.io/<repo>` by `.github/workflows/docker-publish.yml`.
- **Dev-first workflow**: deploy the *local working tree* to the dev server, verify, then
  commit. `bash scripts/deploy-dev.sh` tars the tree (excluding .git/.env/data/.venv/…),
  ships it over `ssh aroxu@dev` (Tailscale SSH, passwordless), and runs
  `docker compose up --build -d`. It **preserves the remote `.env` and `data/`** — never
  overwrite them. Dev details in memory `pinchive-deploy.md`.

## Constraints for assistants

- **Never** use or ask for the user's real Pinterest password. Use dummy creds for any
  Playwright test.
- Avoid `-ExecutionPolicy Bypass` (blocked by the sandbox as a security-weakening flag).
- Commit only at logical milestones; the user often wants dev verification first.
