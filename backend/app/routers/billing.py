"""
Billing router for ScanbonAI.

Endpoints
---------
GET   /api/v1/billing/plans            -- List active billing plans (public).
GET   /api/v1/billing/activate         -- Validate activation token, return user info + plans.
POST  /api/v1/billing/checkout-session -- Create a Stripe Checkout Session.
POST  /webhooks/billing                -- Stripe webhook handler.
GET   /api/v1/billing/status           -- Current billing status (Bearer auth).
POST  /api/v1/billing/portal           -- Create Stripe Customer Portal session (Bearer auth).
"""

from __future__ import annotations

from datetime import datetime, timezone

import stripe
import structlog
from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import BillingPlan, RegistrationToken, User, UserStatus
from app.schemas import SuccessResponse
from app.services.billing import (
    create_checkout_session,
    handle_checkout_completed,
    handle_subscription_updated,
    handle_subscription_deleted,
    get_user_billing_status,
    create_customer_portal_session,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["billing"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    """Body for POST /api/v1/billing/checkout-session."""

    token: str = Field(..., description="Activation/renewal token from WhatsApp")
    plan_code: str = Field(
        ..., description="Plan code: CREDITS_100, UNLIMITED, or ULTRA"
    )


class CheckoutResponse(BaseModel):
    """Response containing the Stripe-hosted checkout URL."""

    checkout_url: str


class BillingPlanResponse(BaseModel):
    """Public representation of a billing plan (no Stripe IDs exposed)."""

    code: str
    name: str
    plan_type: str
    credits_amount: int | None


class ActivationInfoResponse(BaseModel):
    """User information and available plans returned during activation."""

    user_name: str | None
    phone_masked: str
    plans: list[BillingPlanResponse]


class BillingStatusResponse(BaseModel):
    """Summary of the user's current billing state."""

    status: str
    plan_code: str | None
    remaining_credits: int | None
    subscription_status: str | None
    current_period_end: str | None


class PortalResponse(BaseModel):
    """Response containing the Stripe Customer Portal URL."""

    portal_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_phone(phone: str) -> str:
    """
    Mask a phone number for safe display.

    Example: ``"+31612345678"`` -> ``"+316****5678"``
    """
    if len(phone) <= 6:
        return phone[:2] + "****"
    return phone[:4] + "****" + phone[-4:]


async def _get_active_plans(db: AsyncSession) -> list[BillingPlanResponse]:
    """Return all active billing plans as response models."""
    result = await db.execute(
        select(BillingPlan).where(BillingPlan.is_active.is_(True))
    )
    plans = result.scalars().all()
    return [
        BillingPlanResponse(
            code=p.code,
            name=p.name,
            plan_type=(
                p.plan_type.value
                if hasattr(p.plan_type, "value")
                else str(p.plan_type)
            ),
            credits_amount=p.credits_amount,
        )
        for p in plans
    ]


async def _resolve_activation_token(
    db: AsyncSession, token_value: str
) -> tuple[RegistrationToken, User]:
    """
    Look up and validate an activation/renewal token.

    Returns
    -------
    tuple[RegistrationToken, User]
        The validated token and the user it belongs to.

    Raises
    ------
    HTTPException
        404 if the token is unknown, 410 if expired or already used.
    """
    result = await db.execute(
        select(RegistrationToken).where(
            RegistrationToken.token == token_value,
            RegistrationToken.token_type.in_(["activation", "renewal"]),
        )
    )
    reg_token: RegistrationToken | None = result.scalars().first()

    if reg_token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or unknown activation token.",
        )

    now = datetime.now(tz=timezone.utc)

    if reg_token.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Activation token has expired. Please request a new link.",
        )

    if reg_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This activation link has already been used.",
        )

    # Resolve user by phone + tenant
    user_result = await db.execute(
        select(User).where(
            User.whatsapp_phone == reg_token.phone_number,
            User.tenant_id == reg_token.tenant_id,
        )
    )
    user: User | None = user_result.scalars().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No user account found for this activation token.",
        )

    return reg_token, user


# ---------------------------------------------------------------------------
# GET /api/v1/billing/plans
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/billing/plans",
    response_model=SuccessResponse[list[BillingPlanResponse]],
    summary="List active billing plans",
)
async def list_plans(db: DBSession) -> SuccessResponse[list[BillingPlanResponse]]:
    """
    Return all active billing plans.

    This endpoint is public (no authentication required) so that the
    pricing page and activation flow can display plan options without
    requiring a session.
    """
    plans = await _get_active_plans(db)
    logger.info("billing.plans.listed", count=len(plans))
    return SuccessResponse(data=plans)


# ---------------------------------------------------------------------------
# GET /api/v1/billing/activate
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/billing/activate",
    response_model=SuccessResponse[ActivationInfoResponse],
    summary="Validate activation token and return user info",
)
async def activate_info(
    token: str,
    db: DBSession,
) -> SuccessResponse[ActivationInfoResponse]:
    """
    Validate an activation or renewal token and return the associated user
    information together with the list of available plans.

    The token is sent to the user via WhatsApp as part of the activation
    flow.  This endpoint is used by the frontend to pre-fill the checkout
    page.
    """
    log = logger.bind(token=token[:8] + "...")

    reg_token, user = await _resolve_activation_token(db, token)

    plans = await _get_active_plans(db)

    log.info(
        "billing.activate.validated",
        user_id=user.id,
        plans_count=len(plans),
    )

    return SuccessResponse(
        data=ActivationInfoResponse(
            user_name=user.display_name,
            phone_masked=_mask_phone(user.whatsapp_phone),
            plans=plans,
        )
    )


