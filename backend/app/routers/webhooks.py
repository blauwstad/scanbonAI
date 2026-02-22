"""
Multi-tenant WhatsApp webhook router for ScanbonAI.

Endpoints
---------
GET  /hook/whatsapp  -- Webhook verification challenge (required by Meta).
POST /hook/whatsapp  -- Receive inbound WhatsApp messages.

Multi-tenant routing
--------------------
Each inbound message is routed to the correct tenant by looking up the
``phone_number_id`` from the payload metadata against the ``whatsapp_settings``
table.  Verification tokens are also checked per-tenant so that multiple Meta
Business accounts can share the same webhook URL.

Idempotency
-----------
Every inbound message carries a WhatsApp ``message_id`` (``wamid.*``).  We
persist it to the ``webhook_events`` table and skip processing if a row
already exists, preventing duplicate invoice creation on re-delivery.

Security
--------
The POST handler validates the ``X-Hub-Signature-256`` HMAC header using
``settings.META_APP_SECRET`` (the Meta App Secret shared across all tenants)
before doing any work.  Requests with missing or invalid signatures are
rejected with 403 immediately.

Unknown users
-------------
When a message arrives from an unrecognised phone number for a known tenant,
we create a ``RegistrationToken`` and send the user a WhatsApp message
containing a registration link.  This allows self-service onboarding while
still maintaining tenant isolation.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db_session
from app.models import (
    RegistrationToken,
    User,
    WebhookEvent,
    WebhookEventStatus,
    WhatsAppSettings,
)
from app.schemas import WebhookPayload
from app.services.whatsapp import WhatsAppClient, decrypt_token

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/hook", tags=["webhooks"])


# ---------------------------------------------------------------------------
# GET /hook/whatsapp -- Meta verification handshake (multi-tenant)
# ---------------------------------------------------------------------------


@router.get("/whatsapp", summary="WhatsApp webhook verification")
async def verify_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Respond to Meta's webhook verification challenge.

    Meta sends a GET request with three query parameters:

    - ``hub.mode``         -- must be ``"subscribe"``
    - ``hub.verify_token`` -- checked against ALL active WhatsAppSettings records
    - ``hub.challenge``    -- the value we must echo back as plain text

    The verify token is matched against the ``webhook_verify_token`` column in
    the ``whatsapp_settings`` table.  If any active tenant record matches, the
    challenge is echoed back; otherwise we return 403.

    Returns
    -------
    int
        The ``hub.challenge`` value cast to int (as required by Meta).

    Raises
    ------
    403 Forbidden
        If the verify token does not match any active tenant or the mode is
        wrong.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    log = logger.bind(mode=mode)

    if mode != "subscribe":
        log.warning("webhook.verify.bad_mode")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unexpected hub.mode: {mode!r}",
        )

    # Look up the verify token against all active WhatsApp settings
    result = await db.execute(
        select(WhatsAppSettings).where(
            WhatsAppSettings.webhook_verify_token == token,
            WhatsAppSettings.is_active.is_(True),
        )
    )
    wa_settings: WhatsAppSettings | None = result.scalars().first()

    if wa_settings is None:
        log.warning("webhook.verify.bad_token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify token mismatch.",
        )

    log.info(
        "webhook.verify.success",
        tenant_id=wa_settings.tenant_id,
        phone_number_id=wa_settings.phone_number_id,
    )
    # Meta expects the challenge as a plain integer in the response body
    return int(challenge)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# POST /hook/whatsapp -- Receive inbound messages (multi-tenant)
# ---------------------------------------------------------------------------


def _verify_meta_signature(payload: bytes, signature_header: str) -> bool:
    """
    Validate an inbound webhook payload against the X-Hub-Signature-256 header
    using the Meta App Secret (shared across all tenants).

    Parameters
    ----------
    payload:
        Raw request body bytes.
    signature_header:
        Value of the ``X-Hub-Signature-256`` header, e.g.
        ``"sha256=abc123..."``

    Returns
    -------
    bool
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    if not signature_header.startswith("sha256="):
        logger.warning("webhook.signature.bad_format")
        return False

    expected_hash = signature_header[len("sha256="):]
    computed_hash = hmac_mod.new(
        key=settings.META_APP_SECRET.encode(),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    is_valid = hmac_mod.compare_digest(computed_hash, expected_hash)
    if not is_valid:
        logger.warning("webhook.signature.mismatch")
    return is_valid


@router.post(
    "/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="Receive inbound WhatsApp messages",
)
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """
    Handle inbound WhatsApp Cloud API webhook deliveries.

    Processing pipeline
    -------------------
    1. Validate the X-Hub-Signature-256 HMAC header using ``META_APP_SECRET``.
    2. Parse the payload.
    3. For each entry/change, extract the ``phone_number_id`` from metadata and
       resolve the owning tenant via the ``whatsapp_settings`` table.
    4. For each message in the change:
       a. Check idempotency (``webhook_events`` table).
       b. Store the event row (with ``tenant_id``).
       c. Resolve the user by phone + tenant.
       d. If user unknown: create a RegistrationToken and send a registration
          link via WhatsApp.
       e. If user known: enqueue a Celery task for async image processing
          (passing ``tenant_id`` and ``phone_number_id``).
    5. Return 200 immediately (WhatsApp requires a fast acknowledgement).

    Security
    --------
    Any request whose signature does not validate is rejected with 403
    **before** any database interaction.
    """
    # --- Step 1: Validate HMAC signature using META_APP_SECRET ---
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_meta_signature(raw_body, signature):
        logger.warning(
            "webhook.receive.invalid_signature",
            path=str(request.url.path),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        )

    # --- Step 2: Parse payload ---
    try:
        payload = WebhookPayload.model_validate_json(raw_body)
    except Exception as exc:
        logger.warning("webhook.receive.parse_error", error=str(exc))
        # Return 200 anyway; malformed non-message events should not cause
        # WhatsApp to disable our webhook endpoint.
        return {"status": "ignored", "reason": "parse_error"}

    # --- Step 3: Process each entry/change/message ---
    for entry in payload.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue

            value = change.value

            # --- Extract phone_number_id from metadata ---
            metadata: dict[str, Any] = value.get("metadata", {})
            phone_number_id: str = metadata.get("phone_number_id", "")

            if not phone_number_id:
                logger.warning(
                    "webhook.receive.missing_phone_number_id",
                    entry_id=entry.id,
                )
                continue

            # --- Resolve tenant from phone_number_id ---
            wa_result = await db.execute(
                select(WhatsAppSettings).where(
                    WhatsAppSettings.phone_number_id == phone_number_id,
                    WhatsAppSettings.is_active.is_(True),
                )
            )
            wa_settings: WhatsAppSettings | None = wa_result.scalars().first()

            if wa_settings is None:
                logger.warning(
                    "webhook.receive.unknown_phone_number_id",
                    phone_number_id=phone_number_id,
                )
                # Don't break the webhook -- Meta would retry on non-200
                continue

            tenant_id: str = wa_settings.tenant_id

            log = logger.bind(
                tenant_id=tenant_id,
                phone_number_id=phone_number_id,
            )
            log.debug("webhook.receive.tenant_resolved")

            # --- Process messages within this change ---
            messages: list[dict[str, Any]] = value.get("messages", [])
            contacts: list[dict[str, Any]] = value.get("contacts", [])

            contact_map: dict[str, str] = {
                c.get("wa_id", ""): c.get("profile", {}).get("name", "")
                for c in contacts
            }

            for msg in messages:
                await _handle_message(
                    message=msg,
                    contact_name=contact_map.get(msg.get("from", ""), ""),
                    raw_value=value,
                    tenant_id=tenant_id,
                    phone_number_id=phone_number_id,
                    wa_settings=wa_settings,
                    background_tasks=background_tasks,
                    db=db,
                )

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _handle_message(
    message: dict[str, Any],
    contact_name: str,
    raw_value: dict[str, Any],
    tenant_id: str,
    phone_number_id: str,
    wa_settings: WhatsAppSettings,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
) -> None:
    """
    Persist a single inbound message and enqueue processing if new.

    Parameters
    ----------
    message:
        The individual message dict from the ``messages`` array.
    contact_name:
        Display name from the contacts block (may be empty).
    raw_value:
        Full ``value`` dict for the change (stored for debugging).
    tenant_id:
        Resolved tenant UUID for this message.
    phone_number_id:
        The business phone number ID that received this message.
    wa_settings:
        The WhatsAppSettings record for the resolved tenant.
    background_tasks:
        FastAPI background task queue.
    db:
        Database session (already open; caller commits).
    """
    message_id: str = message.get("id", "")
    phone: str = message.get("from", "")
    msg_type: str = message.get("type", "")

    log = logger.bind(
        message_id=message_id,
        phone=phone[:4] + "****" if len(phone) > 4 else phone,
        type=msg_type,
        tenant_id=tenant_id,
    )

    if not message_id:
        log.warning("webhook.message.missing_id")
        return

    # --- Idempotency check ---
    existing = await db.execute(
        select(WebhookEvent).where(WebhookEvent.message_id == message_id)
    )
    if existing.scalars().first() is not None:
        log.info("webhook.message.duplicate")
        return

    # --- Persist webhook event (with tenant_id) ---
    event = WebhookEvent(
        message_id=message_id,
        phone_number=phone,
        tenant_id=tenant_id,
        raw_payload=raw_value,
        status=WebhookEventStatus.RECEIVED,
    )
    db.add(event)
    try:
        await db.flush()
    except IntegrityError:
        # Race condition: another worker inserted the same message_id
        await db.rollback()
        log.info("webhook.message.duplicate_race")
        return

    # --- Resolve user by phone + tenant ---
    user_result = await db.execute(
        select(User).where(
            User.whatsapp_phone == phone,
            User.tenant_id == tenant_id,
        )
    )
    user: User | None = user_result.scalars().first()

    if user is None:
        # Unknown user for this tenant -- send registration link
        log.info(
            "webhook.message.unknown_user",
            contact_name=contact_name,
        )
        await _handle_unknown_user(
            phone=phone,
            tenant_id=tenant_id,
            phone_number_id=phone_number_id,
            wa_settings=wa_settings,
            db=db,
        )
        return

    # --- Route by message type ---
    if msg_type in ("image", "document"):
        media_block = message.get(msg_type, {})
        media_id: str = media_block.get("id", "")
        mime_type: str = media_block.get("mime_type", "image/jpeg")

        if media_id:
            log.info(
                "webhook.message.image_received",
                media_id=media_id,
                mime_type=mime_type,
                contact=contact_name,
                user_id=user.id,
            )
            # Enqueue Celery task for async processing
            background_tasks.add_task(
                _enqueue_invoice_processing,
                message_id=message_id,
                phone=phone,
                media_id=media_id,
                mime_type=mime_type,
                tenant_id=tenant_id,
                phone_number_id=phone_number_id,
            )
        else:
            log.warning("webhook.message.image_no_media_id")

    elif msg_type == "text":
        text_body = message.get("text", {}).get("body", "")
        log.info("webhook.message.text_received", body_preview=text_body[:50])
        # FUTURE: Handle text commands (e.g. "!status", "!help")

    else:
        log.debug("webhook.message.unhandled_type")


async def _handle_unknown_user(
    phone: str,
    tenant_id: str,
    phone_number_id: str,
    wa_settings: WhatsAppSettings,
    db: AsyncSession,
) -> None:
    """
    Handle an inbound message from a phone number not registered for this
    tenant.

    Creates a ``RegistrationToken`` and sends the user a WhatsApp message
    containing a registration link so they can self-onboard.

    Parameters
    ----------
    phone:
        The sender's E.164 phone number (without '+').
    tenant_id:
        The resolved tenant UUID.
    phone_number_id:
        The business phone number ID that received the message.
    wa_settings:
        WhatsAppSettings record (contains encrypted access token).
    db:
        Active database session.
    """
    log = logger.bind(
        phone=phone[:4] + "****" if len(phone) > 4 else phone,
        tenant_id=tenant_id,
        phone_number_id=phone_number_id,
    )

    # Create a registration token (48 bytes of URL-safe randomness)
    token_value = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    reg_token = RegistrationToken(
        token=token_value,
        phone_number=phone,
        tenant_id=tenant_id,
        intake_phone_number_id=phone_number_id,
        expires_at=expires_at,
    )
    db.add(reg_token)

    try:
        await db.flush()
    except IntegrityError:
        # Token collision (astronomically unlikely) -- log and bail
        await db.rollback()
        log.error("webhook.registration.token_collision")
        return

    # Build the registration URL
    registration_url = (
        f"{settings.PUBLIC_BASE_URL}/register/whatsapp?token={token_value}"
    )

    log.info(
        "webhook.registration.token_created",
        expires_at=expires_at.isoformat(),
    )

    # Send the registration link via WhatsApp using the tenant's credentials
    try:
        access_token = decrypt_token(wa_settings.access_token_encrypted)

        async with WhatsAppClient(
            access_token=access_token,
            phone_number_id=phone_number_id,
        ) as wa:
            await wa.send_text_message(
                phone=phone,
                text=(
                    "Welcome! You are not yet registered with this service.\n\n"
                    "Please complete your registration using the link below "
                    "(valid for 24 hours):\n\n"
                    f"{registration_url}"
                ),
            )

        log.info("webhook.registration.link_sent")

    except Exception as exc:
        # Never let send failures crash the webhook handler
        log.error(
            "webhook.registration.send_failed",
            error=str(exc),
        )


def _enqueue_invoice_processing(
    message_id: str,
    phone: str,
    media_id: str,
    mime_type: str,
    tenant_id: str,
    phone_number_id: str,
) -> None:
    """
    Submit an invoice processing task to the Celery queue.

    This is called as a FastAPI BackgroundTask so it runs after the HTTP
    response is sent.  The actual heavy work (download -> quality check ->
    OCR -> extraction) is done inside the Celery worker.

    Parameters
    ----------
    message_id:
        WhatsApp message ID for idempotency tracking.
    phone:
        Sender's E.164 phone number.
    media_id:
        WhatsApp media object ID to download.
    mime_type:
        Declared MIME type from the webhook payload.
    tenant_id:
        The resolved tenant UUID so the worker knows which tenant this
        invoice belongs to.
    phone_number_id:
        The business phone number ID, so the worker can look up the
        correct WhatsApp credentials for media download and replies.
    """
    try:
        from app.workers.ocr_worker import process_invoice  # local import to avoid circular

        process_invoice.apply_async(
            kwargs={
                "message_id": message_id,
                "phone": phone,
                "media_id": media_id,
                "mime_type": mime_type,
                "tenant_id": tenant_id,
                "phone_number_id": phone_number_id,
            },
            countdown=1,  # slight delay to allow DB flush to propagate
        )
        logger.info(
            "webhook.task.enqueued",
            message_id=message_id,
            media_id=media_id,
            tenant_id=tenant_id,
            phone_number_id=phone_number_id,
        )
    except Exception as exc:
        # Never let task enqueue failures crash the webhook handler
        logger.error(
            "webhook.task.enqueue_failed",
            message_id=message_id,
            error=str(exc),
        )
