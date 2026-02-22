"""
OpenClaw integration router for ScanbonAI.

Replaces Meta WhatsApp Cloud API with OpenClaw for WhatsApp message handling.
Provides REST endpoints that the OpenClaw agent (Kimi K2.5) calls when
WhatsApp messages arrive.

Agent endpoints (prefix ``/api/v1/openclaw``)
----------------------------------------------
GET    /user/{phone}              - Look up user by phone number
POST   /process-invoice           - Accept and queue an invoice image for processing
GET    /invoice/{invoice_id}/status - Check invoice processing status

Admin endpoints (prefix ``/api/v1/admin/openclaw``)
----------------------------------------------------
GET    /status                    - Check OpenClaw gateway health
POST   /test                      - Explicit gateway connectivity test

Access control
--------------
Agent endpoints require ``require_openclaw_api_key`` (X-API-Key header).
Admin endpoints require ``require_admin`` (Bearer token for an admin user).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import AdminUser, DBSession, require_admin, require_openclaw_api_key
from app.models import (
    ExtractedData,
    Invoice,
    InvoiceStatus,
    RegistrationToken,
    SignedLinkType,
    Tenant,
    User,
    UserStatus,
    WebhookEvent,
    WebhookEventStatus,
)
from app.schemas import SuccessResponse
from app.services.billing import can_process_invoice, get_remaining_credits, get_user_billing_status
from app.services.signed_urls import build_signed_url
from app.workers import celery_app

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class UserLookupResponse(BaseModel):
    exists: bool
    user_id: str | None = None
    phone: str | None = None
    status: str | None = None
    plan_code: str | None = None
    subscription_status: str | None = None
    remaining_credits: int | None = None
    activation_required: bool = False
    activation_url: str | None = None


class ProcessInvoiceRequest(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20, description="E.164 phone number")
    image_base64: str = Field(..., description="Base64-encoded invoice image")
    mime_type: str = Field(..., description="MIME type of the image (e.g. image/jpeg)")
    contact_name: str | None = Field(default=None, description="WhatsApp contact display name")
    message_id: str | None = Field(default=None, description="OpenClaw message ID for idempotency")


class ProcessInvoiceResponse(BaseModel):
    invoice_id: str | None = None
    status: str
    message: str
    activation_url: str | None = None


class InvoiceStatusResponse(BaseModel):
    invoice_id: str
    status: str
    vendor_name: str | None = None
    invoice_number: str | None = None
    total_amount: str | None = None
    invoice_date: str | None = None
    review_url: str | None = None


class GatewayStatusResponse(BaseModel):
    gateway_reachable: bool
    gateway_url: str
    phone_number: str = "+31618395043"
    error: str | None = None


# ---------------------------------------------------------------------------
# Agent router (OpenClaw API key auth)
# ---------------------------------------------------------------------------

agent_router = APIRouter(
    prefix="/api/v1/openclaw",
    tags=["openclaw-agent"],
    dependencies=[Depends(require_openclaw_api_key)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_phone(phone: str) -> str:
    """
    Normalize phone number to E.164 with '+' prefix.

    OpenClaw may send '31625181578' while existing users are stored
    as '+31625181578'. This ensures consistent matching.
    """
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = f"+{phone}"
    return phone


async def _resolve_tenant(db: AsyncSession) -> str:
    """
    Return the default tenant ID, creating the tenant if it does not exist.

    When OPENCLAW_DEFAULT_TENANT_ID is set and the tenant exists, that ID is
    returned directly. When the setting is empty or the tenant is missing, a
    new tenant is created.
    """
    if settings.OPENCLAW_DEFAULT_TENANT_ID:
        result = await db.execute(
            select(Tenant).where(Tenant.id == settings.OPENCLAW_DEFAULT_TENANT_ID)
        )
        tenant = result.scalars().first()
        if tenant is not None:
            return str(tenant.id)

    # Create a default tenant for OpenClaw
    tenant_id = settings.OPENCLAW_DEFAULT_TENANT_ID or str(uuid.uuid4())
    new_tenant = Tenant(id=tenant_id, name="OpenClaw Default Tenant")
    db.add(new_tenant)
    await db.flush()
    logger.info("openclaw.tenant.created", tenant_id=tenant_id)
    return tenant_id


async def _resolve_user(
    db: AsyncSession, phone: str, tenant_id: str, contact_name: str | None = None
) -> User:
    """
    Look up a user by phone + tenant, auto-creating with PENDING status if not found.
    """
    phone = _normalize_phone(phone)
    result = await db.execute(
        select(User).where(
            User.whatsapp_phone == phone,
            User.tenant_id == tenant_id,
        )
    )
    user: User | None = result.scalars().first()

    if user is not None:
        return user

    # Auto-create user with PENDING status (phone already normalized with '+')
    user = User(
        tenant_id=tenant_id,
        whatsapp_phone=phone,  # already normalized by caller
        display_name=contact_name,
        status=UserStatus.PENDING,
    )
    db.add(user)
    await db.flush()
    logger.info("openclaw.user.auto_created", phone=phone, tenant_id=tenant_id, user_id=user.id)
    return user


async def _generate_activation_token(
    db: AsyncSession, user: User, tenant_id: str
) -> str:
    """
    Generate an activation token and return the full activation URL.
    """
    token_value = secrets.token_urlsafe(48)
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)

    reg_token = RegistrationToken(
        token=token_value,
        phone_number=user.whatsapp_phone,
        tenant_id=tenant_id,
        intake_phone_number_id="openclaw",
        token_type="activation",
        expires_at=expires_at,
    )
    db.add(reg_token)
    await db.flush()

    activation_url = f"{settings.PUBLIC_BASE_URL}/activate?token={token_value}"
    logger.info(
        "openclaw.activation_token.created",
        user_id=user.id,
        phone=user.whatsapp_phone,
        expires_at=expires_at.isoformat(),
    )
    return activation_url


# ---------------------------------------------------------------------------
# GET /api/v1/openclaw/user/{phone}
# ---------------------------------------------------------------------------


@agent_router.get(
    "/user/{phone}",
    response_model=SuccessResponse[UserLookupResponse],
    summary="Look up user by phone number",
)
async def lookup_user(
    phone: str,
    db: DBSession,
) -> SuccessResponse[UserLookupResponse]:
    """
    Look up a user by phone number within the default OpenClaw tenant.

    If the user exists but is PENDING or INACTIVE, an activation token is
    generated and the activation URL is returned so the OpenClaw agent can
    direct the user to complete registration.
    """
    tenant_id = settings.OPENCLAW_DEFAULT_TENANT_ID
    if not tenant_id:
        return SuccessResponse(data=UserLookupResponse(exists=False))

    normalized_phone = _normalize_phone(phone)
    result = await db.execute(
        select(User).where(
            User.whatsapp_phone == normalized_phone,
            User.tenant_id == tenant_id,
        )
    )
    user: User | None = result.scalars().first()

    if user is None:
        logger.info("openclaw.user.lookup.not_found", phone=normalized_phone)
        return SuccessResponse(data=UserLookupResponse(exists=False))

    # Fetch billing info
    billing_status = await get_user_billing_status(db, user.id)
    remaining_credits = await get_remaining_credits(db, user.id)

    user_status = user.status.value if hasattr(user.status, "value") else str(user.status)

    # Generate activation URL for non-active users
    activation_url: str | None = None
    activation_required = user_status in (UserStatus.PENDING.value, UserStatus.INACTIVE.value)

    if activation_required:
        activation_url = await _generate_activation_token(db, user, tenant_id)

    response = UserLookupResponse(
        exists=True,
        user_id=user.id,
        phone=user.whatsapp_phone,
        status=user_status,
        plan_code=billing_status.get("plan_code"),
        subscription_status=billing_status.get("subscription_status"),
        remaining_credits=remaining_credits,
        activation_required=activation_required,
        activation_url=activation_url,
    )

    logger.info(
        "openclaw.user.lookup.found",
        phone=phone,
        user_id=user.id,
        status=user_status,
        activation_required=activation_required,
    )

    return SuccessResponse(data=response)


# ---------------------------------------------------------------------------
# POST /api/v1/openclaw/process-invoice
# ---------------------------------------------------------------------------


@agent_router.post(
    "/process-invoice",
    response_model=SuccessResponse[ProcessInvoiceResponse],
    summary="Accept and queue an invoice image for processing",
)
async def process_invoice(
    body: ProcessInvoiceRequest,
    db: DBSession,
) -> SuccessResponse[ProcessInvoiceResponse]:
    """
    Accept a base64-encoded invoice image from the OpenClaw agent and
    enqueue it for processing.

    Pipeline:
    1. Resolve user by phone + tenant (auto-create if not found).
    2. Check user is ACTIVE; if not, return activation_required.
    3. Billing check via can_process_invoice.
    4. Idempotency check via message_id in WebhookEvent table.
    5. Create WebhookEvent record.
    6. Validate image size.
    7. Create Invoice record (status=PROCESSING).
    8. Enqueue Celery task.
    9. Return queued status with invoice_id.
    """
    log = logger.bind(phone=body.phone, message_id=body.message_id)
    log.info("openclaw.process_invoice.start")

    # Step 1: Resolve tenant and user
    tenant_id = await _resolve_tenant(db)
    user = await _resolve_user(db, body.phone, tenant_id, body.contact_name)

    # Step 2: Check user is ACTIVE
    user_status = user.status.value if hasattr(user.status, "value") else str(user.status)
    if user_status != UserStatus.ACTIVE.value:
        activation_url = await _generate_activation_token(db, user, tenant_id)
        log.info(
            "openclaw.process_invoice.activation_required",
            user_id=user.id,
            user_status=user_status,
        )
        return SuccessResponse(
            data=ProcessInvoiceResponse(
                status="activation_required",
                message=f"User account is {user_status}. Please complete activation first.",
                activation_url=activation_url,
            )
        )

    # Step 3: Billing check
    allowed, reason = await can_process_invoice(db, user)
    if not allowed:
        log.info(
            "openclaw.process_invoice.billing_denied",
            user_id=user.id,
            reason=reason,
        )
        return SuccessResponse(
            data=ProcessInvoiceResponse(
                status="billing_denied",
                message=f"Invoice processing denied: {reason}. Please purchase credits or upgrade your plan.",
            )
        )

    # Step 4: Idempotency check
    if body.message_id:
        existing = await db.execute(
            select(WebhookEvent).where(WebhookEvent.message_id == body.message_id)
        )
        if existing.scalars().first() is not None:
            log.info("openclaw.process_invoice.duplicate", message_id=body.message_id)
            return SuccessResponse(
                data=ProcessInvoiceResponse(
                    status="duplicate",
                    message="This message has already been processed.",
                )
            )

    # Step 5: Create WebhookEvent
    event_message_id = body.message_id or f"openclaw-{uuid.uuid4()}"
    event = WebhookEvent(
        message_id=event_message_id,
        phone_number=body.phone,
        tenant_id=tenant_id,
        raw_payload={"source": "openclaw", "mime_type": body.mime_type},
        status=WebhookEventStatus.RECEIVED,
    )
    db.add(event)
    await db.flush()

    # Step 6: Validate image size
    try:
        image_bytes = base64.b64decode(body.image_base64)
    except Exception as exc:
        log.error("openclaw.process_invoice.invalid_base64", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64-encoded image data.",
        )

    max_size_bytes = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
    if len(image_bytes) > max_size_bytes:
        log.warning(
            "openclaw.process_invoice.image_too_large",
            size_mb=round(len(image_bytes) / (1024 * 1024), 2),
            max_mb=settings.MAX_IMAGE_SIZE_MB,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds maximum size of {settings.MAX_IMAGE_SIZE_MB} MB.",
        )

    # Step 7: Create Invoice record
    file_hash = hashlib.sha256(image_bytes).hexdigest()
    invoice_id = str(uuid.uuid4())
    invoice = Invoice(
        id=invoice_id,
        tenant_id=tenant_id,
        user_id=user.id,
        file_path=f"openclaw/{invoice_id}",
        file_hash=file_hash,
        status=InvoiceStatus.PROCESSING,
        upload_source="openclaw",
        whatsapp_message_id=event_message_id,
        month_partition=datetime.now(tz=timezone.utc).strftime("%Y-%m"),
    )
    db.add(invoice)
    await db.flush()

    # Step 8: Enqueue Celery task
    celery_app.send_task(
        "scanbonai.process_invoice_from_bytes",
        kwargs={
            "phone": body.phone,
            "image_base64": body.image_base64,
            "mime_type": body.mime_type,
            "tenant_id": tenant_id,
            "message_id": event_message_id,
            "contact_name": body.contact_name,
        },
    )

    # Update webhook event status
    event.status = WebhookEventStatus.PROCESSING
    await db.flush()

    log.info(
        "openclaw.process_invoice.queued",
        invoice_id=invoice_id,
        user_id=user.id,
    )

    # Step 9: Return queued status
    return SuccessResponse(
        data=ProcessInvoiceResponse(
            invoice_id=invoice_id,
            status="queued",
            message="Invoice is being processed.",
        )
    )


# ---------------------------------------------------------------------------
# GET /api/v1/openclaw/invoice/{invoice_id}/status
# ---------------------------------------------------------------------------


@agent_router.get(
    "/invoice/{invoice_id}/status",
    response_model=SuccessResponse[InvoiceStatusResponse],
    summary="Check invoice processing status",
)
async def get_invoice_status(
    invoice_id: str,
    db: DBSession,
) -> SuccessResponse[InvoiceStatusResponse]:
    """
    Look up an invoice by ID and return its processing status.

    If extraction is complete (status=EXTRACTED), a summary of the extracted
    data is included along with a signed review URL.
    """
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(selectinload(Invoice.extracted_data))
    )
    invoice: Invoice | None = result.scalars().first()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice {invoice_id!r} not found.",
        )

    invoice_status = invoice.status.value if hasattr(invoice.status, "value") else str(invoice.status)

    # Build summary from extracted data if available
    vendor_name: str | None = None
    invoice_number: str | None = None
    total_amount: str | None = None
    invoice_date: str | None = None
    review_url: str | None = None

    if invoice.extracted_data is not None:
        extracted_json = invoice.extracted_data.extracted_json or {}
        vendor_name = extracted_json.get("vendor_name")
        invoice_number = extracted_json.get("invoice_number")
        total_amount_val = extracted_json.get("total_amount")
        total_amount = str(total_amount_val) if total_amount_val is not None else None
        invoice_date = extracted_json.get("invoice_date")

    # Generate signed review URL for extracted invoices
    if invoice_status == InvoiceStatus.EXTRACTED.value:
        review_url = build_signed_url(
            base_url=settings.PUBLIC_BASE_URL,
            invoice_id=invoice.id,
            user_id=invoice.user_id,
            tenant_id=invoice.tenant_id,
            link_type=SignedLinkType.CORRECTION_FORM,
        )

    response = InvoiceStatusResponse(
        invoice_id=invoice.id,
        status=invoice_status,
        vendor_name=vendor_name,
        invoice_number=invoice_number,
        total_amount=total_amount,
        invoice_date=invoice_date,
        review_url=review_url,
    )

    logger.info(
        "openclaw.invoice.status",
        invoice_id=invoice_id,
        status=invoice_status,
        has_extracted_data=invoice.extracted_data is not None,
    )

    return SuccessResponse(data=response)


# ---------------------------------------------------------------------------
# Admin router (Bearer token auth)
# ---------------------------------------------------------------------------

admin_router = APIRouter(
    prefix="/api/v1/admin/openclaw",
    tags=["admin-openclaw"],
    dependencies=[Depends(require_admin)],
)


async def _check_gateway_health() -> GatewayStatusResponse:
    """
    Call the OpenClaw gateway /health endpoint and return the result.
    """
    gateway_url = settings.OPENCLAW_GATEWAY_URL
    if not gateway_url:
        return GatewayStatusResponse(
            gateway_reachable=False,
            gateway_url="",
            error="OPENCLAW_GATEWAY_URL is not configured.",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers: dict[str, str] = {}
            if settings.OPENCLAW_GATEWAY_TOKEN:
                headers["Authorization"] = f"Bearer {settings.OPENCLAW_GATEWAY_TOKEN}"

            response = await client.get(
                f"{gateway_url.rstrip('/')}/health",
                headers=headers,
            )
            reachable = response.status_code == 200

            logger.info(
                "openclaw.gateway.health_check",
                url=gateway_url,
                status_code=response.status_code,
                reachable=reachable,
            )

            return GatewayStatusResponse(
                gateway_reachable=reachable,
                gateway_url=gateway_url,
                error=None if reachable else f"Gateway returned HTTP {response.status_code}",
            )
    except httpx.RequestError as exc:
        logger.error(
            "openclaw.gateway.health_check.failed",
            url=gateway_url,
            error=str(exc),
        )
        return GatewayStatusResponse(
            gateway_reachable=False,
            gateway_url=gateway_url,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/openclaw/status
# ---------------------------------------------------------------------------


@admin_router.get(
    "/status",
    response_model=SuccessResponse[GatewayStatusResponse],
    summary="Check OpenClaw gateway health",
)
async def gateway_status(
    admin_user: AdminUser,
) -> SuccessResponse[GatewayStatusResponse]:
    """
    Check the OpenClaw gateway health by calling its /health endpoint.

    Returns whether the gateway is reachable, the configured URL, and the
    ScanbonAI WhatsApp phone number.
    """
    result = await _check_gateway_health()
    return SuccessResponse(data=result)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/openclaw/test
# ---------------------------------------------------------------------------


@admin_router.post(
    "/test",
    response_model=SuccessResponse[GatewayStatusResponse],
    summary="Test OpenClaw gateway connectivity",
)
async def gateway_test(
    admin_user: AdminUser,
) -> SuccessResponse[GatewayStatusResponse]:
    """
    Explicitly test connectivity to the OpenClaw gateway.

    Functionally identical to the status endpoint but exposed as a POST
    to make it a deliberate test action.
    """
    result = await _check_gateway_health()
    return SuccessResponse(data=result)
