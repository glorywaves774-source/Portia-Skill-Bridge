"""Plan usage limits.

Later stages register a counter per resource (learners, courses, ...). Stage 1
registers the counters it can already measure: staff and learner memberships.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy import func, select

from app.extensions import db
from app.models.subscription import LIMIT_FIELDS
from app.models.tenant import Tenant
from app.models.user import Membership, Role


class UsageLimitExceeded(PermissionError):
    def __init__(self, resource: str, limit: int, used: int):
        self.resource, self.limit, self.used = resource, limit, used
        super().__init__(f"Your plan allows {limit} {resource.replace('_', ' ')}; {used} already in use. "
                         "Upgrade the plan to add more.")


_counters: dict[str, Callable[[Tenant], int]] = {}


def register_counter(resource: str):
    if resource not in LIMIT_FIELDS:
        raise KeyError(f"Unknown usage resource '{resource}'")

    def decorator(fn):
        _counters[resource] = fn
        return fn
    return decorator


@register_counter("staff")
def _count_staff(tenant: Tenant) -> int:
    return db.session.scalar(
        select(func.count(func.distinct(Membership.user_id))).where(
            Membership.tenant_id == tenant.id, Membership.is_active.is_(True),
            Membership.role.in_(Role.STAFF_ROLES),
        )
    ) or 0


@register_counter("learners")
def _count_learners(tenant: Tenant) -> int:
    """Learner and beneficiary records (archived records don't count)."""
    from app.models.learner import Learner, LearnerStatus
    return db.session.scalar(
        select(func.count(Learner.id)).where(Learner.tenant_id == tenant.id, Learner.status.in_(LearnerStatus.COUNTED))
    ) or 0


def usage(tenant: Tenant, resource: str) -> int | None:
    counter = _counters.get(resource)
    return counter(tenant) if counter else None


def limit(tenant: Tenant, resource: str) -> int | None:
    sub = tenant.current_subscription
    if sub is None:
        return 0
    return sub.plan.limit_for(resource)


def check(tenant: Tenant, resource: str, adding: int = 1) -> None:
    """Raise UsageLimitExceeded if adding `adding` more would go over the plan limit."""
    cap = limit(tenant, resource)
    if cap is None:
        return
    used = usage(tenant, resource) or 0
    if used + adding > cap:
        raise UsageLimitExceeded(resource, cap, used)


def summary(tenant: Tenant) -> list[dict]:
    rows = []
    for resource in LIMIT_FIELDS:
        used = usage(tenant, resource)
        cap = limit(tenant, resource)
        pct = None
        if used is not None and cap:
            pct = min(100, round(used * 100 / cap))
        rows.append({"resource": resource, "used": used, "limit": cap, "percent": pct})
    return rows
