"""
Celery worker tasks for ScanbonAI invoice processing.

Task pipeline
-------------
Two entry-point tasks share a common processing pipeline:

``process_invoice`` (WhatsApp flow)
    Triggered by a Meta WhatsApp webhook.  Downloads the image from the
    WhatsApp CDN using per-tenant credentials and notifies the user via the
    WhatsApp Cloud API.

``process_invoice_from_bytes`` (OpenClaw flow)
    Triggered when an image arrives as base64 bytes from the OpenClaw
    gateway.  Skips WhatsApp credential resolution and media download;
    notifications are sent through the OpenClaw gateway instead.

Both tasks share the same core pipeline:

  1. Resolve tenant + user from the phone number.
  2. Billing enforcement.
  3. Persist raw bytes to storage.
  4. Run image quality gates (blur / resolution / skew).
  5. If quality passes -> call OCR engine.
  6. Extract structured metadata from OCR text.
  7. Persist all results to the database.
  8. Notify the user with a signed review link.
  9. Deduct credit if applicable.

Retry policy
------------
Each step that involves external I/O is wrapped with Celery's built-in retry
mechanism using exponential backoff.  Both tasks are configured with
``max_retries=5`` and an initial retry delay of 30 s (doubling each attempt).

Celery configuration
--------------------
The broker and backend are configured via ``settings.REDIS_URL``.  Worker
processes should be started with::

    celery -A app.workers.ocr_worker worker --loglevel=info

FUTURE: Split into a task chain (Celery canvas) so each step can be
retried independently and partial results are visible in real time.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

import structlog
from celery import Celery
from celery.utils.log import get_task_logger

from app.config import settings

logger = get_task_logger(__name__)
structlog_logger = structlog.get_logger(__name__)

# Type alias for the async notification callback used by the shared pipeline.
# Signature: async (phone: str, text: str) -> None
NotifyFn = Callable[[str, str], Awaitable[None]]

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------

celery_app = Celery(
    "scanbonai",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,           # only ack after task completes (safer)
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # process one task at a time per worker
    task_track_started=True,
    # Retry defaults (overridable per task)
    task_max_retries=5,
)


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """Run an async coroutine from a synchronous Celery task context."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Main task: WhatsApp flow
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,  # initial retry delay in seconds
    acks_late=True,
    name="scanbonai.process_invoice",
)
def process_invoice(
    self: Any,
    *,
    message_id: str,
    phone: str,
    media_id: str,
    mime_type: str,
    tenant_id: str | None = None,
    phone_number_id: str | None = None,
) -> dict[str, Any]:
    """
    Full invoice processing pipeline triggered by a WhatsApp image message.

    Parameters
    ----------
    message_id:
        WhatsApp message ID (used for idempotency tracking).
    phone:
        Sender's E.164 phone number (without '+').
    media_id:
        WhatsApp media object ID to download.
    mime_type:
        Declared MIME type from the webhook payload.
    tenant_id:
        UUID of the tenant that owns the WhatsApp business number.
    phone_number_id:
        WhatsApp phone number ID used for per-tenant API calls.

    Returns
    -------
    dict
        Summary of what was processed (``invoice_id``, ``status``).

    Raises
    ------
    celery.exceptions.Retry
        On transient failures (network errors, DB timeouts).
    """
    log = structlog_logger.bind(
        message_id=message_id,
        phone=phone[:4] + "****",
        media_id=media_id,
    )
    log.info("ocr_worker.task.start")

    try:
        result = run_async(_process_invoice_async(
            message_id=message_id,
            phone=phone,
            media_id=media_id,
            mime_type=mime_type,
            tenant_id=tenant_id,
            phone_number_id=phone_number_id,
            log=log,
        ))
        log.info("ocr_worker.task.complete", invoice_id=result.get("invoice_id"))
        return result

    except Exception as exc:
        log.error("ocr_worker.task.failed", error=str(exc), retry_count=self.request.retries)

        # Exponential backoff: 30s, 60s, 120s, 240s, 480s
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


