"""Bearer-token gate for the remote MCP transport (ADR-017).

Fail-closed: local stdio transport needs no token; the HTTP/SSE transport refuses
to serve unless a token is configured AND matches. Phase 1 grants only the READ
scope; the WRITE scope is reserved for the Phase 2 contribute tools so a read token
can never reach a write tool. Pure logic — the env read happens in the CLI/server.
"""
from __future__ import annotations

import hmac
from collections.abc import Mapping
from enum import StrEnum


class Scope(StrEnum):
    READ = "read"
    WRITE = "write"


class AuthError(Exception):
    """Raised when a remote MCP request fails authentication (fail-closed)."""


def check_token(provided: str | None, expected: str | None) -> Scope:
    """Validate a bearer token in constant time and return its scope.

    Raises ``AuthError`` if no token is configured (``expected`` falsy) — this is
    the fail-closed guard for remote transport — or if ``provided`` is missing or
    does not match. Phase 1 always yields ``Scope.READ`` on success.
    """
    if not expected:
        raise AuthError("remote MCP transport requires MYMEM_MCP_TOKEN to be set")
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthError("invalid or missing MCP bearer token")
    return Scope.READ


def extract_bearer(headers: Mapping[str, str]) -> str | None:
    """Pull a bearer token from an HTTP `Authorization` header (case-insensitive).

    Accepts `Authorization: Bearer <token>` and tolerates a bare `<token>` value.
    Returns None when the header is absent.
    """
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return raw.strip()


def authorize_request(headers: Mapping[str, str], expected_token: str | None) -> Scope:
    """Per-request authorization for the MCP transports.

    Local transports (stdio / in-memory) carry **no HTTP headers** and are not
    network-reachable, so they are allowed without a token. Any request that *does*
    arrive with HTTP headers (the remote transport) must present a valid bearer token
    — `check_token` raises `AuthError` on a missing/mismatched/unset token.
    """
    if not headers:
        return Scope.READ
    return check_token(extract_bearer(headers), expected_token)
