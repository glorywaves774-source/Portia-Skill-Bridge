"""Creating, suspending and reactivating tenant workspaces (platform scope)."""
from __future__ import annotations

from sqlalchemy import func, select

from app.extensions import db
from app.models.base import utcnow
from app.models.subscription import BillingCycle, SubscriptionPlan
from app.models.tenant import (
    OrganizationType,
    Tenant,
    TenantKind,
    TenantStatus,
    generate_tenant_code,
    slugify,
)
from app.models.user import Membership, Role, User
from app.services import audit
from app.services import subscriptions as subs
from app.tenancy import in_platform_scope


# Individual plans for freelance trainers/assessors: the owner also gets those roles.
INDIVIDUAL_PROFESSIONAL_PLANS = {"ind-pro"}


def owner_roles(kind: str, plan_code: str) -> list[str]:
    """Roles the person who owns a new workspace receives."""
    if kind == TenantKind.ORGANIZATION:
        return [Role.ORG_ADMIN]
    if plan_code in INDIVIDUAL_PROFESSIONAL_PLANS:
        return [Role.LEARNER, Role.TRAINER, Role.ASSESSOR]  # freelance trainers and assessors
    return [Role.LEARNER]


class TenantError(ValueError):
    pass


def _require_platform():
    if not in_platform_scope():
        raise PermissionError("Tenant administration must run in platform scope.")


def _unique_slug(name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while db.session.scalar(select(func.count()).select_from(Tenant).where(Tenant.slug == slug)):
        slug = f"{base[:56]}-{n}"
        n += 1
    return slug


def _unique_code(kind: str) -> str:
    for _ in range(20):
        code = generate_tenant_code(kind)
        if not db.session.scalar(select(func.count()).select_from(Tenant).where(Tenant.code == code)):
            return code
    raise TenantError("Could not allocate a unique workspace code; try again.")


def create_tenant(
    *,
    name: str,
    kind: str,
    plan_code: str,
    billing_cycle: str = BillingCycle.MONTHLY,
    organization_type: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    country: str = "GH",
    city: str | None = None,
    region: str | None = None,
    owner: User | None = None,
    actor=None,
    with_trial: bool = True,
) -> Tenant:
    """Create a workspace, start its subscription (trial where available) and add the owner."""
    _require_platform()
    name = (name or "").strip()
    if len(name) < 2:
        raise TenantError("Workspace name must be at least 2 characters.")
    if kind not in TenantKind.ALL:
        raise TenantError(f"Kind must be one of: {', '.join(TenantKind.ALL)}")
    if kind == TenantKind.ORGANIZATION:
        organization_type = organization_type or OrganizationType.OTHER
        if organization_type not in OrganizationType.ALL:
            raise TenantError(f"Unknown organization type '{organization_type}'")
    else:
        organization_type = None

    plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code))
    if plan is None:
        raise TenantError(f"Unknown plan '{plan_code}'. Run `flask seed-plans` first.")

    tenant = Tenant(
        name=name, kind=kind, organization_type=organization_type,
        slug=_unique_slug(name), code=_unique_code(kind),
        email=User.normalise_email(email) if email else None, phone=phone,
        country=(country or "GH").upper()[:2], city=city, region=region,
    )
    db.session.add(tenant)
    db.session.flush()

    subs.start_subscription(tenant, plan, billing_cycle, actor=actor, with_trial=with_trial)

    if owner is not None:
        for role in owner_roles(kind, plan.code):
            db.session.add(Membership(tenant_id=tenant.id, user=owner, role=role, is_owner=True))

    audit.record("tenant.created", tenant_id=tenant.id, actor=actor, entity_type="tenant", entity_id=tenant.id,
                 summary=f"{tenant.name} ({tenant.organization_type_label}) on {plan.name}")
    return tenant


def suspend_tenant(tenant: Tenant, *, reason: str, actor=None) -> Tenant:
    _require_platform()
    reason = (reason or "").strip()
    if not reason:
        raise TenantError("A reason is required to suspend a workspace.")
    if tenant.status != TenantStatus.ACTIVE:
        raise TenantError("Only active workspaces can be suspended.")
    tenant.status = TenantStatus.SUSPENDED
    tenant.suspended_at = utcnow()
    tenant.suspension_reason = reason[:255]
    audit.record("tenant.suspended", tenant_id=tenant.id, actor=actor, entity_type="tenant",
                 entity_id=tenant.id, summary=reason)
    return tenant


def reactivate_tenant(tenant: Tenant, *, actor=None) -> Tenant:
    _require_platform()
    if tenant.status != TenantStatus.SUSPENDED:
        raise TenantError("Only suspended workspaces can be reactivated.")
    tenant.status = TenantStatus.ACTIVE
    tenant.suspended_at = None
    tenant.suspension_reason = None
    audit.record("tenant.reactivated", tenant_id=tenant.id, actor=actor, entity_type="tenant",
                 entity_id=tenant.id, summary="Workspace reactivated")
    return tenant
