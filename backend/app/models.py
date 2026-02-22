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
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
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
    UPLOADED = "uploaded"
    QUALITY_FAILED = "quality_failed"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    EXPORTED = "exported"


class AdminReviewAction(str, enum.Enum):
    APPROVED = "approved"
    OVERRIDDEN = "overridden"
    FLAGGED = "flagged"


class SignedLinkType(str, enum.Enum):
    VIEW_IMAGE = "view_image"
    CORRECTION_FORM = "correction_form"
    CONFIRM = "confirm"


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class WebhookEventStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class UserStatus(str, enum.Enum):
    PENDING = "pending"
    INACTIVE = "inactive"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class PlanType(str, enum.Enum):
    CREDITS = "credits"
    SUBSCRIPTION = "subscription"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"


# ---------------------------------------------------------------------------
# Core MVP tables
# ---------------------------------------------------------------------------


class Tenant(Base):
    """
    Top-level isolation boundary.

    Every other entity belongs to exactly one Tenant.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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
    settings_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default='{}')

    # Relationships
    users: Mapped[list[User]] = relationship("User", back_populates="tenant")
    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="tenant")

    def __repr__(self) -> str:
        return f"<Tenant id={self.id!r} name={self.name!r}>"


class User(Base):
    """
    A WhatsApp end-user who sends invoice images.

    ``whatsapp_phone`` is the WhatsApp E.164 number and acts as the natural key
    within a tenant (the same phone may exist across tenants for multi-org
    scenarios, but the composite unique constraint prevents collisions).
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "whatsapp_phone", name="uq_users_tenant_phone"),
        Index("ix_user_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    whatsapp_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
        nullable=False, default=UserRole.USER, server_default='user'
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
        nullable=False, default=UserStatus.PENDING, server_default='pending'
    )
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Auth token for API access; stored as raw token for demo simplicity
    auth_token: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Company profile (enriched via KVK/KBO or manual entry)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    kvk_number: Mapped[str | None] = mapped_column(String(8), nullable=True)
    kbo_number: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # Registry enrichment tracking
    enrichment_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    enrichment_last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default='never_fetched')
    enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Privacy consent for registry enrichment
    consent_registry_enrichment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
    consent_registry_enrichment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Admin notes
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        return f"<User id={self.id!r} phone={self.whatsapp_phone!r}>"


