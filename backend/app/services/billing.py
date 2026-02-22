"""
Billing service for ScanbonAI.

Handles Stripe integration and credits ledger management for the billing
system.  All Stripe API calls are guarded behind a check for a non-empty
``STRIPE_SECRET_KEY`` so the application can start in environments where
billing is not configured (local dev, CI).

Public API
----------
- get_remaining_credits      -- SUM(delta) from the credits ledger
- can_process_invoice        -- gate check before invoice processing
- deduct_credit              -- insert -1 entry in credits ledger
- is_ultra_plan              -- check if user has an active ULTRA subscription
- create_checkout_session    -- create a Stripe Checkout Session
- handle_checkout_completed  -- process checkout.session.completed webhook
- handle_subscription_updated -- process customer.subscription.updated webhook
- handle_subscription_deleted -- process customer.subscription.deleted webhook
- create_customer_portal_session -- create a Stripe Customer Portal session
- get_user_billing_status    -- return a summary dict of the user's billing state
"""

from __future__ import annotations

from datetime import datetime, timezone

import stripe
import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    BillingPlan,
    UserSubscription,
    UserCreditsLedger,
    User,
    UserStatus,
    PlanType,
    SubscriptionStatus,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Stripe client configuration
# ---------------------------------------------------------------------------

if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _stripe_configured() -> bool:
    """Return True when a Stripe secret key has been provided."""
    return bool(settings.STRIPE_SECRET_KEY)


# ---------------------------------------------------------------------------
# Credits helpers
# ---------------------------------------------------------------------------


async def get_remaining_credits(db: AsyncSession, user_id: str) -> int:
    """
    Return the current credit balance for *user_id*.

    The balance is computed as ``SUM(delta)`` over all rows in the
    ``user_credits_ledger`` table for this user.  Returns ``0`` when no
    rows exist.
    """
    result = await db.execute(
        select(func.coalesce(func.sum(UserCreditsLedger.delta), 0)).where(
            UserCreditsLedger.user_id == user_id,
        )
    )
    return int(result.scalar_one())


async def can_process_invoice(
    db: AsyncSession, user: User
) -> tuple[bool, str]:
    """
    Determine whether *user* is allowed to process another invoice.

    Returns
    -------
    tuple[bool, str]
        ``(allowed, reason)`` where *reason* is a short machine-readable
        string explaining the decision.

    Rules (evaluated in order)
    --------------------------
    1. User status is not ACTIVE  ->  ``(False, "not_active")``
    2. User holds an active UNLIMITED or ULTRA subscription
       ->  ``(True, "subscription_active")``
    3. User has remaining credits > 0  ->  ``(True, "credits_available")``
    4. Otherwise  ->  ``(False, "credits_exhausted")``
    """
    # Rule 1: user must be active
    if user.status != UserStatus.ACTIVE:
        return False, "not_active"

    # Rule 2: active subscription that grants unlimited processing
    sub_result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.user_id == user.id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.plan_code.in_(["UNLIMITED", "ULTRA"]),
        )
    )
    if sub_result.scalars().first() is not None:
        return True, "subscription_active"

    # Rule 3: credits remaining
    credits = await get_remaining_credits(db, user.id)
    if credits > 0:
        return True, "credits_available"

    # Rule 4: exhausted
    return False, "credits_exhausted"


async def deduct_credit(
    db: AsyncSession, user_id: str, invoice_id: str
) -> None:
    """
    Record a single credit deduction (-1) against *user_id* for *invoice_id*.
    """
    entry = UserCreditsLedger(
        user_id=user_id,
        delta=-1,
        reason="invoice_processed",
        invoice_id=invoice_id,
    )
    db.add(entry)
    await db.flush()
    logger.info(
        "billing.credit.deducted",
        user_id=user_id,
        invoice_id=invoice_id,
    )


async def is_ultra_plan(db: AsyncSession, user_id: str) -> bool:
    """Return ``True`` if *user_id* has an active ULTRA subscription."""
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.plan_code == "ULTRA",
        )
    )
    return result.scalars().first() is not None


# ---------------------------------------------------------------------------
# Stripe Checkout
# ---------------------------------------------------------------------------


