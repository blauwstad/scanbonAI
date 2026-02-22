"""
Admin client management router for ScanbonAI.

Endpoints
---------
GET    /api/v1/admin/clients                       - Paginated client list
GET    /api/v1/admin/clients/{client_id}            - Client detail
PUT    /api/v1/admin/clients/{client_id}            - Update client profile
POST   /api/v1/admin/clients/{client_id}/enrich     - Fetch from KVK/KBO registry
POST   /api/v1/admin/clients/{client_id}/consent    - Set registry enrichment consent
POST   /api/v1/admin/clients/{client_id}/suspend    - Suspend client
POST   /api/v1/admin/clients/{client_id}/reactivate - Reactivate client
POST   /api/v1/admin/clients/{client_id}/grant-credits - Grant bonus credits

Access control
--------------
All endpoints require ``require_admin``.  Every query is scoped by
``admin_user.tenant_id`` for multi-tenant isolation.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import AdminUser, DBSession, require_admin
from app.models import (
    AuditLog,
    Invoice,
    RegistryEnrichmentEvent,
    User,
    UserCreditsLedger,
    UserStatus,
    UserSubscription,
)
from app.schemas import PaginatedResponse, SuccessResponse

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/clients",
    tags=["admin-clients"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ClientListItem(BaseModel):
    id: str
    whatsapp_phone: str
    display_name: str | None
    company_name: str | None
    status: str
    plan_code: str | None
    remaining_credits: int | None
    subscription_status: str | None
    invoice_count: int
    created_at: datetime


class ClientDetail(BaseModel):
    # Core user fields
    id: str
    tenant_id: str
    whatsapp_phone: str
    display_name: str | None
    status: str
    # Company profile
    company_name: str | None
    legal_name: str | None
    contact_name: str | None
    address_street: str | None
    address_postal_code: str | None
    address_city: str | None
    address_country: str | None
    vat_number: str | None
    kvk_number: str | None
    kbo_number: str | None
    # Enrichment
    enrichment_source: str | None
    enrichment_last_fetched_at: datetime | None
    enrichment_status: str
    consent_registry_enrichment: bool
    # Admin
    admin_notes: str | None
    # Billing
    plan_code: str | None
    subscription_status: str | None
    remaining_credits: int | None
    current_period_end: datetime | None
    # Stats
    invoice_count: int
    created_at: datetime
    # Related data
    enrichment_history: list[dict[str, Any]] = []
    recent_invoices: list[dict[str, Any]] = []


class ClientUpdateRequest(BaseModel):
    company_name: str | None = None
    legal_name: str | None = None
    contact_name: str | None = None
    address_street: str | None = None
    address_postal_code: str | None = None
    address_city: str | None = None
    address_country: str | None = None
    vat_number: str | None = None
    kvk_number: str | None = None
    kbo_number: str | None = None
    admin_notes: str | None = None


class EnrichRequest(BaseModel):
    registry_type: str = Field(..., pattern=r"^(KVK|KBO)$")


class ConsentRequest(BaseModel):
    consent: bool


class GrantCreditsRequest(BaseModel):
    amount: int = Field(..., gt=0, le=10000)
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_client_or_404(
    client_id: str,
    tenant_id: str,
    db: AsyncSession,
) -> User:
    """Fetch a user within the admin's tenant, raise 404 if not found."""
    result = await db.execute(
        select(User).where(
            User.id == client_id,
            User.tenant_id == tenant_id,
        )
    )
    user: User | None = result.scalars().first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {client_id!r} not found.",
        )
    return user


