# AGENTS.md

Guidance for Codex (and humans) working in this repo.

## What Pinchive is

A self-hosted **Pinterest board archiver**, inspired by TubeArchivist. It downloads
public *and* private boards (your own private boards, via session cookies), stores the
media locally, and serves a dark web UI to browse/search/dedupe them. Designed to run
as a small Docker stack on modest hardware.

## Stack (and why)

- **Python 3.12** + **FastAPI** serving a JSON API (no server-rendered templates).
- **React 19 + HeroUI + Framer Motion** front end, built once at image time by Vite.
  HeroUI is React Aria underneath, so keyboard/focus behaviour comes with the
  components; Framer Motion drives screen transitions and the lightbox. Polling
  replaces the old HTMX partial refresh for live download progress.
- **gallery-dl >= 1.32** (`python -m gallery_dl`) is the download engine; the minimum
  version is required for post-event `--Print` and checkpoint `--post-filter` support.
  **yt-dlp** + **ffmpeg** handle video/story pins.
- **SQLite (WAL)** via **Peewee** for metadata (no SQLAlchemy); **arq + Redis** for the
  async worker and the minute-level crontab dispatcher.
- **Pillow** for perceptual-hash duplicate detection (no numpy).
- **Tailwind CSS 4** with Pinchive design tokens layered over HeroUI's theme
  variables. See "Design" below.

Rationale lives in memory `pinchive-stack.md`.

## Layout

```
app/
  main.py          app factory: routers, /media mount, SPA serving + deep-link fallback
  worker.py        arq worker: job wrappers, crontab dispatcher, resume-on-startup
  core/
    config.py      pydantic-settings, env prefix PINCHIVE_ (see env vars below)
    db.py          Peewee database handle, per-unit-of-work connections, migration
    i18n.py        locale resolution + pure-ASGI LocaleMiddleware (catalogue is client-side)
    timefmt.py     UTC -> configured TZ for display
  models/          Peewee tables: archive.py (Board/Pin/Credential/Tag/...), enums.py, base.py
  api/             routers (boards, pins, duplicates, credentials, system) + schemas.py
  services/
    board_service.py      board lifecycle, listing/filtering, checkpoints, deletion
    pin_service.py        pin queries, sorting, tagging, deletion + count reconcile
    sync_service.py       run gallery-dl and reconcile the result (the download job)
    dedup_service.py      parallel hashing, grouping, stored dup_group + live status
    credential_store.py   credential rows; credential_service.py = cookie file + liveness
    settings_service.py   runtime-editable settings: Setting table over config merge
    downloader.py         gallery-dl command/output protocol, checkpoint stop filter,
                          per-pin stall guard, selective hashing, media scan
    dedup.py              sha256 + 256-bit dHash + banded-LSH grouping
    storage.py            media storage abstraction (local today; see docs/)
frontend/
  src/pages/       Boards / BoardDetail / Duplicates / Settings
  src/components/  app shell, pin card, lightbox, add-board dialog, shared UI
  src/api/         typed client mirroring app/api/schemas.py
  src/i18n/        EN/KO catalogue (every EN key must exist in KO)
  public/fonts/    self-hosted webfonts (committed, not fetched at build time)
scripts/           deploy-dev.sh
tests/             pytest (API, sync, dedup, storage, settings, migration)
```

**Dependency direction is one-way: `core -> models -> services -> api`.** Services own
every DB and filesystem access, so the API layer and the worker share one implementation
of each rule. `core` must never import from `services` or `api`.

## Running

```bash
# backend (needs Redis running)
pip install -e ".[dev]"
uvicorn app.main:app --reload          # API on :8000
arq app.worker.WorkerSettings          # worker

# front end (separate terminal): hot reload, proxies /api + /media to :8000
cd frontend && npm ci && npm run dev   # :5173
# or build once and let uvicorn serve it from frontend/dist
npm run build

# tests / lint
pytest -q
ruff check .
cd frontend && npx tsc -b --noEmit

# full stack
docker compose -f docker-compose.yml up --build -d   # build from source
docker compose up -d                                 # pull the published image
```

App serves on `:8000`; data (db, boards, cookies) lives under `PINCHIVE_DATA_DIR` (`./data`).

## Environment variables (`PINCHIVE_*`)

Defined in `app/core/config.py`. Many are also **runtime-editable** from the Settings page
(persisted in the `Setting` table via `app/services/settings_service.py`, which overrides
env at runtime).

| Var | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` | root for `pinchive.db`, `boards/`, `cookies/` |
| `REDIS_URL` | `redis://localhost:6379` | arq broker |
| `MAX_CONCURRENCY` | 2 | worker parallelism |
| `DL_SLEEP` | 0.8 | polite delay between requests (editable) |
| `PIN_STALL_TIMEOUT` | 600 | per-pin no-data abort in seconds; **no overall board timeout** (editable) |
| `REFRESH_CRON` | `0 */6 * * *` | cookie keep-alive schedule (crontab; empty disables) (editable) |
| `RESYNC_CRON` | `30 4 * * *` | board auto-resync schedule (crontab; empty disables) (editable) |
| `DEDUP_CRON` | `45 */6 * * *` | duplicate recompute schedule (crontab; empty disables) (editable) |
| `PER_PAGE_BOARDS/PINS/DUPES` | 24/60/20 | UI page sizes (editable) |

