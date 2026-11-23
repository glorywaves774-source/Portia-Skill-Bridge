"""Subscription plans and tenant subscriptions.

Money is stored as integer minor units (pesewas for GHS) to avoid rounding errors.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from flask import current_app, has_app_context

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, utcnow


class PlanAudience:
    ORGANIZATION = "organization"
    INDIVIDUAL = "individual"
    ALL = (ORGANIZATION, INDIVIDUAL)


class BillingCycle:
    MONTHLY = "monthly"
    ANNUAL = "annual"
    ALL = (MONTHLY, ANNUAL)


class SubscriptionStatus:
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"      # period ended, awaiting payment, inside grace period
    SUSPENDED = "suspended"    # stopped by a platform administrator
    CANCELLED = "cancelled"    # ended by the subscriber
    EXPIRED = "expired"        # trial or grace period ran out without payment
    ALL = (TRIALING, ACTIVE, PAST_DUE, SUSPENDED, CANCELLED, EXPIRED)
    LIVE = (TRIALING, ACTIVE, PAST_DUE, SUSPENDED)  # at most one per tenant
    LABELS = {
        TRIALING: "Trial", ACTIVE: "Active", PAST_DUE: "Payment due",
        SUSPENDED: "Suspended", CANCELLED: "Cancelled", EXPIRED: "Expired",
    }


# Resource keys used by usage limits.
LIMIT_FIELDS = {
    "learners": "max_learners",
    "staff": "max_staff",
    "courses": "max_courses",
    "programmes": "max_programmes",
    "vacancies": "max_vacancies",
    "storage_mb": "storage_mb",
}


class SubscriptionPlan(TimestampMixin, db.Model):
    __tablename__ = "subscription_plans"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(80), nullable=False)
    audience = db.Column(db.String(20), nullable=False, index=True)
    tagline = db.Column(db.String(160))
    description = db.Column(db.Text)

    currency = db.Column(db.String(3), nullable=False, default="GHS")
    monthly_price_minor = db.Column(db.Integer, nullable=False, default=0)
    annual_price_minor = db.Column(db.Integer, nullable=False, default=0)
    is_custom_pricing = db.Column(db.Boolean, nullable=False, default=False)
    trial_days = db.Column(db.Integer, nullable=False, default=14)

    # None means unlimited.
    max_learners = db.Column(db.Integer)
    max_staff = db.Column(db.Integer)
    max_courses = db.Column(db.Integer)
    max_programmes = db.Column(db.Integer)
    max_vacancies = db.Column(db.Integer)
    storage_mb = db.Column(db.Integer)

    features = db.Column(db.JSON, nullable=False, default=list)
    is_public = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.CheckConstraint("audience IN ('organization', 'individual')", name="audience_valid"),
        db.CheckConstraint("monthly_price_minor >= 0 AND annual_price_minor >= 0", name="prices_non_negative"),
        db.CheckConstraint("trial_days >= 0", name="trial_days_non_negative"),
    )

    @property
    def is_free(self) -> bool:
        return not self.is_custom_pricing and self.monthly_price_minor == 0

    def price_minor(self, cycle: str) -> int:
        if cycle == BillingCycle.ANNUAL:
            return self.annual_price_minor
        if cycle == BillingCycle.MONTHLY:
            return self.monthly_price_minor
        raise ValueError(f"Unknown billing cycle: {cycle}")

    @property
    def annual_saving_minor(self) -> int:
        return max(0, self.monthly_price_minor * 12 - self.annual_price_minor)

    def limit_for(self, resource: str):
        field = LIMIT_FIELDS.get(resource)
        if field is None:
            raise KeyError(f"Unknown usage resource '{resource}'")
        return getattr(self, field)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "audience": self.audience,
            "tagline": self.tagline,
            "currency": self.currency,
            "monthly_price": None if self.is_custom_pricing else self.monthly_price_minor / 100,
            "annual_price": None if self.is_custom_pricing else self.annual_price_minor / 100,
            "custom_pricing": self.is_custom_pricing,
            "trial_days": self.trial_days,
            "limits": {key: getattr(self, field) for key, field in LIMIT_FIELDS.items()},
            "features": list(self.features or []),
        }

    def __repr__(self) -> str:
        return f"<Plan {self.code}>"


def add_months(moment: datetime, months: int) -> datetime:
    """Add calendar months, clamping to the last day (31 Jan + 1 month = 28/29 Feb)."""
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        next_month_first = moment.replace(year=year + 1, month=1, day=1)
    else:
        next_month_first = moment.replace(year=year, month=month + 1, day=1)
    last_day = (next_month_first - timedelta(days=1)).day
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def grace_days() -> int:
    if has_app_context():
        return int(current_app.config.get("BILLING_GRACE_DAYS", 7))
    return 7


class Subscription(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("subscription_plans.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, index=True)
    billing_cycle = db.Column(db.String(10), nullable=False, default=BillingCycle.MONTHLY)

    # Price agreed when the period started (plans can be repriced later).
    price_minor = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default="GHS")

    started_at = db.Column(UTCDateTime(), nullable=False, default=utcnow)
    trial_ends_at = db.Column(UTCDateTime())
    current_period_start = db.Column(UTCDateTime(), nullable=False, default=utcnow)
    current_period_end = db.Column(UTCDateTime(), nullable=False)
    auto_renew = db.Column(db.Boolean, nullable=False, default=True)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)
    cancelled_at = db.Column(UTCDateTime())
    ended_at = db.Column(UTCDateTime())
    suspended_at = db.Column(UTCDateTime())
    status_reason = db.Column(db.String(255))
    # A cheaper plan chosen for the next period (applied at renewal).
    scheduled_plan_id = db.Column(db.Integer, db.ForeignKey("subscription_plans.id"))
    scheduled_cycle = db.Column(db.String(10))
    renewal_attempts = db.Column(db.Integer, nullable=False, default=0)
    last_renewal_attempt_at = db.Column(UTCDateTime())
    reminder_sent_for = db.Column(UTCDateTime())  # the period end a reminder was last sent for

    tenant = db.relationship("Tenant", back_populates="subscriptions")
    plan = db.relationship("SubscriptionPlan", lazy="joined", foreign_keys=[plan_id])
    scheduled_plan = db.relationship("SubscriptionPlan", foreign_keys=[scheduled_plan_id])

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'suspended', 'cancelled', 'expired')",
            name="status_valid",
        ),
        db.CheckConstraint("billing_cycle IN ('monthly', 'annual')", name="billing_cycle_valid"),
        # One live subscription per tenant, enforced by the database.
        db.Index(
            "uq_subscriptions_one_live_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=db.text("status IN ('trialing', 'active', 'past_due', 'suspended')"),
            sqlite_where=db.text("status IN ('trialing', 'active', 'past_due', 'suspended')"),
        ),
    )

    @property
    def status_label(self) -> str:
        return SubscriptionStatus.LABELS.get(self.status, self.status)

    @property
    def is_trial(self) -> bool:
        return self.status == SubscriptionStatus.TRIALING

    def grace_ends_at(self) -> datetime:
        return self.current_period_end + timedelta(days=grace_days())

    def grants_access(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if self.status in (SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE):
            return now < self.current_period_end
        if self.status == SubscriptionStatus.PAST_DUE:
            return now < self.grace_ends_at()
        return False

    def days_remaining(self, now: datetime | None = None) -> int:
        """Whole days left, counting a part-day as a day (a new 14-day trial shows 14)."""
        now = now or utcnow()
        seconds = (self.current_period_end - now).total_seconds()
        return max(0, math.ceil(seconds / 86400))

    def __repr__(self) -> str:
        return f"<Subscription tenant={self.tenant_id} plan={self.plan_id} {self.status}>"