# ---------------------------------------------------------------------------
# New task: OpenClaw / base64 flow
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    acks_late=True,
    name="scanbonai.process_invoice_from_bytes",
)
def process_invoice_from_bytes(
    self: Any,
    *,
    phone: str,
    image_base64: str,
    mime_type: str,
    tenant_id: str,
    message_id: str | None = None,
    contact_name: str | None = None,
) -> dict[str, Any]:
    """
    Invoice processing pipeline triggered by a base64 image from OpenClaw.

    This task mirrors ``process_invoice`` but receives the image bytes
    directly (base64-encoded) rather than downloading from the WhatsApp CDN.
    User notifications are sent via the OpenClaw gateway instead of the
    Meta WhatsApp Cloud API.

    Parameters
    ----------
    phone:
        Sender's E.164 phone number (without '+').
    image_base64:
        Base64-encoded image bytes.
    mime_type:
        Image MIME type (e.g. ``"image/jpeg"``).
    tenant_id:
        UUID of the tenant.
    message_id:
        Optional message ID for idempotency tracking.
    contact_name:
        Optional display name of the sender.

    Returns
    -------
    dict
        Summary of what was processed (``invoice_id``, ``status``).

    Raises
    ------
    celery.exceptions.Retry
        On transient failures (network errors, DB timeouts).
    """
    effective_message_id = message_id or f"openclaw-{uuid.uuid4()}"

    log = structlog_logger.bind(
        message_id=effective_message_id,
        phone=phone[:4] + "****",
        source="openclaw",
        contact_name=contact_name,
    )
    log.info("ocr_worker.task_from_bytes.start")

    try:
        result = run_async(_process_invoice_from_bytes_async(
            phone=phone,
            image_base64=image_base64,
            mime_type=mime_type,
            tenant_id=tenant_id,
            message_id=effective_message_id,
            contact_name=contact_name,
            log=log,
        ))
        log.info("ocr_worker.task_from_bytes.complete", invoice_id=result.get("invoice_id"))
        return result

    except Exception as exc:
        log.error(
            "ocr_worker.task_from_bytes.failed",
            error=str(exc),
            retry_count=self.request.retries,
        )

        # Exponential backoff: 30s, 60s, 120s, 240s, 480s
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


# ---------------------------------------------------------------------------
# Async pipeline: WhatsApp flow
# ---------------------------------------------------------------------------


