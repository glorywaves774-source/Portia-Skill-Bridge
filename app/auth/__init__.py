"""Sign in, sign up, sign out, password reset and workspace selection."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from app.access import SESSION_WORKSPACE_KEY, landing_url, safe_next
from wtforms.fields.choices import SelectChoice

import time

from app.auth.forms import CodeForm, ForgotPasswordForm, ResetPasswordForm, SignInForm, SignUpForm
from app.extensions import db, limiter
from app.models import PlanAudience, SubscriptionPlan, User
from app.services import audit, mailer
from app.services import auth as auth_service
from app.services.subscriptions import SubscriptionError
from app.services.tenants import TenantError
from app.tenancy import platform_scope, tenant_context

bp = Blueprint("auth", __name__)


def _login_limit():
    return current_app.config["LOGIN_RATE_LIMIT"]


def _signup_limit():
    return current_app.config["SIGNUP_RATE_LIMIT"]


def _reset_limit():
    return current_app.config["RESET_RATE_LIMIT"]


def _start_session(user: User, remember: bool) -> None:
    # A fresh session on every sign-in prevents session fixation.
    theme = session.get("theme")
    session.clear()
    if theme:
        session["theme"] = theme
    login_user(user, remember=remember)
    session.permanent = True


@bp.route("/signin", methods=["GET", "POST"])
@limiter.limit(_login_limit, methods=["POST"])
def signin():
    if current_user.is_authenticated:
        return redirect(landing_url(current_user))
    form = SignInForm()
    if request.method == "GET":
        form.next.data = request.args.get("next", "")
    error = None
    if form.validate_on_submit():
        result = auth_service.authenticate(form.email.data, form.password.data)
        if result.user and result.needs_code:
            theme = session.get("theme")
            session.clear()
            if theme:
                session["theme"] = theme
            session[PENDING_KEY] = {"uid": result.user.public_id, "at": time.time(), "tries": 0,
                                    "remember": bool(form.remember.data), "next": safe_next(form.next.data, "")}
            return redirect(url_for("auth.signin_code"))
        if result.user:
            _start_session(result.user, bool(form.remember.data))
            target = safe_next(form.next.data, "")
            return redirect(target or landing_url(result.user))
        error = result.error
    elif request.method == "POST":
        error = "Enter your email and password."
    return render_template("auth/signin.html", form=form, error=error), (401 if error else 200)


PENDING_KEY = "pending_2fa"
PENDING_SECONDS = 300
MAX_CODE_TRIES = 5


@bp.route("/signin/code", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def signin_code():
    """Second step: the code from the person's authenticator app (or a recovery code)."""
    from app.services import twofactor
    pending = session.get(PENDING_KEY)
    if not pending or time.time() - pending.get("at", 0) > PENDING_SECONDS:
        session.pop(PENDING_KEY, None)
        flash("Please sign in again." if pending else "Sign in first.", "info")
        return redirect(url_for("auth.signin"))
    with platform_scope():
        user = db.session.scalar(select(User).where(User.public_id == pending["uid"]))
    if user is None or not user.is_active_account or not user.two_factor_enabled or user.is_locked():
        session.pop(PENDING_KEY, None)
        return redirect(url_for("auth.signin"))
    form = CodeForm()
    error = None
    if form.validate_on_submit():
        with platform_scope():
            how = twofactor.verify(user, form.code.data)
            if how:
                user.register_successful_login()
                audit.record("auth.login", actor=user, entity_type="user", entity_id=user.id,
                             summary="Signed in with " + ("a recovery code" if how == "recovery" else "an app code"))
                db.session.commit()
                remember, target = pending.get("remember"), pending.get("next")
                _start_session(user, bool(remember))
                if how == "recovery":
                    left = twofactor.recovery_left(user)
                    flash(f"You used a recovery code. {left} left. Make new ones under My account if you're "
                          "running low.", "info")
                return redirect(target or landing_url(user))
            user.register_failed_login()
            audit.record("auth.2fa_failed", actor=user, entity_type="user", entity_id=user.id,
                         summary="Wrong sign-in code")
            db.session.commit()
        pending["tries"] = pending.get("tries", 0) + 1
        if pending["tries"] >= MAX_CODE_TRIES:
            session.pop(PENDING_KEY, None)
            flash("Too many wrong codes. Sign in again.", "error")
            return redirect(url_for("auth.signin"))
        session[PENDING_KEY] = pending
        error = "That code didn't work. Use the newest code in your app, or one of your recovery codes."
    elif request.method == "POST":
        error = "Enter the code from your app."
    return render_template("auth/signin_code.html", form=form, error=error), (401 if error else 200)


@bp.post("/signout")
@login_required
def signout():
    with platform_scope():
        audit.record("auth.logout", actor=current_user, entity_type="user", entity_id=current_user.id,
                     summary="Signed out")
        db.session.commit()
    logout_user()
    session.clear()
    flash("You have signed out.", "info")
    return redirect(url_for("auth.signin"))


