"""
Celery worker tasks for ScanbonAI invoice processing.

Task pipeline
-------------
``process_invoice`` orchestrates the full processing pipeline:

  1. Resolve tenant + user from the phone number.
  2. Download the image from WhatsApp.
  3. Persist raw bytes to storage.
  4. Run image quality gates (blur / resolution / skew).
  5. If quality passes → call OCR engine.
  6. Extract structured metadata from OCR text.
  7. Persist all results to the database.
  8. Update invoice status and notify the user via WhatsApp.

Retry policy
------------
Each step that involves external I/O is wrapped with Celery's built-in retry
mechanism using exponential backoff.  The entire ``process_invoice`` task is
also configured for retry on transient failures.

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
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any

import structlog
from celery import Celery
from celery.utils.log import get_task_logger

from app.config import settings

logger = get_task_logger(__name__)
structlog_logger = structlog.get_logger(__name__)

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
# Main task
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
# Async pipeline implementation
# ---------------------------------------------------------------------------


async def _process_invoice_async(
    message_id: str,
    phone: str,
    media_id: str,
    mime_type: str,
    log: Any,
) -> dict[str, Any]:
    """
    Async implementation of the full invoice processing pipeline.

    This function is called from the synchronous Celery task via ``run_async``.
    All DB interactions use SQLAlchemy async sessions opened here.
    """
    from app.database import async_session
    from app.models import (
        ExtractedData,
        Invoice,
        InvoiceStatus,
        OCRResult as OCRResultModel,
        QualityCheck,
        Tenant,
        User,
        WebhookEvent,
        WebhookEventStatus,
    )
    from app.services.ocr_adapter import get_ocr_adapter
    from app.services.quality_check import assess_quality
    from app.services.signed_urls import build_signed_url, SignedLinkType as SLT
    from app.services.storage import save_invoice_image
    from app.services.whatsapp import WhatsAppClient
    from app.services.extraction import extract_invoice_metadata
    from sqlalchemy import select

    async with async_session() as db:

        # ------------------------------------------------------------------
        # Step 1: Resolve user and tenant from phone number
        # ------------------------------------------------------------------
        log.debug("ocr_worker.step1.resolve_user")

        user_result = await db.execute(
            select(User).where(
                User.phone_number == phone,
                User.is_active.is_(True),
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
                Tenant.is_active.is_(True),
            )
        )
        tenant: Tenant | None = tenant_result.scalars().first()

        if tenant is None:
            log.error("ocr_worker.tenant_not_found", tenant_id=user.tenant_id)
            await _mark_webhook_failed(db, message_id, "Tenant not found.")
            return {"status": "error", "reason": "tenant_not_found"}

        log.debug("ocr_worker.user_resolved", user_id=user.id, tenant_id=tenant.id)

        # ------------------------------------------------------------------
        # Step 2: Create invoice record
        # ------------------------------------------------------------------
        invoice_id = str(uuid.uuid4())
        invoice = Invoice(
            id=invoice_id,
            tenant_id=tenant.id,
            user_id=user.id,
            whatsapp_media_id=media_id,
            mime_type=mime_type,
            status=InvoiceStatus.OCR_PENDING,
            month=datetime.now(tz=timezone.utc).strftime("%Y-%m"),
        )
        db.add(invoice)
        await db.flush()
        log.info("ocr_worker.invoice.created", invoice_id=invoice_id)

        # ------------------------------------------------------------------
        # Step 3: Download image from WhatsApp
        # ------------------------------------------------------------------
        log.debug("ocr_worker.step3.download")
        try:
            async with WhatsAppClient() as wa:
                image_bytes = await wa.download_media(media_id)
        except Exception as exc:
            log.error("ocr_worker.download_failed", error=str(exc))
            invoice.status = InvoiceStatus.OCR_FAILED
            await db.commit()
            return {"status": "error", "reason": "download_failed", "invoice_id": invoice_id}

        invoice.file_size_bytes = len(image_bytes)

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
            invoice.storage_path = storage_path
        except ValueError as exc:
            log.warning("ocr_worker.storage_rejected", reason=str(exc))
            invoice.status = InvoiceStatus.QUALITY_FAILED
            await db.commit()
            # Notify user
            async with WhatsAppClient() as wa:
                await wa.send_text_message(
                    phone,
                    f"Your invoice could not be processed: {exc}",
                )
            return {"status": "rejected", "reason": str(exc), "invoice_id": invoice_id}

        # ------------------------------------------------------------------
        # Step 5: Quality check
        # ------------------------------------------------------------------
        log.debug("ocr_worker.step5.quality")
        import tempfile, os

        # Write bytes to a temp file for quality check (uses file path API)
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
            passed=quality_result.passed,
            failure_reasons=quality_result.failure_reasons,
        )
        db.add(qc)

        if not quality_result.passed:
            invoice.status = InvoiceStatus.QUALITY_FAILED
            await db.commit()
            reasons = "; ".join(quality_result.failure_reasons)
            async with WhatsAppClient() as wa:
                await wa.send_text_message(
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
        invoice.status = InvoiceStatus.OCR_PENDING
        await db.flush()

        abs_path = storage_path  # relative path; resolve to absolute
        from app.services.storage import resolve_abs_path
        abs_path = resolve_abs_path(storage_path)

        ocr_adapter = get_ocr_adapter()
        try:
            ocr_result = await ocr_adapter.extract_text(abs_path)
        except Exception as exc:
            log.error("ocr_worker.ocr_failed", error=str(exc))
            invoice.status = InvoiceStatus.OCR_FAILED
            await db.commit()
            return {"status": "ocr_failed", "invoice_id": invoice_id}
        finally:
            await ocr_adapter.close()

        ocr_row = OCRResultModel(
            invoice_id=invoice_id,
            engine=ocr_result.engine,
            raw_text=ocr_result.raw_text,
            raw_response=ocr_result.raw_response,
            confidence=ocr_result.confidence,
            processing_ms=ocr_result.processing_ms,
        )
        db.add(ocr_row)

        # ------------------------------------------------------------------
        # Step 7: Extraction
        # ------------------------------------------------------------------
        log.debug("ocr_worker.step7.extraction")
        invoice.status = InvoiceStatus.EXTRACTION_PENDING
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
                    invoice.month = parsed_date.strftime("%Y-%m")
                except ValueError:
                    pass

            field_confidence = {
                "vendor_name": metadata.vendor_name.confidence,
                "vendor_tax_id": metadata.vendor_tax_id.confidence,
                "invoice_number": metadata.invoice_number.confidence,
                "invoice_date": metadata.invoice_date.confidence,
                "total_amount": metadata.total_amount.confidence,
                "tax_amount": metadata.tax_amount.confidence,
                "currency": metadata.currency.confidence,
            }

            extracted = ExtractedData(
                invoice_id=invoice_id,
                vendor_name=metadata.vendor_name.value,
                vendor_tax_id=metadata.vendor_tax_id.value,
                invoice_number=metadata.invoice_number.value,
                invoice_date=metadata.invoice_date.value,
                total_amount=metadata.total_amount.value,
                tax_amount=metadata.tax_amount.value,
                currency=metadata.currency.value,
                line_items=metadata.line_items,
                field_confidence=field_confidence,
                overall_confidence=metadata.overall_confidence,
            )
            db.add(extracted)

        invoice.status = InvoiceStatus.AWAITING_USER_REVIEW
        await db.flush()

        # ------------------------------------------------------------------
        # Step 8: Notify user with signed review link
        # ------------------------------------------------------------------
        signed_url = build_signed_url(
            base_url="https://app.scanbonai.com",
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
        try:
            async with WhatsAppClient() as wa:
                await wa.send_text_message(phone=phone, text=notify_text)
        except Exception as exc:
            log.warning("ocr_worker.notify_failed", error=str(exc))
            # Non-fatal: don't fail the task because a WhatsApp message failed

        # ------------------------------------------------------------------
        # Step 9: Mark webhook event as processed
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