async def create_checkout_session(
    db: AsyncSession,
    user: User,
    plan_code: str,
    activation_token: str | None = None,
) -> str:
    """
    Create a Stripe Checkout Session and return the session URL.

    Parameters
    ----------
    db:
        Active database session.
    user:
        The user who is purchasing.
    plan_code:
        Code of the ``BillingPlan`` to purchase (e.g. ``"CREDITS_100"``).
    activation_token:
        Optional activation / renewal token value.  Stored in session
        metadata so the webhook handler can mark it as used.

    Returns
    -------
    str
        The Stripe-hosted Checkout page URL.

    Raises
    ------
    ValueError
        If the plan code is unknown, inactive, or Stripe is not configured.
    stripe.error.StripeError
        On any Stripe API failure (logged and re-raised).
    """
    if not _stripe_configured():
        raise ValueError("Stripe is not configured on this server.")

    # Resolve plan
    plan_result = await db.execute(
        select(BillingPlan).where(
            BillingPlan.code == plan_code,
            BillingPlan.is_active.is_(True),
        )
    )
    plan: BillingPlan | None = plan_result.scalars().first()

    if plan is None:
        raise ValueError(f"Unknown or inactive plan: {plan_code!r}")

    log = logger.bind(
        user_id=user.id,
        plan_code=plan_code,
        plan_type=plan.plan_type.value if hasattr(plan.plan_type, "value") else str(plan.plan_type),
    )

    # Find or create Stripe customer
    stripe_customer_id = await _find_or_create_stripe_customer(db, user)

    # Determine checkout mode
    if plan.plan_type == PlanType.CREDITS:
        mode = "payment"
    else:
        mode = "subscription"

    # Build metadata
    metadata: dict[str, str] = {
        "user_id": user.id,
        "tenant_id": user.tenant_id,
        "plan_code": plan_code,
    }
    if activation_token:
        metadata["activation_token"] = activation_token

    success_url = (
        f"{settings.PUBLIC_BASE_URL}/activation-success"
        f"?session_id={{CHECKOUT_SESSION_ID}}"
    )
    cancel_url = f"{settings.PUBLIC_BASE_URL}/activate?canceled=true"

    try:
        session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            mode=mode,
            line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
            metadata=metadata,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        log.info("billing.checkout.created", session_id=session.id)
        return session.url
    except stripe.error.StripeError as exc:
        log.error("billing.checkout.stripe_error", error=str(exc))
        raise


async def _find_or_create_stripe_customer(
    db: AsyncSession, user: User
) -> str:
    """
    Return the Stripe customer ID for *user*.

    If the user already has a ``UserSubscription`` with a
    ``stripe_customer_id``, that value is reused.  Otherwise a new Stripe
    Customer is created.
    """
    # Check for an existing Stripe customer linked to this user
    sub_result = await db.execute(
        select(UserSubscription.stripe_customer_id).where(
            UserSubscription.user_id == user.id,
            UserSubscription.stripe_customer_id.isnot(None),
        ).limit(1)
    )
    existing_customer_id: str | None = sub_result.scalar_one_or_none()

    if existing_customer_id:
        return existing_customer_id

    # Create a new Stripe customer
    try:
        customer = stripe.Customer.create(
            metadata={"user_id": user.id, "tenant_id": user.tenant_id},
            phone=user.whatsapp_phone,
            name=user.display_name or user.whatsapp_phone,
        )
        logger.info(
            "billing.stripe_customer.created",
            user_id=user.id,
            stripe_customer_id=customer.id,
        )
        return customer.id
    except stripe.error.StripeError as exc:
        logger.error(
            "billing.stripe_customer.create_failed",
            user_id=user.id,
            error=str(exc),
        )
        raise


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------


