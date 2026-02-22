"""
Signed links router for ScanbonAI.

Endpoints
---------
GET /api/v1/signed/{token}  – Resolve a signed token and return the resource.

This router handles the end of the magic-link / image-access URL flow.
When a user taps a signed link (sent via WhatsApp), the request lands here.

Token types
-----------
- ``view_image``       – Redirect to (or stream) the invoice image.
- ``correction_form``  – Return invoice data pre-populated for correction UI.
- ``confirm``          – Immediately confirm the invoice as correct.

Security
--------
- Tokens are validated by ``validate_signed_token`` (HMAC-SHA256 + expiry).
- Invalid or expired tokens receive a 403 without leaking details.
- Tenant isolation is enforced: the token's ``tenant_id`` must match the
  invoice's ``tenant_id`` in the database.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import DBSession, get_db_session
from app.models import Invoice, InvoiceStatus, SignedLink, SignedLinkType, UserCorrection
from app.schemas import SignedLinkResolveResponse, SuccessResponse
from app.services.signed_urls import validate_signed_token
from app.services.storage import resolve_abs_path

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/signed", tags=["signed-links"])


# ---------------------------------------------------------------------------
# GET /api/v1/signed/{token}
# ---------------------------------------------------------------------------


@router.get(
    "/{token}",
    summary="Resolve a signed link",
    response_model=None,
)
async def resolve_signed_link(
    token: str,
    db: DBSession,
) -> FileResponse | SuccessResponse[SignedLinkResolveResponse]:
    """
    Resolve a signed token and return the appropriate resource.

    Behaviour by link type
    ----------------------
    - ``view_image``      – Serve the raw invoice image as a ``FileResponse``.
    - ``correction_form`` – Return invoice metadata for the correction UI.
    - ``confirm``         – Auto-confirm the invoice and return a status response.

    Raises
    ------
    403 Forbidden
        Token is invalid, expired, or tenant_id mismatch.
    404 Not Found
        Invoice or image file not found.
    """
    # --- Validate token ---
    try:
        payload = validate_signed_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    invoice_id: str = payload["invoice_id"]
    user_id: str = payload["user_id"]
    tenant_id: str = payload["tenant_id"]
    link_type_str: str = payload["link_type"]

    try:
        link_type = SignedLinkType(link_type_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token.",
        )

    log = logger.bind(
        invoice_id=invoice_id,
        user_id=user_id,
        tenant_id=tenant_id,
        link_type=link_type_str,
    )

    # --- Fetch and validate invoice ---
    invoice_result = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.tenant_id == tenant_id,
        )
    )
    invoice: Invoice | None = invoice_result.scalars().first()

    if invoice is None:
        log.warning("signed_link.invoice_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    # Ensure the user in the token matches the invoice owner
    if invoice.user_id != user_id:
        log.warning("signed_link.user_mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token.",
        )

    log.info("signed_link.resolved")

    # --- Dispatch by link type ---
    if link_type == SignedLinkType.VIEW_IMAGE:
        return await _serve_image(invoice, log)

    if link_type == SignedLinkType.CORRECTION_FORM:
        return await _serve_correction_form(invoice, log)

    if link_type == SignedLinkType.CONFIRM:
        return await _handle_confirm(invoice, db, log)

    # Should never reach here given the enum validation above
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported link type: {link_type_str!r}",
    )


# ---------------------------------------------------------------------------
# Handlers by link type
# ---------------------------------------------------------------------------


async def _serve_image(invoice: Invoice, log: Any) -> FileResponse:
    """Stream the raw invoice image file."""
    if invoice.file_path is None:
        log.warning("signed_link.image_no_path")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image is not yet available.",
        )

    abs_path = resolve_abs_path(invoice.file_path)
    if not Path(abs_path).exists():
        log.error("signed_link.image_file_missing", path=abs_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image file not found on storage.",
        )

    return FileResponse(
        path=abs_path,
        media_type="image/jpeg",
        filename=f"invoice-{invoice.id}.jpg",
    )


async def _serve_correction_form(
    invoice: Invoice,
    log: Any,
) -> SuccessResponse[SignedLinkResolveResponse]:
    """Return metadata for pre-populating the correction form."""
    log.info("signed_link.correction_form.served")
    return SuccessResponse(
        data=SignedLinkResolveResponse(
            link_type=SignedLinkType.CORRECTION_FORM,
            invoice_id=invoice.id,
            user_id=invoice.user_id,
            tenant_id=invoice.tenant_id,
        )
    )


async def _handle_confirm(
    invoice: Invoice,
    db: AsyncSession,
    log: Any,
) -> SuccessResponse[SignedLinkResolveResponse]:
    """Auto-confirm the invoice if it is awaiting review."""
    if invoice.status == InvoiceStatus.EXTRACTED:
        invoice.status = InvoiceStatus.REVIEWED
        log.info("signed_link.confirm.accepted")
    else:
        log.info(
            "signed_link.confirm.noop",
            current_status=invoice.status.value,
        )

    return SuccessResponse(
        data=SignedLinkResolveResponse(
            link_type=SignedLinkType.CONFIRM,
            invoice_id=invoice.id,
            user_id=invoice.user_id,
            tenant_id=invoice.tenant_id,
        )
    )


# ---------------------------------------------------------------------------
# Type annotation shim (Any is needed for the structlog BoundLogger)
# ---------------------------------------------------------------------------
from typing import Any  # noqa: E402 – must be after the functions that reference it
