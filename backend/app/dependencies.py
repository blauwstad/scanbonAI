"""
FastAPI dependency providers for ScanbonAI.

Usage
-----
Inject these into route function signatures with ``Depends``:

    @router.get("/invoices")
    async def list_invoices(
        db: AsyncSession = Depends(get_db_session),
        current_user: User = Depends(get_current_user),
    ) -> ...:
        ...
"""

from __future__ import annotations

import hashlib
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Tenant, User, UserRole

logger = structlog.get_logger(__name__)

# Bearer token extractor (Authorization: Bearer <token>)
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------


async def get_db_session() -> AsyncSession:  # type: ignore[return]
    """
    Yield a database session for the duration of a single request.

    The session is automatically committed on clean exit and rolled back on
    exception.  Do not call ``session.commit()`` inside route handlers unless
    you have a specific reason to flush partial state.
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for cleaner route signatures
DBSession = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------


def _hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of a raw session token."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """
    Resolve the authenticated user from the Bearer token.

    The token is compared directly against ``users.auth_token``
    (stored as raw token for demo simplicity).

    Raises ``401 Unauthorized`` if:
    - No Authorization header is present.
    - The token does not match any user.

    Sets ``request.state.tenant_id`` from the resolved user so that
    ``TenantIsolationMiddleware`` has the correct value for log context.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(
        select(User).where(
            User.auth_token == credentials.credentials,
        )
    )
    user: User | None = result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Propagate tenant context for middleware log binding
    request.state.tenant_id = user.tenant_id
    structlog.contextvars.bind_contextvars(
        user_id=user.id, tenant_id=user.tenant_id
    )

    return user


# Type alias for route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Raise ``403 Forbidden`` if the authenticated user is not an admin.

    Use as a dependency on admin-only routes:

        @router.get("/admin/invoices")
        async def list_all(admin: User = Depends(require_admin)) -> ...:
            ...
    """
    if current_user.role not in (UserRole.ADMIN, "admin"):
        logger.warning(
            "admin_access_denied",
            user_id=current_user.id,
            tenant_id=current_user.tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges are required to access this resource.",
        )
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]


async def require_tenant_access(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Tenant:
    """
    Validate that:
    1. The current user's ``tenant_id`` matches the tenant in ``request.state``.
    2. The tenant record exists.

    This dependency performs a single DB lookup per request and is the
    canonical place to enforce tenant isolation at the API layer.

    Returns the ``Tenant`` ORM object so routes can access tenant settings.
    """
    tenant_id = current_user.tenant_id

    result = await db.execute(
        select(Tenant).where(
            Tenant.id == tenant_id,
        )
    )
    tenant: Tenant | None = result.scalars().first()

    if tenant is None:
        logger.error(
            "tenant_not_found_or_inactive",
            tenant_id=tenant_id,
            user_id=current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant account is inactive or does not exist.",
        )

    return tenant


TenantAccess = Annotated[Tenant, Depends(require_tenant_access)]
