"""
SQLAlchemy ORM models for ScanbonAI.

Table inventory
---------------
Core (MVP)
  - Tenant            – isolates every row in the system
  - User              – WhatsApp end-users linked to a Tenant
  - Invoice           – one image → one invoice record
  - QualityCheck      – per-image quality gate results
  - OCRResult         – raw text returned by the OCR engine
  - ExtractedData     – structured fields parsed from OCR text
  - UserCorrection    – diffs submitted by the user after review
  - AdminReview       – admin approve / override / reject decisions
  - AuditLog          – immutable append-only event trail
  - SignedLink        – time-limited tokens for image access
  - WebhookEvent      – idempotency log for inbound WhatsApp events

Future (stubbed)
  - Expert            – human expert accounts
  - ExpertAssignment  – invoice→expert work queue
  - ExpertReview      – expert-submitted review data
  - ExpertPayout      – payment records for expert work
  - ExpertDispute     – disputes raised on expert reviews
  - TrainingDataset   – curated rows for model fine-tuning
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ---------------------------------------------------------------------------
# Helper defaults
# ---------------------------------------------------------------------------


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InvoiceStatus(str, enum.Enum):
    RECEIVED = "received"
    QUALITY_FAILED = "quality_failed"
    OCR_PENDING = "ocr_pending"
    OCR_FAILED = "ocr_failed"
    EXTRACTION_PENDING = "extraction_pending"
    AWAITING_USER_REVIEW = "awaiting_user_review"
    USER_CONFIRMED = "user_confirmed"
    USER_CORRECTED = "user_corrected"
    ADMIN_REVIEW_PENDING = "admin_review_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class AdminDecision(str, enum.Enum):
    APPROVED = "approved"
    OVERRIDDEN = "overridden"
    REJECTED = "rejected"


class SignedLinkType(str, enum.Enum):
    VIEW_IMAGE = "view_image"
    CORRECTION_FORM = "correction_form"
    CONFIRM = "confirm"


class WebhookEventStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


# ---------------------------------------------------------------------------
# Core MVP tables
# ---------------------------------------------------------------------------


class Tenant(Base):
    """
    Top-level isolation boundary.

    Every other entity belongs to exactly one Tenant.  Tenant slugs are
    used in storage paths and log context so they must be URL-safe.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # settings stored as flexible JSON (e.g. OCR language hints, tax authority config)
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    users: Mapped[list[User]] = relationship("User", back_populates="tenant")
    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="tenant")

    def __repr__(self) -> str:
        return f"<Tenant id={self.id!r} slug={self.slug!r}>"


class User(Base):
    """
    A WhatsApp end-user who sends invoice images.

    ``phone_number`` is the WhatsApp E.164 number and acts as the natural key
    within a tenant (the same phone may exist across tenants for multi-org
    scenarios, but the composite unique constraint prevents collisions).
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_number", name="uq_user_tenant_phone"),
        Index("ix_user_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Magic-link session token (hashed); NULL when no active session
    session_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="user")
    corrections: Mapped[list[UserCorrection]] = relationship(
        "UserCorrection", back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} phone={self.phone_number!r}>"


class Invoice(Base):
    """
    Central record linking an image to its full processing lifecycle.

    ``storage_path`` is the relative path under ``settings.STORAGE_PATH``.
    The month field (YYYY-MM) enables fast filtering for monthly tax reports.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoice_tenant_id", "tenant_id"),
        Index("ix_invoice_user_id", "user_id"),
        Index("ix_invoice_month", "month"),
        Index("ix_invoice_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # WhatsApp media id that was used to download the original image
    whatsapp_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # YYYY-MM extracted from the invoice date (or upload date as fallback)
    month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus), nullable=False, default=InvoiceStatus.RECEIVED
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="invoices")
    user: Mapped[User] = relationship("User", back_populates="invoices")
    quality_check: Mapped[QualityCheck | None] = relationship(
        "QualityCheck", back_populates="invoice", uselist=False
    )
    ocr_result: Mapped[OCRResult | None] = relationship(
        "OCRResult", back_populates="invoice", uselist=False
    )
    extracted_data: Mapped[ExtractedData | None] = relationship(
        "ExtractedData", back_populates="invoice", uselist=False
    )
    corrections: Mapped[list[UserCorrection]] = relationship(
        "UserCorrection", back_populates="invoice"
    )
    admin_review: Mapped[AdminReview | None] = relationship(
        "AdminReview", back_populates="invoice", uselist=False
    )
    signed_links: Mapped[list[SignedLink]] = relationship(
        "SignedLink", back_populates="invoice"
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id!r} status={self.status!r}>"


class QualityCheck(Base):
    """Stores per-image quality gate results before OCR is attempted."""

    __tablename__ = "quality_checks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    blur_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    skew_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reasons: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="quality_check")

    def __repr__(self) -> str:
        return f"<QualityCheck invoice={self.invoice_id!r} passed={self.passed!r}>"


class OCRResult(Base):
    """Raw text output from the OCR engine for a given invoice image."""

    __tablename__ = "ocr_results"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    engine: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "deepseek_v2"
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full engine response payload for debugging / re-processing
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="ocr_result")

    def __repr__(self) -> str:
        return f"<OCRResult invoice={self.invoice_id!r} engine={self.engine!r}>"