async def _process_invoice_async(
    message_id: str,
    phone: str,
    media_id: str,
    mime_type: str,
    tenant_id: str | None,
    phone_number_id: str | None,
    log: Any,
) -> dict[str, Any]:
    """
    Async implementation of the WhatsApp invoice processing pipeline.

    Resolves per-tenant WhatsApp credentials, downloads the media from the
    Meta CDN, then delegates to the shared pipeline for all remaining steps.
    """
    from app.database import async_session
    from app.models import (
        InvoiceStatus,
        WhatsAppSettings,
    )
    from app.services.whatsapp import WhatsAppClient, decrypt_token
    from sqlalchemy import select

    async with async_session() as db:

        # ------------------------------------------------------------------
        # Step 0: Resolve per-tenant WhatsApp credentials
        # ------------------------------------------------------------------
        wa_client_kwargs: dict[str, str] | None = None

        if tenant_id and phone_number_id:
            wa_settings_result = await db.execute(
                select(WhatsAppSettings).where(
                    WhatsAppSettings.tenant_id == tenant_id,
                    WhatsAppSettings.is_active.is_(True),
                )
            )
            wa_settings = wa_settings_result.scalars().first()
            if wa_settings:
                access_token = decrypt_token(wa_settings.access_token_encrypted)
                wa_client_kwargs = {
                    "access_token": access_token,
                    "phone_number_id": phone_number_id,
                }
            else:
                log.warning(
                    "ocr_worker.no_whatsapp_settings",
                    tenant_id=tenant_id,
                    hint="Will process invoice but skip WhatsApp notifications.",
                )

        # ------------------------------------------------------------------
        # Step 1a (WhatsApp-specific): Resolve user for download-failure tracking
        # ------------------------------------------------------------------
        from app.models import Invoice, Tenant, User

        user_result = await db.execute(
            select(User).where(User.whatsapp_phone == phone)
        )
        user: User | None = user_result.scalars().first()

        tenant: Tenant | None = None
        if user:
            tenant_result = await db.execute(
                select(Tenant).where(Tenant.id == user.tenant_id)
            )
            tenant = tenant_result.scalars().first()

        # ------------------------------------------------------------------
        # Step 3: Download image from WhatsApp
        # ------------------------------------------------------------------
        log.debug("ocr_worker.step3.download")
        try:
            if wa_client_kwargs:
                async with WhatsAppClient(**wa_client_kwargs) as wa:
                    image_bytes = await wa.download_media(media_id)
            else:
                log.warning("ocr_worker.download.no_credentials")
                raise RuntimeError("No WhatsApp credentials available for media download.")
        except Exception as exc:
            log.error("ocr_worker.download_failed", error=str(exc))
            invoice_id = str(uuid.uuid4())
            invoice = Invoice(
                id=invoice_id,
                tenant_id=tenant.id if tenant else (tenant_id or ""),
                user_id=user.id if user else "",
                file_path="",
                file_hash="",
                status=InvoiceStatus.QUALITY_FAILED,
                upload_source="whatsapp",
                whatsapp_message_id=message_id,
                month_partition=datetime.now(tz=timezone.utc).strftime("%Y-%m"),
            )
            db.add(invoice)
            await db.commit()
            return {"status": "error", "reason": "download_failed", "invoice_id": invoice_id}

        # ------------------------------------------------------------------
        # Build WhatsApp notification callback
        # ------------------------------------------------------------------
        async def _wa_notify(phone_number: str, text: str) -> None:
            if wa_client_kwargs:
                try:
                    async with WhatsAppClient(**wa_client_kwargs) as wa:
                        await wa.send_text_message(phone=phone_number, text=text)
                except Exception as exc:
                    log.warning("ocr_worker.wa_notify_failed", error=str(exc))

        # ------------------------------------------------------------------
        # Delegate to shared pipeline
        # ------------------------------------------------------------------
        return await _run_common_pipeline(
            db=db,
            phone=phone,
            image_bytes=image_bytes,
            mime_type=mime_type,
            upload_source="whatsapp",
            message_id=message_id,
            notify=_wa_notify,
            log=log,
        )


# ---------------------------------------------------------------------------
# Async pipeline: OpenClaw / base64 flow
# ---------------------------------------------------------------------------


async def _process_invoice_from_bytes_async(
    phone: str,
    image_base64: str,
    mime_type: str,
    tenant_id: str,
    message_id: str,
    contact_name: str | None,
    log: Any,
) -> dict[str, Any]:
    """
    Async implementation of the OpenClaw base64 invoice processing pipeline.

    Decodes the base64 image and builds an OpenClaw notification callback,
    then delegates to the shared pipeline for all remaining steps.
    """
    from app.database import async_session
    from app.services.openclaw import OpenClawClient

    # Decode the base64 image
    log.debug("ocr_worker.decode_base64")
    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as exc:
        log.error("ocr_worker.base64_decode_failed", error=str(exc))
        return {"status": "error", "reason": "base64_decode_failed"}

    if not image_bytes:
        log.error("ocr_worker.empty_image")
        return {"status": "error", "reason": "empty_image"}

    # Build OpenClaw notification callback
    openclaw_client = OpenClawClient()

    async def _oc_notify(phone_number: str, text: str) -> None:
        try:
            await openclaw_client.send_message(phone=phone_number, text=text)
        except Exception as exc:
            log.warning("ocr_worker.openclaw_notify_failed", error=str(exc))

    async with async_session() as db:
        return await _run_common_pipeline(
            db=db,
            phone=phone,
            image_bytes=image_bytes,
            mime_type=mime_type,
            upload_source="openclaw",
            message_id=message_id,
            notify=_oc_notify,
            log=log,
        )


# ---------------------------------------------------------------------------
# Shared pipeline (used by both WhatsApp and OpenClaw flows)
# ---------------------------------------------------------------------------


