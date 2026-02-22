"""
Pydantic v2 schemas for ScanbonAI API request/response payloads.

Naming convention
-----------------
- *Schema   – ORM-bound read schemas (from_attributes=True)
- *Request  – Inbound write payloads validated at the route level
- *Response – Outbound response bodies (may nest *Schema types)
- *Payload  – Incoming third-party webhook payloads (WhatsApp, etc.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.models import AdminDecision, InvoiceStatus, SignedLinkType

# ---------------------------------------------------------------------------
# Generic response wrapper
# ---------------------------------------------------------------------------

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    """Standard envelope for successful API responses."""

    success: bool = True
    data: T
    meta: dict[str, Any] | None = Field(default=None)


class ErrorResponse(BaseModel):
    """Standard envelope for error API responses."""

    success: bool = False
    error: str
    detail: str | None = None
    request_id: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list wrapper."""

    success: bool = True
    data: list[T]
    total: int
    page: int
    page_size: int
    pages: int


# ---------------------------------------------------------------------------
# Tenant
# ---------------------------------------------------------------------------


class TenantSchema(BaseModel):
    """Public representation of a Tenant."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserSchema(BaseModel):
    """Public representation of a User (no session token)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    phone_number: str
    display_name: str | None
    is_active: bool
    is_admin: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Invoice field confidence
# ---------------------------------------------------------------------------


class FieldWithConfidence(BaseModel):
    """A single extracted invoice field with its confidence score."""

    value: str | None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class InvoiceMetadataSchema(BaseModel):
    """
    Structured extraction result exposed to the API.

    Every field carries an individual confidence score so the frontend
    can highlight cells that are likely to need user correction.
    """

    model_config = ConfigDict(from_attributes=True)

    vendor_name: FieldWithConfidence
    vendor_tax_id: FieldWithConfidence
    invoice_number: FieldWithConfidence
    invoice_date: FieldWithConfidence
    total_amount: FieldWithConfidence
    tax_amount: FieldWithConfidence
    currency: FieldWithConfidence
    line_items: list[dict[str, Any]] | None = None
    overall_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Quality Check
# ---------------------------------------------------------------------------


class QualityCheckSchema(BaseModel):
    """Quality gate results for a single invoice image."""

    model_config = ConfigDict(from_attributes=True)

    blur_score: float | None
    resolution_ok: bool | None
    skew_angle: float | None
    passed: bool
    failure_reasons: list[str] | None


# ---------------------------------------------------------------------------
# Invoice (list item)
# ---------------------------------------------------------------------------


class InvoiceListItemSchema(BaseModel):
    """Compact invoice representation used in list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    user_id: str
    status: InvoiceStatus
    month: str | None
    original_filename: str | None
    created_at: datetime
    updated_at: datetime


class InvoiceDetailSchema(InvoiceListItemSchema):
    """Full invoice detail including extracted data and quality check."""

    quality_check: QualityCheckSchema | None = None
    extracted_data: InvoiceMetadataSchema | None = None
    corrections: list[CorrectionSchema] | None = None
    admin_review: AdminReviewSchema | None = None


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


class CorrectionSchema(BaseModel):
    """A single user-submitted field correction (read model)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    field_name: str
    original_value: str | None
    corrected_value: str | None
    created_at: datetime


class CorrectionRequest(BaseModel):
    """Payload for PUT /api/v1/invoices/{id}/corrections."""

    corrections: list[FieldCorrectionItem] = Field(
        ..., min_length=1, description="List of field corrections to apply."
    )


class FieldCorrectionItem(BaseModel):
    """One field–value pair in a correction request."""

    field_name: str = Field(..., min_length=1, max_length=100)
    corrected_value: str | None = Field(
        default=None,
        description="NULL means the field should be cleared.",
    )


# ---------------------------------------------------------------------------
# Admin Review
# ---------------------------------------------------------------------------


class AdminReviewSchema(BaseModel):
    """Admin review record (read model)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    decision: AdminDecision
    override_data: dict[str, Any] | None
    notes: str | None
    reviewed_at: datetime


class AdminReviewRequest(BaseModel):
    """Payload for POST /api/v1/admin/invoices/{id}/review."""

    decision: AdminDecision
    override_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Field overrides when decision is OVERRIDDEN. "
            "Keys must match ExtractedData field names."
        ),
    )
    notes: str | None = Field(default=None, max_length=2000)


class AdminInvoiceDetailSchema(InvoiceDetailSchema):
    """Admin-facing invoice detail that includes diff view data."""

    # Diff between extracted_data and the latest user corrections
    extraction_vs_correction_diff: dict[str, Any] | None = None
    # Diff between user-corrected values and admin override (if any)
    correction_vs_admin_diff: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# WhatsApp webhook payloads
# ---------------------------------------------------------------------------


class WhatsAppContact(BaseModel):
    """Contact block inside a WhatsApp webhook message."""

    wa_id: str
    profile: dict[str, Any] | None = None


class WhatsAppMedia(BaseModel):
    """Media object attached to a WhatsApp message."""

    id: str
    mime_type: str | None = None
    sha256: str | None = None


class WhatsAppMessage(BaseModel):
    """A single inbound WhatsApp message."""

    id: str
    from_: str = Field(alias="from")
    timestamp: str
    type: str
    image: WhatsAppMedia | None = None
    document: WhatsAppMedia | None = None
    text: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class WhatsAppChange(BaseModel):
    """``changes`` entry in the WhatsApp webhook payload."""

    value: dict[str, Any]
    field: str


class WhatsAppEntry(BaseModel):
    """Top-level entry in the WhatsApp webhook payload."""

    id: str
    changes: list[WhatsAppChange]


class WebhookPayload(BaseModel):
    """
    Top-level WhatsApp Cloud API webhook payload.

    Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks
    """

    object: str
    entry: list[WhatsAppEntry]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class MagicLinkRequest(BaseModel):
    """Request body for POST /api/v1/auth/magic-link."""

    phone_number: str = Field(..., min_length=7, max_length=20)
    tenant_slug: str = Field(..., min_length=1, max_length=100)


class MagicLinkResponse(BaseModel):
    """Response confirming that a magic link was sent."""

    message: str = "Magic link sent via WhatsApp."


class AuthVerifyResponse(BaseModel):
    """Response containing session details after magic-link verification."""

    user: UserSchema
    session_expires_at: datetime


# ---------------------------------------------------------------------------
# Signed links
# ---------------------------------------------------------------------------


class SignedLinkSchema(BaseModel):
    """Metadata about a generated signed link."""

    token: str
    link_type: SignedLinkType
    expires_at: datetime
    url: str


class SignedLinkResolveResponse(BaseModel):
    """Payload returned when a signed link is successfully resolved."""

    link_type: SignedLinkType
    invoice_id: str
    user_id: str
    tenant_id: str
    # Only populated for VIEW_IMAGE links
    image_url: str | None = None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class MetricsResponse(BaseModel):
    """Aggregated admin metrics."""

    total_invoices: int
    invoices_by_status: dict[str, int]
    avg_confidence: float | None
    corrections_submitted: int
    approvals: int
    rejections: int
    period_start: datetime | None = None
    period_end: datetime | None = None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Query parameters for the admin export endpoint."""

    tenant_id: str | None = None
    month: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}$",
        description="Month filter in YYYY-MM format.",
    )
    status: InvoiceStatus | None = None
    format: str = Field(default="csv", pattern=r"^(csv|json)$")
