"""People in a workspace: invitations, roles, deactivation and removal.

Safeguards:
* a workspace always keeps at least one active Organization Administrator;
* the workspace owner cannot be deactivated or removed;
* nobody can deactivate or remove themselves (no accidental lock-outs);
* plan limits for staff and learners are checked when inviting, accepting,
  adding roles and reactivating.
"""
from __future__ import annotations

import re

from flask import url_for
from sqlalchemy import delete, select

from app.extensions import db
from app.models import Invitation, Membership, Role, Tenant, TenantKind, User
from app.models.base import utcnow
from app.models.invitation import hash_token
from app.services import audit, limits, mailer
from app.tenancy import platform_scope

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MemberError(ValueError):
    pass


def _resources_for(roles) -> set[str]:
    out = set()
    if any(r in Role.STAFF_ROLES for r in roles):
        out.add("staff")
    if Role.LEARNER in roles:
        out.add("learners")
    return out


def _clean_roles(roles, tenant: Tenant) -> list[str]:
    roles = [r for r in dict.fromkeys(roles or []) if r]
    if not roles:
        raise MemberError("Choose at least one role.")
    unknown = [r for r in roles if r not in Role.TENANT_ROLES]
    if unknown:
        raise MemberError("Unknown role.")
    if tenant.kind == TenantKind.INDIVIDUAL and Role.ORG_ADMIN in roles:
        raise MemberError("Individual workspaces don't have organization administrators.")
    return roles


def memberships_of(tenant: Tenant, user: User) -> list[Membership]:
    return db.session.scalars(
        select(Membership).where(Membership.tenant_id == tenant.id, Membership.user_id == user.id)
    ).all()


def active_admin_ids(tenant: Tenant) -> set[int]:
    return set(db.session.scalars(
        select(Membership.user_id).join(User, User.id == Membership.user_id).where(
            Membership.tenant_id == tenant.id, Membership.role == Role.ORG_ADMIN,
            Membership.is_active.is_(True), User.is_active_account.is_(True))
    ).all())


def _guard_last_admin(tenant: Tenant, user: User, *, losing_admin: bool) -> None:
    if tenant.kind != TenantKind.ORGANIZATION or not losing_admin:
        return
    admins = active_admin_ids(tenant)
    if admins == {user.id}:
        raise MemberError("A workspace needs at least one active Organization Administrator. "
                          "Make someone else an administrator first.")


def _pending_invites(tenant: Tenant) -> list[Invitation]:
    return [i for i in db.session.scalars(select(Invitation).where(Invitation.tenant_id == tenant.id)).all()
            if i.is_pending]


def _check_limits(tenant: Tenant, resources: set[str], *, include_pending: bool) -> None:
    pending = _pending_invites(tenant) if include_pending else []
    for resource in resources:
        reserved = sum(1 for i in pending if resource in _resources_for(i.roles))
        limits.check(tenant, resource, adding=1 + reserved)


# ---- Invitations -------------------------------------------------------------------

def invite(tenant: Tenant, *, email: str, roles, actor: User | None, full_name: str | None = None,
           make_owner: bool = False, message: str | None = None, learner=None,
           employer=None) -> tuple[Invitation, str]:
    email = User.normalise_email(email)
    if not EMAIL_RE.match(email):
        raise MemberError("Enter a valid email address.")
    roles = _clean_roles(roles, tenant)
    if not tenant.is_active:
        raise MemberError("This workspace is suspended.")

    existing_user = db.session.scalar(select(User).where(User.email == email))
    if existing_user is not None:
        current = {m.role for m in memberships_of(tenant, existing_user) if m.is_active}
        if current:
            raise MemberError(f"{email} is already in this workspace. Change their roles on their page instead.")

    for old in _pending_invites(tenant):
        if old.email == email:
            old.revoked_at = utcnow()

    resources = _resources_for(roles)
    if learner is not None:
        resources.discard("learners")  # the learner record is already counted
    _check_limits(tenant, resources, include_pending=True)

    inv = Invitation(tenant_id=tenant.id, email=email, full_name=(full_name or "").strip() or None, roles=roles,
                     learner_id=getattr(learner, "id", None),
                     make_owner=make_owner, message=(message or "").strip()[:500] or None,
                     invited_by_id=getattr(actor, "id", None), token_hash="")
    if learner is None:
        inv.learner_id = None
    inv.employer_id = getattr(employer, "id", None) if Role.EMPLOYER in roles else None
    token = inv.issue_token()
    db.session.add(inv)
    db.session.flush()
    _send(inv, token, tenant, actor)
    audit.record("member.invited", tenant_id=tenant.id, actor=actor, entity_type="invitation", entity_id=inv.id,
                 summary=f"Invited {email} as {', '.join(Role.LABELS[r] for r in roles)}")
    return inv, token


