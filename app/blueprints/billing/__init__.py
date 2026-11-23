"""Paying for the subscription (/app/billing), the Paystack webhook (/webhooks/paystack) and, outside production,
a practice checkout page that stands in for Paystack (/dev/paystack/<reference>)."""
from __future__ import annotations

import json

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import select

from app.access import can, load_workspace, permission_required
from app.extensions import csrf, db, limiter
from app.models import Invoice, InvoiceStatus
from app.models.subscription import BillingCycle, SubscriptionPlan
from app.services import billing, invoices_pdf, paystack
from app.tenancy import platform_scope

bp = Blueprint("billing", __name__)
webhooks_bp = Blueprint("webhooks", __name__)
devpay_bp = Blueprint("devpay", __name__)


def _guard():
    return load_workspace(required=True)


bp.before_request(login_required(_guard))


def _plan(code: str | None) -> SubscriptionPlan:
    plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == (code or "")))
    if plan is None or plan.audience != g.workspace.kind or not plan.is_active:
        abort(404)
    return plan


def _cycle(value: str | None) -> str:
    return value if value in BillingCycle.ALL else BillingCycle.MONTHLY


# ---- Checkout ------------------------------------------------------------------------------------

@bp.get("/checkout")
@permission_required("billing.manage")
def checkout():
    plan, cycle = _plan(request.args.get("plan")), _cycle(request.args.get("cycle"))
    try:
        q = billing.quote(g.workspace, plan, cycle)
    except billing.BillingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("workspace.subscription"))
    sub = billing.live_subscription(g.workspace)
    return render_template("billing/checkout.html", q=q, plan=plan, cycle=cycle, sub=sub,
                           email=g.workspace.billing_email or "", online=paystack.enabled(),
                           test_mode=paystack.is_test_mode(), method=billing.saved_method(g.workspace))


@bp.post("/checkout")
@permission_required("billing.manage")
@limiter.limit("10 per minute")
def start():
    plan, cycle = _plan(request.form.get("plan")), _cycle(request.form.get("cycle"))
    back = url_for("billing.checkout", plan=plan.code, cycle=cycle)
    try:
        q = billing.quote(g.workspace, plan, cycle)
        if not q.payable:
            raise billing.BillingError("There's nothing to pay for this change.")
        inv, url = billing.checkout(g.workspace, q, billing_email=request.form.get("billing_email", ""),
                                    callback_url=url_for("billing.paystack_return", _external=True),
                                    cancel_url=url_for("workspace.subscription", _external=True), actor=current_user)
        db.session.commit()
    except (billing.BillingError, paystack.PaystackError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(back)
    return redirect(url, code=303)


@bp.get("/paystack/return")
@permission_required("billing.view")
def paystack_return():
    """Paystack sends the person back here. The payment is checked with Paystack before anything changes."""
    reference = (request.args.get("reference") or request.args.get("trxref") or "").strip()
    inv = db.session.scalar(select(Invoice).where(Invoice.reference == reference)) if reference else None
    if inv is None:
        flash("We couldn't find that payment. If money left your account, contact PORTIA support with the "
              "Paystack receipt.", "error")
        return redirect(url_for("workspace.subscription"))
    try:
        inv = billing.confirm(reference, actor=current_user)
        db.session.commit()
    except (billing.BillingError, paystack.PaystackError) as exc:
        db.session.rollback()
        flash(f"{exc} If your payment went through, it will show here shortly.", "error")
        return redirect(url_for("billing.invoice", public_id=inv.public_id))
    if inv.is_paid:
        flash(f"Payment received. Thank you! Your plan runs until {inv.period_end:%d %b %Y}.", "success")
    elif inv.status == InvoiceStatus.FAILED:
        flash(f"The payment didn't go through: {inv.failure_reason} You can try again.", "error")
    else:
        flash("We're waiting for Paystack to confirm the payment. This page updates when it does.", "info")
    return redirect(url_for("billing.invoice", public_id=inv.public_id))


# ---- Changes without payment -----------------------------------------------------------------------

@bp.post("/change")
@permission_required("billing.manage")
def change():
    plan, cycle = _plan(request.form.get("plan")), _cycle(request.form.get("cycle"))
    try:
        q = billing.quote(g.workspace, plan, cycle)
        billing.schedule_change(g.workspace, q, actor=current_user)
        db.session.commit()
        flash(q.message or f"Plan changed to {plan.name}.", "success")
    except billing.BillingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("billing.checkout", plan=plan.code, cycle=cycle))
    return redirect(url_for("workspace.subscription"))


