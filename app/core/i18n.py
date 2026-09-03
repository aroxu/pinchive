"""Server-side locale resolution.

The UI catalogue now lives in the React client (``frontend/src/i18n``), which is
where the strings are actually rendered. The server keeps only what it needs to
honour a language choice: resolve the locale per request, expose it to handlers
through a ContextVar, and persist an explicit ``?lang=`` as a cookie so the
choice survives a reload.
"""

from __future__ import annotations

import contextvars

from starlette.datastructures import MutableHeaders
from starlette.requests import Request

DEFAULT = "en"
SUPPORTED = ("en", "ko")
LANG_NAMES = {"en": "EN", "ko": "한국어"}

_current: contextvars.ContextVar[str] = contextvars.ContextVar("locale", default=DEFAULT)


def set_locale(loc: str) -> None:
    _current.set(loc if loc in SUPPORTED else DEFAULT)


def get_locale() -> str:
    return _current.get()


def resolve_locale(request) -> str:
    """?lang= > `lang` cookie > Accept-Language > DEFAULT."""
    q = request.query_params.get("lang")
    if q in SUPPORTED:
        return q
    c = request.cookies.get("lang")
    if c in SUPPORTED:
        return c
    for part in request.headers.get("accept-language", "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in SUPPORTED:
            return code
    return DEFAULT


class LocaleMiddleware:
    """Pure ASGI middleware (NOT BaseHTTPMiddleware) so the ContextVar it sets is
    visible to the endpoint — BaseHTTPMiddleware runs the endpoint in a separate
    task and would drop it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        loc = resolve_locale(request)
        set_locale(loc)
        if request.query_params.get("lang") in SUPPORTED:
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(raw=message.setdefault("headers", []))
                    headers.append(
                        "set-cookie",
                        f"lang={loc}; Max-Age=31536000; Path=/; SameSite=Lax",
                    )
                await send(message)

            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)
