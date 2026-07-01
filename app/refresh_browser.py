"""Optional Playwright-based automatic re-login.

This module is imported lazily by app.tasks only when PINCHIVE_ENABLE_AUTO_REFRESH
is true. It is a scaffold: storing an account password and driving a headless
login is inherently fragile (Pinterest changes markup, adds captchas / 2FA), so
treat this as a best-effort convenience, not a guarantee.

Requires the `refresh` extra:  pip install .[refresh] && playwright install chromium
Passwords, if used, are read from  {cookies_dir}/{cred_id}.login.json
    {"account": "...", "password": "...", "otp_secret": null}
"""

from __future__ import annotations

import json

from app import auth
from app.config import get_settings

settings = get_settings()


async def relogin(cred_id: int) -> bool:
    login_file = settings.cookies_dir / f"{cred_id}.login.json"
    if not login_file.exists():
        return False
    creds = json.loads(login_file.read_text(encoding="utf-8"))
    account = creds.get("account")
    password = creds.get("password")
    if not account or not password:
        return False

    from playwright.async_api import async_playwright  # lazy import

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto("https://www.pinterest.com/login/", wait_until="domcontentloaded")
            await page.fill("#email", account)
            await page.fill("#password", password)
            await page.click("button[type=submit]")
            await page.wait_for_load_state("networkidle", timeout=30000)

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
