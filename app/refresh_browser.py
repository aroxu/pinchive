"""Optional Playwright-based automatic re-login.

This module is imported lazily by app.tasks only when
PINCHIVE_USE_PLAYWRIGHT_FALLBACK is true. It is a scaffold: storing an account
password and driving a headless login is inherently fragile (Pinterest changes
markup, adds captchas / 2FA), so treat this as a best-effort convenience, not a
guarantee.

Requires an image built with INSTALL_PLAYWRIGHT=true (or, locally,
`pip install .[refresh] && playwright install chromium`).
Passwords, if used, are read from  {cookies_dir}/{cred_id}.login.json
    {"account": "...", "password": "...", "otp_secret": null}
"""

from __future__ import annotations

import json

from app import auth
from app.config import get_settings

settings = get_settings()


async def relogin(cred_id: int) -> bool:
    """Attempt a headless re-login. Returns True ONLY if the session is verified
    authenticated afterwards — a failed login (wrong creds, captcha, 2FA) does
    not save cookies or report success, so it never clobbers good state.

    Note: Pinterest's login is a client-rendered SPA; `page.fill` auto-waits for
    the form to render, so we don't need an explicit wait for `#email`.
    """
    login_file = settings.cookies_dir / f"{cred_id}.login.json"
    if not login_file.exists():
        return False
    creds = json.loads(login_file.read_text(encoding="utf-8"))
    account = creds.get("account")
    password = creds.get("password")
    if not account or not password:
        return False

    from playwright.async_api import TimeoutError as PWTimeout  # lazy import
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(user_agent=auth._UA)
            page = await ctx.new_page()
            await page.goto(
                "https://www.pinterest.com/login/",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            await page.fill("#email", account, timeout=30000)
            await page.fill("#password", password, timeout=15000)
            await page.click("button[type=submit]", timeout=15000)
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except PWTimeout:
                pass  # networkidle is flaky; the auth check below is what matters

            if not await _is_authenticated(page):
                return False  # login didn't take — leave existing cookies alone

            cookies = await ctx.cookies()
            payload = json.dumps(
                [
                    {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c["path"],
                        "secure": c.get("secure", False),
                        "expirationDate": c.get("expires", 0),
                    }
                    for c in cookies
                ]
            )
            auth.save_cookies_text(cred_id, payload)
            return True
        finally:
            await browser.close()


async def _is_authenticated(page) -> bool:
    """Confirm the browser session is logged in by reading Pinterest's
    `is_authenticated` flag off a login-gated page (same signal as auth._classify)."""
    try:
        await page.goto(
            "https://www.pinterest.com/settings/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        body = await page.content()
    except Exception:  # noqa: BLE001
        return False
    return auth._AUTH_TRUE.search(body) is not None
