"""
WhatsApp admin settings router for ScanbonAI.

Endpoints
---------
GET  /api/v1/admin/whatsapp-settings             - Get current tenant's WhatsApp settings.
POST /api/v1/admin/whatsapp-settings             - Create WhatsApp settings for the tenant.
PUT  /api/v1/admin/whatsapp-settings             - Update existing WhatsApp settings.
POST /api/v1/admin/whatsapp-settings/test        - Test the WhatsApp connection.
GET  /api/v1/admin/whatsapp-settings/webhook-info - Get webhook URL and verify token.

Access control
--------------
All endpoints require ``require_admin`` which checks ``user.role == admin``.
Tenant isolation is enforced -- admins can only manage settings for their own tenant.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import AdminUser, DBSession, require_admin
from app.models import WhatsAppSettings
from app.schemas import SuccessResponse
from app.services.whatsapp import decrypt_token, encrypt_token, test_connection

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class WhatsAppSettingsRequest(BaseModel):
    """Payload for creating WhatsApp settings."""

    phone_number_id: str = Field(..., min_length=1, max_length=50)
    access_token: str = Field(..., min_length=1)
    display_phone_number: str | None = None
    waba_id: str | None = None
    meta_app_id: str | None = None


class WhatsAppSettingsUpdateRequest(BaseModel):
    """Payload for updating WhatsApp settings. All fields are optional."""

    phone_number_id: str | None = None
    access_token: str | None = None
    display_phone_number: str | None = None
    waba_id: str | None = None
    meta_app_id: str | None = None
    is_active: bool | None = None


class WhatsAppSettingsResponse(BaseModel):
    """Public representation of WhatsApp settings (access token is masked)."""

    id: str
    tenant_id: str
    phone_number_id: str
    display_phone_number: str | None
    waba_id: str | None
    meta_app_id: str | None
    access_token_masked: str  # e.g. "****abcd"
    webhook_verify_token: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebhookInfoResponse(BaseModel):
    """Webhook configuration info for the Meta Developer Console."""

    webhook_url: str
    verify_token: str
    graph_api_version: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_token(encrypted_token: str) -> str:
    """
    Decrypt and mask an access token, showing only the last 4 characters.

    Falls back to a generic mask if decryption fails (e.g. key rotation).
    """
    try:
        plaintext = decrypt_token(encrypted_token)
        if len(plaintext) >= 4:
            return "****" + plaintext[-4:]
        return "****"
    except Exception:
        return "****"


def _settings_to_response(ws: WhatsAppSettings) -> WhatsAppSettingsResponse:
    """Convert a WhatsAppSettings ORM instance to the API response model."""
    return WhatsAppSettingsResponse(
        id=ws.id,
        tenant_id=ws.tenant_id,
        phone_number_id=ws.phone_number_id,
        display_phone_number=ws.display_phone_number,
        waba_id=ws.waba_id,
        meta_app_id=ws.meta_app_id,
        access_token_masked=_mask_token(ws.access_token_encrypted),
        webhook_verify_token=ws.webhook_verify_token,
        is_active=ws.is_active,
        created_at=ws.created_at,
        updated_at=ws.updated_at,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


router = APIRouter(
    prefix="/api/v1/admin/whatsapp-settings",
    tags=["admin-whatsapp"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/whatsapp-settings
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessResponse[WhatsAppSettingsResponse | None],
    summary="Get WhatsApp settings for the current tenant",
)
async def get_whatsapp_settings(
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[WhatsAppSettingsResponse | None]:
    """
    Retrieve the current tenant's WhatsApp Cloud API settings.

    The access token is masked -- only the last 4 characters are shown.
    Returns ``null`` data if no settings have been configured yet.
    """
    result = await db.execute(
        select(WhatsAppSettings).where(
            WhatsAppSettings.tenant_id == admin_user.tenant_id,
        )
    )
    ws: WhatsAppSettings | None = result.scalars().first()

    if ws is None:
        return SuccessResponse(data=None)

    return SuccessResponse(data=_settings_to_response(ws))


# ---------------------------------------------------------------------------
# POST /api/v1/admin/whatsapp-settings
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=SuccessResponse[WhatsAppSettingsResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create WhatsApp settings for the current tenant",
)
async def create_whatsapp_settings(
    body: WhatsAppSettingsRequest,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[WhatsAppSettingsResponse]:
    """
    Create WhatsApp Cloud API settings for the admin's tenant.

    The access token is encrypted before storage. A random
    ``webhook_verify_token`` is generated automatically.

    Returns 409 if settings already exist for this tenant.
    """
    # Check for existing settings
    existing_result = await db.execute(
        select(WhatsAppSettings).where(
            WhatsAppSettings.tenant_id == admin_user.tenant_id,
        )
    )
    if existing_result.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="WhatsApp settings already exist for this tenant. Use PUT to update.",
        )

    encrypted_token = encrypt_token(body.access_token)
    verify_token = secrets.token_urlsafe(32)

    ws = WhatsAppSettings(
        tenant_id=admin_user.tenant_id,
        phone_number_id=body.phone_number_id,
        display_phone_number=body.display_phone_number,
        waba_id=body.waba_id,
        meta_app_id=body.meta_app_id,
        access_token_encrypted=encrypted_token,
        webhook_verify_token=verify_token,
        is_active=True,
    )
    db.add(ws)
    await db.flush()

    logger.info(
        "whatsapp_settings.created",
        tenant_id=admin_user.tenant_id,
        phone_number_id=body.phone_number_id,
        admin_id=admin_user.id,
    )

    return SuccessResponse(data=_settings_to_response(ws))


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/whatsapp-settings
# ---------------------------------------------------------------------------


@router.put(
    "",
    response_model=SuccessResponse[WhatsAppSettingsResponse],
    summary="Update WhatsApp settings for the current tenant",
)
async def update_whatsapp_settings(
    body: WhatsAppSettingsUpdateRequest,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[WhatsAppSettingsResponse]:
    """
    Update existing WhatsApp Cloud API settings for the admin's tenant.

    All fields are optional -- only provided fields are updated.
    If a new ``access_token`` is provided it is re-encrypted before storage.

    Returns 404 if no settings exist for this tenant.
    """
    result = await db.execute(
        select(WhatsAppSettings).where(
            WhatsAppSettings.tenant_id == admin_user.tenant_id,
        )
    )
    ws: WhatsAppSettings | None = result.scalars().first()

    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No WhatsApp settings found for this tenant. Use POST to create.",
        )

    # Apply only the fields that were explicitly provided
    if body.phone_number_id is not None:
        ws.phone_number_id = body.phone_number_id
    if body.access_token is not None:
        ws.access_token_encrypted = encrypt_token(body.access_token)
    if body.display_phone_number is not None:
        ws.display_phone_number = body.display_phone_number
    if body.waba_id is not None:
        ws.waba_id = body.waba_id
    if body.meta_app_id is not None:
        ws.meta_app_id = body.meta_app_id
    if body.is_active is not None:
        ws.is_active = body.is_active

    await db.flush()

    logger.info(
        "whatsapp_settings.updated",
        tenant_id=admin_user.tenant_id,
        admin_id=admin_user.id,
    )

    return SuccessResponse(data=_settings_to_response(ws))


# ---------------------------------------------------------------------------
# POST /api/v1/admin/whatsapp-settings/test
# ---------------------------------------------------------------------------


@router.post(
    "/test",
    response_model=SuccessResponse[dict],
    summary="Test the WhatsApp connection",
)
async def test_whatsapp_connection(
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict]:
    """
    Test the stored WhatsApp Cloud API credentials.

    Decrypts the access token and calls the Meta Graph API to verify
    the phone number ID is reachable and the token is valid.
    """
    result = await db.execute(
        select(WhatsAppSettings).where(
            WhatsAppSettings.tenant_id == admin_user.tenant_id,
        )
    )
    ws: WhatsAppSettings | None = result.scalars().first()

    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No WhatsApp settings found for this tenant.",
        )

    if not ws.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WhatsApp settings are currently disabled.",
        )

    try:
        access_token = decrypt_token(ws.access_token_encrypted)
    except Exception as exc:
        logger.error(
            "whatsapp_settings.decrypt_failed",
            tenant_id=admin_user.tenant_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt access token. The encryption key may have changed.",
        )

    try:
        test_result = await test_connection(access_token, ws.phone_number_id)
    except Exception as exc:
        logger.error(
            "whatsapp_settings.test_failed",
            tenant_id=admin_user.tenant_id,
            error=str(exc),
        )
        return SuccessResponse(
            data={
                "status": "error",
                "message": f"Connection test failed: {exc}",
            }
        )

    logger.info(
        "whatsapp_settings.test_success",
        tenant_id=admin_user.tenant_id,
        admin_id=admin_user.id,
    )

    return SuccessResponse(data=test_result)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/whatsapp-settings/webhook-info
# ---------------------------------------------------------------------------


@router.get(
    "/webhook-info",
    response_model=SuccessResponse[WebhookInfoResponse],
    summary="Get webhook configuration info",
)
async def get_webhook_info(
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[WebhookInfoResponse]:
    """
    Return the webhook URL and verify token needed to configure the
    WhatsApp webhook in the Meta Developer Console.

    The webhook URL is derived from ``PUBLIC_BASE_URL`` in the app config.
    """
    result = await db.execute(
        select(WhatsAppSettings).where(
            WhatsAppSettings.tenant_id == admin_user.tenant_id,
        )
    )
    ws: WhatsAppSettings | None = result.scalars().first()

    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No WhatsApp settings found for this tenant. Create settings first.",
        )

    webhook_url = f"{settings.PUBLIC_BASE_URL}/hook/whatsapp"

    return SuccessResponse(
        data=WebhookInfoResponse(
            webhook_url=webhook_url,
            verify_token=ws.webhook_verify_token,
            graph_api_version=settings.META_GRAPH_API_VERSION,
        )
    )
