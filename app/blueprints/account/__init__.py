"""The signed-in person's own account: profile, password, sessions."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user

from app.access import load_workspace
from app.auth.forms import ChangePasswordForm, CodeForm, ConfirmPasswordForm, ProfileForm
from app.extensions import db
from app.services import audit
from app.services import auth as auth_service
from app.services.auth import workspaces_for
from app.tenancy import platform_scope

bp = Blueprint("account", __name__)


@bp.before_request
@login_required
def _context():
    # Show the workspace menu if one is active, but the account works without one.
    return load_workspace(required=False)


def _extras() -> dict:
    from app.models import NotificationCategory
    from app.services import twofactor
    return {"categories": NotificationCategory.LABELS, "codes_form": ConfirmPasswordForm(prefix="codes"),
            "off_form": ConfirmPasswordForm(prefix="off"), "recovery_left": twofactor.recovery_left(current_user)}


@bp.route("/", methods=["GET", "POST"])
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        current_user.full_name = form.full_name.data.strip()
        current_user.phone = (form.phone.data or "").strip() or None
        with platform_scope():
            audit.record("account.updated", actor=current_user, entity_type="user", entity_id=current_user.id,
                         summary="Updated own profile")
            db.session.commit()
        flash("Your details have been saved.", "success")
        return redirect(url_for("account.profile"))
    return render_template("account/profile.html", form=form, password_form=ChangePasswordForm(), **_extras(),
                           workspaces=workspaces_for(current_user))


@bp.post("/password")
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            auth_service.change_password(current_user, form.current_password.data, form.password.data)
        except auth_service.AuthError as exc:
            form.current_password.errors.append(str(exc))
        else:
            # Other devices are signed out (session token rotated); keep this one signed in.
            workspace = session.get("workspace")
            session.clear()
            login_user(current_user._get_current_object())
            if workspace:
                session["workspace"] = workspace
            flash("Password changed. Other devices have been signed out.", "success")
            return redirect(url_for("account.profile"))
    return render_template("account/profile.html", form=ProfileForm(obj=current_user), password_form=form, **_extras(),
                           workspaces=workspaces_for(current_user)), 400


@bp.post("/sign-out-everywhere")
def sign_out_everywhere():
    current_user.rotate_session_token()
    with platform_scope():
        audit.record("auth.logout_all", actor=current_user, entity_type="user", entity_id=current_user.id,
                     summary="Signed out of all devices")
        db.session.commit()
    session.clear()
    flash("You have been signed out of every device.", "info")
    return redirect(url_for("auth.signin"))


# ---- Email preferences ------------------------------------------------------------------------------

@bp.post("/notifications")
def notification_prefs():
    from app.models import NotificationCategory
    prefs = {c: request.form.get(f"email-{c}") == "1" for c in NotificationCategory.ALL}
    with platform_scope():
        current_user.email_prefs = prefs
        db.session.commit()
    flash("Email choices saved. You'll still see everything under the bell.", "success")
    return redirect(url_for("account.profile") + "#notifications")


# ---- Two-step sign-in -----------------------------------------------------------------------------

SETUP_KEY = "2fa_setup_secret"


@bp.route("/two-step", methods=["GET", "POST"])
def two_step_setup():
    from app.services import twofactor
    if current_user.two_factor_enabled:
        return redirect(url_for("account.profile") + "#two-step")
    secret = session.get(SETUP_KEY)
    if not secret or request.method == "GET":
        secret = session[SETUP_KEY] = twofactor.new_secret()
    form = CodeForm()
    error = None
    if form.validate_on_submit():
        try:
            with platform_scope():
                codes = twofactor.enable(current_user, secret, form.code.data)
                db.session.commit()
            session.pop(SETUP_KEY, None)
            return render_template("account/recovery_codes.html", codes=codes, first_time=True)
        except twofactor.TwoFactorError as exc:
            error = str(exc)
    uri = twofactor.provisioning_uri(current_user, secret)
    return render_template("account/two_step.html", form=form, qr=twofactor.qr_data_uri(uri),
                           secret=" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), error=error), \
        (400 if error else 200)


@bp.post("/two-step/codes")
def two_step_codes():
    from app.services import twofactor
    form = ConfirmPasswordForm(prefix="codes")
    if form.validate_on_submit():
        try:
            with platform_scope():
                codes = twofactor.regenerate_codes(current_user, form.password.data)
                db.session.commit()
            return render_template("account/recovery_codes.html", codes=codes, first_time=False)
        except twofactor.TwoFactorError as exc:
            flash(str(exc), "error")
    return redirect(url_for("account.profile") + "#two-step")


@bp.post("/two-step/off")
def two_step_off():
    from app.services import twofactor
    form = ConfirmPasswordForm(prefix="off")
    if form.validate_on_submit():
        try:
            with platform_scope():
                twofactor.disable(current_user, form.password.data)
                db.session.commit()
            workspace = session.get("workspace")  # stay signed in here; other devices were signed out
            user = current_user._get_current_object()
            session.clear()
            login_user(user)
            if workspace:
                session["workspace"] = workspace
            flash("Two-step sign-in is off. Other devices have been signed out.", "success")
        except twofactor.TwoFactorError as exc:
            flash(str(exc), "error")
    return redirect(url_for("account.profile") + "#two-step")