async def _get_latest_subscription(
    user_id: str,
    db: AsyncSession,
) -> UserSubscription | None:
    """Return the most recent subscription for a user."""
    result = await db.execute(
        select(UserSubscription)
        .where(UserSubscription.user_id == user_id)
        .order_by(UserSubscription.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _get_remaining_credits(
    user_id: str,
    db: AsyncSession,
) -> int | None:
    """Sum the credits ledger for a user. Returns None if no entries exist."""
    result = await db.execute(
        select(func.sum(UserCreditsLedger.delta)).where(
            UserCreditsLedger.user_id == user_id,
        )
    )
    total = result.scalar_one_or_none()
    return int(total) if total is not None else None


async def _create_audit_log(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert an immutable audit log entry."""
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        event_type=event_type,
        payload=payload,
    )
    db.add(entry)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/clients
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedResponse[ClientListItem],
    summary="Paginated client list",
)
async def list_clients(
    admin_user: AdminUser,
    db: DBSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    plan_filter: Annotated[str | None, Query(alias="plan")] = None,
) -> PaginatedResponse[ClientListItem]:
    """
    Return a paginated list of clients within the admin's tenant.

    Supports search by phone, company name, display name, KVK/KBO number,
    and filtering by user status or billing plan code.
    """
    tenant_id = admin_user.tenant_id

    # --- Build the latest-subscription subquery ---
    latest_sub_sq = (
        select(
            UserSubscription.user_id,
            UserSubscription.plan_code,
            UserSubscription.status.label("subscription_status"),
        )
        .where(UserSubscription.tenant_id == tenant_id)
        .distinct(UserSubscription.user_id)
        .order_by(UserSubscription.user_id, UserSubscription.created_at.desc())
        .subquery("latest_sub")
    )

    # --- Build the credits subquery ---
    credits_sq = (
        select(
            UserCreditsLedger.user_id,
            func.sum(UserCreditsLedger.delta).label("remaining_credits"),
        )
        .group_by(UserCreditsLedger.user_id)
        .subquery("credits")
    )

    # --- Build the invoice count subquery ---
    invoice_count_sq = (
        select(
            Invoice.user_id,
            func.count(Invoice.id).label("invoice_count"),
        )
        .where(Invoice.tenant_id == tenant_id)
        .group_by(Invoice.user_id)
        .subquery("inv_count")
    )

    # --- Main query ---
    base_query = (
        select(
            User.id,
            User.whatsapp_phone,
            User.display_name,
            User.company_name,
            User.status,
            User.created_at,
            latest_sub_sq.c.plan_code,
            latest_sub_sq.c.subscription_status,
            credits_sq.c.remaining_credits,
            invoice_count_sq.c.invoice_count,
        )
        .outerjoin(latest_sub_sq, User.id == latest_sub_sq.c.user_id)
        .outerjoin(credits_sq, User.id == credits_sq.c.user_id)
        .outerjoin(invoice_count_sq, User.id == invoice_count_sq.c.user_id)
        .where(User.tenant_id == tenant_id)
    )

    # --- Search filter ---
    if search:
        like_pattern = f"%{search}%"
        base_query = base_query.where(
            or_(
                User.whatsapp_phone.ilike(like_pattern),
                User.company_name.ilike(like_pattern),
                User.display_name.ilike(like_pattern),
                User.kvk_number.ilike(like_pattern),
                User.kbo_number.ilike(like_pattern),
            )
        )

    # --- Status filter ---
    if status_filter:
        try:
            user_status = UserStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status filter: {status_filter!r}. "
                f"Valid values: {[s.value for s in UserStatus]}",
            )
        base_query = base_query.where(User.status == user_status)

    # --- Plan filter ---
    if plan_filter:
        base_query = base_query.where(latest_sub_sq.c.plan_code == plan_filter)

    # --- Count ---
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total: int = count_result.scalar_one()

    # --- Paginate ---
    offset = (page - 1) * page_size
    rows = await db.execute(
        base_query.order_by(User.created_at.desc()).offset(offset).limit(page_size)
    )

    items: list[ClientListItem] = []
    for row in rows.all():
        status_value = row.status
        if hasattr(status_value, "value"):
            status_value = status_value.value
        sub_status = row.subscription_status
        if hasattr(sub_status, "value"):
            sub_status = sub_status.value
        items.append(
            ClientListItem(
                id=row.id,
                whatsapp_phone=row.whatsapp_phone,
                display_name=row.display_name,
                company_name=row.company_name,
                status=status_value,
                plan_code=row.plan_code,
                remaining_credits=(
                    int(row.remaining_credits) if row.remaining_credits is not None else None
                ),
                subscription_status=sub_status,
                invoice_count=row.invoice_count or 0,
                created_at=row.created_at,
            )
        )

    pages = math.ceil(total / page_size) if total else 0

    return PaginatedResponse(
        data=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/clients/{client_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{client_id}",
    response_model=SuccessResponse[ClientDetail],
    summary="Client detail",
)
async def get_client(
    client_id: str,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[ClientDetail]:
    """Return full client profile including billing summary, enrichment history, and recent invoices."""
    user = await _get_client_or_404(client_id, admin_user.tenant_id, db)

    # Billing info
    subscription = await _get_latest_subscription(user.id, db)
    remaining_credits = await _get_remaining_credits(user.id, db)

    # Invoice count
    inv_count_result = await db.execute(
        select(func.count(Invoice.id)).where(
            Invoice.user_id == user.id,
            Invoice.tenant_id == admin_user.tenant_id,
        )
    )
    invoice_count: int = inv_count_result.scalar_one()

    # Enrichment history
    enrichment_result = await db.execute(
        select(RegistryEnrichmentEvent)
        .where(
            RegistryEnrichmentEvent.user_id == user.id,
            RegistryEnrichmentEvent.tenant_id == admin_user.tenant_id,
        )
        .order_by(RegistryEnrichmentEvent.requested_at.desc())
    )
    enrichment_events = enrichment_result.scalars().all()
    enrichment_history = [
        {
            "id": e.id,
            "registry_type": e.registry_type,
            "identifier": e.identifier,
            "requested_at": e.requested_at.isoformat() if e.requested_at else None,
            "success": e.success,
            "error_message": e.error_message,
            "response_status_code": e.response_status_code,
            "applied_fields": e.applied_fields_json,
        }
        for e in enrichment_events
    ]

    # Recent invoices (last 5)
    recent_inv_result = await db.execute(
        select(Invoice)
        .where(
            Invoice.user_id == user.id,
            Invoice.tenant_id == admin_user.tenant_id,
        )
        .order_by(Invoice.created_at.desc())
        .limit(5)
    )
    recent_invoices_rows = recent_inv_result.scalars().all()
    recent_invoices = [
        {
            "id": inv.id,
            "status": inv.status.value if hasattr(inv.status, "value") else inv.status,
            "month_partition": inv.month_partition,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
        }
        for inv in recent_invoices_rows
    ]

    user_status = user.status
    if hasattr(user_status, "value"):
        user_status = user_status.value

    detail = ClientDetail(
        id=user.id,
        tenant_id=user.tenant_id,
        whatsapp_phone=user.whatsapp_phone,
        display_name=user.display_name,
        status=user_status,
        company_name=user.company_name,
        legal_name=user.legal_name,
        contact_name=user.contact_name,
        address_street=user.address_street,
        address_postal_code=user.address_postal_code,
        address_city=user.address_city,
        address_country=user.address_country,
        vat_number=user.vat_number,
        kvk_number=user.kvk_number,
        kbo_number=user.kbo_number,
        enrichment_source=user.enrichment_source,
        enrichment_last_fetched_at=user.enrichment_last_fetched_at,
        enrichment_status=user.enrichment_status,
        consent_registry_enrichment=user.consent_registry_enrichment,
        admin_notes=user.admin_notes,
        plan_code=subscription.plan_code if subscription else None,
        subscription_status=(
            subscription.status.value
            if subscription and hasattr(subscription.status, "value")
            else (subscription.status if subscription else None)
        ),
        remaining_credits=remaining_credits,
        current_period_end=subscription.current_period_end if subscription else None,
        invoice_count=invoice_count,
        created_at=user.created_at,
        enrichment_history=enrichment_history,
        recent_invoices=recent_invoices,
    )

    return SuccessResponse(data=detail)


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/clients/{client_id}
# ---------------------------------------------------------------------------


@router.put(
    "/{client_id}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Update client profile",
)
async def update_client(
    client_id: str,
    body: ClientUpdateRequest,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict[str, Any]]:
    """
    Update company profile fields on a client.

    Only fields explicitly provided (not None) are updated.
    An audit log entry is created with old vs new values.
    """
    user = await _get_client_or_404(client_id, admin_user.tenant_id, db)

    # Determine which fields were explicitly set in the request body
    provided_fields = body.model_dump(exclude_unset=True)
    if not provided_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update.",
        )

    # Build old/new diff and apply changes
    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}

    for field_name, new_value in provided_fields.items():
        old_value = getattr(user, field_name, None)
        if old_value != new_value:
            old_values[field_name] = old_value
            new_values[field_name] = new_value
            setattr(user, field_name, new_value)

    if not new_values:
        return SuccessResponse(data={"message": "No changes detected.", "updated_fields": []})

    # Audit log (never log full addresses in the log message)
    await _create_audit_log(
        db,
        tenant_id=admin_user.tenant_id,
        actor_id=admin_user.id,
        event_type="client_profile_updated",
        payload={
            "client_id": client_id,
            "old": old_values,
            "new": new_values,
        },
    )

    logger.info(
        "admin.client.profile_updated",
        client_id=client_id,
        admin_id=admin_user.id,
        updated_fields=list(new_values.keys()),
    )

    return SuccessResponse(
        data={
            "message": "Client profile updated.",
            "updated_fields": list(new_values.keys()),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/admin/clients/{client_id}/enrich
# ---------------------------------------------------------------------------


@router.post(
    "/{client_id}/enrich",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Fetch from KVK/KBO registry",
)
async def enrich_client(
    client_id: str,
    body: EnrichRequest,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict[str, Any]]:
    """
    Trigger a registry enrichment lookup (KVK or KBO) for a client.

    Requires that the client has given consent for registry enrichment
    and has the appropriate identifier set on their profile.
    """
    user = await _get_client_or_404(client_id, admin_user.tenant_id, db)

    # Privacy check: consent must be granted
    if not user.consent_registry_enrichment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client has not consented to registry enrichment. "
            "Set consent first via the consent endpoint.",
        )

    # Determine identifier
    if body.registry_type == "KVK":
        identifier = user.kvk_number
    else:
        identifier = user.kbo_number

    if not identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Client does not have a {body.registry_type} number set.",
        )

    # Lazy import to avoid hard dependency on service module at import time
    from app.services.registry import enrich_user_from_registry

    profile, error = await enrich_user_from_registry(
        db, user, body.registry_type, identifier, admin_user.id
    )

    logger.info(
        "admin.client.registry_enrichment",
        client_id=client_id,
        admin_id=admin_user.id,
        registry_type=body.registry_type,
        success=error is None,
    )

    if error:
        return SuccessResponse(data={"success": False, "error": error})

    from dataclasses import asdict
    return SuccessResponse(data={
        "success": True,
        "profile": asdict(profile) if profile else None,
    })


# ---------------------------------------------------------------------------
# POST /api/v1/admin/clients/{client_id}/consent
# ---------------------------------------------------------------------------


@router.post(
    "/{client_id}/consent",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Set registry enrichment consent",
)
async def set_consent(
    client_id: str,
    body: ConsentRequest,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict[str, Any]]:
    """Set or revoke the client's consent for registry enrichment."""
    user = await _get_client_or_404(client_id, admin_user.tenant_id, db)

    old_consent = user.consent_registry_enrichment
    user.consent_registry_enrichment = body.consent
    user.consent_registry_enrichment_at = (
        datetime.now(timezone.utc) if body.consent else None
    )

    await _create_audit_log(
        db,
        tenant_id=admin_user.tenant_id,
        actor_id=admin_user.id,
        event_type="client_consent_updated",
        payload={
            "client_id": client_id,
            "old_consent": old_consent,
            "new_consent": body.consent,
        },
    )

    logger.info(
        "admin.client.consent_updated",
        client_id=client_id,
        admin_id=admin_user.id,
        consent=body.consent,
    )

    return SuccessResponse(
        data={
            "message": f"Registry enrichment consent {'granted' if body.consent else 'revoked'}.",
            "consent": body.consent,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/admin/clients/{client_id}/suspend
# ---------------------------------------------------------------------------


@router.post(
    "/{client_id}/suspend",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Suspend client",
)
async def suspend_client(
    client_id: str,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict[str, Any]]:
    """Suspend a client account, preventing further activity."""
    user = await _get_client_or_404(client_id, admin_user.tenant_id, db)

    old_status = user.status.value if hasattr(user.status, "value") else user.status
    user.status = UserStatus.SUSPENDED

    await _create_audit_log(
        db,
        tenant_id=admin_user.tenant_id,
        actor_id=admin_user.id,
        event_type="client_suspended",
        payload={
            "client_id": client_id,
            "old_status": old_status,
            "new_status": UserStatus.SUSPENDED.value,
        },
    )

    logger.info(
        "admin.client.suspended",
        client_id=client_id,
        admin_id=admin_user.id,
        old_status=old_status,
    )

    return SuccessResponse(
        data={
            "message": "Client suspended.",
            "client_id": client_id,
            "status": UserStatus.SUSPENDED.value,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/admin/clients/{client_id}/reactivate
# ---------------------------------------------------------------------------


@router.post(
    "/{client_id}/reactivate",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Reactivate client",
)
async def reactivate_client(
    client_id: str,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict[str, Any]]:
    """Reactivate a previously suspended client account."""
    user = await _get_client_or_404(client_id, admin_user.tenant_id, db)

    old_status = user.status.value if hasattr(user.status, "value") else user.status
    user.status = UserStatus.ACTIVE

    await _create_audit_log(
        db,
        tenant_id=admin_user.tenant_id,
        actor_id=admin_user.id,
        event_type="client_reactivated",
        payload={
            "client_id": client_id,
            "old_status": old_status,
            "new_status": UserStatus.ACTIVE.value,
        },
    )

    logger.info(
        "admin.client.reactivated",
        client_id=client_id,
        admin_id=admin_user.id,
        old_status=old_status,
    )

    return SuccessResponse(
        data={
            "message": "Client reactivated.",
            "client_id": client_id,
            "status": UserStatus.ACTIVE.value,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/admin/clients/{client_id}/grant-credits
# ---------------------------------------------------------------------------


@router.post(
    "/{client_id}/grant-credits",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Grant bonus credits",
)
async def grant_credits(
    client_id: str,
    body: GrantCreditsRequest,
    admin_user: AdminUser,
    db: DBSession,
) -> SuccessResponse[dict[str, Any]]:
    """Grant bonus credits to a client by inserting a ledger entry."""
    user = await _get_client_or_404(client_id, admin_user.tenant_id, db)

    reason = body.reason or "admin_grant"

    ledger_entry = UserCreditsLedger(
        user_id=user.id,
        delta=body.amount,
        reason=reason,
    )
    db.add(ledger_entry)

    await _create_audit_log(
        db,
        tenant_id=admin_user.tenant_id,
        actor_id=admin_user.id,
        event_type="client_credits_granted",
        payload={
            "client_id": client_id,
            "amount": body.amount,
            "reason": reason,
        },
    )

    # Compute new balance
    new_balance = await _get_remaining_credits(user.id, db)

    logger.info(
        "admin.client.credits_granted",
        client_id=client_id,
        admin_id=admin_user.id,
        amount=body.amount,
        reason=reason,
    )

    return SuccessResponse(
        data={
            "message": f"Granted {body.amount} credits to client.",
            "client_id": client_id,
            "amount": body.amount,
            "reason": reason,
            "new_balance": new_balance,
        }
    )
