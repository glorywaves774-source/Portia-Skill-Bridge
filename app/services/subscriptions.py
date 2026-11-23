"""Subscription lifecycle: trials, activation, renewal, expiry, cancellation, plan changes.

Payment collection (Paystack) arrives in Stage 9 and will call `activate()` /
`renew()` after a verified payment. Until then these are called by platform
administrators and the `flask subscriptions process` scheduled command.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.extensions import db
from app.models.subscription import (
    BillingCycle,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    add_months,
)
from app.models.tenant import Tenant
from app.models.base import utcnow
from app.services import audit


class SubscriptionError(ValueError):
    pass


def period_end(start: datetime, cycle: str) -> datetime:
    if cycle == BillingCycle.MONTHLY:
        return add_months(start, 1)
    if cycle == BillingCycle.ANNUAL:
        return add_months(start, 12)
    raise SubscriptionError(f"Unknown billing cycle '{cycle}'")


def _check_audience(tenant: Tenant, plan: SubscriptionPlan) -> None:
    if plan.audience != tenant.kind:
        raise SubscriptionError(
            f"Plan '{plan.name}' is for {plan.audience} accounts; this workspace is an {tenant.kind} account."
        )
    if not plan.is_active:
        raise SubscriptionError(f"Plan '{plan.name}' is no longer available.")


def _live_subscription(tenant: Tenant) -> Subscription | None:
    return db.session.scalar(
        select(Subscription).where(
            Subscription.tenant_id == tenant.id,
            Subscription.status.in_(SubscriptionStatus.LIVE),
        )
    )


def start_subscription(
    tenant: Tenant,
    plan: SubscriptionPlan,
    cycle: str = BillingCycle.MONTHLY,
    *,
    now: datetime | None = None,
    with_trial: bool = True,
    actor=None,
) -> Subscription:
    """Start a new subscription: a trial for paid plans, active straight away for free plans."""
    now = now or utcnow()
    _check_audience(tenant, plan)
    if cycle not in BillingCycle.ALL:
        raise SubscriptionError(f"Unknown billing cycle '{cycle}'")
    if _live_subscription(tenant) is not None:
        raise SubscriptionError("This workspace already has a live subscription. Change plan instead.")

    sub = Subscription(
        tenant_id=tenant.id,
        plan=plan,
        billing_cycle=cycle,
        currency=plan.currency,
        price_minor=0 if plan.is_custom_pricing else plan.price_minor(cycle),
        started_at=now,
        current_period_start=now,
    )
    if plan.is_free:
        sub.status = SubscriptionStatus.ACTIVE
        sub.current_period_end = period_end(now, cycle)
    elif with_trial and plan.trial_days > 0:
        sub.status = SubscriptionStatus.TRIALING
        sub.trial_ends_at = now + timedelta(days=plan.trial_days)
        sub.current_period_end = sub.trial_ends_at
    else:
        # Paid plan without trial: created as awaiting payment until activated.
        sub.status = SubscriptionStatus.PAST_DUE
        sub.current_period_end = now
        sub.status_reason = "Awaiting first payment"

    db.session.add(sub)
    audit.record(
        "subscription.started", tenant_id=tenant.id, actor=actor, entity_type="subscription",
        summary=f"{plan.name} ({cycle}) — {SubscriptionStatus.LABELS[sub.status]}",
        details={"plan": plan.code, "cycle": cycle, "status": sub.status},
    )
    return sub


def activate(sub: Subscription, *, now: datetime | None = None, cycle: str | None = None, actor=None,
             reference: str | None = None) -> Subscription:
    """Begin a fresh paid period (after payment is verified, or by an administrator)."""
    now = now or utcnow()
    if sub.status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED):
        raise SubscriptionError("Ended subscriptions cannot be reactivated; start a new one.")
    if sub.status == SubscriptionStatus.SUSPENDED:
        raise SubscriptionError("Subscription is suspended; reactivate it first.")
    if cycle:
        if cycle not in BillingCycle.ALL:
            raise SubscriptionError(f"Unknown billing cycle '{cycle}'")
        sub.billing_cycle = cycle
    if sub.status == SubscriptionStatus.TRIALING and sub.trial_ends_at and sub.trial_ends_at > now:
        sub.trial_ends_at = now  # paying during a trial ends the trial
    sub.status = SubscriptionStatus.ACTIVE
    sub.current_period_start = now
    sub.current_period_end = period_end(now, sub.billing_cycle)
    sub.price_minor = 0 if sub.plan.is_custom_pricing else sub.plan.price_minor(sub.billing_cycle)
    sub.status_reason = None
    audit.record(
        "subscription.activated", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
        entity_id=sub.id, summary=f"Activated until {sub.current_period_end:%d %b %Y}",
        details={"reference": reference, "cycle": sub.billing_cycle},
    )
    return sub


def renew(sub: Subscription, *, now: datetime | None = None, actor=None, reference: str | None = None) -> Subscription:
    """Extend by one billing period. Renewing early stacks onto the current period."""
    now = now or utcnow()
    if sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.TRIALING):
        raise SubscriptionError(f"A {sub.status_label.lower()} subscription cannot be renewed.")
    start = sub.current_period_end if (sub.status == SubscriptionStatus.ACTIVE and sub.current_period_end > now) else now
    sub.status = SubscriptionStatus.ACTIVE
    sub.current_period_start = start
    sub.current_period_end = period_end(start, sub.billing_cycle)
    sub.price_minor = 0 if sub.plan.is_custom_pricing else sub.plan.price_minor(sub.billing_cycle)
    sub.status_reason = None
    audit.record(
        "subscription.renewed", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
        entity_id=sub.id, summary=f"Renewed until {sub.current_period_end:%d %b %Y}",
        details={"reference": reference},
    )
    return sub


def extend_trial(sub: Subscription, days: int, *, actor=None) -> Subscription:
    if sub.status != SubscriptionStatus.TRIALING:
        raise SubscriptionError("Only a subscription on trial can have its trial extended.")
    if not 1 <= int(days) <= 90:
        raise SubscriptionError("Extend by 1 to 90 days.")
    sub.trial_ends_at = sub.trial_ends_at + timedelta(days=int(days))
    sub.current_period_end = sub.trial_ends_at
    audit.record("subscription.trial_extended", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
                 entity_id=sub.id, summary=f"Trial extended by {days} days to {sub.trial_ends_at:%d %b %Y}")
    return sub


def cancel(sub: Subscription, *, immediately: bool = False, now: datetime | None = None, actor=None,
           reason: str | None = None) -> Subscription:
    now = now or utcnow()
    if sub.status not in SubscriptionStatus.LIVE:
        raise SubscriptionError("Subscription has already ended.")
    sub.auto_renew = False
    sub.cancelled_at = now
    sub.status_reason = reason
    if immediately or sub.status == SubscriptionStatus.TRIALING:
        sub.status = SubscriptionStatus.CANCELLED
        sub.ended_at = now
    else:
        sub.cancel_at_period_end = True
    audit.record(
        "subscription.cancelled", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
        entity_id=sub.id, summary="Cancelled now" if sub.ended_at else "Cancels at end of period",
        details={"reason": reason},
    )
    return sub


def change_plan(sub: Subscription, new_plan: SubscriptionPlan, *, actor=None) -> Subscription:
    """Switch plan. The new price applies from the next billing period (no proration yet)."""
    _check_audience(sub.tenant, new_plan)
    if sub.status not in SubscriptionStatus.LIVE:
        raise SubscriptionError("Subscription has already ended.")
    old = sub.plan
    sub.plan = new_plan
    audit.record(
        "subscription.plan_changed", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
        entity_id=sub.id, summary=f"{old.name} → {new_plan.name}",
        details={"from": old.code, "to": new_plan.code},
    )
    return sub


def suspend(sub: Subscription, *, reason: str, now: datetime | None = None, actor=None) -> Subscription:
    if sub.status not in (SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
        raise SubscriptionError(f"A {sub.status_label.lower()} subscription cannot be suspended.")
    sub.status_reason = f"{sub.status}|{reason}"  # remember previous status for reactivation
    sub.status = SubscriptionStatus.SUSPENDED
    sub.suspended_at = now or utcnow()
    audit.record("subscription.suspended", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
                 entity_id=sub.id, summary=reason)
    return sub


def reactivate(sub: Subscription, *, actor=None) -> Subscription:
    if sub.status != SubscriptionStatus.SUSPENDED:
        raise SubscriptionError("Only suspended subscriptions can be reactivated.")
    previous = (sub.status_reason or "").split("|", 1)[0]
    sub.status = previous if previous in (SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE,
                                          SubscriptionStatus.PAST_DUE) else SubscriptionStatus.ACTIVE
    sub.suspended_at = None
    sub.status_reason = None
    audit.record("subscription.reactivated", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
                 entity_id=sub.id, summary=f"Restored to {sub.status_label}")
    return sub


def process_due(now: datetime | None = None) -> dict:
    """Move subscriptions along when their dates pass. Run daily (platform scope).

    * trial ended                      -> expired
    * active period ended, cancelling  -> cancelled
    * active period ended, auto-renew  -> past_due (free plans renew automatically)
    * active period ended, no renew    -> expired
    * past_due beyond grace period     -> expired
    """
    now = now or utcnow()
    counts = {"expired": 0, "cancelled": 0, "past_due": 0, "renewed": 0}
    candidates = db.session.scalars(
        select(Subscription).where(
            Subscription.status.in_((SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE,
                                     SubscriptionStatus.PAST_DUE)),
            Subscription.current_period_end <= now,
        )
    ).all()
    for sub in candidates:
        if sub.status == SubscriptionStatus.TRIALING:
            _end(sub, SubscriptionStatus.EXPIRED, now, "Trial ended without payment")
            counts["expired"] += 1
        elif sub.status == SubscriptionStatus.ACTIVE:
            if sub.cancel_at_period_end:
                _end(sub, SubscriptionStatus.CANCELLED, now, "Cancelled at period end")
                counts["cancelled"] += 1
            elif sub.auto_renew and sub.plan.is_free:
                renew(sub, now=now)
                counts["renewed"] += 1
            elif sub.auto_renew:
                sub.status = SubscriptionStatus.PAST_DUE
                sub.status_reason = "Renewal payment due"
                audit.record("subscription.past_due", tenant_id=sub.tenant_id, entity_type="subscription",
                             entity_id=sub.id, summary="Renewal payment due")
                counts["past_due"] += 1
            else:
                _end(sub, SubscriptionStatus.EXPIRED, now, "Period ended")
                counts["expired"] += 1
        elif sub.status == SubscriptionStatus.PAST_DUE and now >= sub.grace_ends_at():
            _end(sub, SubscriptionStatus.EXPIRED, now, "Grace period ended without payment")
            counts["expired"] += 1
    return counts


def _end(sub: Subscription, status: str, now: datetime, reason: str) -> None:
    sub.status = status
    sub.ended_at = now
    sub.status_reason = reason
    audit.record(f"subscription.{status}", tenant_id=sub.tenant_id, entity_type="subscription",
                 entity_id=sub.id, summary=reason)
