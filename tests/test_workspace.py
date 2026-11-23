"""Workspace selection, isolation through the web layer, settings and access gates."""
from datetime import timedelta

from app.extensions import db
from app.models import AuditLog, Role, Tenant
from app.services import subscriptions as subs
from app.services.tenants import suspend_tenant
from app.tenancy import platform_scope
from tests.conftest import sign_in


def test_people_page_never_shows_other_workspaces(client, make_tenant, make_person):
    a = make_tenant("Alpha Institute")
    b = make_tenant("Beta NGO", organization_type="ngo")
    make_person("admin@alpha.example", [Role.ORG_ADMIN], a)
    make_person("secret@beta.example", [Role.LEARNER], b, name="Beta Secret Person")
    sign_in(client, "admin@alpha.example")
    html = client.get("/app/people").get_data(as_text=True)
    assert "admin@alpha.example" in html and "secret@beta.example" not in html
    activity = client.get("/app/activity").get_data(as_text=True)
    assert "Beta NGO" not in activity
    api = client.get("/api/v1/workspace/members").get_json()["data"]
    assert [p["email"] for p in api] == ["admin@alpha.example"]


def test_person_in_two_workspaces_chooses_and_switches(client, make_tenant, make_person):
    a = make_tenant("Alpha Institute")
    b = make_tenant("Beta NGO", organization_type="ngo")
    user = make_person("both@example.com", [Role.TRAINER], a)
    with platform_scope():
        from app.models import Membership
        db.session.add(Membership(tenant_id=b.id, user_id=user.id, role=Role.ORG_ADMIN))
        db.session.commit()
    r = sign_in(client, "both@example.com")
    assert r.headers["Location"].endswith("/workspaces")
    page = client.get("/workspaces").get_data(as_text=True)
    assert "Alpha Institute" in page and "Beta NGO" in page
    client.post(f"/workspaces/{b.public_id}")
    assert client.get("/app/people").status_code == 200        # admin in Beta
    client.post(f"/workspaces/{a.public_id}")
    assert client.get("/app/people").status_code == 403        # trainer in Alpha


def test_cannot_select_a_workspace_you_do_not_belong_to(client, make_tenant, make_person):
    a = make_tenant("Alpha Institute")
    b = make_tenant("Beta NGO", organization_type="ngo")
    make_person("admin@alpha.example", [Role.ORG_ADMIN], a)
    sign_in(client, "admin@alpha.example")
    assert client.post(f"/workspaces/{b.public_id}").status_code == 404
    # Tampering with the session is ignored too
    with client.session_transaction() as s:
        s["workspace"] = b.public_id
    html = client.get("/app/").get_data(as_text=True)
    assert "Alpha Institute" in html and "Beta NGO" not in html


def test_platform_admin_without_workspace_lands_on_console(client, make_person):
    make_person("boss@kayron.example", platform_admin=True)
    assert sign_in(client, "boss@kayron.example").headers["Location"].endswith("/admin/")


def test_person_without_workspace(client, make_person):
    make_person("lonely@example.com")
    assert sign_in(client, "lonely@example.com").headers["Location"].endswith("/no-workspace")
    assert client.get("/app/").status_code == 302


def test_suspended_workspace_is_blocked(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    with platform_scope():
        suspend_tenant(db.session.get(Tenant, org.id), reason="Unpaid")
        db.session.commit()
    sign_in(client, "esi@attc.example")
    r = client.get("/app/")
    assert r.status_code == 403 and b"suspended" in r.data
    assert client.get("/app/subscription").status_code == 403


def test_lapsed_subscription_blocks_all_but_plan_page(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    make_person("t@attc.example", [Role.TRAINER], org)
    with platform_scope():
        sub = db.session.get(Tenant, org.id).current_subscription
        subs.process_due(now=sub.current_period_end + timedelta(minutes=1))
        db.session.commit()
    sign_in(client, "esi@attc.example")
    assert client.get("/app/people").status_code == 402
    assert client.get("/app/subscription").status_code == 200
    page = client.get("/app/").get_data(as_text=True)
    assert "subscription has ended" in page
    client.post("/signout")
    sign_in(client, "t@attc.example")
    assert client.get("/app/subscription").status_code == 403


def test_workspace_settings_update_and_audit(client, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    sign_in(client, "esi@attc.example")
    r = client.post("/app/settings", data={"name": "ATTC Accra", "organization_type": "training_centre",
                                           "email": "hello@attc.example", "website": "https://attc.example",
                                           "city": "Accra", "region": "Greater Accra"})
    assert r.status_code == 302
    with platform_scope():
        t = db.session.get(Tenant, org.id)
        db.session.refresh(t)
        assert t.name == "ATTC Accra" and t.region == "Greater Accra"
        assert AuditLog.query.filter_by(action="workspace.updated", tenant_id=org.id).count() == 1
    bad = client.post("/app/settings", data={"name": "X", "website": "not a url"})
    assert bad.status_code == 200 and b"between 2 and 160" in bad.data and b"https://" in bad.data
