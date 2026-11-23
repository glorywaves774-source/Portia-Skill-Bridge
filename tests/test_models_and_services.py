import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Membership, Role, SubscriptionPlan, Tenant, TenantStatus, User
from app.seeds import PLANS, seed_plans
from app.services import limits
from app.services.tenants import TenantError, create_tenant, reactivate_tenant, suspend_tenant
from app.tenancy import platform_scope, tenant_context


def test_seed_plans_is_idempotent_and_prices_match_brief(app):
    assert seed_plans() == (0, 0)
    assert seed_plans(update=True) == (0, len(PLANS))
    prices = {p.code: p.monthly_price_minor for p in SubscriptionPlan.query}
    assert prices["org-starter"] == 15000 and prices["org-professional"] == 35000
    assert prices["org-business"] == 75000
    enterprise = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-enterprise"))
    assert enterprise.is_custom_pricing and not enterprise.is_free
    starter = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-starter"))
    assert starter.annual_price_minor == 10 * starter.monthly_price_minor
    assert starter.annual_saving_minor == 2 * starter.monthly_price_minor


def test_both_audiences_have_plans(app):
    audiences = {p.audience for p in SubscriptionPlan.query}
    assert audiences == {"organization", "individual"}


def test_tenant_codes_and_slugs_are_unique(make_tenant):
    a = make_tenant("Same Name")
    b = make_tenant("Same Name")
    assert a.slug != b.slug and a.code != b.code
    assert a.code.startswith("PSB-ORG-") and len(a.code) == 14


def test_create_tenant_requires_platform_scope(app):
    with pytest.raises(PermissionError):
        create_tenant(name="Rogue", kind="organization", plan_code="org-starter")
    with tenant_context(1):
        with pytest.raises(PermissionError):
            create_tenant(name="Rogue", kind="organization", plan_code="org-starter")


@pytest.mark.parametrize("kwargs", [
    dict(name="", kind="organization", plan_code="org-starter"),
    dict(name="Ok Name", kind="alien", plan_code="org-starter"),
    dict(name="Ok Name", kind="organization", plan_code="no-such-plan"),
    dict(name="Ok Name", kind="organization", plan_code="org-starter", organization_type="pirate"),
])
def test_create_tenant_validates_input(app, kwargs):
    with platform_scope():
        with pytest.raises(TenantError):
            create_tenant(**kwargs)
    db.session.rollback()


def test_owner_gets_admin_membership(app):
    owner = User(email="Owner@Example.com ".strip().lower(), full_name="Owner")
    db.session.add(owner)
    with platform_scope():
        t = create_tenant(name="Owned Org", kind="organization", plan_code="org-starter", owner=owner)
        db.session.commit()
        m = Membership.query.filter_by(tenant_id=t.id).one()
        assert m.role == Role.ORG_ADMIN and m.is_owner


def test_suspend_and_reactivate_tenant(make_tenant):
    t = make_tenant()
    with platform_scope():
        with pytest.raises(TenantError):
            suspend_tenant(t, reason="  ")
        suspend_tenant(t, reason="Unpaid invoice")
        assert t.status == TenantStatus.SUSPENDED and not t.has_access
        with pytest.raises(TenantError):
            suspend_tenant(t, reason="again")
        reactivate_tenant(t)
        assert t.status == TenantStatus.ACTIVE and t.has_access


def test_password_hashing(app):
    u = User(email="a@b.example", full_name="A")
    with pytest.raises(ValueError):
        u.set_password("short")
    u.set_password("correct horse battery")
    assert u.password_hash.startswith("scrypt:") and "correct" not in u.password_hash
    assert u.check_password("correct horse battery") and not u.check_password("wrong password!")


def test_usage_limits(make_tenant):
    t = make_tenant(plan="org-starter")  # 5 staff
    with platform_scope():
        for i in range(5):
            u = User(email=f"s{i}@x.example", full_name=f"S{i}")
            db.session.add(Membership(tenant_id=t.id, user=u, role=Role.TRAINER))
        db.session.commit()
        assert limits.usage(t, "staff") == 5
        with pytest.raises(limits.UsageLimitExceeded):
            limits.check(t, "staff")
        limits.check(t, "learners", adding=100)
        with pytest.raises(limits.UsageLimitExceeded):
            limits.check(t, "learners", adding=101)
        rows = {r["resource"]: r for r in limits.summary(t)}
        assert rows["staff"]["percent"] == 100 and rows["courses"]["used"] == 0 and rows["vacancies"]["used"] is None


def test_unlimited_plans_never_block(make_tenant):
    t = make_tenant(plan="org-enterprise")
    with platform_scope():
        limits.check(t, "learners", adding=1_000_000)