class ExtractedData(Base):
    """
    Structured invoice fields extracted from OCR text via LLM prompt.

    Each field carries its own ``confidence`` score so the UI can highlight
    low-confidence cells for user correction.
    """

    __tablename__ = "extracted_data"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Core invoice fields
    vendor_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    total_amount: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tax_amount: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    line_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Per-field confidence scores stored as {"field_name": 0.95, ...}
    field_confidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Overall extraction confidence (average of field scores)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="extracted_data")

    def __repr__(self) -> str:
        return f"<ExtractedData invoice={self.invoice_id!r}>"


class UserCorrection(Base):
    """
    Field-level diffs submitted by the user after reviewing extraction.

    ``original_value`` is snapshotted at correction time so admin can see
    exactly what was changed without joining other tables.
    """

    __tablename__ = "user_corrections"
    __table_args__ = (Index("ix_correction_invoice_id", "invoice_id"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="corrections")
    user: Mapped[User] = relationship("User", back_populates="corrections")

    def __repr__(self) -> str:
        return (
            f"<UserCorrection invoice={self.invoice_id!r} field={self.field_name!r}>"
        )


class AdminReview(Base):
    """Records the outcome of an admin's decision on an invoice."""

    __tablename__ = "admin_reviews"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    reviewed_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    decision: Mapped[AdminDecision] = mapped_column(
        Enum(AdminDecision), nullable=False
    )
    # Override values if decision == OVERRIDDEN; NULL otherwise
    override_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="admin_review")
    reviewer: Mapped[User] = relationship("User", foreign_keys=[reviewed_by])

    def __repr__(self) -> str:
        return (
            f"<AdminReview invoice={self.invoice_id!r} decision={self.decision!r}>"
        )


class AuditLog(Base):
    """
    Immutable append-only log of every state-changing event.

    Never DELETE or UPDATE rows in this table.  Set ``cascade`` to RESTRICT
    at the DB level so invoice deletion is blocked while audit rows exist.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_tenant_id", "tenant_id"),
        Index("ix_audit_invoice_id", "invoice_id"),
        Index("ix_audit_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False
    )
    invoice_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    actor_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuditLog event={self.event_type!r} invoice={self.invoice_id!r}>"


class SignedLink(Base):
    """
    Time-limited, signed tokens for accessing invoice images or forms.

    The ``token`` column stores the full signed token string (HMAC-SHA256).
    ``used_at`` is set on first use to enforce one-time-use semantics if needed.
    """

    __tablename__ = "signed_links"
    __table_args__ = (
        Index("ix_signed_link_token", "token", unique=True),
        Index("ix_signed_link_invoice_id", "invoice_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False
    )
    token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    link_type: Mapped[SignedLinkType] = mapped_column(
        Enum(SignedLinkType), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="signed_links")

    def __repr__(self) -> str:
        return f"<SignedLink type={self.link_type!r} invoice={self.invoice_id!r}>"


class WebhookEvent(Base):
    """
    Idempotency guard for inbound WhatsApp webhook deliveries.

    WhatsApp may deliver the same event more than once.  We store the
    ``message_id`` (WhatsApp's own ID) and reject duplicates before
    enqueuing any processing task.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_message_id", "message_id", unique=True),
        Index("ix_webhook_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    message_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[WebhookEventStatus] = mapped_column(
        Enum(WebhookEventStatus),
        nullable=False,
        default=WebhookEventStatus.RECEIVED,
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<WebhookEvent id={self.message_id!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# FUTURE: Expert workflow tables (stubbed – not yet wired to routers)
# ---------------------------------------------------------------------------


class Expert(Base):
    """
    FUTURE: Human expert account for manual review escalations.

    Experts are distinct from admin Users; they receive assignments via the
    ExpertAssignment queue and are compensated via ExpertPayout.
    """

    __tablename__ = "experts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Specialisation tags stored as JSON array, e.g. ["vat", "import_duty"]
    specialisations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExpertAssignment(Base):
    """FUTURE: Links an invoice to an expert for manual review."""

    __tablename__ = "expert_assignments"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    expert_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("experts.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExpertReview(Base):
    """FUTURE: Structured review data submitted by an expert."""

    __tablename__ = "expert_reviews"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    assignment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("expert_assignments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    reviewed_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExpertPayout(Base):
    """FUTURE: Payment record for completed expert review work."""

    __tablename__ = "expert_payouts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    expert_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("experts.id"), nullable=False
    )
    review_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("expert_reviews.id"), nullable=False, unique=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExpertDispute(Base):
    """FUTURE: Dispute raised against an expert review decision."""

    __tablename__ = "expert_disputes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    review_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("expert_reviews.id"), nullable=False
    )
    raised_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TrainingDataset(Base):
    """
    FUTURE: Curated extraction examples used for model fine-tuning.

    Rows are promoted from approved invoices where the extracted data
    matched the final admin-confirmed values with high confidence.
    """

    __tablename__ = "training_datasets"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("invoices.id"), nullable=False, unique=True
    )
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ground-truth labels after all corrections and admin review
    ground_truth: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    split: Mapped[str] = mapped_column(
        String(20), nullable=False, default="train"
    )  # "train" | "val" | "test"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
