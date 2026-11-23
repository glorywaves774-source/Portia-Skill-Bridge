"""Inviting people, roles, deactivation, removal, safeguards and plan limits."""
import re

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Invitation, Membership, Role, User
from app.services import limits, mailer, members
from app.tenancy import platform_scope, tenant_context
from tests.conftest import PASSWORD, sign_in


@pytest.fixture(autouse=True)
def _outbox():
    mailer.outbox.clear()


@pytest.fixture()
def admin(client, org, make_person):
    user = make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True, name="Esi Boateng")
    sign_in(client, "esi@attc.example")
    return user


def _invite_link():
    return re.search(r"http://localhost(/invite/\S+)", mailer.outbox[-1].get_content()).group(1)


def _invite(client, email="kojo@example.com", roles=("trainer",), **extra):
    return client.post("/app/people/invite", data={"email": email, "roles": list(roles), **extra},
                       follow_redirects=True)


def test_invite_new_person_who_creates_account(app, client, org, admin):
    r = _invite(client, full_name="Kojo Mensah", message="Welcome aboard!")
    assert b"Invitation sent to kojo@example.com" in r.data
    msg = mailer.outbox[-1]
    assert msg["To"] == "kojo@example.com" and "Accra Technical Training Centre" in msg["Subject"]
    assert "Welcome aboard!" in msg.get_content()
    link = _invite_link()

    newcomer = app.test_client()
    page = newcomer.get(link)
    assert page.status_code == 200 and b"Create your account" in page.data and b"k\xe2\x80\xa2" in page.data
    r = newcomer.post(link, data={"full_name": "Kojo Mensah", "password": "Kojo-pass-2026",
                                  "confirm": "Kojo-pass-2026", "accept_terms": "y"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/")
    assert newcomer.get("/app/").status_code == 200
    with platform_scope():
        m = Membership.query.join(User).filter(User.email == "kojo@example.com").one()
        assert m.role == Role.TRAINER and m.tenant_id == org.id
        assert Invitation.query.one().accepted_at is not None
    assert newcomer.get(link).status_code == 410  # single use


def test_existing_user_signs_in_then_accepts(app, client, org, admin, make_tenant, make_person):
    other = make_tenant("Beta NGO", organization_type="ngo")
    make_person("abena@example.com", [Role.LEARNER], other)
    _invite(client, email="Abena@Example.com", roles=["assessor"])
    link = _invite_link()
    c = app.test_client()
    assert b"Sign in to accept" in c.get(link).data
    sign_in(c, "abena@example.com")
    assert b"Accept and open workspace" in c.get(link).data
    r = c.post(link)
    assert r.status_code == 302
    with platform_scope():
        roles = {(m.tenant_id, m.role) for m in Membership.query.join(User).filter(User.email == "abena@example.com")}
    assert roles == {(other.id, Role.LEARNER), (org.id, Role.ASSESSOR)}


def test_invitation_for_someone_else_is_refused(app, client, org, admin, make_person):
    make_person("mallory@example.com", [], None)
    _invite(client, email="kojo@example.com")
    c = app.test_client()
    sign_in(c, "mallory@example.com")
    r = c.post(_invite_link())
    assert r.status_code == 403
    with platform_scope():
        assert Invitation.query.one().accepted_at is None


def test_revoked_and_expired_invitations_cannot_be_used(app, client, org, admin):
    _invite(client)
    link = _invite_link()
    with tenant_context(org.id):
        inv = Invitation.query.one()
        public_id = inv.public_id
    client.post(f"/app/invitations/{public_id}/revoke")
    assert app.test_client().get(link).status_code == 410
    assert app.test_client().get("/invite/not-a-real-token").status_code == 404


def test_resend_replaces_the_link(app, client, org, admin):
    _invite(client)
    first = _invite_link()
    with tenant_context(org.id):
        public_id = Invitation.query.one().public_id
    client.post(f"/app/invitations/{public_id}/resend")
    second = _invite_link()
    assert first != second
    assert app.test_client().get(first).status_code == 404
    assert app.test_client().get(second).status_code == 200


def test_token_is_stored_only_as_a_hash(client, org, admin):
    _invite(client)
    token = _invite_link().rsplit("/", 1)[1]
    with platform_scope():
        inv = Invitation.query.one()
        assert token not in inv.token_hash and len(inv.token_hash) == 64


def test_cannot_invite_existing_member(client, org, admin):
    r = _invite(client, email="esi@attc.example")
    assert b"already in this workspace" in r.data


@pytest.mark.parametrize("data, message", [
    ({"email": "not-an-email", "roles": ["trainer"]}, b"Invalid email"),
    ({"email": "a@b.example"}, b"Choose at least one role"),
    ({"email": "a@b.example", "roles": ["super_admin"]}, b"not a valid choice"),
])
def test_invite_validation(client, org, admin, data, message):
    assert message in client.post("/app/people/invite", data=data, follow_redirects=True).data


def test_staff_limit_counts_pending_invitations(client, make_tenant, make_person):
    t = make_tenant("Small Centre", plan="org-starter")  # 5 staff
    make_person("owner@small.example", [Role.ORG_ADMIN], t, owner=True)
    sign_in(client, "owner@small.example")
    for i in range(4):
        assert b"Invitation sent" in _invite(client, email=f"t{i}@small.example").data
    r = _invite(client, email="t5@small.example")
    assert b"Your plan allows 5 staff" in r.data
    # Learners are counted separately
    assert b"Invitation sent" in _invite(client, email="l1@small.example", roles=["learner"]).data


def test_change_roles(client, org, admin, make_person):
    p = make_person("yaw@attc.example", [Role.TRAINER], org)
    r = client.post(f"/app/people/{p.public_id}/roles", data={"roles": ["trainer", "assessor"]},
                    follow_redirects=True)
    assert b"Roles updated" in r.data
    with platform_scope():
        assert {m.role for m in Membership.query.filter_by(user_id=p.id)} == {Role.TRAINER, Role.ASSESSOR}


def test_last_admin_cannot_lose_admin_role(client, org, admin):
    r = client.post(f"/app/people/{admin.public_id}/roles", data={"roles": ["trainer"]}, follow_redirects=True)
    assert b"at least one active Organization Administrator" in r.data


def test_second_admin_allows_handover(client, org, admin, make_person):
    make_person("kwame@attc.example", [Role.ORG_ADMIN], org)
    r = client.post(f"/app/people/{admin.public_id}/roles", data={"roles": ["org_admin", "trainer"]},
                    follow_redirects=True)
    assert b"Roles updated" in r.data


def test_deactivate_reactivate_and_remove(app, client, org, admin, make_person):
    p = make_person("yaw@attc.example", [Role.TRAINER], org)
    other = app.test_client()
    sign_in(other, "yaw@attc.example")
    assert other.get("/app/").status_code == 200
    client.post(f"/app/people/{p.public_id}/status", data={"action": "deactivate"})
    assert other.get("/app/").status_code == 302  # lost access to the workspace
    client.post(f"/app/people/{p.public_id}/status", data={"action": "reactivate"})
    assert other.get("/app/").status_code == 200
    r = client.post(f"/app/people/{p.public_id}/status", data={"action": "remove"}, follow_redirects=True)
    assert b"has been removed" in r.data
    with platform_scope():
        assert Membership.query.filter_by(user_id=p.id).count() == 0
        assert db.session.get(User, p.id) is not None  # account itself stays


def test_owner_and_self_are_protected(client, org, admin, make_person):
    r = client.post(f"/app/people/{admin.public_id}/status", data={"action": "deactivate"}, follow_redirects=True)
    assert b"can&#39;t deactivate yourself" in r.data
    make_person("kwame@attc.example", [Role.ORG_ADMIN], org)
    client.post("/signout")
    sign_in(client, "kwame@attc.example")
    r = client.post(f"/app/people/{admin.public_id}/status", data={"action": "remove"}, follow_redirects=True)
    assert b"owner can&#39;t be removed" in r.data


def test_cannot_manage_people_from_another_workspace(client, org, admin, make_tenant, make_person):
    other = make_tenant("Beta NGO", organization_type="ngo")
    outsider = make_person("secret@beta.example", [Role.TRAINER], other)
    assert client.get(f"/app/people/{outsider.public_id}").status_code == 404
    assert client.post(f"/app/people/{outsider.public_id}/status", data={"action": "remove"}).status_code == 404
    assert client.post(f"/app/people/{outsider.public_id}/roles", data={"roles": ["learner"]}).status_code == 404
    with platform_scope():
        assert Membership.query.filter_by(user_id=outsider.id).count() == 1


def test_only_members_manage_can_change_people(client, org, make_person):
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    p = make_person("yaw@attc.example", [Role.TRAINER], org)
    sign_in(client, "tm@attc.example")
    assert client.get(f"/app/people/{p.public_id}").status_code == 200  # can view
    assert client.post(f"/app/people/{p.public_id}/roles", data={"roles": ["org_admin"]}).status_code == 403
    assert client.post("/app/people/invite", data={"email": "x@y.example", "roles": ["org_admin"]}).status_code == 403


def test_individual_workspace_cannot_have_org_admins(client, make_tenant, make_person):
    t = make_tenant("Ama Owusu", kind="individual", plan="ind-pro")
    make_person("ama@example.com", [Role.LEARNER, Role.TRAINER], t, owner=True)
    with platform_scope():
        with pytest.raises(members.MemberError):
            members.invite(t, email="x@example.com", roles=[Role.ORG_ADMIN], actor=None)


def test_deactivated_account_is_signed_out_immediately(app, org, make_person):
    p = make_person("yaw@attc.example", [Role.TRAINER], org)
    c = app.test_client()
    sign_in(c, "yaw@attc.example")
    p.is_active_account = False
    db.session.commit()
    assert c.get("/app/").status_code == 302
