from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Subscription, SubscriptionPlan, SubscriptionStatus
from app.models.subscription import add_months
from app.services import subscriptions as subs
from app.tenancy import platform_scope

T0 = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)


def plan(code):
    return db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code))


@pytest.mark.parametrize("start, months, expected", [
    (datetime(2026, 1, 31, tzinfo=timezone.utc), 1, datetime(2026, 2, 28, tzinfo=timezone.utc)),
    (datetime(2028, 1, 31, tzinfo=timezone.utc), 1, datetime(2028, 2, 29, tzinfo=timezone.utc)),
    (datetime(2026, 11, 30, tzinfo=timezone.utc), 1, datetime(2026, 12, 30, tzinfo=timezone.utc)),
    (datetime(2026, 12, 15, tzinfo=timezone.utc), 1, datetime(2027, 1, 15, tzinfo=timezone.utc)),
    (datetime(2026, 3, 31, tzinfo=timezone.utc), 12, datetime(2027, 3, 31, tzinfo=timezone.utc)),
])
def test_add_months_handles_month_ends(start, months, expected):
    assert add_months(start, months) == expected


def test_paid_plan_starts_with_trial(make_tenant):
    t = make_tenant(plan="org-professional")
    with platform_scope():
        sub = t.current_subscription
        assert sub.status == SubscriptionStatus.TRIALING
        assert sub.trial_ends_at == sub.current_period_end
        assert (sub.trial_ends_at - sub.started_at).days == 14
        assert sub.price_minor == 35000
        assert t.has_access


def test_free_individual_plan_is_active_immediately(make_tenant):
    t = make_tenant("Kofi Mensah", kind="individual", plan="ind-free")
    with platform_scope():
        sub = t.current_subscription
        assert sub.status == SubscriptionStatus.ACTIVE and sub.trial_ends_at is None
        assert t.organization_type is None and t.code.startswith("PSB-IND-")


def test_plan_audience_must_match_account_kind(make_tenant):
    with pytest.raises(subs.SubscriptionError):
        make_tenant("Ama", kind="individual", plan="org-starter")
    db.session.rollback()


def test_only_one_live_subscription_per_tenant(make_tenant):
    t = make_tenant()
    with platform_scope():
        with pytest.raises(subs.SubscriptionError):
            subs.start_subscription(t, plan("org-business"))
        # …and the database enforces it too
        db.session.add(Subscription(tenant_id=t.id, plan=plan("org-business"), status="active",
                                    current_period_end=T0))
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


def test_activate_after_trial_starts_paid_period(make_tenant):
    t = make_tenant(plan="org-starter")
    with platform_scope():
        sub = subs.activate(t.current_subscription, now=T0, cycle="annual", reference="PSK_123")
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.current_period_end == datetime(2027, 1, 31, 9, 0, tzinfo=timezone.utc)
        assert sub.price_minor == 150000  # GHS 1,500 = 10 x monthly


def test_early_renewal_stacks_onto_current_period(make_tenant):
    t = make_tenant(plan="org-starter")
    with platform_scope():
        sub = subs.activate(t.current_subscription, now=T0)
        assert sub.current_period_end == datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc)
        subs.renew(sub, now=T0 + timedelta(days=10))
        assert sub.current_period_end == datetime(2026, 3, 28, 9, 0, tzinfo=timezone.utc)


def test_trial_expires_without_payment(make_tenant):
    t = make_tenant(plan="org-starter")
    with platform_scope():
        sub = t.current_subscription
        counts = subs.process_due(now=sub.trial_ends_at + timedelta(minutes=1))
        assert counts["expired"] == 1 and sub.status == SubscriptionStatus.EXPIRED
        assert not t.has_access


def test_lapsed_paid_subscription_goes_past_due_then_expires(make_tenant, app):
    t = make_tenant(plan="org-starter")
    with platform_scope():
        sub = subs.activate(t.current_subscription, now=T0)
        end = sub.current_period_end
        subs.process_due(now=end + timedelta(hours=1))
        assert sub.status == SubscriptionStatus.PAST_DUE
        assert sub.grants_access(now=end + timedelta(days=3))  # grace period
        subs.process_due(now=end + timedelta(days=app.config["BILLING_GRACE_DAYS"], hours=2))
        assert sub.status == SubscriptionStatus.EXPIRED
        assert not sub.grants_access(now=end + timedelta(days=30))


def test_payment_during_grace_restores_service(make_tenant):
    t = make_tenant(plan="org-starter")
    with platform_scope():
        sub = subs.activate(t.current_subscription, now=T0)
        subs.process_due(now=sub.current_period_end + timedelta(hours=1))
        paid_at = sub.current_period_end + timedelta(days=2)
        subs.renew(sub, now=paid_at)
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.current_period_start == paid_at


def test_free_plan_renews_automatically(make_tenant):
    t = make_tenant("Kofi", kind="individual", plan="ind-free")
    with platform_scope():
        sub = t.current_subscription
        old_end = sub.current_period_end
        assert subs.process_due(now=old_end + timedelta(minutes=5))["renewed"] == 1
        assert sub.status == SubscriptionStatus.ACTIVE and sub.current_period_end > old_end


def test_cancel_at_period_end(make_tenant):
    t = make_tenant(plan="org-starter")
    with platform_scope():
        sub = subs.activate(t.current_subscription, now=T0)
        subs.cancel(sub, now=T0 + timedelta(days=3), reason="Programme ended")
        assert sub.status == SubscriptionStatus.ACTIVE and sub.cancel_at_period_end
        subs.process_due(now=sub.current_period_end + timedelta(minutes=1))
        assert sub.status == SubscriptionStatus.CANCELLED
        with pytest.raises(subs.SubscriptionError):
            subs.renew(sub)


def test_cancelling_a_trial_ends_it_now(make_tenant):
    t = make_tenant()
    with platform_scope():
        sub = subs.cancel(t.current_subscription)
        assert sub.status == SubscriptionStatus.CANCELLED and sub.ended_at


def test_suspend_and_reactivate_restores_previous_status(make_tenant):
    t = make_tenant()
    with platform_scope():
        sub = t.current_subscription
        subs.suspend(sub, reason="Fraud check")
        assert not sub.grants_access()
        subs.reactivate(sub)
        assert sub.status == SubscriptionStatus.TRIALING


def test_change_plan_respects_audience(make_tenant):
    t = make_tenant()
    with platform_scope():
        subs.change_plan(t.current_subscription, plan("org-business"))
        assert t.current_subscription.plan.code == "org-business"
        with pytest.raises(subs.SubscriptionError):
            subs.change_plan(t.current_subscription, plan("ind-plus"))


def test_lifecycle_is_audited(make_tenant):
    from app.models import AuditLog
    t = make_tenant()
    with platform_scope():
        subs.activate(t.current_subscription)
        db.session.commit()
        actions = {a.action for a in AuditLog.query.filter_by(tenant_id=t.id)}
    assert {"tenant.created", "subscription.started", "subscription.activated"} <= actions
