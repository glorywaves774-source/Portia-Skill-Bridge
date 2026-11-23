"""Role-based access: each role sees exactly the pages it should, in its own workspace only."""
import pytest

from app.models import Role
from app.rbac import OWNER_PERMISSIONS, PERMISSIONS, ROLE_PERMISSIONS, permissions_for
from tests.conftest import sign_in

PAGES = {
    "/app/": "workspace.view",
    "/app/people": "members.view",
    "/app/subscription": "billing.view",
    "/app/activity": "audit.view",
    "/app/settings": "workspace.settings",
    "/app/access": "workspace.view",
}


def test_every_role_has_a_permission_set():
    assert set(ROLE_PERMISSIONS) == set(Role.TENANT_ROLES)
    for perms in ROLE_PERMISSIONS.values():
        assert perms <= set(PERMISSIONS)
    assert OWNER_PERMISSIONS <= set(PERMISSIONS)


def test_key_separations_of_duty():
    assert "billing.manage" not in ROLE_PERMISSIONS[Role.TRAINER]
    assert "members.manage" not in ROLE_PERMISSIONS[Role.TRAINING_MANAGER]
    assert "assessments.conduct" not in ROLE_PERMISSIONS[Role.TRAINER]
    assert "learners.view" not in ROLE_PERMISSIONS[Role.EMPLOYER]
    assert "payments.view" in ROLE_PERMISSIONS[Role.FINANCE_OFFICER]
    assert ROLE_PERMISSIONS[Role.LEARNER] == {"workspace.view", "self.profile", "self.learning", "self.jobs"}
    assert permissions_for([Role.TRAINER, Role.ASSESSOR]) >= {"courses.manage", "assessments.conduct"}


@pytest.mark.parametrize("role", Role.TENANT_ROLES)
def test_page_access_matches_permissions(client, org, make_person, role):
    make_person(f"{role}@attc.example", [role], org)
    sign_in(client, f"{role}@attc.example")
    perms = ROLE_PERMISSIONS[role]
    for url, needed in PAGES.items():
        expected = 200 if needed in perms else 403
        assert client.get(url).status_code == expected, (role, url)


def test_menu_only_shows_permitted_pages(client, org, make_person):
    make_person("trainer@attc.example", [Role.TRAINER], org)
    sign_in(client, "trainer@attc.example")
    html = client.get("/app/").get_data(as_text=True)
    assert 'href="/app/people"' not in html and 'href="/app/subscription"' not in html
    assert 'href="/account/"' in html


def test_owner_gets_management_even_with_learner_role(client, make_tenant, make_person):
    t = make_tenant("Ama Owusu", kind="individual", plan="ind-plus")
    make_person("ama@example.com", [Role.LEARNER], t, owner=True)
    sign_in(client, "ama@example.com")
    assert client.get("/app/subscription").status_code == 200
    assert client.get("/app/settings").status_code == 200


def test_api_me_reports_roles_and_permissions(client, org, make_person):
    make_person("fo@attc.example", [Role.FINANCE_OFFICER], org)
    sign_in(client, "fo@attc.example")
    data = client.get("/api/v1/me").get_json()
    assert data["workspace"]["code"] == org.code
    assert data["workspace"]["role_codes"] == [Role.FINANCE_OFFICER]
    assert "billing.manage" in data["permissions"] and "members.view" not in data["permissions"]


def test_api_members_requires_permission(client, org, make_person):
    make_person("t@attc.example", [Role.TRAINER], org)
    make_person("a@attc.example", [Role.ORG_ADMIN], org)
    sign_in(client, "t@attc.example")
    assert client.get("/api/v1/workspace/members").status_code == 403
    client.post("/signout")
    sign_in(client, "a@attc.example")
    data = client.get("/api/v1/workspace/members").get_json()["data"]
    assert {p["email"] for p in data} == {"t@attc.example", "a@attc.example"}
