"""
WhatsApp webhook router for ScanbonAI.

Endpoints
---------
GET  /hook/whatsapp  – Webhook verification challenge (required by Meta).
POST /hook/whatsapp  – Receive inbound WhatsApp messages.

Idempotency
-----------
Every inbound message carries a WhatsApp ``message_id`` (``wamid.*``).  We
persist it to the ``webhook_events`` table and skip processing if a row
already exists, preventing duplicate invoice creation on re-delivery.

Security
--------
The POST handler validates the ``X-Hub-Signature-256`` HMAC header before
doing any work.  Requests with missing or invalid signatures are rejected
with 403 immediately.

FUTURE: When multi-tenant webhook routing is implemented, route the message
to the correct tenant based on the ``WHATSAPP_PHONE_NUMBER_ID`` value in the
payload (each tenant will have its own phone number ID).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db_session
from app.models import WebhookEvent, WebhookEventStatus
from app.schemas import WebhookPayload
from app.services.whatsapp import WhatsAppClient

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/hook", tags=["webhooks"])


# ---------------------------------------------------------------------------
# GET /hook/whatsapp – Meta verification handshake
# ---------------------------------------------------------------------------


@router.get("/whatsapp", summary="WhatsApp webhook verification")
async def verify_webhook(request: Request) -> dict[str, Any]:
    """
    Respond to Meta's webhook verification challenge.

    Meta sends a GET request with three query parameters:
    - ``hub.mode``         – must be ``"subscribe"``
    - ``hub.verify_token`` – must match ``settings.WHATSAPP_VERIFY_TOKEN``
    - ``hub.challenge``    – the value we must echo back as plain text

    Returns
    -------
    int
        The ``hub.challenge`` value cast to int (as required by Meta).

    Raises
    ------
    403 Forbidden
        If the verify token does not match or the mode is wrong.
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

    if token != settings.WHATSAPP_VERIFY_TOKEN:
        log.warning("webhook.verify.bad_token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify token mismatch.",
        )

    log.info("webhook.verify.success")
    # Meta expects the challenge as a plain integer in the response body
    return int(challenge)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# POST /hook/whatsapp – Receive inbound messages
# ---------------------------------------------------------------------------


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

    Processing pipeline (asynchronous)
    ------------------------------------
    1. Validate the X-Hub-Signature-256 HMAC header.
    2. Parse the payload.
    3. For each message in the event:
       a. Check idempotency (``webhook_events`` table).
       b. Store the event row.
       c. Enqueue a Celery task for async image processing.
    4. Return 200 immediately (WhatsApp requires a fast acknowledgement).

    Security
    --------
    Any request whose signature does not validate is rejected with 403
    **before** any database interaction.
    """
    # --- Step 1: Validate HMAC signature ---
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not WhatsAppClient.verify_webhook_signature(raw_body, signature):
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
    background_tasks:
        FastAPI background task queue.
    db:
        Database session (already open; caller commits).
    """
    message_id: str = message.get("id", "")
    phone: str = message.get("from", "")
    msg_type: str = message.get("type", "")

    log = logger.bind(message_id=message_id, phone=phone[:4] + "****", type=msg_type)

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

    # --- Persist webhook event ---
    event = WebhookEvent(
        message_id=message_id,
        phone_number=phone,
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
            )
            # Enqueue Celery task for async processing
            background_tasks.add_task(
                _enqueue_invoice_processing,
                message_id=message_id,
                phone=phone,
                media_id=media_id,
                mime_type=mime_type,
            )
        else:
            log.warning("webhook.message.image_no_media_id")

    elif msg_type == "text":
        text_body = message.get("text", {}).get("body", "")
        log.info("webhook.message.text_received", body_preview=text_body[:50])
        # FUTURE: Handle text commands (e.g. "!status", "!help")

    else:
        log.debug("webhook.message.unhandled_type")


def _enqueue_invoice_processing(
    message_id: str,
    phone: str,
    media_id: str,
    mime_type: str,
) -> None:
    """
    Submit an invoice processing task to the Celery queue.

    This is called as a FastAPI BackgroundTask so it runs after the HTTP
    response is sent.  The actual heavy work (download → quality check →
    OCR → extraction) is done inside the Celery worker.
    """
    try:
        from app.workers.ocr_worker import process_invoice  # local import to avoid circular

        process_invoice.apply_async(
            kwargs={
                "message_id": message_id,
                "phone": phone,
                "media_id": media_id,
                "mime_type": mime_type,
            },
            countdown=1,  # slight delay to allow DB flush to propagate
        )
        logger.info(
            "webhook.task.enqueued",
            message_id=message_id,
            media_id=media_id,
        )
    except Exception as exc:
        # Never let task enqueue failures crash the webhook handler
        logger.error(
            "webhook.task.enqueue_failed",
            message_id=message_id,
            error=str(exc),
        )
