"""HTTP bearer authentication placed outside the MCP protocol stack."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BearerAuthMiddleware:
    """Require one exact bearer token for every HTTP request.

    Comparison is constant-time. The response is deliberately generic and contains neither the
    expected token nor the rejected credential.
    """

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self._expected = f"Bearer {api_key}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"authorization"),
            b"",
        )
        if not hmac.compare_digest(supplied, self._expected):
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Callable[[Message], Awaitable[Any]]) -> None:
        body = b'{"error":"unauthorized","message":"A valid bearer token is required."}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b'Bearer realm="mcp"'),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
