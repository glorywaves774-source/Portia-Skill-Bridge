"""Super Administrator tools: workspaces, subscriptions, people and plans."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Membership, Role, SubscriptionPlan, Tenant, User
from app.services import mailer
from app.tenancy import platform_scope
from tests.conftest import sign_in


@pytest.fixture()
def boss(client, make_person):
    mailer.outbox.clear()
    user = make_person("boss@kayron.example", platform_admin=True, name="Platform Boss")
    sign_in(client, "boss@kayron.example")
    return user


def test_create_workspace_and_invite_owner(app, client, boss):
    r = client.post("/admin/tenants/new", data={
        "kind": "organization", "name": "Tema Fisheries Training", "organization_type": "training_centre",
        "plan": "org-business", "billing_cycle": "annual", "start_trial": "y",
        "owner_name": "Adjoa Tetteh", "owner_email": "adjoa@tft.example", "city": "Tema"})
    assert r.status_code == 302
    with platform_scope():
        t = Tenant.query.one()
        assert t.name == "Tema Fisheries Training" and t.current_subscription.plan.code == "org-business"
        assert t.current_subscription.billing_cycle == "annual"
    link = re.search(r"http://localhost(/invite/\S+)", mailer.outbox[-1].get_content()).group(1)
    owner = app.test_client()
    owner.post(link, data={"full_name": "Adjoa Tetteh", "password": "Adjoa-pass-2026", "confirm": "Adjoa-pass-2026",
                           "accept_terms": "y"})
    with platform_scope():
        m = Membership.query.one()
        assert m.role == Role.ORG_ADMIN and m.is_owner
    assert owner.get("/app/settings").status_code == 200


def test_create_workspace_validation(client, boss):
    r = client.post("/admin/tenants/new", data={"kind": "organization", "name": "X", "plan": "org-starter",
                                                "billing_cycle": "monthly", "owner_name": "A", "owner_email": "bad"})
    assert r.status_code == 200 and b"Invalid email" in r.data and b"type of organization" in r.data
    r = client.post("/admin/tenants/new", data={"kind": "individual", "name": "Ama", "plan": "org-starter",
                                                "billing_cycle": "monthly", "owner_name": "Ama O",
                                                "owner_email": "ama@example.com"}, follow_redirects=True)
    assert b"for organization accounts" in r.data


def _sub_action(client, code, **data):
    return client.post(f"/admin/tenants/{code}/subscription", data=data, follow_redirects=True)


def test_subscription_actions(client, boss, make_tenant):
    t = make_tenant(plan="org-starter")
    r = _sub_action(client, t.code, action="extend_trial", days="7")
    assert b"Trial now ends" in r.data
    r = _sub_action(client, t.code, action="activate", billing_cycle="annual", reference="MOMO-55512")
    assert b"Paid period started" in r.data
    r = _sub_action(client, t.code, action="change_plan", plan="org-professional")
    assert b"Plan changed to Professional" in r.data
    r = _sub_action(client, t.code, action="change_plan", plan="ind-plus")
    assert b"Not a valid choice" in r.data
    r = _sub_action(client, t.code, action="renew", reference="BANK-778")
    assert b"Extended until" in r.data
    r = _sub_action(client, t.code, action="cancel_now", note="Programme closed")
    assert b"Subscription cancelled" in r.data
    r = _sub_action(client, t.code, action="renew")
    assert b"no live subscription" in r.data
    r = _sub_action(client, t.code, action="start", plan="org-business", billing_cycle="monthly")
    assert b"Started Business" in r.data
    with platform_scope():
        actions = [a.action for a in AuditLog.query.filter_by(tenant_id=t.id)]
        refs = [a.details.get("reference") for a in AuditLog.query.filter_by(action="subscription.activated")]
    assert "subscription.trial_extended" in actions and "MOMO-55512" in refs


def test_user_admin_actions(app, client, boss, org, make_person):
    p = make_person("yaw@attc.example", [Role.TRAINER], org)
    p.locked_until = p.created_at + timedelta(days=999)
    db.session.commit()
    url = f"/admin/users/{p.public_id}/action"
    assert b"can sign in again" in client.post(url, data={"action": "unlock"}, follow_redirects=True).data
    assert b"emailed" in client.post(url, data={"action": "send_reset"}, follow_redirects=True).data
    assert mailer.outbox[-1]["To"] == "yaw@attc.example"
    yaw = app.test_client()
    sign_in(yaw, "yaw@attc.example")
    client.post(url, data={"action": "deactivate"})
    assert yaw.get("/app/").status_code == 302
    client.post(url, data={"action": "reactivate"})
    client.post(url, data={"action": "grant_admin"})
    db.session.refresh(p)
    assert p.is_platform_admin and p.is_active_account


def test_super_admin_safeguards(client, boss):
    url = f"/admin/users/{boss.public_id}/action"
    assert b"can&#39;t deactivate your own" in client.post(url, data={"action": "deactivate"}, follow_redirects=True).data
    assert b"own Super Administrator" in client.post(url, data={"action": "revoke_admin"}, follow_redirects=True).data


def test_users_list_and_detail(client, boss, org, make_person):
    make_person("yaw@attc.example", [Role.TRAINER], org, name="Yaw Asante")
    assert b"Yaw Asante" in client.get("/admin/users?q=yaw").data
    assert b"Yaw Asante" not in client.get("/admin/users?filter=admins").data
    u = db.session.scalar(select(User).where(User.email == "yaw@attc.example"))
    page = client.get(f"/admin/users/{u.public_id}")
    assert page.status_code == 200 and b"Accra Technical Training Centre" in page.data


def test_edit_plan_and_seed_keeps_edits(app, client, boss):
    r = client.post("/admin/plans/org-starter/edit", data={
        "name": "Starter", "tagline": "Small centres", "monthly_price": "175.00", "annual_price": "1750.00",
        "trial_days": "21", "max_learners": "120", "max_staff": "6", "max_courses": "", "max_programmes": "3",
        "max_vacancies": "5", "storage_mb": "5000", "features": "One\nTwo\n", "is_public": "y", "is_active": "y"})
    assert r.status_code == 302
    plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-starter"))
    db.session.refresh(plan)
    assert plan.monthly_price_minor == 17500 and plan.max_courses is None and plan.features == ["One", "Two"]
    assert app.test_cli_runner().invoke(args=["seed-plans"]).output.startswith("Plans ready: 0 created, 0 updated")
    db.session.refresh(plan)
    assert plan.monthly_price_minor == 17500
    assert b"GHS 175" in client.get("/pricing").data


def test_non_admins_cannot_use_admin_tools(client, org, make_person, make_tenant):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    sign_in(client, "esi@attc.example")
    assert client.get("/admin/tenants/new").status_code == 403
    assert client.post("/admin/plans/org-starter/edit", data={"name": "Free!"}).status_code == 403
    assert client.post(f"/admin/tenants/{org.code}/subscription", data={"action": "renew"}).status_code == 403
