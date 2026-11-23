"""Signing in and out, sign-up, password reset, lockout and session security."""
import re

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Membership, Role, Tenant, User
from app.services import mailer
from app.tenancy import platform_scope
from tests.conftest import PASSWORD, sign_in


@pytest.fixture(autouse=True)
def _clear_outbox():
    mailer.outbox.clear()


def test_sign_in_and_land_on_workspace(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    r = sign_in(client, "ESI@attc.example ")
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/")
    assert client.get("/app/").status_code == 200


def test_wrong_password_gives_generic_error(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    r1 = sign_in(client, "esi@attc.example", "wrong-password")
    r2 = sign_in(client, "nobody@attc.example", "wrong-password")
    assert r1.status_code == r2.status_code == 401
    assert b"don&#39;t match an account" in r1.data and b"don&#39;t match an account" in r2.data


def test_account_locks_after_repeated_failures(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    for _ in range(5):
        sign_in(client, "esi@attc.example", "wrong-password")
    r = sign_in(client, "esi@attc.example")  # right password, but locked
    assert r.status_code == 401 and b"Too many failed attempts" in r.data
    with platform_scope():
        actions = [a.action for a in AuditLog.query.order_by(AuditLog.id)]
    assert actions.count("auth.login_failed") == 5 and "auth.login_locked" in actions


def test_sign_in_is_rate_limited_per_ip(client, org):
    codes = [sign_in(client, "x@y.example", "bad").status_code for _ in range(11)]
    assert codes[-1] == 429


def test_deactivated_account_cannot_sign_in(client, org, make_person):
    u = make_person("gone@attc.example", [Role.TRAINER], org)
    u.is_active_account = False
    db.session.commit()
    assert b"deactivated" in sign_in(client, "gone@attc.example").data


@pytest.mark.parametrize("target, expected", [
    ("/app/people", "/app/people"),
    ("https://evil.example/phish", "/app/"),
    ("//evil.example", "/app/"),
    ("/\\evil.example", "/app/"),
    ("javascript:alert(1)", "/app/"),
])
def test_next_parameter_cannot_redirect_off_site(client, org, make_person, target, expected):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    r = sign_in(client, "esi@attc.example", next=target)
    assert r.headers["Location"].endswith(expected)
    assert "evil" not in r.headers["Location"]


def test_session_is_renewed_on_sign_in(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    with client.session_transaction() as s:
        s["planted"] = "attacker-value"
    sign_in(client, "esi@attc.example")
    with client.session_transaction() as s:
        assert "planted" not in s


def test_sign_out_requires_post_and_ends_session(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    sign_in(client, "esi@attc.example")
    assert client.get("/signout").status_code == 405
    assert client.post("/signout").status_code == 302
    assert client.get("/app/").status_code == 302


def test_protected_pages_redirect_to_sign_in_with_next(client):
    r = client.get("/app/people")
    assert r.status_code == 302 and "/signin?next=/app/people" in r.headers["Location"]
    assert client.get("/api/v1/me").status_code == 401


def test_csrf_protects_sign_in(app, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    app.config["WTF_CSRF_ENABLED"] = True
    c = app.test_client()
    assert sign_in(c, "esi@attc.example").status_code in (400, 401)  # no token
    html = c.get("/signin").get_data(as_text=True)
    token = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html).group(1)
    assert sign_in(c, "esi@attc.example", csrf_token=token).status_code == 302


# ---- Sign-up -----------------------------------------------------------------

SIGNUP = dict(kind="organization", plan="org-starter", billing_cycle="monthly",
              organization_name="Ho Vocational Institute", organization_type="vocational_institute",
              full_name="Kafui Agbeko", email="kafui@hvi.example", city="Ho",
              password="Strong-pass-2026", confirm="Strong-pass-2026", accept_terms="y")


def test_organization_sign_up_creates_workspace_owner_and_trial(client, app):
    r = client.post("/signup", data=SIGNUP)
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/")
    with platform_scope():
        tenant = Tenant.query.one()
        assert tenant.name == "Ho Vocational Institute" and tenant.kind == "organization"
        assert tenant.current_subscription.status == "trialing"
        m = Membership.query.one()
        assert m.role == Role.ORG_ADMIN and m.is_owner and m.user.email == "kafui@hvi.example"
    page = client.get("/app/")
    assert b"free trial of Starter has started" in page.data


def test_individual_sign_up(client):
    data = dict(SIGNUP, kind="individual", plan="ind-free", organization_name="", organization_type="")
    assert client.post("/signup", data=data).status_code == 302
    with platform_scope():
        t = Tenant.query.one()
        assert t.kind == "individual" and t.name == "Kafui Agbeko"
        assert t.current_subscription.status == "active"


def test_independent_pro_owner_gets_trainer_and_assessor_roles(client):
    data = dict(SIGNUP, kind="individual", plan="ind-pro", organization_name="", organization_type="")
    client.post("/signup", data=data)
    with platform_scope():
        roles = {m.role for m in Membership.query}
    assert roles == {Role.LEARNER, Role.TRAINER, Role.ASSESSOR}


@pytest.mark.parametrize("change, message", [
    (dict(password="short", confirm="short"), b"at least 10 characters"),
    (dict(password="onlyletterspassword", confirm="onlyletterspassword"), b"Mix letters"),
    (dict(confirm="Different-2026"), b"match"),
    (dict(email="not-an-email"), b"Invalid email"),
    (dict(accept_terms=""), b"accept the terms"),
    (dict(organization_name=""), b"organization&#39;s name"),
    (dict(plan="org-enterprise"), b"Not a valid choice"),
    (dict(plan="ind-plus"), b"Not a valid choice"),
    (dict(phone="call me maybe"), b"valid phone"),
])
def test_sign_up_validation(client, change, message):
    r = client.post("/signup", data=dict(SIGNUP, **change))
    assert r.status_code == 200 and message in r.data
    with platform_scope():
        assert Tenant.query.count() == 0


def test_sign_up_with_existing_email_is_refused(client, org, make_person):
    make_person("kafui@hvi.example", [Role.LEARNER], org)
    r = client.post("/signup", data=SIGNUP)
    assert r.status_code == 400 and b"already exists" in r.data


def test_sign_up_is_rate_limited(client):
    codes = [client.post("/signup", data=dict(SIGNUP, email=f"p{i}@x.example")).status_code for i in range(6)]
    assert codes[-1] == 429


# ---- Password reset ---------------------------------------------------------------

def _reset_link():
    body = mailer.outbox[-1].get_content()
    return re.search(r"http://localhost(/reset-password/\S+)", body).group(1)


def test_password_reset_flow(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    r = client.post("/forgot-password", data={"email": "esi@attc.example"})
    assert b"If an account uses that email" in r.data and len(mailer.outbox) == 1
    link = _reset_link()
    assert client.get(link).status_code == 200
    r = client.post(link, data={"password": "Brand-new-pass-1", "confirm": "Brand-new-pass-1"})
    assert r.status_code == 302
    assert sign_in(client, "esi@attc.example", "Brand-new-pass-1").status_code == 302
    # The link works only once
    assert client.get(link).status_code == 400


def test_password_reset_does_not_reveal_unknown_emails(client):
    r = client.post("/forgot-password", data={"email": "nobody@nowhere.example"})
    assert b"If an account uses that email" in r.data and mailer.outbox == []


def test_tampered_or_expired_reset_tokens_are_rejected(client, org, make_person, app):
    from app.services.auth import make_reset_token, user_from_reset_token
    u = make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    token = make_reset_token(u)
    assert client.get(f"/reset-password/{token}x").status_code == 400
    assert user_from_reset_token(token, max_age=-1) is None


def test_reset_clears_lockout(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    for _ in range(5):
        sign_in(client, "esi@attc.example", "wrong-password")
    client.post("/forgot-password", data={"email": "esi@attc.example"})
    client.post(_reset_link(), data={"password": "Brand-new-pass-1", "confirm": "Brand-new-pass-1"})
    assert sign_in(client, "esi@attc.example", "Brand-new-pass-1").status_code == 302


# ---- Account ---------------------------------------------------------------------------

def test_change_password_signs_out_other_devices(app, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    laptop, phone = app.test_client(), app.test_client()
    sign_in(laptop, "esi@attc.example")
    sign_in(phone, "esi@attc.example")
    r = laptop.post("/account/password", data={"current_password": PASSWORD, "password": "Another-pass-77",
                                               "confirm": "Another-pass-77"})
    assert r.status_code == 302
    assert laptop.get("/app/").status_code == 200   # this device stays signed in
    assert phone.get("/app/").status_code == 302    # the other one is signed out


def test_change_password_needs_current_password(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    sign_in(client, "esi@attc.example")
    r = client.post("/account/password", data={"current_password": "nope-nope-1", "password": "Another-pass-77",
                                               "confirm": "Another-pass-77"})
    assert r.status_code == 400 and b"current password is not correct" in r.data


def test_sign_out_everywhere(app, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    a, b = app.test_client(), app.test_client()
    sign_in(a, "esi@attc.example")
    sign_in(b, "esi@attc.example")
    a.post("/account/sign-out-everywhere")
    assert a.get("/app/").status_code == 302 and b.get("/app/").status_code == 302


def test_update_profile(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    sign_in(client, "esi@attc.example")
    client.post("/account/", data={"full_name": "Esi A. Boateng", "phone": "+233 24 000 1111"})
    u = db.session.scalar(select(User).where(User.email == "esi@attc.example"))
    db.session.refresh(u)
    assert u.full_name == "Esi A. Boateng" and u.phone == "+233 24 000 1111"