async def handle_checkout_completed(
    db: AsyncSession, session: dict
) -> None:
    """
    Process a ``checkout.session.completed`` Stripe webhook event.

    Steps
    -----
    1. Look up the user from ``session.metadata.user_id``.
    2. Create or update a ``UserSubscription`` record.
    3. If the plan is a credits plan, add credits to the ledger.
    4. Set ``user.status = ACTIVE``.
    """
    metadata: dict = session.get("metadata", {})
    user_id: str = metadata.get("user_id", "")
    plan_code: str = metadata.get("plan_code", "")
    stripe_customer_id: str = session.get("customer", "")

    log = logger.bind(
        user_id=user_id,
        plan_code=plan_code,
        stripe_session_id=session.get("id"),
    )

    if not user_id or not plan_code:
        log.warning("billing.checkout_completed.missing_metadata")
        return

    # 1. Look up user
    user_result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user: User | None = user_result.scalars().first()

    if user is None:
        log.error("billing.checkout_completed.user_not_found")
        return

    # Resolve plan
    plan_result = await db.execute(
        select(BillingPlan).where(BillingPlan.code == plan_code)
    )
    plan: BillingPlan | None = plan_result.scalars().first()

    if plan is None:
        log.error("billing.checkout_completed.plan_not_found")
        return

    # 2. Create or update UserSubscription
    stripe_subscription_id: str | None = session.get("subscription")

    # Look for an existing subscription row for this user + plan
    existing_sub_result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.user_id == user_id,
            UserSubscription.plan_code == plan_code,
        )
    )
    existing_sub: UserSubscription | None = existing_sub_result.scalars().first()

    if existing_sub:
        existing_sub.stripe_customer_id = stripe_customer_id
        existing_sub.stripe_subscription_id = stripe_subscription_id
        existing_sub.status = SubscriptionStatus.ACTIVE
        log.info("billing.checkout_completed.subscription_updated")
    else:
        new_sub = UserSubscription(
            user_id=user_id,
            tenant_id=user.tenant_id,
            plan_code=plan_code,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            status=SubscriptionStatus.ACTIVE,
        )
        db.add(new_sub)
        log.info("billing.checkout_completed.subscription_created")

    # 3. If credits plan, add credits to the ledger
    if plan.plan_type == PlanType.CREDITS and plan.credits_amount:
        ledger_entry = UserCreditsLedger(
            user_id=user_id,
            delta=plan.credits_amount,
            reason="purchase",
            stripe_payment_intent_id=session.get("payment_intent"),
        )
        db.add(ledger_entry)
        log.info(
            "billing.checkout_completed.credits_added",
            credits=plan.credits_amount,
        )

    # 4. Activate user
    user.status = UserStatus.ACTIVE

    # Mark activation token as used (if present)
    activation_token_value = metadata.get("activation_token")
    if activation_token_value:
        from app.models import RegistrationToken

        token_result = await db.execute(
            select(RegistrationToken).where(
                RegistrationToken.token == activation_token_value,
            )
        )
        reg_token: RegistrationToken | None = token_result.scalars().first()
        if reg_token and reg_token.used_at is None:
            reg_token.used_at = datetime.now(tz=timezone.utc)
            log.info("billing.checkout_completed.token_marked_used")

    await db.flush()
    log.info("billing.checkout_completed.done")


async def handle_subscription_updated(
    db: AsyncSession, subscription: dict
) -> None:
    """
    Process a ``customer.subscription.updated`` Stripe webhook event.

    Updates the ``UserSubscription.status`` to reflect the current Stripe
    subscription status (active, past_due, incomplete, etc.).
    """
    stripe_subscription_id: str = subscription.get("id", "")
    stripe_status: str = subscription.get("status", "")

    log = logger.bind(
        stripe_subscription_id=stripe_subscription_id,
        stripe_status=stripe_status,
    )

    if not stripe_subscription_id:
        log.warning("billing.subscription_updated.missing_id")
        return

    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.stripe_subscription_id == stripe_subscription_id,
        )
    )
    user_sub: UserSubscription | None = result.scalars().first()

    if user_sub is None:
        log.warning("billing.subscription_updated.not_found")
        return

    # Map Stripe status to our enum
    status_map = {
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "incomplete": SubscriptionStatus.INCOMPLETE,
    }
    new_status = status_map.get(stripe_status)
    if new_status is None:
        log.warning(
            "billing.subscription_updated.unknown_status",
            raw_status=stripe_status,
        )
        return

    user_sub.status = new_status

    # Update current_period_end if provided
    period_end_ts = subscription.get("current_period_end")
    if period_end_ts:
        user_sub.current_period_end = datetime.fromtimestamp(
            period_end_ts, tz=timezone.utc
        )

    await db.flush()
    log.info(
        "billing.subscription_updated.done",
        user_id=user_sub.user_id,
        new_status=new_status.value,
    )


