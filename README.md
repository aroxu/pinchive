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

## Docker images

Two published variants (built from the one Dockerfile via
[docker-bake.hcl](docker-bake.hcl), pushed to GHCR by
[the CI workflow](.github/workflows/docker-publish.yml)):

| Tag | Contents | Size | Use |
|---|---|---|---|
| `ghcr.io/aroxu/pinchive:latest` | slim (no browser) | ~1 GB | default |
| `ghcr.io/aroxu/pinchive:playwright` | + chromium | ~1.4 GB | auto re-login fallback |

Pull instead of building by setting `PINCHIVE_IMAGE` in `.env`, then
`docker compose pull && docker compose up -d`. Build both locally with
`docker buildx bake`.

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

## Organizing your archive

- **Search & filter.** Filter boards by text, status, or tag; filter pins inside
  a board by text, media type, or "duplicates only".
- **Sort & view.** Order pins by newest / oldest / largest / smallest / name,
  and switch thumbnail size (small / medium / large).
- **Tags.** Free-form labels on both boards and pins (`+ tag` inputs); click a
  tag or use the dropdown to filter.
- **Bulk actions.** Select pins (checkboxes / select-all) and add a tag, remove
  a tag, or delete them in one go from the board's bulk bar.
- **Duplicate detection.** The **Duplicates** page finds the same image across
  pins/boards — both exact byte matches (SHA-256) and visually identical
  re-encodes/resizes (64-bit perceptual dHash, Pillow). Each group marks the
  highest-resolution copy **KEEP** and pre-selects the rest; one click removes
  the extra files from disk. Detection is non-destructive until you confirm.
- **Pagination.** Board list, pin grids, and the Duplicates page paginate
  (sizes configurable via `PINCHIVE_PER_PAGE_*`).
- **Automatic re-sync.** A cron re-downloads boards every
  `PINCHIVE_RESYNC_EVERY_HOURS` (default 24; `0` disables) to pull new pins —
  cheap, since the per-board archive only fetches new ones. Toggle **auto-sync**
  per board from its card to opt individual boards out.
- **Multilingual UI** (English / 한국어). Auto-detects from `Accept-Language`;
  switch from the nav (choice persists in a cookie). Add a language by extending
  the catalogs in [app/i18n.py](app/i18n.py) — no gettext toolchain.

Each board keeps its **own** `--download-archive`, so a board stays a faithful
mirror (a pin shared across boards downloads into each) while re-syncing still
fetches only new pins — redundancy is surfaced by the Duplicates view, not
silently dropped.

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
`REFRESH_EVERY_HOURS` (session keep-alive cron), `RESYNC_EVERY_HOURS` (board
auto-resync cron; `0` disables), `PER_PAGE_BOARDS`/`PER_PAGE_PINS`/`PER_PAGE_DUPES`.

## Layout

```
app/
  main.py         FastAPI routes + HTMX partials
  tasks.py        arq worker: download_board, refresh cron
  downloader.py   gallery-dl subprocess wrapper + progress parsing
  auth.py         cookie normalise (Netscape/JSON) + liveness check
  dedup.py        sha256 + perceptual dHash + duplicate grouping
  models.py       Board / Pin / Credential / Tag
templates/  static/   Jinja2 + design-token CSS
```

## Notes

- Each board keeps its own per-board `--download-archive`, so re-syncing a board
  only fetches *new* pins.
- No shadows anywhere — depth is pure canvas/card contrast, per the design system.
- ffmpeg is bundled in the image for muxing video pins.

MIT.
