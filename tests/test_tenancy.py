"""Tenant isolation: the most important guarantee of the platform."""
import pytest
from sqlalchemy import delete, func, select, update

from app.extensions import db
from app.models import AuditLog, Membership, Role, Subscription, Tenant, User
from app.services import audit
from app.tenancy import (
    TenantContextRequired,
    TenantIsolationError,
    platform_scope,
    tenant_context,
)


def test_reads_are_limited_to_the_current_tenant(two_tenants):
    a, b = two_tenants
    with tenant_context(a):
        rows = db.session.scalars(select(Membership)).all()
        assert {m.tenant_id for m in rows} == {a}
        assert Membership.query.count() == 1
        assert {s.tenant_id for s in Subscription.query.all()} == {a}


def test_cannot_fetch_another_tenants_record_by_id(two_tenants):
    a, b = two_tenants
    with platform_scope():
        b_member_id = db.session.scalar(select(Membership.id).where(Membership.tenant_id == b))
    db.session.expunge_all()
    with tenant_context(a):
        assert db.session.get(Membership, b_member_id) is None
        assert db.session.scalar(select(Membership).where(Membership.id == b_member_id)) is None


def test_tenant_table_itself_is_isolated(two_tenants):
    a, b = two_tenants
    with tenant_context(a):
        assert [t.id for t in Tenant.query.all()] == [a]
        assert db.session.get(Tenant, b) is None


def test_joins_and_aggregates_are_filtered(two_tenants):
    a, _ = two_tenants
    with tenant_context(a):
        count = db.session.scalar(select(func.count(Membership.id)))
        assert count == 1
        joined = db.session.execute(select(User.email).join(Membership)).scalars().all()
        assert joined == ["ama@alpha.example"]


def test_relationship_loads_are_filtered(two_tenants):
    a, _ = two_tenants
    with tenant_context(a):
        user = db.session.scalar(select(User).where(User.email == "kofi@beta.example"))
        assert user is not None  # people are global identities…
        assert user.memberships == []  # …but another tenant's roles stay hidden


def test_bulk_update_and_delete_cannot_touch_other_tenants(two_tenants):
    a, b = two_tenants
    with tenant_context(a):
        db.session.execute(update(Membership).values(role=Role.TRAINER))
        db.session.execute(delete(Membership).where(Membership.role == Role.TRAINER))
        db.session.commit()
    with platform_scope():
        remaining = db.session.scalars(select(Membership)).all()
        assert [(m.tenant_id, m.role) for m in remaining] == [(b, Role.LEARNER)]


def test_writing_a_record_for_another_tenant_is_refused(two_tenants):
    a, b = two_tenants
    with tenant_context(a):
        user = User(email="x@example.com", full_name="X")
        db.session.add(user)
        db.session.add(Membership(tenant_id=b, user=user, role=Role.LEARNER))
        with pytest.raises(TenantIsolationError):
            db.session.flush()
    db.session.rollback()


def test_new_records_default_to_the_current_tenant(two_tenants):
    a, _ = two_tenants
    with tenant_context(a):
        user = User(email="new@example.com", full_name="New")
        m = Membership(user=user, role=Role.TRAINER)
        db.session.add_all([user, m])
        db.session.commit()
        assert m.tenant_id == a


def test_moving_a_record_to_another_tenant_is_refused(two_tenants):
    a, b = two_tenants
    with tenant_context(a):
        m = Membership.query.one()
        m.tenant_id = b
        with pytest.raises(TenantIsolationError):
            db.session.flush()
    db.session.rollback()


def test_cannot_modify_another_tenant_workspace(two_tenants):
    a, b = two_tenants
    with platform_scope():
        other = db.session.get(Tenant, b)
    with tenant_context(a):
        other.name = "Hijacked"
        with pytest.raises(TenantIsolationError):
            db.session.flush()
    db.session.rollback()


def test_queries_without_any_scope_fail_closed(two_tenants):
    with pytest.raises(TenantContextRequired):
        Membership.query.all()
    with pytest.raises(TenantContextRequired):
        db.session.scalars(select(Tenant)).all()
    with pytest.raises(TenantContextRequired):
        db.session.scalars(select(AuditLog)).all()


def test_writes_without_any_scope_fail_closed(two_tenants):
    a, _ = two_tenants
    db.session.add(Membership(tenant_id=a, user=User(email="z@z.example", full_name="Z"), role=Role.LEARNER))
    with pytest.raises(TenantContextRequired):
        db.session.flush()
    db.session.rollback()


def test_audit_log_is_isolated(two_tenants):
    a, b = two_tenants
    with platform_scope():
        audit.record("platform.test", summary="platform-wide")
        db.session.commit()
    with tenant_context(a):
        rows = AuditLog.query.all()
        assert rows and all(r.tenant_id == a for r in rows)


def test_platform_scope_sees_everything(two_tenants):
    with platform_scope():
        assert Tenant.query.count() == 2
        assert Membership.query.count() == 2


def test_include_all_tenants_option_only_in_platform_scope(two_tenants):
    a, _ = two_tenants
    with tenant_context(a):
        with pytest.raises(TenantIsolationError):
            db.session.scalars(select(Membership).execution_options(include_all_tenants=True)).all()


def test_scope_is_reset_between_requests(app, two_tenants):
    from app.tenancy import use_tenant_for_request

    @app.get("/_probe")
    def probe():
        try:
            Membership.query.all()
            return "leak"
        except TenantContextRequired:
            return "closed"

    use_tenant_for_request(two_tenants[0])  # simulate a scope left over from earlier work
    assert app.test_client().get("/_probe").get_data(as_text=True) == "closed"


def test_tenant_context_rejects_non_integer_ids():
    with pytest.raises(TypeError):
        with tenant_context("1"):
            pass
