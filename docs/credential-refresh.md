# Credential refresh & the Playwright fallback

Pinchive keeps a registered Pinterest session usable over time with **two
layers**. Layer 1 is on by default and covers the common case; layer 2 is an
opt-in fallback for when a session is genuinely dead.

## Layer 1 — keep-alive cookie rotation (default, on)

On the `PINCHIVE_REFRESH_CRON` schedule (default `0 */6 * * *`) the worker, for
each stored credential:

1. makes an authenticated request to Pinterest with the stored cookies,
2. lets Pinterest rotate `_pinterest_sess` via `Set-Cookie` (sliding session),
3. **persists the rotated cookie back** to `data/cookies/<id>.txt` — but only
   when the session is confirmed live (judged by the page's `is_authenticated`
   flag, not the URL).

So a session that Pinterest keeps sliding stays alive indefinitely with no
re-pasting. The manual **Validate** button and registering a new credential run
the same keep-alive immediately.

**Limits.** This cannot resurrect a session that Pinterest has invalidated
server-side (explicit logout, password change, long inactivity, security
challenge). Those get flagged `expired` in the UI. Recovering them needs a fresh
login — that's layer 2.

## Layer 2 — Playwright re-login fallback (opt-in, off)

When enabled, a credential that layer 1 finds dead triggers a **headless browser
re-login** to mint new cookies from a stored username/password.

> ⚠️ Best-effort only. Automated login is fragile: Pinterest changes its markup,
> and captcha / 2FA / security challenges will defeat it. It also means storing
> a password on disk. Treat it as a convenience, not a guarantee.

### Enable it (Docker)

The fallback needs chromium, which only ships in the **`:playwright`** image
variant (the default `:latest` is slim). Two ways to get it:

**Pull the published variant** (no local build):

```dotenv
# .env
PINCHIVE_IMAGE=ghcr.io/aroxu/pinchive:playwright
PINCHIVE_USE_PLAYWRIGHT_FALLBACK=true
```
```bash
docker compose pull && docker compose up -d
```

**Or build it locally:**

```dotenv
# .env
INSTALL_PLAYWRIGHT=true                  # bake chromium into the local build
PINCHIVE_USE_PLAYWRIGHT_FALLBACK=true
```
```bash
docker compose up --build -d
```

Chromium + its OS deps add ~400 MB (installed to `/ms-playwright`). The slim
image simply keeps the fallback a no-op. Verified end-to-end on a real deploy:
chromium drives Pinterest's login form; a failed login (bad creds / captcha /
2FA) now returns without saving cookies, so it never clobbers good state.

### Provide the login profile

For each credential id `<id>` that should auto re-login, drop a file next to its
cookies:

```
data/cookies/<id>.login.json
```

```json
{ "account": "you@example.com", "password": "…", "otp_secret": null }
```

The id is shown implicitly by the credential's cookie file
(`data/cookies/<id>.txt`). Keep this file readable only by you.

### Enable it (local / no Docker)

```bash
pip install ".[refresh]"
python -m playwright install chromium
export PINCHIVE_USE_PLAYWRIGHT_FALLBACK=true   # PowerShell: $env:PINCHIVE_USE_PLAYWRIGHT_FALLBACK="true"
```

## Which switch does what

| Switch | Phase | Effect | Default |
|---|---|---|---|
| `PINCHIVE_REFRESH_CRON` | run | keep-alive schedule (layer 1) | `0 */6 * * *` |
| `INSTALL_PLAYWRIGHT` | build | put chromium + `refresh` extra in the image | `false` |
| `PINCHIVE_USE_PLAYWRIGHT_FALLBACK` | run | attempt re-login on a dead session | `false` |

If `PINCHIVE_USE_PLAYWRIGHT_FALLBACK=true` but the image was built without
`INSTALL_PLAYWRIGHT`, the import fails softly and the fallback is skipped — the
credential just stays flagged `expired`.

Implementation: [app/refresh_browser.py](../app/refresh_browser.py),
[app/auth.py](../app/auth.py) (`refresh_session`), [app/tasks.py](../app/tasks.py).