def resend(inv: Invitation, *, actor: User | None) -> str:
    if inv.accepted_at or inv.revoked_at:
        raise MemberError("This invitation can no longer be sent.")
    token = inv.issue_token()
    _send(inv, token, inv.tenant, actor)
    audit.record("member.invite_resent", tenant_id=inv.tenant_id, actor=actor, entity_type="invitation",
                 entity_id=inv.id, summary=f"Invitation sent again to {inv.email}")
    return token


def revoke(inv: Invitation, *, actor: User | None) -> None:
    if not inv.is_pending:
        raise MemberError("Only pending invitations can be cancelled.")
    inv.revoked_at = utcnow()
    audit.record("member.invite_revoked", tenant_id=inv.tenant_id, actor=actor, entity_type="invitation",
                 entity_id=inv.id, summary=f"Invitation for {inv.email} cancelled")


def _send(inv: Invitation, token: str, tenant: Tenant, actor: User | None) -> None:
    link = url_for("invitations.respond", token=token, _external=True)
    who = getattr(actor, "full_name", None) or "The PORTIA SkillBridge team"
    roles = ", ".join(Role.LABELS[r] for r in inv.roles)
    body = (f"Hello{' ' + inv.full_name.split(' ')[0] if inv.full_name else ''},\n\n"
            f"{who} has invited you to join {tenant.name} on PORTIA SkillBridge as: {roles}.\n\n")
    if inv.message:
        body += f"Their message: {inv.message}\n\n"
    body += f"Accept the invitation here (the link works for 7 days):\n{link}\n\nPORTIA SkillBridge"
    mailer.send(inv.email, f"You're invited to join {tenant.name} on PORTIA SkillBridge", body)
    inv.last_sent_at = utcnow()
    inv.send_count = (inv.send_count or 0) + 1


def find_by_token(token: str) -> Invitation | None:
    with platform_scope():
        return db.session.scalar(select(Invitation).where(Invitation.token_hash == hash_token(token or "")))


def accept(inv: Invitation, user: User) -> Tenant:
    """Add the person to the workspace with the invited roles. Runs in platform scope."""
    with platform_scope():
        tenant = inv.tenant
        if not inv.is_pending:
            raise MemberError({"accepted": "This invitation has already been used.",
                               "revoked": "This invitation was cancelled.",
                               "expired": "This invitation has expired. Ask for a new one."}[inv.status()])
        if User.normalise_email(user.email) != inv.email:
            raise MemberError("This invitation was sent to a different email address.")
        if not tenant.is_active:
            raise MemberError("This workspace is suspended.")
        existing = {m.role: m for m in memberships_of(tenant, user)}
        held = {r for r, m in existing.items() if m.is_active}
        needed = _resources_for(inv.roles) - _resources_for(held)
        if inv.learner_id:
            needed.discard("learners")
        _check_limits(tenant, needed, include_pending=False)
        if inv.learner_id:
            from app.models.learner import Learner
            learner = db.session.get(Learner, inv.learner_id)
            if learner is not None and learner.tenant_id == tenant.id:
                if learner.user_id not in (None, user.id):
                    raise MemberError("This learner record is already linked to another account.")
                learner.user_id = user.id
        for role in inv.roles:
            m = existing.get(role)
            if m is None:
                m = Membership(tenant_id=tenant.id, user=user, role=role, is_owner=inv.make_owner)
                db.session.add(m)
            else:
                m.is_active = True
                m.is_owner = m.is_owner or inv.make_owner
            if role == Role.EMPLOYER and inv.employer_id:
                m.employer_id = inv.employer_id
        inv.accepted_at = utcnow()
        inv.accepted_by_id = user.id
        audit.record("member.joined", tenant_id=tenant.id, actor=user, entity_type="user", entity_id=user.id,
                     summary=f"{user.full_name} joined as {', '.join(Role.LABELS[r] for r in inv.roles)}")
        db.session.flush()
        from app.services import notifications
        notifications.notify_permission(tenant.id, "members.manage", exclude=user.id, category="people",
                                        title=f"{user.full_name} joined",
                                        body=f"{user.full_name} accepted the invitation and joined as "
                                             f"{', '.join(Role.LABELS[r] for r in inv.roles)}.",
                                        link=f"/app/people/{user.public_id}", email=False)
        db.session.commit()
        return tenant


