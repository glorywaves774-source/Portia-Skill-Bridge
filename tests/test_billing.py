"""Stage 9: Paystack payments, invoices, plan changes, saved cards, automatic renewals and reminders."""
import json
from datetime import timedelta
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Invoice, PaymentMethod, Role, Tenant
from app.models.base import utcnow
from app.models.subscription import Subscription, SubscriptionPlan
from app.services import billing, mailer, paystack
from app.services import subscriptions as subs
from app.tenancy import platform_scope, tenant_context
from tests.conftest import sign_in

EMAIL = "accounts@attc.example"


@pytest.fixture()
def admin(app, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    c = app.test_client()
    sign_in(c, "esi@attc.example")
    mailer.outbox.clear()
    return c


def _fake(app):
    with app.app_context():
        return paystack.fake()


def _plan(code) -> SubscriptionPlan:
    with platform_scope():
        return db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code))


def _sub(tenant_id) -> Subscription:
    with platform_scope():
        db.session.expire_all()
        return db.session.get(Tenant, tenant_id).current_subscription


def _invoices(tenant_id):
    with platform_scope():
        db.session.expire_all()
        return db.session.scalars(select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.id)).all()


def _start(client, plan="org-professional", cycle="monthly", email=EMAIL):
    r = client.post("/app/billing/checkout", data={"plan": plan, "cycle": cycle, "billing_email": email})
    assert r.status_code == 303, r.data[:500]
    return urlparse(r.headers["Location"]).path  # the (practice) Paystack page


def _pay(client, plan="org-professional", cycle="monthly", outcome="card", email=EMAIL):
    page = _start(client, plan, cycle, email)
    r = client.post(page, data={"outcome": outcome})
    assert r.status_code == 302
    back = urlparse(r.headers["Location"])
    return client.get(back.path + "?" + back.query, follow_redirects=True)


