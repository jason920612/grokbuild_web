"""Access gate.

Requests that arrive from outside the host — through the Cloudflare tunnel (they
carry ``CF-*`` headers even though cloudflared connects to loopback) or from
another LAN device — must present the access token, either as ``?key=<token>``
(which then sets a cookie) or via the cookie itself. Genuine local loopback
requests (no CF headers) are exempt so the host browser just works.

Anyone who reaches the public URL without the token gets 403.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection, Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp

from . import config

_LOOPBACK = {"127.0.0.1", "::1", "localhost", None}


def is_external(conn: HTTPConnection) -> bool:
    """True if the request came from the tunnel or a non-loopback client."""
    h = conn.headers
    if "cf-ray" in h or "cf-connecting-ip" in h or "x-forwarded-for" in h:
        return True
    client = conn.client.host if conn.client else None
    return client not in _LOOPBACK


def has_valid_token(conn: HTTPConnection) -> bool:
    key = conn.query_params.get("key") or conn.cookies.get(config.COOKIE_NAME)
    return bool(key) and _consteq(key, config.ACCESS_TOKEN)


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


class AccessGate(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not is_external(request) or has_valid_token(request):
            response = await call_next(request)
            # Persist the token as a cookie once it arrives via the query string.
            if request.query_params.get("key") and has_valid_token(request):
                response.set_cookie(
                    config.COOKIE_NAME,
                    config.ACCESS_TOKEN,
                    max_age=60 * 60 * 24 * 30,
                    httponly=True,
                    samesite="lax",
                    path="/",
                )
            return response
        return PlainTextResponse(
            "403 — 需要有效的存取連結（請掃描終端機顯示的 QR code）。",
            status_code=403,
        )


def add_gate(app: ASGIApp) -> None:
    app.add_middleware(AccessGate)
