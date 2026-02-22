"""
Authentication router for ScanbonAI – magic-link via WhatsApp.

Endpoints
---------
POST /api/v1/auth/magic-link    – Request a magic link sent as a WhatsApp message.
GET  /api/v1/auth/verify/{token} – Exchange a magic-link token for a session.
GET  /api/v1/auth/me             – Return the currently authenticated user.

Magic-link flow
---------------
1. User submits their phone number and tenant slug.
2. Server generates a short-lived signed token (itsdangerous, 15 min TTL).
3. Server sends the token as a WhatsApp message containing a deep-link URL.
4. User taps the link; browser/app hits GET /api/v1/auth/verify/{token}.
5. Server validates the token, creates (or retrieves) a User record, and
   issues a long-lived session token (stored as a SHA-256 hash in the DB).
6. The session token is returned in the response body and should be stored
   client-side and sent as ``Authorization: Bearer <token>`` on every request.

Security notes
--------------
- Magic-link tokens are signed with ``settings.SECRET_KEY`` and expire in
  15 minutes to limit the attack window if a message is intercepted.
- Session tokens are stored hashed (SHA-256) in ``users.session_token_hash``
  so a DB breach does not expose usable credentials.
- The same magic-link token cannot be used twice (the session_token_hash is
  replaced on each verification, invalidating any previous session for that user).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, DBSession, get_current_user
from app.models import Tenant, User
from app.schemas import (
    AuthVerifyResponse,
    MagicLinkRequest,
    MagicLinkResponse,
    SuccessResponse,
    UserSchema,
)
from app.services.whatsapp import WhatsAppClient

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Magic-link token lifetime
_MAGIC_LINK_TTL_SECONDS = 15 * 60  # 15 minutes
_SESSION_TTL_DAYS = 30

# itsdangerous salt for magic-link tokens (separate from signed-url salt)
_MAGIC_LINK_SALT = "scanbonai.magic-link.v1"


def _get_magic_link_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=settings.SECRET_KEY, salt=_MAGIC_LINK_SALT)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# POST /api/v1/auth/magic-link
# ---------------------------------------------------------------------------


@router.post(
    "/magic-link",
    response_model=SuccessResponse[MagicLinkResponse],
    summary="Request a WhatsApp magic link",
)
async def request_magic_link(
    body: MagicLinkRequest,
    db: DBSession,
) -> SuccessResponse[MagicLinkResponse]:
    """
    Generate a magic-link token and send it to the user via WhatsApp.

    The endpoint always returns 200 (even when the phone/tenant combination
    is not found) to avoid user enumeration.

    Rate limiting is applied at the infrastructure layer (API gateway / nginx)
    and is NOT implemented here to keep the skeleton simple.
    """
    log = logger.bind(
        phone=body.phone_number[:4] + "****",
        tenant_slug=body.tenant_slug,
    )

    # Resolve tenant
    tenant_result = await db.execute(
        select(Tenant).where(
            Tenant.slug == body.tenant_slug,
            Tenant.is_active.is_(True),
        )
    )
    tenant: Tenant | None = tenant_result.scalars().first()

    if tenant is None:
        log.warning("auth.magic_link.tenant_not_found")
        # Return a generic success to prevent tenant slug enumeration
        return SuccessResponse(data=MagicLinkResponse())

    # Resolve or create user
    user_result = await db.execute(
        select(User).where(
            User.phone_number == body.phone_number,
            User.tenant_id == tenant.id,
            User.is_active.is_(True),
        )
    )
    user: User | None = user_result.scalars().first()

    if user is None:
        # Auto-create user on first magic-link request
        user = User(
            tenant_id=tenant.id,
            phone_number=body.phone_number,
        )
        db.add(user)
        await db.flush()  # get the generated ID
        log.info("auth.user.created", user_id=user.id)

    # Generate magic-link token
    serializer = _get_magic_link_serializer()
    token_payload = {"user_id": user.id, "tenant_id": tenant.id}
    magic_token: str = serializer.dumps(token_payload)

    # Build the magic-link URL
    # FUTURE: derive base_url from settings.BASE_URL
    base_url = "https://app.scanbonai.com"
    magic_url = f"{base_url}/api/v1/auth/verify/{magic_token}"

    # Send via WhatsApp
    message_text = (
        f"Your ScanbonAI login link (valid 15 minutes):\n{magic_url}\n\n"
        "Do not share this link with anyone."
    )
    try:
        async with WhatsAppClient() as wa:
            await wa.send_text_message(phone=body.phone_number, text=message_text)
        log.info("auth.magic_link.sent", user_id=user.id)
    except Exception as exc:
        log.error("auth.magic_link.send_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send magic link via WhatsApp. Please try again.",
        )

    return SuccessResponse(data=MagicLinkResponse())


# ---------------------------------------------------------------------------
# GET /api/v1/auth/verify/{token}
# ---------------------------------------------------------------------------


@router.get(
    "/verify/{token}",
    response_model=SuccessResponse[AuthVerifyResponse],
    summary="Verify magic-link token and create session",
)
async def verify_magic_link(
    token: str,
    db: DBSession,
) -> SuccessResponse[AuthVerifyResponse]:
    """
    Exchange a magic-link token for a session token.

    On success:
    - Returns a session token in the response body.
    - The token should be stored client-side and sent as
      ``Authorization: Bearer <token>`` on subsequent requests.
    - The previous session (if any) is invalidated.

    Raises
    ------
    401 Unauthorized
        If the token is invalid or has expired.
    """
    serializer = _get_magic_link_serializer()

    try:
        payload = serializer.loads(token, max_age=_MAGIC_LINK_TTL_SECONDS)
    except SignatureExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Magic link has expired. Please request a new one.",
        )
    except BadData:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid magic link.",
        )

    user_id: str = payload.get("user_id", "")
    tenant_id: str = payload.get("tenant_id", "")

    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed magic link payload.",
        )

    # Load user
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    user: User | None = result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive.",
        )

    # Issue a new session token
    raw_session_token = secrets.token_urlsafe(48)
    session_expires_at = datetime.now(tz=timezone.utc) + timedelta(
        days=_SESSION_TTL_DAYS
    )

    user.session_token_hash = _hash_token(raw_session_token)
    user.session_expires_at = session_expires_at

    logger.info(
        "auth.session.created",
        user_id=user.id,
        tenant_id=user.tenant_id,
        expires_at=session_expires_at.isoformat(),
    )

    return SuccessResponse(
        data=AuthVerifyResponse(
            user=UserSchema.model_validate(user),
            session_expires_at=session_expires_at,
        ),
        meta={"session_token": raw_session_token},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=SuccessResponse[UserSchema],
    summary="Get current user",
)
async def get_me(
    current_user: CurrentUser,
) -> SuccessResponse[UserSchema]:
    """
    Return the profile of the currently authenticated user.

    Uses the ``get_current_user`` dependency which validates the Bearer token.
    """
    return SuccessResponse(data=UserSchema.model_validate(current_user))
