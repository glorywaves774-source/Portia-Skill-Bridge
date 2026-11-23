"""Invoices, payments and saved payment methods (Paystack)."""
from __future__ import annotations

import secrets

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id


def new_reference() -> str:
    """Our transaction reference, sent to Paystack. Random, so it can't be guessed."""
    return "PSB-" + secrets.token_hex(10).upper()


class InvoiceStatus:
    OPEN = "open"        # waiting for payment
    PAID = "paid"
    FAILED = "failed"    # an automatic charge or checkout failed; a new invoice is made for the next try
    VOID = "void"        # cancelled before payment
    LABELS = {OPEN: "Awaiting payment", PAID: "Paid", FAILED: "Failed", VOID: "Cancelled"}


class InvoiceKind:
    NEW = "new"            # first payment, after a trial, or after the subscription ended
    RENEWAL = "renewal"    # another period on the same plan
    UPGRADE = "upgrade"    # move up now; unused time on the old plan is credited
    LABELS = {NEW: "New subscription", RENEWAL: "Renewal", UPGRADE: "Plan upgrade"}


PAYMENT_CHANNELS = {"card": "Card", "mobile_money": "Mobile Money", "bank": "Bank", "bank_transfer": "Bank transfer",
                    "ussd": "USSD", "qr": "QR", "manual": "Recorded by PORTIA"}


class Invoice(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    number = db.Column(db.String(40), nullable=False)
    reference = db.Column(db.String(40), nullable=False, unique=True, default=new_reference)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("subscription_plans.id"), nullable=False)
    billing_cycle = db.Column(db.String(10), nullable=False)
    kind = db.Column(db.String(10), nullable=False, default=InvoiceKind.NEW)
    description = db.Column(db.String(255), nullable=False)
    price_minor = db.Column(db.Integer, nullable=False)          # the plan's price for one period
    credit_minor = db.Column(db.Integer, nullable=False, default=0)  # unused time on the old plan
    amount_minor = db.Column(db.Integer, nullable=False)         # what is charged
    currency = db.Column(db.String(3), nullable=False, default="GHS")
    status = db.Column(db.String(10), nullable=False, default=InvoiceStatus.OPEN, index=True)
    billing_email = db.Column(db.String(255))
    automatic = db.Column(db.Boolean, nullable=False, default=False)  # charged to a saved method by the daily job
    period_start = db.Column(UTCDateTime())
    period_end = db.Column(UTCDateTime())
    provider = db.Column(db.String(20), nullable=False, default="paystack")  # paystack | manual
    channel = db.Column(db.String(20))
    channel_detail = db.Column(db.String(80))   # e.g. "Visa •••• 4081" or "MTN Mobile Money"
    provider_txn_id = db.Column(db.String(40))
    paid_at = db.Column(UTCDateTime())
    failure_reason = db.Column(db.String(255))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    tenant = db.relationship("Tenant")
    plan = db.relationship("SubscriptionPlan", lazy="joined")
    subscription = db.relationship("Subscription")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "number", name="uq_invoices_tenant_number"),
        db.CheckConstraint("status IN ('open', 'paid', 'failed', 'void')", name="status_valid"),
        db.CheckConstraint("kind IN ('new', 'renewal', 'upgrade')", name="kind_valid"),
        db.CheckConstraint("amount_minor >= 0 AND price_minor >= 0 AND credit_minor >= 0", name="amounts_valid"),
    )

    @property
    def status_label(self) -> str:
        return InvoiceStatus.LABELS.get(self.status, self.status)

    @property
    def kind_label(self) -> str:
        return InvoiceKind.LABELS.get(self.kind, self.kind)

    @property
    def channel_label(self) -> str:
        return PAYMENT_CHANNELS.get(self.channel or "", (self.channel or "").replace("_", " ").title())

    @property
    def is_paid(self) -> bool:
        return self.status == InvoiceStatus.PAID


class PaymentMethod(TenantScopedMixin, TimestampMixin, db.Model):
    """A reusable Paystack authorization, used for automatic renewals. Never shown in full."""
    __tablename__ = "payment_methods"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    authorization_code = db.Column(db.String(80), nullable=False)
    signature = db.Column(db.String(80))       # Paystack's fingerprint of the card, to avoid duplicates
    channel = db.Column(db.String(20), nullable=False, default="card")
    brand = db.Column(db.String(40))           # visa, mastercard, MTN…
    last4 = db.Column(db.String(4))
    exp_month = db.Column(db.String(2))
    exp_year = db.Column(db.String(4))
    bank = db.Column(db.String(80))
    email = db.Column(db.String(255), nullable=False)  # Paystack ties the authorization to this email
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    @property
    def label(self) -> str:
        brand = (self.brand or self.channel or "Card").strip().title()
        if self.last4:
            return f"{brand} •••• {self.last4}"
        return brand

    @property
    def expiry(self) -> str | None:
        if self.exp_month and self.exp_year:
            return f"{int(self.exp_month):02d}/{self.exp_year[-2:]}"
        return None
