"""Paying for subscriptions: quotes, invoices, Paystack checkout, verification, saved cards and renewals.

Rules (also shown to people on the checkout page):
* First payment, payment after a trial, or after the subscription ended: the full price; the paid period starts
  when payment is confirmed.
* Renewing the same plan: the full price; the new period starts when the current one ends (paying early never
  loses days).
* Moving to a more expensive plan (or from monthly to yearly): it starts straight away and the unused part of the
  current paid period is taken off the price.
* Moving to a cheaper plan: nothing to pay now; the change happens when the current period ends. It is refused if
  the workspace uses more than the cheaper plan allows.
* Custom-priced plans are arranged with the sales team, not paid online.
Payments are only trusted after the server verifies them with Paystack (or a signed webhook arrives), and only when
the amount and currency match the invoice. Processing the same payment twice changes nothing the second time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import Invoice, InvoiceKind, InvoiceStatus, PaymentMethod, TenantCounter
from app.models.base import utcnow
from app.models.subscription import (BillingCycle, Subscription, SubscriptionPlan, SubscriptionStatus,
                                     add_months)
from app.models.tenant import Tenant
from app.services import audit, limits, mailer, paystack
from app.services import notifications as notes
from app.services import subscriptions as subs

MIN_CHARGE_MINOR = 100           # GHS 1: anything smaller is not worth charging
MAX_RENEWAL_ATTEMPTS = 3         # automatic charges per period (one a day while payment is due)
REMINDER_DAYS = 3                # warn this many days before a trial or unpaid period ends


class BillingError(ValueError):
    pass


def money(minor: int | None, currency: str = "GHS") -> str:
    if minor is None:
        return "—"
    amount = minor / 100
    return f"{currency} {amount:,.0f}" if amount == int(amount) else f"{currency} {amount:,.2f}"


def _period_days(start: datetime, cycle: str) -> float:
    end = add_months(start, 12 if cycle == BillingCycle.ANNUAL else 1)
    return (end - start).total_seconds() / 86400


def _daily_rate(plan: SubscriptionPlan, cycle: str) -> float:
    return plan.price_minor(cycle) / (365.0 if cycle == BillingCycle.ANNUAL else 30.4)


# ---- Quotes ------------------------------------------------------------------------------------

@dataclass
class Quote:
    plan: SubscriptionPlan
    cycle: str
    kind: str                  # new | renewal | upgrade | downgrade | free | contact
    price_minor: int
    credit_minor: int = 0
    amount_minor: int = 0
    starts: str = "now"        # now | period_end
    starts_on: datetime | None = None
    message: str = ""
    over_limits: list[str] | None = None

    @property
    def payable(self) -> bool:
        return self.kind in (InvoiceKind.NEW, InvoiceKind.RENEWAL, InvoiceKind.UPGRADE) and self.amount_minor > 0


def live_subscription(tenant: Tenant) -> Subscription | None:
    sub = tenant.current_subscription
    return sub if sub is not None and sub.status in SubscriptionStatus.LIVE else None


def over_limits(tenant: Tenant, plan: SubscriptionPlan) -> list[str]:
    out = []
    for row in limits.summary(tenant):
        cap = plan.limit_for(row["resource"])
        if cap is not None and row["used"] is not None and row["used"] > cap:
            name = row["resource"].replace("_mb", " (MB)").replace("_", " ")
            out.append(f"{name}: using {row['used']}, the {plan.name} plan allows {cap}")
    return out


def quote(tenant: Tenant, plan: SubscriptionPlan, cycle: str, *, now: datetime | None = None) -> Quote:
    now = now or utcnow()
    if cycle not in BillingCycle.ALL:
        raise BillingError("Choose monthly or yearly billing.")
    if plan.audience != tenant.kind or not plan.is_active:
        raise BillingError("That plan isn't available for this workspace.")
    sub = live_subscription(tenant)
    if sub is not None and sub.status == SubscriptionStatus.SUSPENDED:
        raise BillingError("This workspace is suspended. Please contact PORTIA support.")
    if plan.is_custom_pricing:
        return Quote(plan, cycle, "contact", 0, message="This plan is priced for you by our sales team.")
    price = plan.price_minor(cycle)
    paid_active = sub is not None and sub.status == SubscriptionStatus.ACTIVE and sub.current_period_end > now
    same = sub is not None and sub.plan_id == plan.id and sub.billing_cycle == cycle

    if plan.is_free:
        if paid_active and not sub.plan.is_free:
            return Quote(plan, cycle, "downgrade", 0, starts="period_end", starts_on=sub.current_period_end,
                         over_limits=over_limits(tenant, plan),
                         message=f"Your plan changes to {plan.name} on {sub.current_period_end:%d %b %Y}.")
        return Quote(plan, cycle, "free", 0, over_limits=over_limits(tenant, plan),
                     message=f"{plan.name} is free. It starts straight away.")
    if same and sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
        start = sub.current_period_end if paid_active else now
        return Quote(plan, cycle, InvoiceKind.RENEWAL, price, amount_minor=price, starts="period_end" if paid_active
                     else "now", starts_on=start,
                     message=(f"Adds another {'year' if cycle == BillingCycle.ANNUAL else 'month'} after your current "
                              f"period ends on {sub.current_period_end:%d %b %Y}." if paid_active
                              else "Your paid period starts as soon as the payment is confirmed."))
    if paid_active and not sub.plan.is_free:
        if _daily_rate(plan, cycle) < _daily_rate(sub.plan, sub.billing_cycle):
            return Quote(plan, cycle, "downgrade", price, starts="period_end", starts_on=sub.current_period_end,
                         over_limits=over_limits(tenant, plan),
                         message=f"Nothing to pay now. You move to {plan.name} when your current period ends on "
                                 f"{sub.current_period_end:%d %b %Y}, and pay {money(price, plan.currency)} then.")
        total = (sub.current_period_end - sub.current_period_start).total_seconds()
        left = max(0.0, (sub.current_period_end - now).total_seconds())
        credit = int(sub.price_minor * left / total) if total > 0 else 0
        credit = min(sub.price_minor, int(round(credit / 100)) * 100)  # to the nearest cedi
        amount = max(0, price - credit)
        if amount < MIN_CHARGE_MINOR:
            return Quote(plan, cycle, "downgrade", price, starts="period_end", starts_on=sub.current_period_end,
                         over_limits=over_limits(tenant, plan),
                         message=f"Your unused time covers this change, so you move to {plan.name} when your "
                                 f"current period ends on {sub.current_period_end:%d %b %Y}.")
        return Quote(plan, cycle, InvoiceKind.UPGRADE, price, credit_minor=credit, amount_minor=amount,
                     message=f"{plan.name} starts as soon as the payment is confirmed. The unused part of your "
                             f"current plan ({money(credit, plan.currency)}) is taken off the price.")
    return Quote(plan, cycle, InvoiceKind.NEW, price, amount_minor=price,
                 message="Your paid period starts as soon as the payment is confirmed."
                 + (" Paying now ends your free trial." if sub is not None and sub.is_trial else ""))


# ---- Plan changes that need no payment ---------------------------------------------------------------

def schedule_change(tenant: Tenant, q: Quote, *, actor) -> Subscription:
    sub = live_subscription(tenant)
    if q.kind not in ("downgrade", "free"):
        raise BillingError("This change needs a payment.")
    if q.over_limits:
        raise BillingError("Your workspace uses more than the " + q.plan.name + " plan allows (" +
                           "; ".join(q.over_limits) + "). Remove some first, then try again.")
    if q.kind == "free":
        if sub is None:
            sub = subs.start_subscription(tenant, q.plan, q.cycle, actor=actor)
        else:
            sub.plan = q.plan
            sub.billing_cycle = q.cycle
            subs.activate(sub, actor=actor, reference="free plan")
        sub.scheduled_plan_id = sub.scheduled_cycle = None
        return sub
    sub.scheduled_plan_id = q.plan.id
    sub.scheduled_cycle = q.cycle
    sub.cancel_at_period_end = False
    sub.auto_renew = True
    audit.record("billing.change_scheduled", tenant_id=tenant.id, actor=actor, entity_type="subscription",
                 entity_id=sub.id, summary=f"Moves to {q.plan.name} ({q.cycle}) on {sub.current_period_end:%d %b %Y}")
    return sub


def cancel_scheduled_change(sub: Subscription, *, actor) -> None:
    if not sub.scheduled_plan_id:
        raise BillingError("No plan change is waiting.")
    name = sub.scheduled_plan.name
    sub.scheduled_plan_id = sub.scheduled_cycle = None
    audit.record("billing.change_cancelled", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
                 entity_id=sub.id, summary=f"Kept the current plan (no move to {name})")


def set_auto_renew(sub: Subscription, on: bool, *, actor) -> None:
    sub.auto_renew = bool(on)
    sub.cancel_at_period_end = not on and sub.status == SubscriptionStatus.ACTIVE
    audit.record("billing.auto_renew", tenant_id=sub.tenant_id, actor=actor, entity_type="subscription",
                 entity_id=sub.id, summary="Automatic renewal turned " + ("on" if on else "off (ends with this period)"))


# ---- Invoices and checkout ----------------------------------------------------------------------

def _number(tenant: Tenant) -> str:
    n = TenantCounter.next(tenant.id, "invoice")
    tail = (tenant.code or "WS").split("-")[-1]
    return f"INV-{tail}-{utcnow():%Y}-{n:05d}"


def create_invoice(tenant: Tenant, q: Quote, *, billing_email: str | None, actor=None,
                   automatic: bool = False) -> Invoice:
    if not q.payable:
        raise BillingError("There's nothing to pay for this change.")
    sub = live_subscription(tenant)
    label = "year" if q.cycle == BillingCycle.ANNUAL else "month"
    inv = Invoice(tenant_id=tenant.id, number=_number(tenant), subscription_id=sub.id if sub else None,
                  plan_id=q.plan.id, billing_cycle=q.cycle, kind=q.kind,
                  description=f"{q.plan.name} plan, one {label}" + (" (upgrade)" if q.kind == InvoiceKind.UPGRADE else ""),
                  price_minor=q.price_minor, credit_minor=q.credit_minor, amount_minor=q.amount_minor,
                  currency=q.plan.currency, billing_email=billing_email, automatic=automatic,
                  created_by_id=getattr(actor, "id", None))
    db.session.add(inv)
    db.session.flush()
    audit.record("billing.invoice_created", tenant_id=tenant.id, actor=actor, entity_type="invoice", entity_id=inv.id,
                 summary=f"{inv.number}: {inv.description}, {money(inv.amount_minor, inv.currency)}")
    return inv


def checkout(tenant: Tenant, q: Quote, *, billing_email: str, callback_url: str, cancel_url: str,
             actor) -> tuple[Invoice, str]:
    """Create the invoice and a Paystack checkout. Returns the invoice and the page to send the person to."""
    if not paystack.enabled():
        raise BillingError("Online payment isn't set up for this site yet. Please contact PORTIA support.")
    email = (billing_email or "").strip().lower()
    if "@" not in email or len(email) > 255:
        raise BillingError("Enter the email address for receipts.")
    tenant.billing_email = email
    # Earlier unpaid checkouts are replaced, so only one invoice waits for payment.
    for old in db.session.scalars(select(Invoice).where(Invoice.tenant_id == tenant.id,
                                                       Invoice.status == InvoiceStatus.OPEN,
                                                       Invoice.automatic.is_(False))):
        old.status = InvoiceStatus.VOID
        old.failure_reason = "Replaced by a newer checkout"
    inv = create_invoice(tenant, q, billing_email=email, actor=actor)
    data = paystack.client().initialize(
        email=email, amount=inv.amount_minor, currency=inv.currency, reference=inv.reference,
        callback_url=callback_url,
        metadata={"workspace": tenant.public_id, "invoice": inv.number, "cancel_action": cancel_url})
    url = data.get("authorization_url") or ""
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")) and paystack.backend() == "live":
        raise BillingError("Paystack didn't return a checkout page. Please try again.")
    return inv, url


def find_invoice(reference: str) -> Invoice | None:
    """Look up an invoice by our reference across workspaces (call in platform scope for webhooks)."""
    stmt = select(Invoice).where(Invoice.reference == reference)
    if db.engine.dialect.name == "postgresql":
        stmt = stmt.with_for_update(of=Invoice)  # lock just the invoice row (the plan is joined in)
    return db.session.scalar(stmt)


def confirm(reference: str, *, data: dict | None = None, actor=None) -> Invoice:
    """Verify a payment with Paystack and apply it. Safe to call any number of times."""
    inv = find_invoice(reference)
    if inv is None:
        raise BillingError("We couldn't find that payment.")
    if inv.status == InvoiceStatus.PAID:
        return inv
    if data is None or paystack.backend() == "live":
        data = paystack.client().verify(reference)  # never trust a browser or webhook body on its own
    status = (data.get("status") or "").lower()
    if status != "success":
        if status in ("failed", "reversed"):
            inv.status = InvoiceStatus.FAILED if inv.status == InvoiceStatus.OPEN else inv.status
            inv.failure_reason = (data.get("gateway_response") or "The payment didn't go through.")[:255]
        return inv
    if int(data.get("amount") or 0) != inv.amount_minor or (data.get("currency") or inv.currency) != inv.currency:
        inv.status = InvoiceStatus.FAILED
        inv.failure_reason = "The amount paid didn't match the invoice. PORTIA support will contact you."
        audit.record("billing.amount_mismatch", tenant_id=inv.tenant_id, entity_type="invoice", entity_id=inv.id,
                     summary=f"{inv.number}: expected {inv.amount_minor}, Paystack reported {data.get('amount')}")
        current_app.logger.error("Paystack amount mismatch for %s", inv.reference)
        return inv
    _mark_paid(inv, data, actor=actor)
    return inv


def _mark_paid(inv: Invoice, data: dict, *, actor=None, provider: str = "paystack",
               now: datetime | None = None) -> None:
    now = now or utcnow()
    tenant = db.session.get(Tenant, inv.tenant_id)
    auth = data.get("authorization") or {}
    inv.status = InvoiceStatus.PAID
    inv.provider = provider
    inv.paid_at = now
    inv.channel = (data.get("channel") or auth.get("channel") or "")[:20] or None
    inv.provider_txn_id = str(data.get("id") or "")[:40] or None
    brand = (auth.get("brand") or auth.get("card_type") or auth.get("bank") or "").strip()
    detail = f"{brand.title()} •••• {auth['last4']}" if auth.get("last4") else (brand.upper() if brand else None)
    inv.channel_detail = detail[:80] if detail else None
    inv.failure_reason = None
    sub = _apply(tenant, inv, actor=actor, now=now)
    inv.subscription_id = sub.id
    inv.period_start, inv.period_end = sub.current_period_start, sub.current_period_end
    if auth.get("reusable") and auth.get("authorization_code"):
        save_method(tenant, auth, email=inv.billing_email or (data.get("customer") or {}).get("email") or "", actor=actor)
    audit.record("billing.paid", tenant_id=tenant.id, actor=actor, entity_type="invoice", entity_id=inv.id,
                 summary=f"{inv.number} paid: {money(inv.amount_minor, inv.currency)} by "
                         f"{inv.channel_detail or inv.channel_label or provider}",
                 details={"reference": inv.reference, "provider_txn": inv.provider_txn_id})
    notes.notify_permission(tenant.id, "billing.manage", category="billing",
                            title=f"Payment received: {money(inv.amount_minor, inv.currency)}",
                            body=f"{inv.description}. Paid until {inv.period_end:%d %b %Y}.",
                            link=f"/app/billing/invoices/{inv.public_id}", email=False)
    _send(tenant, inv.billing_email, f"Receipt {inv.number}: {money(inv.amount_minor, inv.currency)} paid",
          f"Thank you. We received {money(inv.amount_minor, inv.currency)} for {tenant.name}.\n\n"
          f"{inv.description}\nPaid period: {inv.period_start:%d %b %Y} to {inv.period_end:%d %b %Y}\n"
          f"Receipt number: {inv.number}\n\nYou can download the receipt from Subscription → Invoices.")


def _apply(tenant: Tenant, inv: Invoice, *, actor=None, now: datetime | None = None) -> Subscription:
    sub = live_subscription(tenant)
    plan = inv.plan
    if sub is None:
        sub = subs.start_subscription(tenant, plan, inv.billing_cycle, now=now, with_trial=False, actor=actor)
        db.session.flush()
        return subs.activate(sub, now=now, cycle=inv.billing_cycle, actor=actor, reference=inv.reference)
    if sub.status == SubscriptionStatus.SUSPENDED:
        raise BillingError("This workspace is suspended. The payment is recorded; support will help restore it.")
    sub.renewal_attempts = 0
    sub.scheduled_plan_id = sub.scheduled_cycle = None
    sub.cancel_at_period_end = False
    sub.auto_renew = True
    if inv.kind == InvoiceKind.RENEWAL and sub.plan_id == plan.id and sub.billing_cycle == inv.billing_cycle \
            and sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
        return subs.renew(sub, now=now, actor=actor, reference=inv.reference)
    if sub.plan_id != plan.id:
        subs.change_plan(sub, plan, actor=actor)
    return subs.activate(sub, now=now, cycle=inv.billing_cycle, actor=actor, reference=inv.reference)


# ---- Saved payment methods ----------------------------------------------------------------------

def save_method(tenant: Tenant, auth: dict, *, email: str, actor=None) -> PaymentMethod:
    existing = db.session.scalars(select(PaymentMethod).where(PaymentMethod.tenant_id == tenant.id,
                                                              PaymentMethod.is_active.is_(True))).all()
    for pm in existing:  # one saved method per workspace: the newest replaces the others
        if pm.authorization_code != auth["authorization_code"]:
            pm.is_active = False
    pm = next((p for p in existing if p.authorization_code == auth["authorization_code"]), None)
    if pm is None:
        pm = PaymentMethod(tenant_id=tenant.id, authorization_code=auth["authorization_code"][:80],
                           created_by_id=getattr(actor, "id", None))
        db.session.add(pm)
    pm.is_active = True
    pm.signature = (auth.get("signature") or "")[:80] or None
    pm.channel = (auth.get("channel") or "card")[:20]
    pm.brand = (auth.get("brand") or auth.get("card_type") or "")[:40] or None
    pm.last4 = (auth.get("last4") or "")[:4] or None
    pm.exp_month = (str(auth.get("exp_month") or ""))[:2] or None
    pm.exp_year = (str(auth.get("exp_year") or ""))[:4] or None
    pm.bank = (auth.get("bank") or "")[:80] or None
    pm.email = (email or "")[:255]
    db.session.flush()
    return pm


def saved_method(tenant: Tenant) -> PaymentMethod | None:
    return db.session.scalar(select(PaymentMethod).where(PaymentMethod.tenant_id == tenant.id,
                                                         PaymentMethod.is_active.is_(True))
                             .order_by(PaymentMethod.id.desc()))


def remove_method(pm: PaymentMethod, *, actor) -> None:
    pm.is_active = False
    audit.record("billing.card_removed", tenant_id=pm.tenant_id, actor=actor, entity_type="payment_method",
                 entity_id=pm.id, summary=f"Removed saved {pm.label}; renewals will need paying by hand")


# ---- Payments recorded by PORTIA staff (bank transfer, cash, cheque) -------------------------------

def record_manual_payment(tenant: Tenant, plan: SubscriptionPlan, cycle: str, *, amount_minor: int,
                          method: str, reference: str, actor) -> Invoice:
    if amount_minor <= 0:
        raise BillingError("Enter the amount received.")
    if not reference:
        raise BillingError("Enter the payment reference (receipt, transfer or MoMo transaction number).")
    sub = live_subscription(tenant)
    same = sub is not None and sub.plan_id == plan.id and sub.billing_cycle == cycle \
        and sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)
    q = Quote(plan, cycle, InvoiceKind.RENEWAL if same else InvoiceKind.NEW,
              plan.price_minor(cycle) if not plan.is_custom_pricing else amount_minor, amount_minor=amount_minor)
    q.price_minor = max(q.price_minor, amount_minor)
    q.credit_minor = q.price_minor - amount_minor
    inv = create_invoice(tenant, q, billing_email=tenant.billing_email, actor=actor)
    _mark_paid(inv, {"channel": "manual", "id": reference[:40], "authorization": {"brand": method}},
               actor=actor, provider="manual")
    inv.channel_detail = f"{method} · ref {reference}"[:80]
    return inv


# ---- Daily job: reminders, automatic renewals and failed payments ----------------------------------

def _send(tenant: Tenant, to: str | None, subject: str, body: str) -> None:
    recipients = [to] if to else [m.user.email for m in tenant.memberships if m.is_owner and m.user.is_active]
    for r in dict.fromkeys(x for x in recipients if x):
        try:
            mailer.send(r, subject, body + "\n\nPORTIA SkillBridge")
        except Exception:  # noqa: BLE001 — a mail outage must not stop billing
            current_app.logger.exception("Could not send billing email to %s", r)


def renewal_plan(sub: Subscription) -> tuple[SubscriptionPlan, str]:
    return (sub.scheduled_plan or sub.plan), (sub.scheduled_cycle or sub.billing_cycle)


def charge_renewals(now: datetime | None = None) -> dict:
    """Charge saved cards for periods that have ended (platform scope). Retries once a day while payment is due."""
    now = now or utcnow()
    counts = {"charged": 0, "failed": 0, "switched": 0}
    due = db.session.scalars(select(Subscription).where(
        Subscription.status.in_((SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)),
        Subscription.current_period_end <= now, Subscription.auto_renew.is_(True),
        Subscription.cancel_at_period_end.is_(False))).all()
    for sub in due:
        if sub.status == SubscriptionStatus.PAST_DUE and now >= sub.grace_ends_at():
            continue
        tenant = sub.tenant
        plan, cycle = renewal_plan(sub)
        if sub.scheduled_plan_id and over_limits(tenant, plan):
            sub.scheduled_plan_id = sub.scheduled_cycle = None  # can't move down; renew as is and say so
            plan, cycle = sub.plan, sub.billing_cycle
            _send(tenant, tenant.billing_email, "Your plan change didn't happen",
                  f"{tenant.name} uses more than the plan you chose allows, so it stays on {plan.name}.")
        if plan.is_free:
            sub.plan, sub.billing_cycle = plan, cycle
            sub.scheduled_plan_id = sub.scheduled_cycle = None
            subs.renew(sub, now=now)
            counts["switched"] += 1
            continue
        if plan.is_custom_pricing or sub.plan.is_free:
            continue
        pm = saved_method(tenant)
        if pm is None:
            continue
        if sub.renewal_attempts >= MAX_RENEWAL_ATTEMPTS or (
                sub.last_renewal_attempt_at and now - sub.last_renewal_attempt_at < timedelta(hours=20)):
            continue
        q = Quote(plan, cycle, InvoiceKind.RENEWAL if plan.id == sub.plan_id and cycle == sub.billing_cycle
                  else InvoiceKind.NEW, plan.price_minor(cycle), amount_minor=plan.price_minor(cycle))
        inv = create_invoice(tenant, q, billing_email=pm.email or tenant.billing_email, automatic=True)
        sub.renewal_attempts += 1
        sub.last_renewal_attempt_at = now
        try:
            data = paystack.client().charge_authorization(
                email=pm.email, amount=inv.amount_minor, currency=inv.currency,
                authorization_code=pm.authorization_code, reference=inv.reference,
                metadata={"workspace": tenant.public_id, "invoice": inv.number, "automatic": True})
        except paystack.PaystackError as exc:
            data = {"status": "failed", "gateway_response": str(exc)}
        status = (data.get("status") or "").lower()
        if status == "success" and int(data.get("amount") or 0) == inv.amount_minor:
            _mark_paid(inv, data, now=now)
            counts["charged"] += 1
        elif status in ("pending", "processing", "ongoing", "queued"):
            counts["pending"] = counts.get("pending", 0) + 1  # the webhook confirms it when Paystack finishes
        else:
            inv.status = InvoiceStatus.FAILED
            inv.failure_reason = (data.get("gateway_response") or "The card was declined.")[:255]
            counts["failed"] += 1
            left = MAX_RENEWAL_ATTEMPTS - sub.renewal_attempts
            audit.record("billing.renewal_failed", tenant_id=tenant.id, entity_type="invoice", entity_id=inv.id,
                         summary=f"{inv.number}: automatic renewal failed ({inv.failure_reason})")
            notes.notify_permission(tenant.id, "billing.manage", category="billing",
                                    title="Automatic renewal failed",
                                    body=f"{pm.label} was declined ({inv.failure_reason}). Pay under Subscription "
                                         f"before {sub.grace_ends_at():%d %b %Y} to avoid any interruption.",
                                    link="/app/subscription", email=False)
            _send(tenant, pm.email or tenant.billing_email, f"Payment failed for {tenant.name}",
                  f"We couldn't take {money(inv.amount_minor, inv.currency)} from {pm.label} for your {plan.name} "
                  f"plan ({inv.failure_reason}).\n\n"
                  + (f"We'll try again tomorrow ({left} more tr{'y' if left == 1 else 'ies'}). " if left else "")
                  + f"To avoid any interruption, pay now under Subscription. Your workspace keeps working until "
                    f"{sub.grace_ends_at():%d %b %Y}.")
    return counts


def send_reminders(now: datetime | None = None) -> int:
    """Email workspaces whose trial or unpaid period ends within REMINDER_DAYS. One reminder per period."""
    now = now or utcnow()
    sent = 0
    soon = db.session.scalars(select(Subscription).where(
        Subscription.status.in_((SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE)),
        Subscription.current_period_end > now,
        Subscription.current_period_end <= now + timedelta(days=REMINDER_DAYS))).all()
    for sub in soon:
        if sub.reminder_sent_for == sub.current_period_end or sub.plan.is_free:
            continue
        tenant = sub.tenant
        if sub.status == SubscriptionStatus.ACTIVE and (saved_method(tenant) and sub.auto_renew
                                                        and not sub.cancel_at_period_end):
            continue  # it will renew by itself
        if sub.status == SubscriptionStatus.ACTIVE and sub.cancel_at_period_end:
            continue  # they chose to stop
        plan, cycle = renewal_plan(sub)
        what = "free trial" if sub.is_trial else f"{sub.plan.name} plan"
        _send(tenant, tenant.billing_email, f"Your {what} ends on {sub.current_period_end:%d %b %Y}",
              f"The {what} for {tenant.name} ends on {sub.current_period_end:%d %b %Y}.\n\n"
              f"To keep using PORTIA SkillBridge, open Subscription and pay "
              f"{money(plan.price_minor(cycle), plan.currency)} with Mobile Money or a card.")
        notes.notify_permission(tenant.id, "billing.manage", category="billing",
                                title=f"Your {what} ends on {sub.current_period_end:%d %b %Y}",
                                body=f"Pay {money(plan.price_minor(cycle), plan.currency)} under Subscription to keep "
                                     "your workspace running.", link="/app/subscription", email=False,
                                dedupe_key=f"period-end:{sub.id}:{sub.current_period_end:%Y%m%d}")
        sub.reminder_sent_for = sub.current_period_end
        sent += 1
    return sent


def run_daily(now: datetime | None = None) -> dict:
    """Everything the daily billing job does, in order (platform scope)."""
    now = now or utcnow()
    counts = {"reminders": send_reminders(now)}
    counts.update(charge_renewals(now))
    counts.update(subs.process_due(now))
    return counts
