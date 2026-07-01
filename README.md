# Pinchive

Self-hosted **Pinterest board archiver**. Paste a board URL — Pinchive pulls
every pin (images + video) to your own disk. Public boards, and private boards
via your session cookies. Inspired by [TubeArchivist](https://www.tubearchivist.com/).

Dark, teal, ClickHouse-flavored UI. Low-footprint by design: FastAPI + HTMX +
SQLite + gallery-dl, no Node build, no Elasticsearch.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | gallery-dl ecosystem lives here |
| Download engine | **gallery-dl** | native Pinterest board support, cookie auth for private |
| Web | **FastAPI** + **HTMX** + Jinja2 | server-rendered, no SPA build step |
| Styling | hand-written CSS (design tokens) | dark-only, no Tailwind/Node in the image |
| DB | **SQLite** (WAL) | single file, no server |
| Queue / cron | **arq** + Redis | async worker + scheduled credential refresh |
| Deploy | Docker Compose | `web` + `worker` + `redis` |

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build -d
# open http://localhost:8000
```

`web` serves the UI, `worker` runs downloads, `redis` is the job broker.
Downloads land in `./data/boards/<slug>/`; the SQLite db in `./data/pinchive.db`.

## Private boards

1. Log in to pinterest.com in your browser.
2. Export cookies with a "Get cookies.txt" extension (or a JSON cookie exporter).
3. **Credentials → paste → Save & validate.**
4. Pick that session when adding a private board.

Only `_pinterest_sess` is strictly required.

**Keep-alive rotation.** Every `PINCHIVE_REFRESH_EVERY_HOURS` (default 6) the
worker makes an authenticated request per credential and **persists the rotated
`_pinterest_sess` back to disk** — so a registered session stays alive on its
own as long as Pinterest keeps sliding it, no re-pasting. Liveness is judged by
the page's `is_authenticated` flag; a session that has genuinely been logged out
server-side is flagged expired in the UI (and only a full re-login can recover
it — see below).

### Optional Playwright re-login fallback

For sessions that die server-side (rotation can't help there), an opt-in
headless-browser re-login can mint fresh cookies. Off by default. Enable via two
`.env` switches and a rebuild:

```dotenv
INSTALL_PLAYWRIGHT=true                 # build: bake chromium into the image
PINCHIVE_USE_PLAYWRIGHT_FALLBACK=true   # run:  attempt re-login on a dead session
```

Best-effort only — captcha/2FA will defeat it, and it stores a password on disk.
Full setup (login profile, local install, switch matrix):
**[docs/credential-refresh.md](docs/credential-refresh.md)**.

## Local dev (no Docker)

```bash
python -m venv .venv && . .venv/Scripts/activate   # PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
powershell -File scripts/fetch_assets.ps1          # htmx + fonts (fonts optional)
redis-server &                                     # or a container
uvicorn app.main:app --reload
arq app.tasks.WorkerSettings                        # separate terminal
```

## Configuration

All env vars are `PINCHIVE_*` — see [.env.example](.env.example).
Key ones: `MAX_CONCURRENCY` (parallel downloads), `DL_SLEEP` (politeness delay),
`REFRESH_HOUR`/`REFRESH_MINUTE` (session re-check cron).

## Layout

```
app/
  main.py         FastAPI routes + HTMX partials
  tasks.py        arq worker: download_board, refresh cron
  downloader.py   gallery-dl subprocess wrapper + progress parsing
  auth.py         cookie normalise (Netscape/JSON) + liveness check
  models.py       Board / Pin / Credential
templates/  static/   Jinja2 + design-token CSS
```

## Notes

- gallery-dl uses a shared `--download-archive`, so re-syncing a board only
  fetches *new* pins.
- No shadows anywhere — depth is pure canvas/card contrast, per the design system.
- ffmpeg is bundled in the image for muxing video pins.

MIT.
