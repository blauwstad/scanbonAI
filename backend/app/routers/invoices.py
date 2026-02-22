"""
Invoice router for ScanbonAI – user-facing endpoints.

Endpoints
---------
GET    /api/v1/invoices                     – List invoices for the current user.
GET    /api/v1/invoices/{id}                – Full invoice detail with extraction.
GET    /api/v1/invoices/{id}/image          – Signed URL to the invoice image.
PUT    /api/v1/invoices/{id}/corrections    – Submit field corrections.
POST   /api/v1/invoices/{id}/confirm        – Confirm extraction as correct.

Tenant isolation
----------------
Every query is scoped to ``current_user.tenant_id`` and ``current_user.id``
(regular users can only see their own invoices).  Admins bypass the user
filter via the admin router.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import CurrentUser, DBSession, get_current_user, get_db_session
from app.models import (
    ExtractedData,
    Invoice,
    InvoiceStatus,
    SignedLink,
    SignedLinkType,
    UserCorrection,
)
from app.schemas import (
    CorrectionRequest,
    InvoiceDetailSchema,
    InvoiceListItemSchema,
    InvoiceMetadataSchema,
    PaginatedResponse,
    SignedLinkSchema,
    SuccessResponse,
)
from app.services.signed_urls import build_signed_url, generate_signed_token
from app.services.storage import resolve_abs_path

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _get_invoice_or_404(
    invoice_id: str,
    user_id: str,
    tenant_id: str,
    db: AsyncSession,
    *,
    load_relations: bool = False,
) -> Invoice:
    """Fetch an invoice owned by the given user+tenant, raising 404 if absent."""
    query = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.user_id == user_id,
        Invoice.tenant_id == tenant_id,
    )
    if load_relations:
        query = query.options(
            selectinload(Invoice.quality_check),
            selectinload(Invoice.extracted_data),
            selectinload(Invoice.corrections),
            selectinload(Invoice.admin_review),
        )

    result = await db.execute(query)
    invoice: Invoice | None = result.scalars().first()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice {invoice_id!r} not found.",
        )
    return invoice


# ---------------------------------------------------------------------------
# GET /api/v1/invoices
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedResponse[InvoiceListItemSchema],
    summary="List invoices",
)
async def list_invoices(
    db: DBSession,
    current_user: CurrentUser,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    invoice_status: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[InvoiceListItemSchema]:
    """
    Return a paginated list of invoices belonging to the current user.

    Filters
    -------
    month:
        Optional ``YYYY-MM`` filter (e.g. ``?month=2024-03``).
    status:
        Filter by invoice processing status.
    """
    base_query = select(Invoice).where(
        Invoice.user_id == current_user.id,
        Invoice.tenant_id == current_user.tenant_id,
    )

    if month:
        base_query = base_query.where(Invoice.month == month)
    if invoice_status:
        base_query = base_query.where(Invoice.status == invoice_status)

    # Count total rows for pagination metadata
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total: int = count_result.scalar_one()

    # Fetch current page
    offset = (page - 1) * page_size
    result = await db.execute(
        base_query.order_by(Invoice.created_at.desc()).offset(offset).limit(page_size)
    )
    invoices = result.scalars().all()

    import math

    return PaginatedResponse(
        data=[InvoiceListItemSchema.model_validate(inv) for inv in invoices],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/{invoice_id}",
    response_model=SuccessResponse[InvoiceDetailSchema],
    summary="Invoice detail",
)
async def get_invoice(
    invoice_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> SuccessResponse[InvoiceDetailSchema]:
    """
    Return the full invoice detail including quality check results,
    extracted data, and user corrections.
    """
    invoice = await _get_invoice_or_404(
        invoice_id,
        current_user.id,
        current_user.tenant_id,
        db,
        load_relations=True,
    )

    detail = _build_detail_schema(invoice)
    return SuccessResponse(data=detail)


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/{id}/image
# ---------------------------------------------------------------------------


@router.get(
    "/{invoice_id}/image",
    response_model=SuccessResponse[SignedLinkSchema],
    summary="Get signed image URL",
)
async def get_invoice_image_url(
    invoice_id: str,
    db: DBSession,
    current_user: CurrentUser,
    request_obj: Any = Depends(lambda: None),  # placeholder for request injection
) -> SuccessResponse[SignedLinkSchema]:
    """
    Generate and return a signed URL for accessing the invoice image.

    The signed URL is valid for ``settings.SIGNED_URL_EXPIRY_SECONDS`` seconds.
    The URL resolves via ``GET /api/v1/signed/{token}``.

    Callers should treat the URL as ephemeral and not cache it beyond its
    ``expires_at`` timestamp.
    """
    from datetime import datetime, timedelta, timezone

    invoice = await _get_invoice_or_404(
        invoice_id, current_user.id, current_user.tenant_id, db
    )

    if invoice.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image is not yet available for this invoice.",
        )

    token = generate_signed_token(
        invoice_id=invoice_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        link_type=SignedLinkType.VIEW_IMAGE,
    )

    expires_at = datetime.now(tz=timezone.utc) + timedelta(
        seconds=settings.SIGNED_URL_EXPIRY_SECONDS
    )

    # Persist the signed link record (for audit / future revocation)
    signed_link = SignedLink(
        invoice_id=invoice_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        token=token,
        link_type=SignedLinkType.VIEW_IMAGE,
        expires_at=expires_at,
    )
    db.add(signed_link)

    # Build the URL – base URL would typically come from settings or request
    # FUTURE: extract base_url from settings.BASE_URL or request.base_url
    base_url = "https://app.scanbonai.com"
    url = f"{base_url}/api/v1/signed/{token}"

    logger.info(
        "invoice.image_url.generated",
        invoice_id=invoice_id,
        user_id=current_user.id,
    )

    return SuccessResponse(
        data=SignedLinkSchema(
            token=token,
            link_type=SignedLinkType.VIEW_IMAGE,
            expires_at=expires_at,
            url=url,
        )
    )


# ---------------------------------------------------------------------------
# PUT /api/v1/invoices/{id}/corrections
# ---------------------------------------------------------------------------


@router.put(
    "/{invoice_id}/corrections",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Submit field corrections",
)
async def submit_corrections(
    invoice_id: str,
    body: CorrectionRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> SuccessResponse[dict[str, Any]]:
    """
    Submit user corrections for extracted invoice fields.

    The endpoint accepts a list of ``{field_name, corrected_value}`` pairs.
    Each pair is stored as a ``UserCorrection`` row with the original
    extracted value snapshotted at write time.

    The invoice status is updated to ``USER_CORRECTED`` after the write.

    Allowed fields
    --------------
    Only fields that exist on ``ExtractedData`` can be corrected:
    vendor_name, vendor_tax_id, invoice_number, invoice_date,
    total_amount, tax_amount, currency.
    """
    _CORRECTABLE_FIELDS = {
        "vendor_name",
        "vendor_tax_id",
        "invoice_number",
        "invoice_date",
        "total_amount",
        "tax_amount",
        "currency",
    }

    invoice = await _get_invoice_or_404(
        invoice_id,
        current_user.id,
        current_user.tenant_id,
        db,
        load_relations=True,
    )

    # Only invoices awaiting review can be corrected
    if invoice.status not in (
        InvoiceStatus.AWAITING_USER_REVIEW,
        InvoiceStatus.USER_CORRECTED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Invoice in status {invoice.status!r} cannot accept corrections. "
                "It must be in 'awaiting_user_review' or 'user_corrected'."
            ),
        )

    # Validate field names
    invalid_fields = {
        item.field_name
        for item in body.corrections
        if item.field_name not in _CORRECTABLE_FIELDS
    }
    if invalid_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown correctable fields: {sorted(invalid_fields)}",
        )

    # Retrieve current extracted values for snapshotting
    extracted: ExtractedData | None = invoice.extracted_data

    created_corrections: list[str] = []
    for item in body.corrections:
        original = getattr(extracted, item.field_name, None) if extracted else None
        correction = UserCorrection(
            invoice_id=invoice_id,
            user_id=current_user.id,
            field_name=item.field_name,
            original_value=str(original) if original is not None else None,
            corrected_value=item.corrected_value,
        )
        db.add(correction)
        created_corrections.append(item.field_name)

    invoice.status = InvoiceStatus.USER_CORRECTED

    logger.info(
        "invoice.corrections.submitted",
        invoice_id=invoice_id,
        user_id=current_user.id,
        fields=created_corrections,
    )

    return SuccessResponse(
        data={
            "invoice_id": invoice_id,
            "corrections_applied": created_corrections,
            "new_status": InvoiceStatus.USER_CORRECTED.value,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/invoices/{id}/confirm
# ---------------------------------------------------------------------------


@router.post(
    "/{invoice_id}/confirm",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Confirm invoice as correct",
)
async def confirm_invoice(
    invoice_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> SuccessResponse[dict[str, Any]]:
    """
    Mark the extracted invoice data as correct without any corrections.

    This endpoint is the "approve" action on the user-review step.  After
    confirmation the invoice moves to ``user_confirmed`` status and is queued
    for admin review.

    Raises 409 if the invoice is not in ``awaiting_user_review`` status.
    """
    invoice = await _get_invoice_or_404(
        invoice_id, current_user.id, current_user.tenant_id, db
    )

    if invoice.status != InvoiceStatus.AWAITING_USER_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Invoice in status {invoice.status!r} cannot be confirmed. "
                "It must be in 'awaiting_user_review'."
            ),
        )

    invoice.status = InvoiceStatus.USER_CONFIRMED

    logger.info(
        "invoice.confirmed",
        invoice_id=invoice_id,
        user_id=current_user.id,
    )

    return SuccessResponse(
        data={
            "invoice_id": invoice_id,
            "new_status": InvoiceStatus.USER_CONFIRMED.value,
        }
    )


# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------


def _build_detail_schema(invoice: Invoice) -> InvoiceDetailSchema:
    """
    Build an ``InvoiceDetailSchema`` from an ORM Invoice with loaded relations.
    """
    extracted_schema: InvoiceMetadataSchema | None = None
    if invoice.extracted_data:
        ed = invoice.extracted_data
        fc: dict[str, Any] = ed.field_confidence or {}

        def _fwc(field_name: str) -> dict[str, Any]:
            return {
                "value": getattr(ed, field_name, None),
                "confidence": fc.get(field_name),
            }

        extracted_schema = InvoiceMetadataSchema(
            vendor_name=_fwc("vendor_name"),
            vendor_tax_id=_fwc("vendor_tax_id"),
            invoice_number=_fwc("invoice_number"),
            invoice_date=_fwc("invoice_date"),
            total_amount=_fwc("total_amount"),
            tax_amount=_fwc("tax_amount"),
            currency=_fwc("currency"),
            line_items=ed.line_items,
            overall_confidence=ed.overall_confidence,
        )

    return InvoiceDetailSchema.model_validate(
        {
            **invoice.__dict__,
            "extracted_data": extracted_schema,
        }
    )