async def _run_common_pipeline(
    *,
    db: Any,
    phone: str,
    image_bytes: bytes,
    mime_type: str,
    upload_source: str,
    message_id: str,
    notify: NotifyFn,
    log: Any,
) -> dict[str, Any]:
    """
    Shared async pipeline that processes an invoice image after acquisition.

    This function is called by both ``_process_invoice_async`` (WhatsApp flow)
    and ``_process_invoice_from_bytes_async`` (OpenClaw flow).  It handles:

    1. User/tenant resolution from the phone number.
    2. Billing enforcement.
    3. Invoice record creation.
    4. Image persistence to storage.
    5. Quality check.
    6. OCR.
    7. Metadata extraction.
    8. Signed review link generation + user notification.
    9. Credit deduction.
    10. Webhook event marking.

    Parameters
    ----------
    db:
        An active SQLAlchemy async session.
    phone:
        Sender's E.164 phone number (without '+').
    image_bytes:
        Raw image bytes (already downloaded / decoded).
    mime_type:
        Image MIME type.
    upload_source:
        How the image was received (``"whatsapp"`` or ``"openclaw"``).
    message_id:
        Message ID for idempotency tracking.
    notify:
        Async callback ``(phone, text) -> None`` for sending user
        notifications.  The WhatsApp flow passes a callback that uses
        ``WhatsAppClient``; the OpenClaw flow passes one that uses
        ``OpenClawClient``.
    log:
        Bound structlog logger.
    """
    from app.models import (
        ExtractedData,
        Invoice,
        InvoiceStatus,
        OCRResult as OCRResultModel,
        QualityCheck,
        Tenant,
        User,
    )
    from app.services.ocr_adapter import get_ocr_adapter
    from app.services.quality_check import assess_quality
    from app.services.signed_urls import build_signed_url, SignedLinkType as SLT
    from app.services.storage import save_invoice_image
    from app.services.extraction import extract_invoice_metadata
    from sqlalchemy import select

    # ------------------------------------------------------------------
    # Step 1: Resolve user and tenant from phone number
    # ------------------------------------------------------------------
    log.debug("ocr_worker.step1.resolve_user")

    user_result = await db.execute(
        select(User).where(
            User.whatsapp_phone == phone,
        )
    )
    user: User | None = user_result.scalars().first()

    if user is None:
        log.error("ocr_worker.user_not_found", phone=phone[:4] + "****")
        await _mark_webhook_failed(db, message_id, "User not found for phone.")
        return {"status": "error", "reason": "user_not_found"}

    tenant_result = await db.execute(
        select(Tenant).where(
            Tenant.id == user.tenant_id,
        )
    )
    tenant: Tenant | None = tenant_result.scalars().first()

    if tenant is None:
        log.error("ocr_worker.tenant_not_found", tenant_id=user.tenant_id)
        await _mark_webhook_failed(db, message_id, "Tenant not found.")
        return {"status": "error", "reason": "tenant_not_found"}

    log.debug("ocr_worker.user_resolved", user_id=user.id, tenant_id=tenant.id)

    # ------------------------------------------------------------------
    # Billing enforcement (defense in depth)
    # ------------------------------------------------------------------
    from app.services.billing import can_process_invoice, deduct_credit, is_ultra_plan
    from app.models import UserStatus

    if user.status != UserStatus.ACTIVE:
        log.warning("ocr_worker.user_not_active", user_id=user.id, status=user.status.value)
        await _mark_webhook_failed(db, message_id, f"User not active: {user.status.value}")
        return {"status": "billing_denied", "reason": "not_active"}

    allowed, reason = await can_process_invoice(db, user)
    if not allowed:
        log.warning("ocr_worker.billing_denied", user_id=user.id, reason=reason)
        await _mark_webhook_failed(db, message_id, f"Billing check: {reason}")
        return {"status": "billing_denied", "reason": reason}

    # ------------------------------------------------------------------
    # Step 2: Create invoice record
    # ------------------------------------------------------------------
    invoice_id = str(uuid.uuid4())
    file_hash_value = hashlib.sha256(image_bytes).hexdigest()
    invoice = Invoice(
        id=invoice_id,
        tenant_id=tenant.id,
        user_id=user.id,
        file_path="",  # placeholder; set after storage
        file_hash=file_hash_value,
        status=InvoiceStatus.PROCESSING,
        upload_source=upload_source,
        whatsapp_message_id=message_id,
        month_partition=datetime.now(tz=timezone.utc).strftime("%Y-%m"),
    )
    db.add(invoice)
    await db.flush()
    log.info("ocr_worker.invoice.created", invoice_id=invoice_id)

    # ------------------------------------------------------------------
    # Step 4: Persist image to storage
    # ------------------------------------------------------------------
    log.debug("ocr_worker.step4.storage")
    try:
        storage_path = save_invoice_image(
            tenant_id=tenant.id,
            user_id=user.id,
            image_bytes=image_bytes,
            invoice_id=invoice_id,
            mime_type=mime_type,
        )
        invoice.file_path = storage_path
    except ValueError as exc:
        log.warning("ocr_worker.storage_rejected", reason=str(exc))
        invoice.status = InvoiceStatus.QUALITY_FAILED
        await db.commit()
        await notify(phone, f"Your invoice could not be processed: {exc}")
        return {"status": "rejected", "reason": str(exc), "invoice_id": invoice_id}

    # ------------------------------------------------------------------
    # Step 5: Quality check
    # ------------------------------------------------------------------
    log.debug("ocr_worker.step5.quality")
    import tempfile, os

    ext = _mime_to_ext(mime_type)
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        quality_result = assess_quality(tmp_path)
    finally:
        os.unlink(tmp_path)

    qc = QualityCheck(
        invoice_id=invoice_id,
        blur_score=quality_result.blur_score,
        resolution_ok=quality_result.resolution_ok,
        skew_angle=quality_result.skew_angle,
        overall_pass=quality_result.passed,
        failure_reasons=quality_result.failure_reasons,
    )
    db.add(qc)

    if not quality_result.passed:
        invoice.status = InvoiceStatus.QUALITY_FAILED
        await db.commit()
        reasons = "; ".join(quality_result.failure_reasons)
        await notify(
            phone,
            f"Your invoice image failed quality checks: {reasons}\n"
            "Please send a clearer photo.",
        )
        return {
            "status": "quality_failed",
            "reasons": quality_result.failure_reasons,
            "invoice_id": invoice_id,
        }

    # ------------------------------------------------------------------
    # Step 6: OCR
    # ------------------------------------------------------------------
    log.debug("ocr_worker.step6.ocr")
    invoice.status = InvoiceStatus.PROCESSING
    await db.flush()

    from app.services.storage import resolve_abs_path
    abs_path = resolve_abs_path(storage_path)

    ocr_adapter = get_ocr_adapter()
    try:
        ocr_result = await ocr_adapter.extract_text(abs_path)
    except Exception as exc:
        log.error("ocr_worker.ocr_failed", error=str(exc))
        invoice.status = InvoiceStatus.QUALITY_FAILED
        await db.commit()
        return {"status": "ocr_failed", "invoice_id": invoice_id}
    finally:
        await ocr_adapter.close()

    ocr_row = OCRResultModel(
        invoice_id=invoice_id,
        raw_text=ocr_result.raw_text,
        model_name=ocr_result.engine,
        model_version="v1",
        processing_time_ms=ocr_result.processing_ms,
    )
    db.add(ocr_row)
    await db.flush()

    # ------------------------------------------------------------------
    # Step 7: Extraction
    # ------------------------------------------------------------------
    log.debug("ocr_worker.step7.extraction")
    invoice.status = InvoiceStatus.PROCESSING
    await db.flush()

    try:
        metadata = await extract_invoice_metadata(
            ocr_text=ocr_result.raw_text,
            image_path=abs_path,
        )
    except Exception as exc:
        log.error("ocr_worker.extraction_failed", error=str(exc))
        # Extraction failure is non-fatal; continue with partial data
        metadata = None

    if metadata:
        # Derive the billing month from extracted invoice date if available
        if metadata.invoice_date.value:
            try:
                parsed_date = datetime.strptime(
                    metadata.invoice_date.value, "%Y-%m-%d"
                )
                invoice.month_partition = parsed_date.strftime("%Y-%m")
            except ValueError:
                pass

        extracted_json = {
            "vendor_name": metadata.vendor_name.value,
            "vendor_tax_id": metadata.vendor_tax_id.value,
            "invoice_number": metadata.invoice_number.value,
            "invoice_date": metadata.invoice_date.value,
            "total_amount": metadata.total_amount.value,
            "tax_amount": metadata.tax_amount.value,
            "currency": metadata.currency.value,
            "line_items": metadata.line_items,
        }

        confidence_scores = {
            "vendor_name": metadata.vendor_name.confidence,
            "vendor_tax_id": metadata.vendor_tax_id.confidence,
            "invoice_number": metadata.invoice_number.confidence,
            "invoice_date": metadata.invoice_date.confidence,
            "total_amount": metadata.total_amount.confidence,
            "tax_amount": metadata.tax_amount.confidence,
            "currency": metadata.currency.confidence,
            "overall": metadata.overall_confidence,
        }

        extracted = ExtractedData(
            invoice_id=invoice_id,
            ocr_result_id=ocr_row.invoice_id,
            extracted_json=extracted_json,
            confidence_scores=confidence_scores,
            extraction_version="v1",
        )
        db.add(extracted)

    invoice.status = InvoiceStatus.EXTRACTED
    await db.flush()

    # ------------------------------------------------------------------
    # Step 8: Notify user with signed review link
    # ------------------------------------------------------------------
    signed_url = build_signed_url(
        base_url=settings.PUBLIC_BASE_URL,
        invoice_id=invoice_id,
        user_id=user.id,
        tenant_id=tenant.id,
        link_type=SLT.CORRECTION_FORM,
    )

    notify_text = (
        "Your invoice has been processed! Please review the extracted data:\n"
        f"{signed_url}\n\n"
        "Tap the link to confirm or correct the details."
    )
    await notify(phone, notify_text)

    # ------------------------------------------------------------------
    # Step 9: Deduct credit if on credits plan
    # ------------------------------------------------------------------
    allowed, reason = await can_process_invoice(db, user)
    if reason == "credits_available":
        await deduct_credit(db, str(user.id), invoice_id)
        log.info("ocr_worker.credit_deducted", user_id=user.id, invoice_id=invoice_id)

    # ------------------------------------------------------------------
    # Step 10: Mark webhook event as processed
    # ------------------------------------------------------------------
    await _mark_webhook_processed(db, message_id, invoice_id)
    await db.commit()

    log.info(
        "ocr_worker.pipeline.complete",
        invoice_id=invoice_id,
        status=invoice.status.value,
    )

    return {
        "status": "success",
        "invoice_id": invoice_id,
        "invoice_status": invoice.status.value,
    }


# ---------------------------------------------------------------------------
# DB helper utilities
# ---------------------------------------------------------------------------


async def _mark_webhook_processed(
    db: Any, message_id: str, invoice_id: str
) -> None:
    from app.models import WebhookEvent, WebhookEventStatus
    from sqlalchemy import select

    result = await db.execute(
        select(WebhookEvent).where(WebhookEvent.message_id == message_id)
    )
    event = result.scalars().first()
    if event:
        event.status = WebhookEventStatus.PROCESSED
        event.processed_at = datetime.now(tz=timezone.utc)


async def _mark_webhook_failed(
    db: Any, message_id: str, reason: str
) -> None:
    from app.models import WebhookEvent, WebhookEventStatus
    from sqlalchemy import select

    result = await db.execute(
        select(WebhookEvent).where(WebhookEvent.message_id == message_id)
    )
    event = result.scalars().first()
    if event:
        event.status = WebhookEventStatus.FAILED
        event.error_detail = reason
    await db.commit()


def _mime_to_ext(mime_type: str) -> str:
    """Return a file extension for the given MIME type."""
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }.get(mime_type, ".jpg")