class Invoice(Base):
    """
    Central record linking an image to its full processing lifecycle.

    ``file_path`` is the path to the stored invoice image.
    The month_partition field (YYYY-MM) enables fast filtering for monthly tax reports.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoice_tenant_id", "tenant_id"),
        Index("ix_invoice_user_id", "user_id"),
        Index("ix_invoice_month_partition", "month_partition"),
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
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
        nullable=False, default=InvoiceStatus.UPLOADED
    )
    upload_source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="whatsapp"
    )
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # YYYY-MM partition key for monthly tax reports
    month_partition: Mapped[str] = mapped_column(String(7), nullable=False)
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
    shadow_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    exposure_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reasons: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="quality_check")

    def __repr__(self) -> str:
        return f"<QualityCheck invoice={self.invoice_id!r} overall_pass={self.overall_pass!r}>"


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
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    processing_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="ocr_result")

    def __repr__(self) -> str:
        return f"<OCRResult invoice={self.invoice_id!r} model={self.model_name!r}>"


class ExtractedData(Base):
    """
    Structured invoice fields extracted from OCR text via LLM prompt.

    All extracted fields are stored as a single JSONB document with
    per-field confidence scores.
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
    ocr_result_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("ocr_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    extracted_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="extracted_data")
    ocr_result: Mapped[OCRResult] = relationship("OCRResult")

    def __repr__(self) -> str:
        return f"<ExtractedData invoice={self.invoice_id!r}>"


class UserCorrection(Base):
    """
    JSON-level diffs submitted by the user after reviewing extraction.

    Stores the full corrected document and a computed diff from the original.
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
    extracted_data_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("extracted_data.id", ondelete="CASCADE"),
        nullable=False,
    )
    corrected_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    diff_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    corrected_by_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="corrections")
    extracted_data: Mapped[ExtractedData] = relationship("ExtractedData")
    user: Mapped[User] = relationship("User", back_populates="corrections", foreign_keys=[corrected_by_user_id])

    def __repr__(self) -> str:
        return f"<UserCorrection invoice={self.invoice_id!r}>"


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
    reviewer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    action: Mapped[AdminReviewAction] = mapped_column(
        Enum(AdminReviewAction, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
        nullable=False
    )
    # Override values if action == OVERRIDDEN; NULL otherwise
    override_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="admin_review")
    reviewer: Mapped[User] = relationship("User", foreign_keys=[reviewer_id])

    def __repr__(self) -> str:
        return (
            f"<AdminReview invoice={self.invoice_id!r} action={self.action!r}>"
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
        Enum(SignedLinkType, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
        nullable=False
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
        Enum(WebhookEventStatus, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
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


class WhatsAppSettings(Base):
    """
    Per-tenant WhatsApp Cloud API configuration.

    Stores the Meta Business credentials needed to send/receive WhatsApp
    messages for a specific tenant.  The access_token is stored encrypted
    using Fernet with the TOKEN_ENCRYPTION_KEY env variable.
    """

    __tablename__ = "whatsapp_settings"
    __table_args__ = (
        Index("ix_wa_settings_phone_number_id", "phone_number_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    phone_number_id: Mapped[str] = mapped_column(String(50), nullable=False)
    display_phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    waba_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    meta_app_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_verify_token: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    tenant: Mapped[Tenant] = relationship("Tenant")

    def __repr__(self) -> str:
        return f"<WhatsAppSettings tenant={self.tenant_id!r} phone={self.phone_number_id!r}>"


class RegistrationToken(Base):
    """
    Short-lived token for WhatsApp-initiated user registration.

    When an unknown phone number sends a message to the business number,
    we create a token and send the user a registration link.  The token
    pre-fills their phone and tenant context on the registration page.
    """

    __tablename__ = "registration_tokens"
    __table_args__ = (
        Index("ix_reg_token", "token", unique=True),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    intake_phone_number_id: Mapped[str] = mapped_column(String(50), nullable=False)
    token_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default='registration'
    )  # registration | activation | renewal
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RegistrationToken phone={self.phone_number!r} type={self.token_type!r}>"


# ---------------------------------------------------------------------------
# Billing tables
# ---------------------------------------------------------------------------


class BillingPlan(Base):
    """Billing plan definitions (seeded, rarely changed)."""

    __tablename__ = "billing_plans"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    plan_type: Mapped[PlanType] = mapped_column(
        Enum(PlanType, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
        nullable=False,
    )
    credits_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stripe_product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    stripe_price_id: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<BillingPlan code={self.code!r} type={self.plan_type!r}>"


class UserSubscription(Base):
    """Tracks a user's active billing subscription or one-time purchase."""

    __tablename__ = "user_subscriptions"
    __table_args__ = (
        Index("ix_user_sub_user_id", "user_id"),
        Index("ix_user_sub_stripe_customer_id", "stripe_customer_id"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    plan_code: Mapped[str] = mapped_column(String(50), nullable=False)
    stripe_customer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, values_callable=lambda e: [x.value for x in e], create_constraint=False, native_enum=False),
        nullable=False, default=SubscriptionStatus.ACTIVE,
    )
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User")
    tenant: Mapped[Tenant] = relationship("Tenant")

    def __repr__(self) -> str:
        return f"<UserSubscription user={self.user_id!r} plan={self.plan_code!r}>"


class UserCreditsLedger(Base):
    """Append-only ledger for credits tracking. SUM(delta) = remaining credits."""

    __tablename__ = "user_credits_ledger"
    __table_args__ = (
        Index("ix_credits_user_id", "user_id"),
        Index("ix_credits_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserCreditsLedger user={self.user_id!r} delta={self.delta!r}>"


class RegistryEnrichmentEvent(Base):
    """Audit trail for KVK/KBO registry lookups."""

    __tablename__ = "registry_enrichment_events"
    __table_args__ = (
        Index("ix_enrichment_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    registry_type: Mapped[str] = mapped_column(String(10), nullable=False)  # KVK or KBO
    identifier: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_by_admin_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    applied_fields_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<RegistryEnrichmentEvent user={self.user_id!r} type={self.registry_type!r}>"


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
