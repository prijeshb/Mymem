"""FastMCP middleware enforcing the per-request bearer token (ADR-017, F3).

Attached only when a token is configured (the remote HTTP transport). It reads the
incoming `Authorization` header and rejects any request that does not present the
valid token — closing the gap where the token only gated *starting* the server.
Local stdio/in-memory requests carry no HTTP headers and pass through (not
network-reachable). Imports `fastmcp`, so it is loaded lazily by `server.py`.
"""
from __future__ import annotations

from typing import Any

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

from mymem.interop.mcp.auth import authorize_request


class BearerAuthMiddleware(Middleware):
    """Reject every request that lacks a valid bearer token (HTTP transport only)."""

    def __init__(self, expected_token: str) -> None:
        self._expected = expected_token

    async def on_request(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        # include_all=True so the (normally filtered) Authorization header is visible.
        authorize_request(get_http_headers(include_all=True), self._expected)
        return await call_next(context)
