"""Bearer-token authentication for the MCP endpoint (transport checklist T9).

Every HTTP request must carry `Authorization: Bearer <token>`. The token comes from the
ON_TIME_API_TOKEN environment variable (Secrets Manager on AWS). Without a token the server only
runs on a loopback address, so it is never exposed unauthenticated.
"""

import hmac

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class BearerTokenMiddleware:
    """Requires the token on `protected` path prefixes; anything else (the web client's static
    files) is public. Every MCP and agent request is protected."""

    def __init__(self, app: ASGIApp, token: str, protected: tuple[str, ...] = ("/mcp", "/agent")) -> None:
        if not token:
            raise ValueError("token must be non-empty")
        self.app = app
        self._token = token.encode()
        self._protected = protected

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["path"].startswith(self._protected)
            and not self._authorized(Headers(scope=scope))
        ):
            response = JSONResponse(
                {"error": "unauthorized", "error_description": "Missing or invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="on-time"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, headers: Headers) -> bool:
        scheme, _, credentials = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not credentials:
            return False
        return hmac.compare_digest(credentials.strip().encode(), self._token)
