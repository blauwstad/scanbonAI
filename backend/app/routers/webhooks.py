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

State machine
-------------
When a message arrives, the user is resolved (or auto-created) and routed
through a state machine based on ``UserStatus``:

  PENDING / INACTIVE  -> send activation link
  SUSPENDED           -> send support message
  ACTIVE              -> process invoices or handle text commands

Billing enforcement is applied before enqueueing invoice processing:
credits-based plans must have remaining credits.
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
    UserStatus,
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
) -> Any:
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
    # Meta expects the challenge echoed back as plain text
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=challenge)


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
       c. Resolve or auto-create the user by phone + tenant.
       d. Route through the user status state machine.
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


async def _create_token(
    db: AsyncSession,
    phone: str,
    tenant_id: str,
    phone_number_id: str,
    token_type: str = "activation",
    expires_hours: int = 24,
) -> str | None:
    """
    Create a RegistrationToken and return the token string.

    Returns None on collision (astronomically unlikely).
    """
    token_value = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_hours)

    reg_token = RegistrationToken(
        token=token_value,
        phone_number=phone,
        tenant_id=tenant_id,
        intake_phone_number_id=phone_number_id,
        token_type=token_type,
        expires_at=expires_at,
    )
    db.add(reg_token)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        logger.error("webhook.token.collision", token_type=token_type)
        return None

    return token_value


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
    Persist a single inbound message and route through the user status
    state machine.

    State machine
    -------------
    1. Unknown user   -> auto-create with PENDING status, send activation link
    2. PENDING/INACTIVE -> send activation link
    3. SUSPENDED      -> send support message
    4. ACTIVE         -> process invoices or handle text commands (with billing)

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

    access_token = decrypt_token(wa_settings.access_token_encrypted)

    # ------------------------------------------------------------------
    # STATE 1: Unknown user -> auto-create with PENDING status
    # ------------------------------------------------------------------
    if user is None:
        log.info(
            "webhook.message.unknown_user",
            contact_name=contact_name,
        )
        await _handle_unknown_user(
            phone=phone,
            contact_name=contact_name,
            tenant_id=tenant_id,
            phone_number_id=phone_number_id,
            wa_settings=wa_settings,
            db=db,
        )
        return

    # ------------------------------------------------------------------
    # STATE 2: PENDING or INACTIVE -> send activation link
    # ------------------------------------------------------------------
    if user.status in (UserStatus.PENDING, UserStatus.INACTIVE):
        log.info("webhook.message.user_not_active", status=user.status.value)

        # Only create a new token if there is no recent unexpired one
        existing_token_result = await db.execute(
            select(RegistrationToken).where(
                RegistrationToken.phone_number == phone,
                RegistrationToken.tenant_id == tenant_id,
                RegistrationToken.token_type == "activation",
                RegistrationToken.expires_at > datetime.now(timezone.utc),
                RegistrationToken.used_at.is_(None),
            )
        )
        existing_token = existing_token_result.scalars().first()

        if existing_token:
            token_value = existing_token.token
        else:
            token_value = await _create_token(
                db, phone, tenant_id, phone_number_id, token_type="activation"
            )

        if token_value:
            activation_url = f"{settings.PUBLIC_BASE_URL}/activate?token={token_value}"
            try:
                async with WhatsAppClient(
                    access_token=access_token,
                    phone_number_id=phone_number_id,
                ) as wa:
                    await wa.send_text_message(
                        phone=phone,
                        text=(
                            "Your Scanbon account is not yet active.\n"
                            f"Activate now: {activation_url}"
                        ),
                    )
                log.info("webhook.activation.link_sent")
            except Exception as exc:
                log.error("webhook.activation.send_failed", error=str(exc))
        return

    # ------------------------------------------------------------------
    # STATE 3: SUSPENDED -> send support message
    # ------------------------------------------------------------------
    if user.status == UserStatus.SUSPENDED:
        log.info("webhook.message.user_suspended")
        try:
            async with WhatsAppClient(
                access_token=access_token,
                phone_number_id=phone_number_id,
            ) as wa:
                await wa.send_text_message(
                    phone=phone,
                    text="Your account is currently suspended. Please contact support.",
                )
        except Exception as exc:
            log.error("webhook.suspended.send_failed", error=str(exc))
        return

    # ------------------------------------------------------------------
    # STATE 4: ACTIVE -> process invoices or handle text commands
    # ------------------------------------------------------------------
    if msg_type == "text":
        text_body = message.get("text", {}).get("body", "")
        log.info("webhook.message.text_received", body_preview=text_body[:50])
        await _handle_text_command(
            text=text_body,
            user=user,
            wa_settings=wa_settings,
            phone=phone,
            phone_number_id=phone_number_id,
            db=db,
        )

    elif msg_type in ("image", "document"):
        from app.services.billing import can_process_invoice

        media_block = message.get(msg_type, {})
        media_id: str = media_block.get("id", "")
        mime_type: str = media_block.get("mime_type", "image/jpeg")

        if not media_id:
            log.warning("webhook.message.image_no_media_id")
            return

        # --- Billing enforcement ---
        allowed, reason = await can_process_invoice(db, user)

        if not allowed and reason == "credits_exhausted":
            log.info("webhook.billing.credits_exhausted", user_id=user.id)
            renewal_token = await _create_token(
                db, phone, tenant_id, phone_number_id, token_type="renewal"
            )
            if renewal_token:
                renewal_url = f"{settings.PUBLIC_BASE_URL}/activate?token={renewal_token}"
                try:
                    async with WhatsAppClient(
                        access_token=access_token,
                        phone_number_id=phone_number_id,
                    ) as wa:
                        await wa.send_text_message(
                            phone=phone,
                            text=(
                                "You've used all your invoice credits.\n"
                                f"Purchase more to continue: {renewal_url}"
                            ),
                        )
                except Exception as exc:
                    log.error("webhook.billing.send_failed", error=str(exc))
            return

        if not allowed and reason == "not_active":
            log.info("webhook.billing.not_active", user_id=user.id)
            activation_token = await _create_token(
                db, phone, tenant_id, phone_number_id, token_type="activation"
            )
            if activation_token:
                activation_url = f"{settings.PUBLIC_BASE_URL}/activate?token={activation_token}"
                try:
                    async with WhatsAppClient(
                        access_token=access_token,
                        phone_number_id=phone_number_id,
                    ) as wa:
                        await wa.send_text_message(
                            phone=phone,
                            text=(
                                "Your Scanbon account is not yet active.\n"
                                f"Activate now: {activation_url}"
                            ),
                        )
                except Exception as exc:
                    log.error("webhook.billing.activation_send_failed", error=str(exc))
            return

        # --- Allowed: enqueue processing ---
        log.info(
            "webhook.message.image_received",
            media_id=media_id,
            mime_type=mime_type,
            contact=contact_name,
            user_id=user.id,
        )
        background_tasks.add_task(
            _enqueue_invoice_processing,
            message_id=message_id,
            phone=phone,
            media_id=media_id,
            mime_type=mime_type,
            tenant_id=tenant_id,
            phone_number_id=phone_number_id,
        )

        # Acknowledge receipt
        try:
            async with WhatsAppClient(
                access_token=access_token,
                phone_number_id=phone_number_id,
            ) as wa:
                await wa.send_text_message(
                    phone=phone,
                    text="Invoice received! Processing now...",
                )
        except Exception as exc:
            log.error("webhook.ack.send_failed", error=str(exc))

    else:
        log.debug("webhook.message.unhandled_type")


