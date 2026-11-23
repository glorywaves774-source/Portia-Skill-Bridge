"""Accepting an invitation: join with an existing account, or create one."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, session, url_for
from flask_login import current_user, login_user
from flask_wtf import FlaskForm
from sqlalchemy import select

from app.access import SESSION_WORKSPACE_KEY
from app.auth.forms import AcceptInvitationForm
from app.extensions import db, limiter
from app.models import Role, User
from app.services import audit, limits, members
from app.tenancy import platform_scope

bp = Blueprint("invitations", __name__)


class ConfirmForm(FlaskForm):
    """No fields: just the CSRF token for a confirm button."""


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}{'•' * max(1, len(name) - 1)}@{domain}"


@bp.route("/invite/<token>", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def respond(token: str):
    inv = members.find_by_token(token)
    if inv is None or not inv.is_pending:
        state = inv.status() if inv else "invalid"
        return render_template("invitations/unavailable.html", state=state), 410 if inv else 404

    with platform_scope():
        tenant = inv.tenant
        tenant_name, roles = tenant.name, [Role.LABELS[r] for r in inv.roles]
        inviter = inv.invited_by.full_name if inv.invited_by else None
    existing = db.session.scalar(select(User).where(User.email == inv.email))
    ctx = dict(inv=inv, tenant_name=tenant_name, roles=roles, inviter=inviter, masked=_mask(inv.email))

    # Signed in already
    if current_user.is_authenticated:
        if User.normalise_email(current_user.email) != inv.email:
            return render_template("invitations/respond.html", mode="wrong_account", **ctx), 403
        form = ConfirmForm()  # carries the CSRF token
        if form.is_submitted():
            if not form.validate():
                return render_template("invitations/respond.html", mode="accept", form=form, **ctx), 400
            return _accept(inv, current_user._get_current_object())
        return render_template("invitations/respond.html", mode="accept", form=form, **ctx)

    # Has an account but isn't signed in
    if existing is not None:
        return render_template("invitations/respond.html", mode="sign_in", **ctx)

    # New to the platform: create the account and join in one step
    form = AcceptInvitationForm(data={"full_name": inv.full_name} if inv.full_name else None)
    if form.validate_on_submit():
        user = User(email=inv.email, full_name=form.full_name.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()
        with platform_scope():
            audit.record("auth.signup", tenant_id=inv.tenant_id, actor=user, entity_type="user", entity_id=user.id,
                         summary="Created account from invitation")
        return _accept(inv, user, new_account=True)
    return render_template("invitations/respond.html", mode="create", form=form, **ctx)


def _accept(inv, user: User, new_account: bool = False):
    try:
        tenant = members.accept(inv, user)
    except (members.MemberError, limits.UsageLimitExceeded) as exc:
        db.session.rollback()
        return render_template("invitations/unavailable.html", state="error", message=str(exc)), 409
    if new_account:
        session.clear()
        login_user(user)
    session[SESSION_WORKSPACE_KEY] = tenant.public_id
    flash(f"Welcome to {tenant.name}!", "success")
    return redirect(url_for("workspace.dashboard"))
