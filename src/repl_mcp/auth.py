"""Authentication middleware for repl-mcp MCP server."""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that checks for a valid Bearer token in the Authorization header."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or not secrets.compare_digest(
            auth_header[7:], self.token
        ):
            return JSONResponse(
                {"error": "Unauthorized", "message": "Invalid or missing bearer token"},
                status_code=401,
            )
        return await call_next(request)


def generate_token() -> str:
    """Generate a secure random token."""
    return secrets.token_urlsafe(32)