# ---- Managing people already in the workspace ---------------------------------------------

def set_roles(tenant: Tenant, user: User, roles, *, actor: User) -> None:
    roles = _clean_roles(roles, tenant)
    current = memberships_of(tenant, user)
    if not current:
        raise MemberError("This person is not in this workspace.")
    held = {m.role for m in current}
    active = any(m.is_active for m in current)
    owner = any(m.is_owner for m in current)
    _guard_last_admin(tenant, user, losing_admin=Role.ORG_ADMIN in held and Role.ORG_ADMIN not in roles and active)
    if active:
        new_resources = _resources_for(roles) - _resources_for(held)
        _check_limits(tenant, new_resources, include_pending=False)
    removed = held - set(roles)
    added = [r for r in roles if r not in held]
    if not removed and not added:
        return
    if removed:
        db.session.execute(delete(Membership).where(Membership.tenant_id == tenant.id,
                                                    Membership.user_id == user.id, Membership.role.in_(removed)))
    for role in added:
        db.session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role, is_owner=owner, is_active=active))
    audit.record("member.roles_changed", tenant_id=tenant.id, actor=actor, entity_type="user", entity_id=user.id,
                 summary=f"{user.full_name}: {', '.join(Role.LABELS[r] for r in roles)}",
                 details={"added": added, "removed": sorted(removed)})


def _guard_person(tenant: Tenant, user: User, actor: User, verb: str) -> list[Membership]:
    current = memberships_of(tenant, user)
    if not current:
        raise MemberError("This person is not in this workspace.")
    if actor and actor.id == user.id:
        raise MemberError(f"You can't {verb} yourself.")
    if any(m.is_owner for m in current):
        raise MemberError(f"The workspace owner can't be {verb}d.")
    return current


def deactivate(tenant: Tenant, user: User, *, actor: User) -> None:
    current = _guard_person(tenant, user, actor, "deactivate")
    _guard_last_admin(tenant, user, losing_admin=any(m.role == Role.ORG_ADMIN and m.is_active for m in current))
    for m in current:
        m.is_active = False
    audit.record("member.deactivated", tenant_id=tenant.id, actor=actor, entity_type="user", entity_id=user.id,
                 summary=f"{user.full_name} deactivated")


def reactivate(tenant: Tenant, user: User, *, actor: User) -> None:
    current = memberships_of(tenant, user)
    if not current:
        raise MemberError("This person is not in this workspace.")
    if all(m.is_active for m in current):
        return
    _check_limits(tenant, _resources_for({m.role for m in current}), include_pending=False)
    for m in current:
        m.is_active = True
    audit.record("member.reactivated", tenant_id=tenant.id, actor=actor, entity_type="user", entity_id=user.id,
                 summary=f"{user.full_name} reactivated")


def remove(tenant: Tenant, user: User, *, actor: User) -> None:
    current = _guard_person(tenant, user, actor, "remove")
    _guard_last_admin(tenant, user, losing_admin=any(m.role == Role.ORG_ADMIN and m.is_active for m in current))
    for m in current:
        db.session.delete(m)
    audit.record("member.removed", tenant_id=tenant.id, actor=actor, entity_type="user", entity_id=user.id,
                 summary=f"{user.full_name} removed from the workspace")