def _webhook(client, fake, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post("/webhooks/paystack", data=body, content_type="application/json",
                       headers={"X-Paystack-Signature": signature or fake.sign(body)})


# ---- Paying ------------------------------------------------------------------------------------------

def test_paying_during_trial_activates_and_saves_the_card(app, admin, org):
    price = _plan("org-professional").monthly_price_minor
    r = _pay(admin)
    assert b"Payment received" in r.data
    sub = _sub(org.id)
    assert sub.status == "active" and sub.plan.code == "org-professional"
    assert sub.trial_ends_at <= utcnow() and 27 <= (sub.current_period_end - sub.current_period_start).days <= 31
    inv = _invoices(org.id)[0]
    assert (inv.status, inv.amount_minor, inv.kind, inv.channel_detail) == ("paid", price, "new", "Visa •••• 4081")
    assert inv.number.startswith("INV-") and inv.period_end == sub.current_period_end
    with tenant_context(org.id):
        pm = db.session.scalar(select(PaymentMethod))
        assert pm.label == "Visa •••• 4081" and pm.email == EMAIL
        assert db.session.get(Tenant, org.id).billing_email == EMAIL
    # Only the billing email, amount and non-personal ids went to Paystack.
    call = [c for c in _fake(app).calls if c[0] == "initialize"][-1][1]
    assert call["email"] == EMAIL and set(call["metadata"]) == {"workspace", "invoice", "cancel_action"}
    receipt = mailer.outbox[-1]
    assert receipt["To"] == EMAIL and "Receipt" in receipt["Subject"]


def test_declined_payment_changes_nothing(admin, org):
    r = _pay(admin, outcome="decline")
    assert b"didn" in r.data  # "The payment didn't go through"
    assert _sub(org.id).status == "trialing"
    assert _invoices(org.id)[0].status == "failed"


def test_mobile_money_is_not_saved_for_renewals(admin, org):
    _pay(admin, outcome="momo")
    inv = _invoices(org.id)[0]
    assert inv.status == "paid" and inv.channel == "mobile_money"
    with tenant_context(org.id):
        assert db.session.scalar(select(PaymentMethod)) is None


def test_checkout_needs_a_receipt_email(admin, org):
    r = admin.post("/app/billing/checkout", data={"plan": "org-professional", "cycle": "monthly",
                                                  "billing_email": "not-an-email"}, follow_redirects=True)
    assert b"email address for receipts" in r.data and _invoices(org.id) == []


def test_only_one_checkout_waits_at_a_time(admin, org):
    _start(admin)
    _start(admin, cycle="annual")
    statuses = [i.status for i in _invoices(org.id)]
    assert statuses == ["void", "open"]


# ---- Webhooks, verification and replay -----------------------------------------------------------------

def test_webhook_and_return_are_idempotent(app, admin, org):
    _pay(admin)
    first = _sub(org.id).current_period_end
    inv = _invoices(org.id)[0]
    fake = _fake(app)
    data = fake.verify(inv.reference)
    for _ in range(2):
        assert _webhook(admin, fake, {"event": "charge.success", "data": data}).status_code == 200
    admin.get(f"/app/billing/paystack/return?reference={inv.reference}")
    assert _sub(org.id).current_period_end == first
    assert [i.status for i in _invoices(org.id)] == ["paid"]
    with platform_scope():
        assert len(db.session.scalars(select(AuditLog).where(AuditLog.action == "billing.paid")).all()) == 1


def test_webhook_confirms_a_payment_when_the_person_never_returns(app, admin, org):
    page = _start(admin)
    ref = page.rsplit("/", 1)[-1]
    fake = _fake(app)
    data = fake.complete(ref)
    anon = app.test_client()
    assert _webhook(anon, fake, {"event": "charge.success", "data": data}, signature="0" * 128).status_code == 401
    assert _sub(org.id).status == "trialing"
    assert _webhook(anon, fake, {"event": "charge.success", "data": data}).status_code == 200
    assert _sub(org.id).status == "active" and _invoices(org.id)[0].status == "paid"
    other = {"event": "charge.success", "data": {**data, "reference": "SOMEONE-ELSE"}}
    assert _webhook(anon, fake, other).data == b"not ours"
    assert _webhook(anon, fake, {"event": "transfer.success", "data": {}}).data == b"ignored"


def test_wrong_amount_is_not_accepted(app, admin, org):
    page = _start(admin)
    ref = page.rsplit("/", 1)[-1]
    _fake(app).complete(ref, amount=100)  # someone tampered with the amount
    admin.get(f"/app/billing/paystack/return?reference={ref}")
    assert _sub(org.id).status == "trialing"
    inv = _invoices(org.id)[0]
    assert inv.status == "failed" and "didn't match" in inv.failure_reason
    with platform_scope():
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "billing.amount_mismatch"))


# ---- Plan changes ----------------------------------------------------------------------------------

