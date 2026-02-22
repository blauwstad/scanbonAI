"""
Custom ASGI middleware for ScanbonAI.

Middleware stack (outermost → innermost):
  CorrelationIDMiddleware   – injects X-Request-ID into every request/response
  TenantIsolationMiddleware – resolves tenant from JWT claim / header and
                              attaches it to request.state for downstream use
"""

from __future__ import annotations

import uuid
from typing import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger(__name__)

# Header names
X_REQUEST_ID = "X-Request-ID"
X_TENANT_ID = "X-Tenant-ID"


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Injects a correlation ID into every request/response cycle.

    If the client sends an ``X-Request-ID`` header its value is reused;
    otherwise a new UUID4 is generated.  The ID is bound to the structlog
    context so every log line emitted during the request carries it.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(X_REQUEST_ID) or str(uuid.uuid4())

        # Bind to structlog context for this async task
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Make the ID available on request.state for route handlers
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[X_REQUEST_ID] = request_id
        return response


class TenantIsolationMiddleware(BaseHTTPMiddleware):
    """
    Resolves and validates the current tenant on every request.

    Resolution order:
    1. ``tenant_id`` claim inside the decoded JWT (set by ``get_current_user``
       dependency after authentication).
    2. ``X-Tenant-ID`` request header (used by internal services and tests).
    3. None – unauthenticated / pre-auth routes (e.g. /hook/whatsapp, /health).

    The resolved tenant_id is stored on ``request.state.tenant_id`` so that
    every query layer can enforce row-level isolation without re-parsing the
    token.

    IMPORTANT: This middleware does **not** validate the tenant against the
    database on every request (that would add a DB round-trip per call).
    Validation happens in the ``require_tenant_access`` dependency at the
    route level.
    """

    # Routes that are exempt from tenant resolution (public endpoints)
    _PUBLIC_PREFIXES: tuple[str, ...] = (
        "/health",
        "/hook/",
        "/metrics",
        "/api/v1/auth/",
    )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Skip public routes
        path = request.url.path
        is_public = any(path.startswith(prefix) for prefix in self._PUBLIC_PREFIXES)

        if is_public:
            request.state.tenant_id = None
            return await call_next(request)

        # 1. Check if a previous middleware/dependency has already set it
        tenant_id: str | None = getattr(request.state, "tenant_id", None)

        # 2. Fall back to explicit header (service-to-service calls)
        if not tenant_id:
            tenant_id = request.headers.get(X_TENANT_ID)

        if tenant_id:
            structlog.contextvars.bind_contextvars(tenant_id=tenant_id)

        request.state.tenant_id = tenant_id

        response = await call_next(request)
        return response