## Key behaviors

- **Credential keep-alive**: a cron re-hits Pinterest and persists the rotated
  `Set-Cookie` so a registered credential stays alive. Liveness is heuristic
  (`"is_authenticated"` page flag). A session Pinterest has killed server-side is
  flagged `expired` — recovery means re-pasting fresh cookies. Browser/password-based
  Playwright re-login has been removed completely; there is one browser-free image.
- **No board timeout**: `job_timeout = 7 days`. Instead each file has a per-pin stall
  guard (`downloader.http.timeout`). Interrupted downloads (status downloading/queued/
  pending) are **re-enqueued on worker startup** (`_resume_interrupted`).
- **Checkpoint-based incremental sync**: `Board.sync_checkpoint` stores a newest-first
  JSON window of the latest ten Pinterest pin IDs. Normal manual and scheduled syncs
  pass that window to gallery-dl; because Pinterest emits newest pins first, extraction
  stops *before downloading media* when any stored ID is reached. Existing/upgraded
  boards seed the first checkpoint from their stored `Pin.pinterest_id` values ordered
  by filename, avoiding an initial full network walk. If all old checkpoint pins have
  disappeared and gallery-dl reaches the end normally, the first ten IDs from that run
  replace the window. An interrupted/error run that never reaches a checkpoint must not
  replace it, or pins can be missed on the next sync.
- **Forced full sync**: one route serves both modes —
  `POST /api/boards/{id}/resync` is the incremental sync and
  `POST /api/boards/{id}/resync?full=true` bypasses the checkpoint for an end-to-end
  scan. `Board.force_full_sync` persists only so a worker/container restart resumes the
  requested full scan; it is cleared once gallery-dl returns. Each board's
  `.gallery-dl-archive.db` still prevents actual file re-downloads. A board already
  queued/downloading is never double-queued.
- **Incremental hashing**: a checkpoint sync hashes only media downloaded in that run
  and preserves cached hashes for existing pins. Initial/forced scans hash normally;
  scheduled/manual duplicate recomputation fills any missing hashes later.
- **Duplicate detection**: exact `content_sha256` + 256-bit perceptual dHash. Grouping
  uses union-find over sha + **banded LSH** (pigeonhole) with an **aspect-ratio guard** —
  intent is "same image, resolution-only", not "looks similar". Groups are **precomputed
  and stored** in `Pin.dup_group` (not computed per view). `NEAR_THRESHOLD=10` bits /256,
  `ASPECT_TOL=0.04`. Flat/blank images yield no phash (avoids false collisions). Recompute
  parallelizes hashing with `ProcessPoolExecutor`; the Settings page has a manual Rescan.
- **i18n**: the UI catalogue lives in the client
  (`frontend/src/i18n/catalogue.ts`), where the strings are rendered; KO must cover
  every EN key. The server keeps only locale resolution: a pure-ASGI
  `LocaleMiddleware` (a `BaseHTTPMiddleware` drops the ContextVar) resolving
  `?lang=` > cookie > `Accept-Language`, and persisting an explicit `?lang=` as a
  cookie. Korean UI uses `word-break: keep-all`.
- **Board names**: `Board.display_title` / `readable_name_from_url` un-percent-encode the
  URL name segment so Korean board names don't render as punycode/%-escapes.

## Design

A persistent sidebar app shell, collage board covers, CSS-column masonry pins,
responsive mobile navigation, and media lightboxes. Core tokens are near-black
`#0b0d0d`, panel `#0f1212`, card `#161a1a`, raised card `#1d2222`, and electric teal
`#2e999c`, with roughly 13px radii and no shadows. Inter (700/600/400) is used for UI
and JetBrains Mono for code. Korean uses **Noto Sans KR (본고딕)** first and
**Pretendard** as fallback. The locale selector lives in Settings, not the sidebar; the
sidebar footer shows session and active counts.

These tokens are declared in `frontend/src/index.css` twice on purpose: as Tailwind
`@theme` colours for utility classes, and as an `@layer base` override of HeroUI's own
theme variables (`--accent`, `--surface`, `--field-background`, …) so HeroUI components
adopt the palette instead of their stock blue-on-grey defaults.

## Gotchas (learned the hard way — full list in memory `pinchive-gotchas.md`)

- Peewee table names must stay pinned to the original schema (`boardtaglink`,
  `pintaglink`, `deletedpin`, singular `board`/`pin`/…) so an existing `pinchive.db`
  opens without a data migration. `legacy_table_names = False` plus an explicit
  `table_name` on every model.