# ---------------------------------------------------------------------------
# POST /api/v1/billing/checkout-session
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/billing/checkout-session",
    response_model=SuccessResponse[CheckoutResponse],
    summary="Create a Stripe checkout session",
)
async def create_checkout(
    body: CheckoutRequest,
    db: DBSession,
) -> SuccessResponse[CheckoutResponse]:
    """
    Resolve the user from the activation token and create a Stripe Checkout
    Session for the requested plan.

    Returns the Stripe-hosted checkout page URL.
    """
    log = logger.bind(
        token=body.token[:8] + "...",
        plan_code=body.plan_code,
    )

    reg_token, user = await _resolve_activation_token(db, body.token)

    try:
        checkout_url = await create_checkout_session(
            db, user, body.plan_code, activation_token=body.token
        )
    except ValueError as exc:
        log.warning("billing.checkout.validation_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except stripe.error.StripeError as exc:
        log.error("billing.checkout.stripe_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment provider error. Please try again.",
        )

    log.info("billing.checkout.session_created", user_id=user.id)

    return SuccessResponse(
        data=CheckoutResponse(checkout_url=checkout_url)
    )


# ---------------------------------------------------------------------------
# POST /webhooks/billing  (Stripe webhook)
# ---------------------------------------------------------------------------


@router.post(
    "/webhooks/billing",
    status_code=status.HTTP_200_OK,
    summary="Stripe webhook handler",
)
async def stripe_webhook(
    request: Request,
    db: DBSession,
) -> dict[str, str]:
    """
    Receive and process Stripe webhook events.

    The handler verifies the webhook signature using the
    ``STRIPE_WEBHOOK_SECRET`` before processing any event.  Supported
    event types:

    - ``checkout.session.completed``
    - ``customer.subscription.updated``
    - ``customer.subscription.deleted``

    Returns 200 for all events (including unhandled types) to prevent
    Stripe from disabling the webhook endpoint.
    """
    raw_body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error("billing.webhook.secret_not_configured")
        return {"status": "error", "reason": "webhook_secret_not_configured"}

    # Verify the Stripe signature
    try:
        event = stripe.Webhook.construct_event(
            payload=raw_body,
            sig_header=sig_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        logger.warning("billing.webhook.invalid_payload")
        return {"status": "error", "reason": "invalid_payload"}
    except stripe.error.SignatureVerificationError:
        logger.warning("billing.webhook.invalid_signature")
        return {"status": "error", "reason": "invalid_signature"}

    event_type: str = event.get("type", "")
    event_data: dict = event.get("data", {}).get("object", {})

    log = logger.bind(
        event_type=event_type,
        event_id=event.get("id"),
    )

    try:
        if event_type == "checkout.session.completed":
            await handle_checkout_completed(db, event_data)
            log.info("billing.webhook.checkout_completed")

        elif event_type == "customer.subscription.updated":
            await handle_subscription_updated(db, event_data)
            log.info("billing.webhook.subscription_updated")

        elif event_type == "customer.subscription.deleted":
            await handle_subscription_deleted(db, event_data)
            log.info("billing.webhook.subscription_deleted")

        else:
            log.debug("billing.webhook.unhandled_event")

    except Exception as exc:
        # Never return a non-200 to Stripe -- log the error and move on
        log.exception(
            "billing.webhook.handler_error",
            error=str(exc),
        )

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /api/v1/billing/status
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/billing/status",
    response_model=SuccessResponse[BillingStatusResponse],
    summary="Get current billing status",
)
async def billing_status(
    current_user: CurrentUser,
    db: DBSession,
) -> SuccessResponse[BillingStatusResponse]:
    """
    Return the billing status for the currently authenticated user.

    Includes the active plan, subscription status, remaining credits, and
    current period end date.
    """
    billing = await get_user_billing_status(db, current_user.id)

    user_status = (
        current_user.status.value
        if hasattr(current_user.status, "value")
        else str(current_user.status)
    )

    logger.info(
        "billing.status.queried",
        user_id=current_user.id,
        plan_code=billing["plan_code"],
    )

    return SuccessResponse(
        data=BillingStatusResponse(
            status=user_status,
            plan_code=billing["plan_code"],
            remaining_credits=billing["remaining_credits"],
            subscription_status=billing["subscription_status"],
            current_period_end=billing["current_period_end"],
        )
    )


# ---------------------------------------------------------------------------
# POST /api/v1/billing/portal
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/billing/portal",
    response_model=SuccessResponse[PortalResponse],
    summary="Create Stripe Customer Portal session",
)
async def customer_portal(
    current_user: CurrentUser,
    db: DBSession,
) -> SuccessResponse[PortalResponse]:
    """
    Create a Stripe Customer Portal session for the authenticated user.

    The portal allows the user to manage their subscription, update
    payment methods, and view billing history.
    """
    log = logger.bind(user_id=current_user.id)

    try:
        portal_url = await create_customer_portal_session(db, current_user)
    except ValueError as exc:
        log.warning("billing.portal.validation_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except stripe.error.StripeError as exc:
        log.error("billing.portal.stripe_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment provider error. Please try again.",
        )

    log.info("billing.portal.created")

    return SuccessResponse(
        data=PortalResponse(portal_url=portal_url)
    )
