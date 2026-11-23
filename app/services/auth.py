"""Signing in, signing up and password resets."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import Membership, SubscriptionPlan, Tenant, TenantKind, User
from app.services import audit
from app.services.tenants import create_tenant
from app.tenancy import platform_scope

# Used to spend the same time on unknown emails as on real ones (no account enumeration by timing).
_DUMMY_HASH = "scrypt:32768:8:1$k1mA9cA1oGd1Vx2n$" + "0" * 128

RESET_SALT = "password-reset"
RESET_MAX_AGE = 60 * 60  # one hour


class AuthError(ValueError):
    pass


@dataclass
class LoginResult:
    user: User | None
    error: str | None = None
    needs_code: bool = False   # password right; two-step sign-in code still needed


GENERIC_LOGIN_ERROR = "That email and password don't match an account."


def authenticate(email: str, password: str) -> LoginResult:
    email = User.normalise_email(email)
    user = db.session.scalar(select(User).where(User.email == email)) if email else None
    if user is None:
        check_password_hash(_DUMMY_HASH, password or "")
        _audit_login("auth.login_failed", None, email)
        return LoginResult(None, GENERIC_LOGIN_ERROR)
    if user.is_locked():
        _audit_login("auth.login_locked", user, email)
        return LoginResult(None, "Too many failed attempts. Try again in 15 minutes or reset your password.")
    if not user.check_password(password or ""):
        user.register_failed_login()
        _audit_login("auth.login_failed", user, email)
        return LoginResult(None, GENERIC_LOGIN_ERROR)
    if not user.is_active_account:
        _audit_login("auth.login_inactive", user, email)
        return LoginResult(None, "This account has been deactivated. Contact your administrator.")
    if user.two_factor_enabled:
        _audit_login("auth.login_password_ok", user, email)
        return LoginResult(user, needs_code=True)
    user.register_successful_login()
    _audit_login("auth.login", user, email)
    return LoginResult(user)


def _audit_login(action: str, user: User | None, email: str) -> None:
    with platform_scope():
        audit.record(action, actor=user, actor_label=email or "unknown", entity_type="user",
                     entity_id=getattr(user, "id", None),
                     summary={"auth.login": "Signed in", "auth.login_failed": "Failed sign-in",
                              "auth.login_password_ok": "Password accepted; waiting for the sign-in code",
                              "auth.2fa_failed": "Wrong sign-in code",
                              "auth.login_locked": "Sign-in blocked: account locked",
                              "auth.login_inactive": "Sign-in blocked: account deactivated"}[action])
        db.session.commit()


def register(*, full_name: str, email: str, password: str, kind: str, plan_code: str,
             billing_cycle: str = "monthly", organization_name: str | None = None,
             organization_type: str | None = None, phone: str | None = None, city: str | None = None,
             country: str = "GH") -> tuple[User, Tenant]:
    """Self-service sign-up: a person plus their first workspace, on a trial where the plan has one."""
    email = User.normalise_email(email)
    if db.session.scalar(select(User.id).where(User.email == email)):
        raise AuthError("An account with this email already exists. Sign in instead.")
    plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code))
    if plan is None or not plan.is_public or not plan.is_active or plan.is_custom_pricing:
        raise AuthError("Please choose one of the listed plans.")
    if plan.audience != kind:
        raise AuthError("That plan is not available for this type of account.")

    user = User(email=email, full_name=full_name.strip(), phone=(phone or None))
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    name = organization_name.strip() if kind == TenantKind.ORGANIZATION else user.full_name
    with platform_scope():
        tenant = create_tenant(name=name, kind=kind, plan_code=plan_code, billing_cycle=billing_cycle,
                               organization_type=organization_type, email=email, phone=phone, city=city,
                               country=country, owner=user, actor=user)
        audit.record("auth.signup", tenant_id=tenant.id, actor=user, entity_type="user", entity_id=user.id,
                     summary=f"Signed up on {plan.name}")
        db.session.commit()
    return user, tenant


# ---- Password reset -----------------------------------------------------------

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=RESET_SALT)


def _fingerprint(user: User) -> str:
    # Changes whenever the password changes, so a token works only once.
    return hashlib.sha256((user.password_hash or "").encode()).hexdigest()[:16]


def make_reset_token(user: User) -> str:
    return _serializer().dumps({"u": user.public_id, "f": _fingerprint(user)})


def user_from_reset_token(token: str, max_age: int = RESET_MAX_AGE) -> User | None:
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.scalar(select(User).where(User.public_id == data.get("u")))
    if user is None or not user.is_active_account or data.get("f") != _fingerprint(user):
        return None
    return user


def reset_password(user: User, new_password: str) -> None:
    user.set_password(new_password)  # also rotates the session token: signs out everywhere
    user.failed_login_count = 0
    user.locked_until = None
    with platform_scope():
        audit.record("auth.password_reset", actor=user, entity_type="user", entity_id=user.id,
                     summary="Password reset by email link")
        db.session.commit()


def change_password(user: User, current: str, new_password: str) -> None:
    if not user.check_password(current):
        raise AuthError("Your current password is not correct.")
    if current == new_password:
        raise AuthError("Choose a password different from your current one.")
    user.set_password(new_password)
    with platform_scope():
        audit.record("auth.password_changed", actor=user, entity_type="user", entity_id=user.id,
                     summary="Password changed")
        db.session.commit()


# ---- Workspaces a person can open ------------------------------------------------

def workspaces_for(user: User) -> list[tuple[Tenant, list[Membership]]]:
    """Every workspace where this person has an active membership, with their memberships there."""
    with platform_scope():
        rows = db.session.execute(
            select(Tenant, Membership)
            .join(Membership, Membership.tenant_id == Tenant.id)
            .where(Membership.user_id == user.id, Membership.is_active.is_(True))
            .order_by(Tenant.name)
        ).all()
    grouped: dict[int, tuple[Tenant, list[Membership]]] = {}
    for tenant, membership in rows:
        grouped.setdefault(tenant.id, (tenant, []))[1].append(membership)
    return list(grouped.values())
