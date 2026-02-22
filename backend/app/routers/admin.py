"""
Admin router for ScanbonAI.

Endpoints
---------
GET  /api/v1/admin/invoices           – All invoices across users (with diffs).
GET  /api/v1/admin/invoices/{id}      – Invoice detail with full diff view data.
POST /api/v1/admin/invoices/{id}/review – Approve / override / reject.
GET  /api/v1/admin/metrics            – Aggregated processing metrics.
GET  /api/v1/admin/export             – Export invoices as CSV or JSON.

Access control
--------------
All endpoints require ``require_admin`` which checks ``user.is_admin == True``.
Tenant isolation is still enforced – admin users see all invoices within their
own tenant only.

FUTURE: Super-admin role that can view across tenants.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import AdminUser, DBSession, require_admin
from app.models import (
    AdminDecision,
    AdminReview,
    ExtractedData,
    Invoice,
    InvoiceStatus,
    UserCorrection,
)
from app.schemas import (
    AdminInvoiceDetailSchema,
    AdminReviewRequest,
    InvoiceListItemSchema,
    InvoiceMetadataSchema,
    MetricsResponse,
    PaginatedResponse,
    SuccessResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _get_admin_invoice_or_404(
    invoice_id: str,
    tenant_id: str,
    db: AsyncSession,
    *,
    load_relations: bool = False,
) -> Invoice:
    """Fetch any invoice within the tenant, raise 404 if not found."""
    query = select(Invoice).where(
        Invoice.id == invoice_id,
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
# GET /api/v1/admin/invoices
# ---------------------------------------------------------------------------


@router.get(
    "/invoices",
    response_model=PaginatedResponse[InvoiceListItemSchema],
    summary="List all invoices (admin)",
)
async def admin_list_invoices(
    admin_user: AdminUser,
    db: DBSession,
    user_id: Annotated[str | None, Query()] = None,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    invoice_status: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaginatedResponse[InvoiceListItemSchema]:
    """
    Return a paginated list of all invoices within the admin's tenant.

    Supports filtering by ``user_id``, ``month``, and ``status``.
    """
    base_query = select(Invoice).where(Invoice.tenant_id == admin_user.tenant_id)

    if user_id:
        base_query = base_query.where(Invoice.user_id == user_id)
    if month:
        base_query = base_query.where(Invoice.month == month)
    if invoice_status:
        base_query = base_query.where(Invoice.status == invoice_status)

    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total: int = count_result.scalar_one()

    offset = (page - 1) * page_size
    result = await db.execute(
        base_query.order_by(Invoice.created_at.desc()).offset(offset).limit(page_size)
    )
    invoices = result.scalars().all()

    return PaginatedResponse(
        data=[InvoiceListItemSchema.model_validate(inv) for inv in invoices],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/invoices/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/invoices/{invoice_id}",
    response_model=SuccessResponse[AdminInvoiceDetailSchema],
    summary="Invoice detail with diff view (admin)",
)
async def admin_get_invoice(
    invoice_id: str,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[AdminInvoiceDetailSchema]:
    """
    Return full invoice detail including:
    - Extracted data with per-field confidence.
    - User corrections diff (extraction vs. corrected values).
    - Admin review (if already reviewed).
    - Admin override diff (correction vs. admin override, if applicable).
    """
    invoice = await _get_admin_invoice_or_404(
        invoice_id, admin_user.tenant_id, db, load_relations=True
    )

    detail = _build_admin_detail_schema(invoice)
    return SuccessResponse(data=detail)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/invoices/{id}/review
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/{invoice_id}/review",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Submit admin review decision",
)
async def admin_review_invoice(
    invoice_id: str,
    body: AdminReviewRequest,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict[str, Any]]:
    """
    Submit an admin review decision for an invoice.

    Decision options
    ----------------
    - ``approved``   – Mark the invoice as approved without changes.
    - ``overridden`` – Approve with admin-supplied field overrides.
    - ``rejected``   – Reject the invoice (e.g. it is not a valid tax invoice).

    The ``override_data`` field is required when decision is ``overridden``
    and must contain a dict of ``{field_name: new_value}`` pairs.
    """
    invoice = await _get_admin_invoice_or_404(
        invoice_id, admin_user.tenant_id, db
    )

    # Validate state transition: only invoices pending admin review can be reviewed
    if invoice.status not in (
        InvoiceStatus.USER_CONFIRMED,
        InvoiceStatus.USER_CORRECTED,
        InvoiceStatus.ADMIN_REVIEW_PENDING,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Invoice in status {invoice.status!r} cannot be reviewed. "
                "It must be in 'user_confirmed', 'user_corrected', or "
                "'admin_review_pending'."
            ),
        )

    # Validate override_data presence when decision is OVERRIDDEN
    if body.decision == AdminDecision.OVERRIDDEN and not body.override_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="override_data is required when decision is 'overridden'.",
        )

    # Create or replace the admin review record
    existing_result = await db.execute(
        select(AdminReview).where(AdminReview.invoice_id == invoice_id)
    )
    existing: AdminReview | None = existing_result.scalars().first()

    if existing:
        existing.decision = body.decision
        existing.override_data = body.override_data
        existing.notes = body.notes
        existing.reviewed_by = admin_user.id
        existing.reviewed_at = datetime.now(tz=timezone.utc)
    else:
        review = AdminReview(
            invoice_id=invoice_id,
            reviewed_by=admin_user.id,
            decision=body.decision,
            override_data=body.override_data,
            notes=body.notes,
        )
        db.add(review)

    # Update invoice status
    new_status_map = {
        AdminDecision.APPROVED: InvoiceStatus.APPROVED,
        AdminDecision.OVERRIDDEN: InvoiceStatus.APPROVED,
        AdminDecision.REJECTED: InvoiceStatus.REJECTED,
    }
    invoice.status = new_status_map[body.decision]

    logger.info(
        "admin.review.submitted",
        invoice_id=invoice_id,
        admin_id=admin_user.id,
        decision=body.decision.value,
    )

    return SuccessResponse(
        data={
            "invoice_id": invoice_id,
            "decision": body.decision.value,
            "new_status": invoice.status.value,
        }
    )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/metrics
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    response_model=SuccessResponse[MetricsResponse],
    summary="Aggregated admin metrics",
)
async def admin_metrics(
    admin_user: AdminUser,
    db: DBSession,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> SuccessResponse[MetricsResponse]:
    """
    Return aggregated metrics for the admin's tenant.

    Optional ``month`` filter scopes all counts to a single month.
    """
    base_filter = [Invoice.tenant_id == admin_user.tenant_id]
    if month:
        base_filter.append(Invoice.month == month)

    # Total invoices
    total_result = await db.execute(
        select(func.count(Invoice.id)).where(*base_filter)
    )
    total_invoices: int = total_result.scalar_one()

    # Invoices by status
    status_result = await db.execute(
        select(Invoice.status, func.count(Invoice.id))
        .where(*base_filter)
        .group_by(Invoice.status)
    )
    invoices_by_status: dict[str, int] = {
        row[0].value: row[1] for row in status_result.all()
    }

    # Average confidence
    conf_result = await db.execute(
        select(func.avg(ExtractedData.overall_confidence)).where(
            ExtractedData.invoice_id.in_(
                select(Invoice.id).where(*base_filter)
            )
        )
    )
    avg_confidence: float | None = conf_result.scalar_one()

    # Correction count
    corrections_result = await db.execute(
        select(func.count(UserCorrection.id)).where(
            UserCorrection.invoice_id.in_(
                select(Invoice.id).where(*base_filter)
            )
        )
    )
    corrections_count: int = corrections_result.scalar_one()

    # Approval / rejection counts
    approvals = invoices_by_status.get(InvoiceStatus.APPROVED.value, 0)
    rejections = invoices_by_status.get(InvoiceStatus.REJECTED.value, 0)

    return SuccessResponse(
        data=MetricsResponse(
            total_invoices=total_invoices,
            invoices_by_status=invoices_by_status,
            avg_confidence=round(avg_confidence, 4) if avg_confidence is not None else None,
            corrections_submitted=corrections_count,
            approvals=approvals,
            rejections=rejections,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/export
# ---------------------------------------------------------------------------


@router.get(
    "/export",
    summary="Export invoices (CSV or JSON)",
)
async def admin_export(
    admin_user: AdminUser,
    db: DBSession,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    invoice_status: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    fmt: Annotated[str, Query(alias="format", pattern=r"^(csv|json)$")] = "csv",
) -> StreamingResponse:
    """
    Export all matching invoices with their extracted data as CSV or JSON.

    The response is streamed to avoid loading large datasets into memory.
    """
    base_filter = [Invoice.tenant_id == admin_user.tenant_id]
    if month:
        base_filter.append(Invoice.month == month)
    if invoice_status:
        base_filter.append(Invoice.status == invoice_status)

    result = await db.execute(
        select(Invoice)
        .where(*base_filter)
        .options(selectinload(Invoice.extracted_data))
        .order_by(Invoice.created_at.asc())
    )
    invoices = result.scalars().all()

    rows = [_invoice_to_export_row(inv) for inv in invoices]

    if fmt == "json":
        content = json.dumps(rows, default=str)
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="export-{month or "all"}.json"'
            },
        )

    # CSV
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="export-{month or "all"}.csv"'
        },
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_admin_detail_schema(invoice: Invoice) -> AdminInvoiceDetailSchema:
    """Build an ``AdminInvoiceDetailSchema`` with diff view data populated."""
    from app.routers.invoices import _build_detail_schema  # local import

    base = _build_detail_schema(invoice)

    # Build extraction vs. correction diff
    ext_vs_corr: dict[str, Any] = {}
    if invoice.extracted_data and invoice.corrections:
        ed = invoice.extracted_data
        for corr in invoice.corrections:
            extracted_val = getattr(ed, corr.field_name, None)
            ext_vs_corr[corr.field_name] = {
                "extracted": extracted_val,
                "corrected": corr.corrected_value,
                "changed": str(extracted_val) != str(corr.corrected_value),
            }

    # Build correction vs. admin override diff
    corr_vs_admin: dict[str, Any] = {}
    if invoice.admin_review and invoice.admin_review.override_data:
        correction_map = {
            corr.field_name: corr.corrected_value
            for corr in (invoice.corrections or [])
        }
        for field_name, override_val in invoice.admin_review.override_data.items():
            corr_val = correction_map.get(field_name)
            corr_vs_admin[field_name] = {
                "corrected": corr_val,
                "admin_override": override_val,
                "changed": str(corr_val) != str(override_val),
            }

    return AdminInvoiceDetailSchema(
        **base.model_dump(),
        extraction_vs_correction_diff=ext_vs_corr or None,
        correction_vs_admin_diff=corr_vs_admin or None,
    )


def _invoice_to_export_row(invoice: Invoice) -> dict[str, Any]:
    """Flatten an invoice + extracted data into a dict row for CSV/JSON export."""
    ed: ExtractedData | None = invoice.extracted_data
    return {
        "invoice_id": invoice.id,
        "user_id": invoice.user_id,
        "tenant_id": invoice.tenant_id,
        "status": invoice.status.value,
        "month": invoice.month,
        "created_at": invoice.created_at.isoformat(),
        "vendor_name": ed.vendor_name if ed else None,
        "vendor_tax_id": ed.vendor_tax_id if ed else None,
        "invoice_number": ed.invoice_number if ed else None,
        "invoice_date": ed.invoice_date if ed else None,
        "total_amount": ed.total_amount if ed else None,
        "tax_amount": ed.tax_amount if ed else None,
        "currency": ed.currency if ed else None,
        "overall_confidence": ed.overall_confidence if ed else None,
    }
