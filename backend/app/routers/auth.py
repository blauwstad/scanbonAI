"""
Authentication router for ScanbonAI.

Endpoints
---------
POST /api/v1/auth/register       – Create a new account.
POST /api/v1/auth/login          – Log in with phone + password.
GET  /api/v1/auth/me             – Return the currently authenticated user.
POST /api/v1/auth/magic-link     – Request a magic link sent as a WhatsApp message.
GET  /api/v1/auth/verify/{token} – Exchange a magic-link token for a session.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, DBSession, get_current_user
from app.models import Tenant, User, UserRole
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


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=7, max_length=20, description="Phone in E.164 format")
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field(default="user", pattern=r"^(user|admin)$")
    admin_invite_code: str | None = Field(default=None, description="Required for admin registration")


class LoginRequest(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20)
    password: str = Field(..., min_length=1, max_length=128)


class AuthResponse(BaseModel):
    user: UserSchema
    access_token: str
    refresh_token: str
    expires_at: str


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=SuccessResponse[AuthResponse],
    summary="Create a new account",
)
async def register(
    body: RegisterRequest,
    db: DBSession,
) -> SuccessResponse[AuthResponse]:
    """
    Register a new user account with phone + password.

    For admin registration, a valid ``admin_invite_code`` must be provided
    matching the server's ADMIN_INVITE_CODE environment variable.
    """
    log = logger.bind(phone=body.phone[:4] + "****", role=body.role)

    # Validate admin invite code
    if body.role == "admin":
        if not settings.ADMIN_INVITE_CODE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin registration is disabled.",
            )
        if body.admin_invite_code != settings.ADMIN_INVITE_CODE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid admin invite code.",
            )

    # Resolve default tenant (first tenant in DB)
    tenant_result = await db.execute(select(Tenant).limit(1))
    tenant: Tenant | None = tenant_result.scalars().first()

    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No tenant configured. Please run seed script first.",
        )

    # Check for existing user with same phone in this tenant
    existing = await db.execute(
        select(User).where(
            User.whatsapp_phone == body.phone,
            User.tenant_id == tenant.id,
        )
    )
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this phone number already exists.",
        )

    # Create user
    auth_token = secrets.token_urlsafe(48)
    user_role = UserRole.ADMIN if body.role == "admin" else UserRole.USER
    user = User(
        tenant_id=tenant.id,
        whatsapp_phone=body.phone,
        display_name=body.name,
        role=user_role,
        password_hash=_hash_password(body.password),
        auth_token=auth_token,
    )
    db.add(user)
    await db.flush()

    log.info("auth.user.registered", user_id=user.id)

    expires = datetime.now(tz=timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)

    return SuccessResponse(
        data=AuthResponse(
            user=UserSchema.model_validate(user),
            access_token=auth_token,
            refresh_token=f"refresh-{user.id[:8]}",
            expires_at=expires.isoformat(),
        )
    )


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=SuccessResponse[AuthResponse],
    summary="Log in with phone and password",
)
async def login(
    body: LoginRequest,
    db: DBSession,
) -> SuccessResponse[AuthResponse]:
    """
    Authenticate with phone number and password.
    Returns access and refresh tokens on success.
    """
    log = logger.bind(phone=body.phone[:4] + "****")

    result = await db.execute(
        select(User).where(User.whatsapp_phone == body.phone)
    )
    user: User | None = result.scalars().first()

    if user is None or not user.password_hash:
        log.warning("auth.login.failed", reason="user_not_found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password.",
        )

    if not _verify_password(body.password, user.password_hash):
        log.warning("auth.login.failed", reason="bad_password", user_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password.",
        )

    # Issue new session token
    auth_token = secrets.token_urlsafe(48)
    user.auth_token = auth_token

    log.info("auth.login.success", user_id=user.id)

    expires = datetime.now(tz=timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)

    return SuccessResponse(
        data=AuthResponse(
            user=UserSchema.model_validate(user),
            access_token=auth_token,
            refresh_token=f"refresh-{user.id[:8]}",
            expires_at=expires.isoformat(),
        )
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


# ---------------------------------------------------------------------------
# POST /api/v1/auth/magic-link  (kept for future WhatsApp integration)
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
    """
    log = logger.bind(
        phone=body.phone_number[:4] + "****",
        tenant_slug=body.tenant_slug,
    )

    # Resolve tenant
    tenant_result = await db.execute(
        select(Tenant).where(
            Tenant.name == body.tenant_slug,
        )
    )
    tenant: Tenant | None = tenant_result.scalars().first()

    if tenant is None:
        log.warning("auth.magic_link.tenant_not_found")
        return SuccessResponse(data=MagicLinkResponse())

    # Resolve or create user
    user_result = await db.execute(
        select(User).where(
            User.whatsapp_phone == body.phone_number,
            User.tenant_id == tenant.id,
        )
    )
    user: User | None = user_result.scalars().first()

    if user is None:
        user = User(
            tenant_id=tenant.id,
            whatsapp_phone=body.phone_number,
        )
        db.add(user)
        await db.flush()
        log.info("auth.user.created", user_id=user.id)

    # Generate magic-link token
    serializer = _get_magic_link_serializer()
    token_payload = {"user_id": user.id, "tenant_id": tenant.id}
    magic_token: str = serializer.dumps(token_payload)

    base_url = "https://demo.qlickz.com"
    magic_url = f"{base_url}/api/v1/auth/verify/{magic_token}"

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

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
        )
    )
    user: User | None = result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive.",
        )

    raw_session_token = secrets.token_urlsafe(48)
    session_expires_at = datetime.now(tz=timezone.utc) + timedelta(
        days=_SESSION_TTL_DAYS
    )

    user.auth_token = raw_session_token

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