async def _handle_text_command(
    text: str,
    user: User,
    wa_settings: WhatsAppSettings,
    phone: str,
    phone_number_id: str,
    db: AsyncSession,
) -> None:
    """
    Handle text commands from active users.

    Supported commands: plan, plans, upgrade, help, status.
    Any other text prompts a usage hint.
    """
    cmd = text.strip().lower()
    access_token = decrypt_token(wa_settings.access_token_encrypted)

    if cmd in ("plan", "plans", "upgrade"):
        from app.services.billing import get_user_billing_status

        billing = await get_user_billing_status(db, user.id)
        plan = billing.get("plan_code", "None")
        credits = billing.get("remaining_credits")

        # Create a renewal token so user can upgrade/renew
        renewal_token = await _create_token(
            db,
            phone,
            user.tenant_id,
            phone_number_id,
            token_type="renewal",
        )

        reply = f"Your plan: {plan}"
        if credits is not None:
            reply += f"\nCredits remaining: {credits}"
        if renewal_token:
            reply += (
                f"\n\nUpgrade or renew: "
                f"{settings.PUBLIC_BASE_URL}/activate?token={renewal_token}"
            )

    elif cmd == "help":
        reply = (
            "Scanbon Help:\n"
            "- Send invoice photo to process\n"
            "- Type 'plan' for your plan info\n"
            "- Type 'status' for account status\n"
            "- Type 'upgrade' to change plan"
        )

    elif cmd == "status":
        from app.services.billing import get_user_billing_status

        billing = await get_user_billing_status(db, user.id)
        reply = (
            f"Account: {user.status.value}\n"
            f"Plan: {billing.get('plan_code', 'None')}"
        )
        if billing.get("remaining_credits") is not None:
            reply += f"\nCredits: {billing['remaining_credits']}"

    else:
        reply = (
            "Send a photo of your invoice to process it.\n"
            "Commands: plan, status, help, upgrade"
        )

    async with WhatsAppClient(
        access_token=access_token,
        phone_number_id=phone_number_id,
    ) as wa:
        await wa.send_text_message(phone=phone, text=reply)