@bp.post("/change/cancel")
@permission_required("billing.manage")
def cancel_change():
    sub = billing.live_subscription(g.workspace) or abort(404)
    try:
        billing.cancel_scheduled_change(sub, actor=current_user)
        db.session.commit()
        flash(f"You'll stay on {sub.plan.name}.", "success")
    except billing.BillingError as exc:
        flash(str(exc), "error")
    return redirect(url_for("workspace.subscription"))


@bp.post("/auto-renew")
@permission_required("billing.manage")
def auto_renew():
    sub = billing.live_subscription(g.workspace) or abort(404)
    on = request.form.get("on") == "1"
    billing.set_auto_renew(sub, on, actor=current_user)
    db.session.commit()
    flash("Automatic renewal is on." if on else
          f"Automatic renewal is off. Your plan ends on {sub.current_period_end:%d %b %Y} unless you pay again.",
          "success")
    return redirect(url_for("workspace.subscription"))


@bp.post("/card/<public_id>/remove")
@permission_required("billing.manage")
def remove_card(public_id):
    pm = billing.saved_method(g.workspace)
    if pm is None or pm.public_id != public_id:
        abort(404)
    billing.remove_method(pm, actor=current_user)
    db.session.commit()
    flash("Card removed. Renewals will need paying by hand; we'll email a reminder before each one.", "success")
    return redirect(url_for("workspace.subscription"))


# ---- Invoices and receipts -------------------------------------------------------------------------

@bp.get("/invoices")
@permission_required("billing.view")
def invoices():
    rows = db.session.scalars(select(Invoice).where(Invoice.status != InvoiceStatus.VOID)
                              .order_by(Invoice.created_at.desc())).all()
    return render_template("billing/invoices.html", invoices=rows)


def _invoice(public_id: str) -> Invoice:
    return db.session.scalar(select(Invoice).where(Invoice.public_id == public_id)) or abort(404)


@bp.get("/invoices/<public_id>")
@permission_required("billing.view")
def invoice(public_id):
    inv = _invoice(public_id)
    return render_template("billing/invoice.html", inv=inv, can_pay=can("billing.manage"))


@bp.get("/invoices/<public_id>.pdf")
@permission_required("billing.view")
def invoice_pdf(public_id):
    inv = _invoice(public_id)
    resp = Response(invoices_pdf.render(inv, g.workspace), mimetype="application/pdf")
    kind = "receipt" if inv.is_paid else "invoice"
    resp.headers["Content-Disposition"] = f'attachment; filename="{kind}-{inv.number}.pdf"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


# ---- Paystack webhook --------------------------------------------------------------------------------

@webhooks_bp.post("/paystack")
@csrf.exempt
@limiter.limit("300 per minute")
def paystack_webhook():
    raw = request.get_data()
    if not paystack.signature_ok(raw, request.headers.get("X-Paystack-Signature")):
        current_app.logger.warning("Paystack webhook with a bad signature from %s", request.remote_addr)
        return Response("bad signature", status=401)
    try:
        event = json.loads(raw.decode() or "{}")
    except ValueError:
        return Response("bad body", status=400)
    data = event.get("data") or {}
    if event.get("event") != "charge.success" or not data.get("reference"):
        return Response("ignored", status=200)
    with platform_scope():  # a webhook isn't tied to a signed-in workspace; the invoice says which one
        if billing.find_invoice(data["reference"]) is None:
            return Response("not ours", status=200)
        try:
            billing.confirm(data["reference"], data=data)
            db.session.commit()
        except (billing.BillingError, paystack.PaystackError):
            db.session.rollback()
            current_app.logger.exception("Paystack webhook for %s could not be applied", data["reference"])
            return Response("retry", status=500)  # Paystack tries again later
    return Response("ok", status=200)


# ---- Practice checkout (fake backend only) ------------------------------------------------------------

@devpay_bp.before_request
def _only_fake():
    if paystack.backend() != "fake":
        abort(404)


@devpay_bp.route("/<reference>", methods=["GET", "POST"])
def practice(reference):
    fake = paystack.fake()
    t = fake.transactions.get(reference) or abort(404)
    if request.method == "POST":
        outcome = request.form.get("outcome", "success")
        if outcome == "cancel":
            return redirect(t["metadata"].get("cancel_action") or "/")
        fake.complete(reference, outcome="success" if outcome in ("card", "momo") else "failed",
                      channel="mobile_money" if outcome == "momo" else "card")
        return redirect(f"{t['callback_url']}?trxref={reference}&reference={reference}")
    return render_template("billing/dev_checkout.html", t=t)
