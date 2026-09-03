# AGENTS.md

Guidance for Codex (and humans) working in this repo.

## What Pinchive is

A self-hosted **Pinterest board archiver**, inspired by TubeArchivist. It downloads
public *and* private boards (your own private boards, via session cookies), stores the
media locally, and serves a dark web UI to browse/search/dedupe them. Designed to run
as a small Docker stack on modest hardware.

## Stack (and why)

- **Python 3.12**, **FastAPI + HTMX + Jinja2** — server-rendered pages, no SPA/Node build.
  HTMX gives live polling (download progress, stats) without a frontend framework.
- **gallery-dl >= 1.32** (`python -m gallery_dl`) is the download engine; the minimum
  version is required for post-event `--Print` and checkpoint `--post-filter` support.
  **yt-dlp** + **ffmpeg** handle video/story pins.
- **SQLite (WAL)** via **SQLModel** for metadata; **arq + Redis** for the async worker and
  the minute-level crontab dispatcher.
- **Pillow** for perceptual-hash duplicate detection (no numpy).
- Hand-written CSS design tokens (no Tailwind). See "Design" below.

Rationale lives in memory `pinchive-stack.md`.

## Layout

```
app/
  main.py          FastAPI app, routes, pure-ASGI LocaleMiddleware (i18n)
  config.py        pydantic-settings, env prefix PINCHIVE_ (see env vars below)
  appsettings.py   runtime-editable settings: Setting DB table over config merge
  models.py        SQLModel tables, including per-board sync checkpoint/full-sync state
                   (NO `from __future__ import annotations` — see gotchas)
  db.py            engine/session
  auth.py          cookie ingest (Netscape/JSON -> Netscape file), liveness heuristic
  tasks.py         arq worker: checkpoint-aware board sync, credential refresh, dedup,
                   scheduled resync + resume-on-startup
  downloader.py    gallery-dl command/output protocol, checkpoint stop filter,
                   per-pin stall guard, selective hashing, media scan/name extraction
  dedup.py         sha256 + 256-bit dHash + banded-LSH grouping
  i18n.py          EN/KO catalogs, t(), locale resolution
templates/         sidebar app shell, boards, masonry detail, duplicates, settings + partials/
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
- **Forced full sync**: normal re-sync is `POST /boards/{id}/redownload`; explicit
  end-to-end scan is `POST /boards/{id}/full-resync`. `Board.force_full_sync` bypasses
  the checkpoint and persists only so a worker/container restart resumes the requested
  full scan; it is cleared once gallery-dl returns. Each board's
  `.gallery-dl-archive.db` still prevents actual file re-downloads.
- **Incremental hashing**: a checkpoint sync hashes only media downloaded in that run
  and preserves cached hashes for existing pins. Initial/forced scans hash normally;
  scheduled/manual duplicate recomputation fills any missing hashes later.
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

## Design

The current UI follows the imported `Pinchive.dc.html` direction: a persistent sidebar
app shell, collage board covers, CSS-column masonry pins, responsive mobile navigation,
and media lightboxes. Core tokens are near-black `#0b0d0d`, panel `#0f1212`, card
`#161a1a`, raised card `#1d2222`, and electric teal `#2e999c`, with roughly 13px radii
and no shadows. Inter (700/600/400) is used for UI and JetBrains Mono for code. Korean
uses **Noto Sans KR (본고딕)** first and **Pretendard** as fallback. The locale selector
lives in Settings, not the sidebar; the sidebar footer shows session and active counts.

## Gotchas (learned the hard way — full list in memory `pinchive-gotchas.md`)

- `app/models.py` must **not** use `from __future__ import annotations` — SQLModel needs
  real types for `List["X"]` relationships (else `InvalidRequestError`).
- In-container templates need `ENV PYTHONPATH=/app`.
- Bind-mounted `/data` is host-owned; entrypoint runs as root, `chown`s `/data`, then
  `gosu pinchive` drops privileges. Do **not** add `USER pinchive` to the Dockerfile.
- Board folder is `{slug}-{board_id}` — plain slug collides when two boards share a URL.
- `select(Pin.rel_path)` yields scalars in SQLModel — don't unpack `for (r,) in ...`.
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

- Multi-stage Dockerfile (assets fetch + runtime), tini → entrypoint → gosu.
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

## Current verified baseline (2026-08-05)

- Commit `875fed2` is on `main`; local verification passed **116 pytest tests**, Ruff,
  and `pip check`.
- Dev DB migration contains `sync_checkpoint` and `force_full_sync`; health reports
  `{"status":"ok","queue":true}` and the Korean UI exposes **강제 전체 동기화**.
- A real upgraded-board incremental run seeded a 10-ID checkpoint from 2,711 existing
  IDs, downloaded 636 new pins, reached the old checkpoint, and finished with 3,354
  stored pins. A subsequent no-new-pin run stopped at the checkpoint with 0 downloads,
  0 skips, and completed in 5.61 seconds.
- The forced-full route/UI path was verified without intentionally issuing a redundant
  end-to-end Pinterest scan. GitHub Actions successfully published the amd64/arm64 GHCR
  manifest.

## Constraints for assistants

- **Never** use or ask for the user's real Pinterest password.
- Avoid `-ExecutionPolicy Bypass` (blocked by the sandbox as a security-weakening flag).
- Commit only at logical milestones; the user often wants dev verification first.