def test_upgrade_takes_off_unused_time(app, admin, org):
    _pay(admin)  # Professional, monthly, just paid
    with tenant_context(org.id):
        t = db.session.get(Tenant, org.id)
        sub = t.current_subscription
        biz = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-business"))
        half = sub.current_period_start + (sub.current_period_end - sub.current_period_start) / 2
        q = billing.quote(t, biz, "monthly", now=half)
        assert q.kind == "upgrade"
        assert abs(q.credit_minor - sub.price_minor // 2) <= 100 and q.credit_minor % 100 == 0
        assert q.amount_minor == biz.monthly_price_minor - q.credit_minor
    _pay(admin, plan="org-business")
    inv = _invoices(org.id)[-1]
    assert inv.kind == "upgrade" and inv.credit_minor > 0 and inv.amount_minor < inv.price_minor
    assert _sub(org.id).plan.code == "org-business"


def test_downgrade_waits_for_the_period_end_then_charges_the_cheaper_plan(app, admin, org):
    _pay(admin, plan="org-business")
    r = admin.post("/app/billing/change", data={"plan": "org-starter", "cycle": "monthly"}, follow_redirects=True)
    assert b"Nothing to pay now" in r.data or b"current period ends" in r.data
    sub = _sub(org.id)
    assert sub.plan.code == "org-business" and sub.scheduled_plan.code == "org-starter"
    with platform_scope():
        counts = billing.run_daily(now=sub.current_period_end + timedelta(minutes=5))
        db.session.commit()
    assert counts["charged"] == 1
    sub = _sub(org.id)
    assert sub.plan.code == "org-starter" and sub.scheduled_plan_id is None and sub.status == "active"
    inv = _invoices(org.id)[-1]
    assert inv.automatic and inv.amount_minor == _plan("org-starter").monthly_price_minor


def test_downgrade_is_refused_when_the_workspace_is_too_big(app, admin, org, make_person):
    _pay(admin, plan="org-business")
    with platform_scope():
        starter = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-starter"))
        starter.max_staff = 0
        db.session.commit()
    r = admin.post("/app/billing/change", data={"plan": "org-starter", "cycle": "monthly"}, follow_redirects=True)
    assert b"uses more than the Starter plan allows" in r.data
    assert _sub(org.id).scheduled_plan_id is None


def test_keep_current_plan_cancels_a_scheduled_change(admin, org):
    _pay(admin, plan="org-business")
    admin.post("/app/billing/change", data={"plan": "org-starter", "cycle": "monthly"})
    admin.post("/app/billing/change/cancel")
    assert _sub(org.id).scheduled_plan_id is None


def test_free_and_custom_plans(app, make_tenant, make_person):
    t = make_tenant("Ama Owusu", kind="individual", plan="ind-plus")
    make_person("ama@example.com", [Role.LEARNER], t, owner=True)
    c = app.test_client()
    sign_in(c, "ama@example.com")
    c.post("/app/billing/change", data={"plan": "ind-free", "cycle": "monthly"})
    sub = _sub(t.id)
    assert sub.plan.code == "ind-free" and sub.status == "active"
    org = make_tenant("Big Org", plan="org-starter")
    make_person("boss@big.example", [Role.ORG_ADMIN], org, owner=True)
    c2 = app.test_client()
    sign_in(c2, "boss@big.example")
    assert b"sales team" in c2.get("/app/billing/checkout?plan=org-enterprise&cycle=monthly").data
    r = c2.post("/app/billing/checkout", data={"plan": "org-enterprise", "cycle": "monthly", "billing_email": EMAIL},
                follow_redirects=True)
    assert b"nothing to pay" in r.data and _invoices(org.id) == []


# ---- Automatic renewals, failed payments, reminders -----------------------------------------------------

def test_failed_renewals_retry_daily_then_the_workspace_lapses(app, admin, org):
    _pay(admin)
    fake = _fake(app)
    end = _sub(org.id).current_period_end
    mailer.outbox.clear()
    for day in range(3):
        fake.fail_next_charge = "Insufficient funds"
        with platform_scope():
            counts = billing.run_daily(now=end + timedelta(days=day, hours=1))
            db.session.commit()
        assert counts["failed"] == 1
    assert _sub(org.id).status == "past_due"
    assert "Insufficient funds" in mailer.outbox[-1].get_content() and "Payment failed" in mailer.outbox[-1]["Subject"]
    with platform_scope():
        assert billing.run_daily(now=end + timedelta(days=3, hours=1))["failed"] == 0  # three tries only
        billing.run_daily(now=end + timedelta(days=8))
        db.session.commit()
    assert _sub(org.id).status == "expired"
    assert [i.status for i in _invoices(org.id)].count("failed") == 3


def test_renewal_retry_succeeds_and_restores_service(app, admin, org):
    _pay(admin)
    fake = _fake(app)
    end = _sub(org.id).current_period_end
    fake.fail_next_charge = "Do not honour"
    with platform_scope():
        billing.run_daily(now=end + timedelta(hours=1))
        db.session.commit()
        assert billing.run_daily(now=end + timedelta(hours=5))["failed"] == 0  # not twice in a day
        counts = billing.run_daily(now=end + timedelta(days=1, hours=1))
        db.session.commit()
    assert counts["charged"] == 1
    sub = _sub(org.id)
    assert sub.status == "active" and sub.renewal_attempts == 0 and sub.current_period_end > end


def test_turning_off_auto_renew_and_removing_the_card(app, admin, org):
    _pay(admin)
    admin.post("/app/billing/auto-renew", data={"on": "0"})
    sub = _sub(org.id)
    assert not sub.auto_renew and sub.cancel_at_period_end
    with platform_scope():
        billing.run_daily(now=sub.current_period_end + timedelta(hours=1))
        db.session.commit()
    assert _sub(org.id).status == "cancelled"
    with tenant_context(org.id):
        pm = db.session.scalar(select(PaymentMethod))
    assert admin.post(f"/app/billing/card/{pm.public_id}/remove").status_code == 302
    with tenant_context(org.id):
        assert db.session.scalar(select(PaymentMethod).where(PaymentMethod.is_active.is_(True))) is None


def test_reminders_are_sent_once_before_a_trial_ends(app, org, make_person):
    make_person("owner@attc.example", [Role.ORG_ADMIN], org, owner=True)
    mailer.outbox.clear()
    end = _sub(org.id).current_period_end
    with platform_scope():
        assert billing.send_reminders(now=end - timedelta(days=2)) == 1
        db.session.commit()
        assert billing.send_reminders(now=end - timedelta(days=1)) == 0
    assert mailer.outbox[-1]["To"] == "owner@attc.example" and "free trial ends" in mailer.outbox[-1]["Subject"]


# ---- Access, isolation and documents --------------------------------------------------------------------

def test_a_lapsed_workspace_can_pay_to_come_back(app, admin, org):
    with platform_scope():
        subs.process_due(now=_sub(org.id).current_period_end + timedelta(days=1))
        db.session.commit()
    assert _sub(org.id).status == "expired"
    assert admin.get("/app/learners/").status_code == 402
    assert admin.get("/app/billing/checkout?plan=org-starter&cycle=monthly").status_code == 200
    _pay(admin, plan="org-starter")
    assert _sub(org.id).status == "active" and _sub(org.id).plan.code == "org-starter"
    assert admin.get("/app/learners/").status_code == 200


@pytest.mark.parametrize("role,view,pay", [
    (Role.FINANCE_OFFICER, 200, 200), (Role.TRAINER, 403, 403), (Role.ME_OFFICER, 403, 403),
    (Role.LEARNER, 403, 403)])
def test_who_can_see_and_pay(app, org, make_person, role, view, pay):
    make_person("p@attc.example", [role], org)
    c = app.test_client()
    sign_in(c, "p@attc.example")
    assert c.get("/app/subscription").status_code == view
    assert c.get("/app/billing/invoices").status_code == view
    assert c.get("/app/billing/checkout?plan=org-professional&cycle=monthly").status_code == pay


def test_invoices_stay_inside_their_workspace(app, admin, org, make_tenant, make_person):
    _pay(admin)
    inv = _invoices(org.id)[0]
    other = make_tenant("Beta NGO", plan="org-starter")
    make_person("beta@beta.example", [Role.ORG_ADMIN], other, owner=True)
    c = app.test_client()
    sign_in(c, "beta@beta.example")
    assert c.get(f"/app/billing/invoices/{inv.public_id}").status_code == 404
    assert c.get(f"/app/billing/invoices/{inv.public_id}.pdf").status_code == 404
    r = c.get(f"/app/billing/paystack/return?reference={inv.reference}", follow_redirects=True)
    assert b"couldn" in r.data and _sub(other.id).status == "trialing"


def test_receipt_pdf_and_invoice_pages(admin, org):
    _pay(admin)
    inv = _invoices(org.id)[0]
    page = admin.get(f"/app/billing/invoices/{inv.public_id}")
    assert page.status_code == 200 and inv.number.encode() in page.data and b"Visa" in page.data
    pdf = admin.get(f"/app/billing/invoices/{inv.public_id}.pdf")
    assert pdf.data[:4] == b"%PDF" and f"receipt-{inv.number}.pdf" in pdf.headers["Content-Disposition"]
    assert pdf.headers["Cache-Control"] == "private, no-store"
    listing = admin.get("/app/billing/invoices")
    assert inv.number.encode() in listing.data
    sub_page = admin.get("/app/subscription")
    assert b"Visa" in sub_page.data and b"Recent payments" in sub_page.data


def test_super_admin_records_a_bank_transfer(app, org, make_person):
    make_person("boss@kayron.example", platform_admin=True)
    c = app.test_client()
    sign_in(c, "boss@kayron.example")
    with platform_scope():
        code = db.session.get(Tenant, org.id).code
    r = c.post(f"/admin/tenants/{code}/payments", data={"pay-plan": "org-professional", "pay-billing_cycle": "annual",
                                                        "pay-amount": "3000.00", "pay-method": "Bank transfer",
                                                        "pay-reference": "GCB-778812"}, follow_redirects=True)
    assert r.status_code == 200 and b"Payment recorded" in r.data
    inv = _invoices(org.id)[0]
    assert (inv.provider, inv.status, inv.amount_minor) == ("manual", "paid", 300000)
    assert "GCB-778812" in inv.channel_detail
    sub = _sub(org.id)
    assert sub.status == "active" and sub.billing_cycle == "annual"
    assert c.get("/admin/billing").status_code == 200


def test_online_payment_is_off_in_production_without_keys(app, admin, org):
    app.config.update(ENV_NAME="production", PAYSTACK_BACKEND="")
    try:
        assert not paystack.enabled()
        page = admin.get("/app/billing/checkout?plan=org-professional&cycle=monthly")
        assert b"switched on" in page.data
        r = admin.post("/app/billing/checkout", data={"plan": "org-professional", "cycle": "monthly",
                                                      "billing_email": EMAIL}, follow_redirects=True)
        assert b"set up" in r.data and _invoices(org.id) == []
        assert admin.get("/dev/paystack/PSB-ANY").status_code == 404
    finally:
        app.config.update(ENV_NAME="testing", PAYSTACK_BACKEND="fake")


def test_security_policy_allows_only_paystack_checkout(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "form-action 'self' https://checkout.paystack.com" in csp and "script-src 'self';" in csp


def test_live_client_talks_to_paystack(app, monkeypatch):
    seen = {}

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, auth=req.headers["Authorization"], body=json.loads(req.data or b"{}"))
        return Resp(json.dumps({"status": True, "data": {"authorization_url": "https://checkout.paystack.com/x",
                                                         "reference": "R1"}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with app.app_context():
        c = paystack.LiveClient("sk_test_abc")
        out = c.initialize(email=EMAIL, amount=30000, currency="GHS", reference="R1",
                           callback_url="https://x/return", metadata={"invoice": "INV-1"})
    assert out["authorization_url"].startswith("https://checkout.paystack.com")
    assert seen["url"] == "https://api.paystack.co/transaction/initialize" and seen["auth"] == "Bearer sk_test_abc"
    assert seen["body"]["amount"] == 30000 and seen["body"]["currency"] == "GHS"

    def refusing(req, timeout):
        return Resp(json.dumps({"status": False, "message": "Invalid key"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", refusing)
    with app.app_context(), pytest.raises(paystack.PaystackError, match="Invalid key"):
        paystack.LiveClient("sk_test_abc").verify("R1")


def test_webhook_signature_uses_the_secret_key(app):
    with app.app_context():
        app.config["PAYSTACK_SECRET_KEY"] = "sk_test_secret"
        app.config["PAYSTACK_BACKEND"] = "live"
        try:
            import hashlib
            import hmac
            body = b'{"event":"charge.success"}'
            good = hmac.new(b"sk_test_secret", body, hashlib.sha512).hexdigest()
            assert paystack.signature_ok(body, good)
            assert not paystack.signature_ok(body, good[:-1] + "0")
            assert not paystack.signature_ok(body, None)
        finally:
            app.config["PAYSTACK_SECRET_KEY"] = None
            app.config["PAYSTACK_BACKEND"] = "fake"