- The Peewee database uses `autoconnect=False`: production code borrows a connection per
  unit of work via `core.db.connection()` / `transaction()` (reentrant). Touching a model
  outside one raises `InterfaceError: database connection not opened` — which is why the
  test fixture holds one connection open per test.
- `Setting` has a natural text primary key, so Peewee cannot tell insert from update.
  Upsert with `insert(...).on_conflict(conflict_target=[Setting.key], preserve=[...])`.
- `ENV PYTHONPATH=/app` resolves `import app` to the source tree next to
  `frontend/dist`, not the copy pip put in site-packages, so `BASE_DIR` is right.
- `StaticFiles` refuses to mount a missing directory, so `create_app()` calls
  `settings.ensure_dirs()` before mounting `/media` — a fresh install has no data dir yet.
- The SPA catch-all must be registered last and fall back to `index.html`, or deep links
  like `/boards/3` 404 on reload.
- `AnimatePresence mode="wait"` keeps presenting the already-exited screen unless the
  key changes; the route key is `${locale}:${pathname}` so a language switch re-mounts.
- Bind-mounted `/data` is host-owned; entrypoint runs as root, `chown`s `/data`, then
  `gosu pinchive` drops privileges. Do **not** add `USER pinchive` to the Dockerfile.
- Board folder is `{slug}-{board_id}` — plain slug collides when two boards share a URL.
- i18n must use the pure-ASGI middleware, not `BaseHTTPMiddleware`.
- Checkpoint output is a protocol, not ordinary progress: `--Print post:` emits
  `__PINCHIVE_PIN_ID__:<id>` and the post filter emits
  `__PINCHIVE_CHECKPOINT__:<id>` immediately before `abort()`. The parser must consume
  these markers without incrementing download/skip/error counts.
- Stop incremental extraction with gallery-dl `--post-filter`, which runs before an old
  pin's media request. Do not infer a checkpoint from filenames or kill the subprocess
  after a file starts downloading.
- Never treat a merely non-empty `seen_pin_ids` list as an authoritative checkpoint.
  Update it only when `stopped_at_checkpoint` is true or gallery-dl returned code 0.

## Docker / deploy

- Multi-stage Dockerfile: a `node:22-alpine` stage builds the React bundle, the
  `python:3.12-slim` runtime copies only `frontend/dist` — so no Node ships in the
  runtime layer. tini → entrypoint → gosu.
- **One browser-free image**, built for both `linux/amd64` and `linux/arm64` by
  `docker-bake.hcl`, is published as `ghcr.io/aroxu/pinchive:latest` (and a ref/version
  tag) by `.github/workflows/docker-publish.yml`.
- `compose.yaml` is the default pull-based stack: `docker compose up -d` uses the GHCR
  image. Source builds are explicit: `docker compose -f docker-compose.yml up --build -d`.
- **Dev-first workflow**: deploy the *local working tree* to the dev server, verify, then
  commit. `bash scripts/deploy-dev.sh` tars the tree (excluding .git/.env/data/.venv/…),
  ships it over `ssh aroxu@dev` (Tailscale SSH, passwordless), and runs
  the explicit source-build Compose file. It **preserves the remote `.env` and `data/`**
  — never overwrite them. If Bash/WSL is unavailable on Windows, use tar + SCP + SSH
  with the same exclusions and preservation rules. Dev details live in
  `pinchive-deploy.md`.

## Current verified baseline (2026-09-03)

The backend was re-layered onto Peewee and the front end rebuilt on React/HeroUI/Framer
Motion (commits `9535fdd`, `a96816e`, `c80bfc6`, `9276faf`).

- **126 pytest tests** pass; Ruff clean (rule set pinned in `pyproject.toml` so a Ruff
  upgrade cannot silently change the gate); `pip check` clean; front-end `tsc -b
  --noEmit` clean and `npm run build` succeeds (~175 kB gzip JS).
- Peewee opens the pre-existing schema unchanged and the migration adds
  `sync_checkpoint`, `force_full_sync`, `storage_backend`; cascade delete verified.
- Verified in a real browser against `uvicorn` + the built bundle: all four screens
  render, deep links fall back to the SPA, Korean board names decode from
  percent-encoding, the view-size buttons and settings save round-trip to the API
  (`per_page_pins` 60 → 33), EN/KO switching re-renders, and the lightbox opens
  uncropped and closes on Escape with body scroll restored.
- Dedup on seeded data grouped exactly the one genuine duplicate pair (600×400 plus its
  300×200 resize) out of 13 pins, leaving the other 11 ungrouped.
- **Not verified locally: the Docker image build** (no Docker on this machine). Only the
  Dockerfile's build-context references were checked statically.

## Constraints for assistants

- **Never** use or ask for the user's real Pinterest password.
- Avoid `-ExecutionPolicy Bypass` (blocked by the sandbox as a security-weakening flag).
- Commit only at logical milestones; the user often wants dev verification first.