async def _handle_unknown_user(
    phone: str,
    contact_name: str,
    tenant_id: str,
    phone_number_id: str,
    wa_settings: WhatsAppSettings,
    db: AsyncSession,
) -> None:
    """
    Handle an inbound message from a phone number not registered for this
    tenant.

    Auto-creates a User with status=PENDING, creates an activation token,
    and sends the user a WhatsApp message containing an activation link.

    Parameters
    ----------
    phone:
        The sender's E.164 phone number (without '+').
    contact_name:
        Display name from the WhatsApp contacts block.
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

    # --- Auto-create user with PENDING status ---
    new_user = User(
        tenant_id=tenant_id,
        whatsapp_phone=phone,
        display_name=contact_name or None,
        status=UserStatus.PENDING,
    )
    db.add(new_user)

    try:
        await db.flush()
    except IntegrityError:
        # User was created between the SELECT and INSERT (race condition).
        # Roll back and let the next message pick them up.
        await db.rollback()
        log.info("webhook.unknown_user.race_condition")
        return

    log.info("webhook.unknown_user.created", user_id=new_user.id)

    # --- Create activation token ---
    token_value = await _create_token(
        db, phone, tenant_id, phone_number_id, token_type="activation"
    )

    if not token_value:
        return

    # Build the activation URL
    activation_url = f"{settings.PUBLIC_BASE_URL}/activate?token={token_value}"

    log.info(
        "webhook.activation.token_created",
        user_id=new_user.id,
    )

    # Send the activation link via WhatsApp using the tenant's credentials
    try:
        access_token = decrypt_token(wa_settings.access_token_encrypted)

        async with WhatsAppClient(
            access_token=access_token,
            phone_number_id=phone_number_id,
        ) as wa:
            await wa.send_text_message(
                phone=phone,
                text=(
                    "Welcome to Scanbon! Activate your account in 30 seconds:\n"
                    f"{activation_url}"
                ),
            )

        log.info("webhook.activation.link_sent")

    except Exception as exc:
        # Never let send failures crash the webhook handler
        log.error(
            "webhook.activation.send_failed",
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