async def handle_subscription_deleted(
    db: AsyncSession, subscription: dict
) -> None:
    """
    Process a ``customer.subscription.deleted`` Stripe webhook event.

    Steps
    -----
    1. Set the ``UserSubscription.status`` to ``CANCELED``.
    2. Check if the user has any other active subscriptions or remaining
       credits.
    3. If not, set ``user.status = SUSPENDED``.
    """
    stripe_subscription_id: str = subscription.get("id", "")

    log = logger.bind(stripe_subscription_id=stripe_subscription_id)

    if not stripe_subscription_id:
        log.warning("billing.subscription_deleted.missing_id")
        return

    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.stripe_subscription_id == stripe_subscription_id,
        )
    )
    user_sub: UserSubscription | None = result.scalars().first()

    if user_sub is None:
        log.warning("billing.subscription_deleted.not_found")
        return

    # 1. Cancel this subscription
    user_sub.status = SubscriptionStatus.CANCELED
    user_id = user_sub.user_id

    log = log.bind(user_id=user_id)

    # 2. Check for other active subscriptions
    other_active_result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.id != user_sub.id,
        )
    )
    has_other_active = other_active_result.scalars().first() is not None

    # Check remaining credits
    remaining = await get_remaining_credits(db, user_id)

    # 3. Suspend user if no active subscriptions and no credits
    if not has_other_active and remaining <= 0:
        user_result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user: User | None = user_result.scalars().first()
        if user:
            user.status = UserStatus.SUSPENDED
            log.info("billing.subscription_deleted.user_suspended")

    await db.flush()
    log.info("billing.subscription_deleted.done")


# ---------------------------------------------------------------------------
# Customer Portal
# ---------------------------------------------------------------------------


async def create_customer_portal_session(
    db: AsyncSession, user: User
) -> str:
    """
    Create a Stripe Customer Portal session for *user* and return the URL.

    Raises
    ------
    ValueError
        If Stripe is not configured or the user has no Stripe customer ID.
    stripe.error.StripeError
        On any Stripe API failure.
    """
    if not _stripe_configured():
        raise ValueError("Stripe is not configured on this server.")

    # Look up the user's Stripe customer ID
    sub_result = await db.execute(
        select(UserSubscription.stripe_customer_id).where(
            UserSubscription.user_id == user.id,
            UserSubscription.stripe_customer_id.isnot(None),
        ).limit(1)
    )
    customer_id: str | None = sub_result.scalar_one_or_none()

    if not customer_id:
        raise ValueError("No Stripe customer record found for this user.")

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{settings.PUBLIC_BASE_URL}/billing",
        )
        logger.info(
            "billing.portal.created",
            user_id=user.id,
            portal_session_id=portal_session.id,
        )
        return portal_session.url
    except stripe.error.StripeError as exc:
        logger.error(
            "billing.portal.stripe_error",
            user_id=user.id,
            error=str(exc),
        )
        raise


# ---------------------------------------------------------------------------
# Billing status
# ---------------------------------------------------------------------------


async def get_user_billing_status(
    db: AsyncSession, user_id: str
) -> dict:
    """
    Return a summary dict describing the user's current billing state.

    Keys
    ----
    - ``plan_code``           -- code of the active plan (or ``None``)
    - ``subscription_status`` -- status string (or ``None``)
    - ``remaining_credits``   -- integer credit balance
    - ``current_period_end``  -- ISO-8601 string (or ``None``)
    """
    # Active subscription (prefer ULTRA > UNLIMITED > anything)
    sub_result = await db.execute(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
        )
        .order_by(UserSubscription.created_at.desc())
        .limit(1)
    )
    active_sub: UserSubscription | None = sub_result.scalars().first()

    remaining = await get_remaining_credits(db, user_id)

    period_end: str | None = None
    if active_sub and active_sub.current_period_end:
        period_end = active_sub.current_period_end.isoformat()

    return {
        "plan_code": active_sub.plan_code if active_sub else None,
        "subscription_status": (
            active_sub.status.value
            if active_sub and hasattr(active_sub.status, "value")
            else (str(active_sub.status) if active_sub else None)
        ),
        "remaining_credits": remaining,
        "current_period_end": period_end,
    }