def _plan_choices(kind: str):
    plans = db.session.scalars(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.audience == kind, SubscriptionPlan.is_public.is_(True),
               SubscriptionPlan.is_active.is_(True), SubscriptionPlan.is_custom_pricing.is_(False))
        .order_by(SubscriptionPlan.sort_order)
    ).all()
    return plans


@bp.route("/signup", methods=["GET", "POST"])
@limiter.limit(_signup_limit, methods=["POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(landing_url(current_user))
    form = SignUpForm()
    if request.method == "GET":
        kind = request.args.get("for")
        form.kind.data = kind if kind in PlanAudience.ALL else "organization"
        if request.args.get("billing") in ("monthly", "annual"):
            form.billing_cycle.data = request.args["billing"]
    kind = form.kind.data if form.kind.data in PlanAudience.ALL else "organization"
    org_plans, ind_plans = _plan_choices("organization"), _plan_choices("individual")
    plans = org_plans if kind == "organization" else ind_plans
    form.plan.choices = [SelectChoice(p.code, p.name) for p in plans]
    if request.method == "GET":
        wanted = request.args.get("plan")
        form.plan.data = wanted if wanted in {p.code for p in plans} else (
            next((p.code for p in plans if p.is_featured), plans[0].code if plans else None))

    error = None
    if form.validate_on_submit():
        try:
            user, tenant = auth_service.register(
                full_name=form.full_name.data, email=form.email.data, password=form.password.data,
                kind=kind, plan_code=form.plan.data, billing_cycle=form.billing_cycle.data,
                organization_name=form.organization_name.data, organization_type=form.organization_type.data or None,
                phone=form.phone.data, city=form.city.data,
            )
        except (auth_service.AuthError, TenantError, SubscriptionError) as exc:
            db.session.rollback()
            error = str(exc)
        else:
            _start_session(user, remember=False)
            session[SESSION_WORKSPACE_KEY] = tenant.public_id
            with tenant_context(tenant.id):
                sub = tenant.current_subscription
                if sub.is_trial:
                    flash(f"Welcome! Your {sub.plan.trial_days}-day free trial of {sub.plan.name} has started.",
                          "success")
                else:
                    flash(f"Welcome to PORTIA SkillBridge, {user.first_name}!", "success")
            return redirect(url_for("workspace.dashboard"))
    return render_template("auth/signup.html", form=form, error=error, kind=kind,
                           org_plans=org_plans, ind_plans=ind_plans), (400 if error else 200)


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit(_reset_limit, methods=["POST"])
def forgot_password():
    form = ForgotPasswordForm()
    sent = False
    if form.validate_on_submit():
        email = User.normalise_email(form.email.data)
        user = db.session.scalar(select(User).where(User.email == email))
        if user and user.is_active_account:
            link = url_for("auth.reset_password", token=auth_service.make_reset_token(user), _external=True)
            mailer.send(user.email, "Reset your PORTIA SkillBridge password",
                        f"Hello {user.first_name},\n\nUse this link within one hour to choose a new password:\n"
                        f"{link}\n\nIf you didn't ask for this, you can ignore this email; your password "
                        "has not changed.\n\nPORTIA SkillBridge")
            with platform_scope():
                audit.record("auth.reset_requested", actor=user, entity_type="user", entity_id=user.id,
                             summary="Password reset link sent")
                db.session.commit()
        sent = True  # same answer whether or not the account exists
    return render_template("auth/forgot.html", form=form, sent=sent)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit(_reset_limit, methods=["POST"])
def reset_password(token: str):
    user = auth_service.user_from_reset_token(token)
    if user is None:
        return render_template("auth/reset.html", form=None, invalid=True), 400
    form = ResetPasswordForm()
    if form.validate_on_submit():
        auth_service.reset_password(user, form.password.data)
        logout_user()
        session.clear()
        flash("Your password has been changed. Sign in with your new password.", "success")
        return redirect(url_for("auth.signin"))
    return render_template("auth/reset.html", form=form, invalid=False)


@bp.get("/workspaces")
@login_required
def choose_workspace():
    options = auth_service.workspaces_for(current_user)
    return render_template("auth/workspaces.html", options=options,
                           current=session.get(SESSION_WORKSPACE_KEY), next=request.args.get("next", ""))


@bp.post("/workspaces/<public_id>")
@login_required
def select_workspace(public_id: str):
    options = auth_service.workspaces_for(current_user)
    match = next((t for t, _ in options if t.public_id == public_id), None)
    if match is None:
        abort(404)  # not a member: behave as if it doesn't exist
    session[SESSION_WORKSPACE_KEY] = match.public_id
    return redirect(safe_next(request.form.get("next"), url_for("workspace.dashboard")))


@bp.get("/no-workspace")
@login_required
def no_workspace():
    if auth_service.workspaces_for(current_user):
        return redirect(landing_url(current_user))
    return render_template("auth/no_workspace.html")
