"""SaaS Super Administrator console: every workspace, plan, subscription and audit entry.

Only people with `User.is_platform_admin` can open it. All views run in
platform scope (see `app.tenancy`).
"""
from __future__ import annotations

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select

from app.extensions import db, limiter
from app.services import audit as audit_service
from app.tenancy import platform_scope
from app.models import (
    AuditLog,
    Invoice,
    InvoiceStatus,
    Membership,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    Tenant,
    TenantKind,
    TenantStatus,
)
from app.services import limits
from app.services.tenants import TenantError, reactivate_tenant, suspend_tenant
from app.access import require_platform_admin

bp = Blueprint("admin", __name__)


@bp.before_request
def _platform_admin_only():
    return require_platform_admin()


def _count(stmt) -> int:
    return db.session.scalar(stmt) or 0


@bp.get("/")
def overview():
    live = SubscriptionStatus.LIVE
    stats = {
        "tenants": _count(select(func.count(Tenant.id))),
        "organizations": _count(select(func.count(Tenant.id)).where(Tenant.kind == TenantKind.ORGANIZATION)),
        "individuals": _count(select(func.count(Tenant.id)).where(Tenant.kind == TenantKind.INDIVIDUAL)),
        "suspended": _count(select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.SUSPENDED)),
        "active_subs": _count(select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.ACTIVE)),
        "trials": _count(select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.TRIALING)),
        "past_due": _count(select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.PAST_DUE)),
        "plans": _count(select(func.count(SubscriptionPlan.id)).where(SubscriptionPlan.is_active.is_(True))),
    }
    # Monthly recurring revenue from active paid subscriptions (annual / 12).
    active = db.session.scalars(select(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE)).all()
    stats["mrr_minor"] = sum(s.price_minor // 12 if s.billing_cycle == "annual" else s.price_minor for s in active)

    by_plan = db.session.execute(
        select(SubscriptionPlan.name, func.count(Subscription.id))
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .where(Subscription.status.in_(live))
        .group_by(SubscriptionPlan.name, SubscriptionPlan.sort_order)
        .order_by(SubscriptionPlan.sort_order)
    ).all()
    recent_tenants = db.session.scalars(select(Tenant).order_by(Tenant.created_at.desc()).limit(6)).all()
    recent_events = db.session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)).all()
    return render_template("admin/overview.html", stats=stats, by_plan=by_plan,
                           recent_tenants=recent_tenants, recent_events=recent_events)


@bp.get("/tenants")
def tenants():
    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind") or ""
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Tenant.name.ilike(like) | Tenant.code.ilike(like) | Tenant.email.ilike(like))
    if kind in TenantKind.ALL:
        stmt = stmt.where(Tenant.kind == kind)
    rows = db.session.scalars(stmt.limit(200)).all()
    return render_template("admin/tenants.html", tenants=rows, q=q, kind=kind)


@bp.get("/tenants/<code>")
def tenant_detail(code: str):
    tenant = db.session.scalar(select(Tenant).where(Tenant.code == code)) or abort(404)
    members = db.session.scalars(select(Membership).where(Membership.tenant_id == tenant.id)).all()
    events = db.session.scalars(
        select(AuditLog).where(AuditLog.tenant_id == tenant.id).order_by(AuditLog.created_at.desc()).limit(20)
    ).all()
    invitations = [i for i in db.session.scalars(select(Invitation).where(Invitation.tenant_id == tenant.id)
                                                 .order_by(Invitation.created_at.desc()))]
    sub_form = SubscriptionActionForm()
    sub_form.plan.choices = _c([("", "—")]) + _plan_choices(tenant.kind)
    pay_form = ManualPaymentForm(prefix="pay")
    pay_form.plan.choices = _plan_choices(tenant.kind)
    sub = tenant.current_subscription
    if sub is not None and request.method == "GET":
        pay_form.plan.data, pay_form.billing_cycle.data = sub.plan.code, sub.billing_cycle
    invoices = db.session.scalars(select(Invoice).where(Invoice.tenant_id == tenant.id)
                                  .order_by(Invoice.created_at.desc()).limit(10)).all()
    return render_template("admin/tenant_detail.html", tenant=tenant, members=members, events=events,
                           usage=limits.summary(tenant), invitations=invitations, sub_form=sub_form,
                           pay_form=pay_form, invoices=invoices)


@bp.post("/tenants/<code>/payments")
def tenant_payment(code: str):
    from app.services import billing
    tenant = db.session.scalar(select(Tenant).where(Tenant.code == code)) or abort(404)
    form = ManualPaymentForm(prefix="pay")
    form.plan.choices = _plan_choices(tenant.kind)
    if not form.validate_on_submit():
        flash(" ".join(e for f in form for e in f.errors) or "Check the form.", "error")
        return redirect(url_for("admin.tenant_detail", code=code))
    try:
        inv = billing.record_manual_payment(tenant, _plan_by_code(form.plan.data), form.billing_cycle.data,
                                            amount_minor=int(round(form.amount.data * 100)), method=form.method.data,
                                            reference=form.reference.data, actor=current_user)
        db.session.commit()
        flash(f"Payment recorded as {inv.number}. Paid until {inv.period_end:%d %b %Y}.", "success")
    except (billing.BillingError, SubscriptionError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.tenant_detail", code=code))


@bp.get("/billing")
def billing_overview():
    from datetime import timedelta

    from app.models.base import utcnow
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = month_start.replace(month=1)
    paid = select(func.coalesce(func.sum(Invoice.amount_minor), 0)).where(Invoice.status == InvoiceStatus.PAID)
    totals = {
        "month": db.session.scalar(paid.where(Invoice.paid_at >= month_start)),
        "year": db.session.scalar(paid.where(Invoice.paid_at >= year_start)),
        "count_month": db.session.scalar(select(func.count()).select_from(Invoice).where(
            Invoice.status == InvoiceStatus.PAID, Invoice.paid_at >= month_start)),
        "failed_week": db.session.scalar(select(func.count()).select_from(Invoice).where(
            Invoice.status == InvoiceStatus.FAILED, Invoice.created_at >= now - timedelta(days=7))),
        "past_due": db.session.scalar(select(func.count()).select_from(Subscription).where(
            Subscription.status == SubscriptionStatus.PAST_DUE)),
    }
    status = request.args.get("status", "")
    stmt = select(Invoice).order_by(Invoice.created_at.desc()).limit(100)
    if status in InvoiceStatus.LABELS:
        stmt = stmt.where(Invoice.status == status)
    else:
        stmt = stmt.where(Invoice.status != InvoiceStatus.VOID)
    from app.services import paystack
    return render_template("admin/billing.html", totals=totals, invoices=db.session.scalars(stmt).all(),
                           status=status, statuses=InvoiceStatus.LABELS, online=paystack.enabled(),
                           backend=paystack.backend(), test_mode=paystack.is_test_mode())


@bp.post("/tenants/<code>/status")
def tenant_status(code: str):
    tenant = db.session.scalar(select(Tenant).where(Tenant.code == code)) or abort(404)
    action = request.form.get("action")
    try:
        if action == "suspend":
            suspend_tenant(tenant, reason=request.form.get("reason", ""), actor=current_user)
            flash(f"{tenant.name} has been suspended.", "success")
        elif action == "reactivate":
            reactivate_tenant(tenant, actor=current_user)
            flash(f"{tenant.name} has been reactivated.", "success")
        else:
            abort(400)
        db.session.commit()
    except TenantError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.tenant_detail", code=tenant.code))


@bp.get("/plans")
def plans():
    rows = db.session.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order)).all()
    counts = dict(db.session.execute(
        select(Subscription.plan_id, func.count(Subscription.id))
        .where(Subscription.status.in_(SubscriptionStatus.LIVE)).group_by(Subscription.plan_id)
    ).all())
    return render_template("admin/plans.html", plans=rows, counts=counts)


@bp.get("/audit")
def audit_log():
    rows = db.session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all()
    return render_template("admin/audit.html", events=rows)


# ---- Stage 3: creating workspaces, subscriptions, people and plans -------------------------

from decimal import Decimal  # noqa: E402

from app.blueprints.admin.forms import ManualPaymentForm, NewTenantForm, PlanForm, SubscriptionActionForm  # noqa: E402
from app.models import Invitation, User  # noqa: E402
from app.models.subscription import BillingCycle  # noqa: E402
from app.services import audit, mailer, members  # noqa: E402
from app.services import auth as auth_service  # noqa: E402
from app.services import subscriptions as subs  # noqa: E402
from app.services.tenants import create_tenant, owner_roles  # noqa: E402
from app.services.subscriptions import SubscriptionError  # noqa: E402
from app.auth.forms import _c  # noqa: E402


def _plan_choices(audience: str | None = None):
    stmt = select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True)).order_by(SubscriptionPlan.sort_order)
    if audience:
        stmt = stmt.where(SubscriptionPlan.audience == audience)
    return _c((p.code, f"{p.name} ({p.audience})") for p in db.session.scalars(stmt))


@bp.route("/tenants/new", methods=["GET", "POST"])
def tenant_new():
    form = NewTenantForm()
    form.plan.choices = _plan_choices()
    if form.validate_on_submit():
        plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == form.plan.data))
        try:
            tenant = create_tenant(name=form.name.data, kind=form.kind.data, plan_code=form.plan.data,
                                   billing_cycle=form.billing_cycle.data,
                                   organization_type=form.organization_type.data or None, city=form.city.data,
                                   email=form.owner_email.data, actor=current_user,
                                   with_trial=bool(form.start_trial.data))
            members.invite(tenant, email=form.owner_email.data, full_name=form.owner_name.data,
                           roles=owner_roles(tenant.kind, plan.code), make_owner=True, actor=current_user,
                           message=form.message.data)
            db.session.commit()
        except (TenantError, SubscriptionError, members.MemberError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
        else:
            flash(f"{tenant.name} created. An invitation has been emailed to {form.owner_email.data}.", "success")
            return redirect(url_for("admin.tenant_detail", code=tenant.code))
    return render_template("admin/tenant_new.html", form=form)


@bp.post("/tenants/<code>/subscription")
def tenant_subscription(code: str):
    tenant = db.session.scalar(select(Tenant).where(Tenant.code == code)) or abort(404)
    form = SubscriptionActionForm()
    form.plan.choices = _c([("", "—")]) + _plan_choices(tenant.kind)
    if not form.validate_on_submit():
        flash(" ".join(e for f in form for e in f.errors) or "Check the form.", "error")
        return redirect(url_for("admin.tenant_detail", code=code))
    sub = tenant.current_subscription
    live = sub is not None and sub.status in SubscriptionStatus.LIVE
    action, ref = form.action.data, form.reference.data or None
    try:
        if action == "start":
            if live:
                raise SubscriptionError("This workspace already has a live subscription.")
            plan = _plan_by_code(form.plan.data)
            subs.start_subscription(tenant, plan, form.billing_cycle.data or BillingCycle.MONTHLY, actor=current_user,
                                    with_trial=False)
            msg = f"Started {plan.name}; record the payment to activate it."
        elif not live:
            raise SubscriptionError("There is no live subscription. Use “Start a new subscription”.")
        elif action == "activate":
            subs.activate(sub, cycle=form.billing_cycle.data or None, actor=current_user, reference=ref)
            msg = f"Paid period started; runs until {sub.current_period_end:%d %b %Y}."
        elif action == "renew":
            subs.renew(sub, actor=current_user, reference=ref)
            msg = f"Extended until {sub.current_period_end:%d %b %Y}."
        elif action == "extend_trial":
            if not form.days.data:
                raise SubscriptionError("Enter how many days to add.")
            subs.extend_trial(sub, form.days.data, actor=current_user)
            msg = f"Trial now ends on {sub.trial_ends_at:%d %b %Y}."
        elif action == "change_plan":
            subs.change_plan(sub, _plan_by_code(form.plan.data), actor=current_user)
            msg = f"Plan changed to {sub.plan.name}."
        elif action in ("cancel", "cancel_now"):
            subs.cancel(sub, immediately=action == "cancel_now", actor=current_user, reason=form.note.data)
            msg = "Subscription cancelled." if action == "cancel_now" else "Subscription will end with this period."
        else:
            abort(400)
        db.session.commit()
        flash(msg, "success")
    except SubscriptionError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.tenant_detail", code=code))


def _plan_by_code(code: str | None) -> SubscriptionPlan:
    plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == (code or "")))
    if plan is None:
        raise SubscriptionError("Choose a plan.")
    return plan


@bp.post("/invitations/<public_id>/resend")
def resend_invitation(public_id: str):
    inv = db.session.scalar(select(Invitation).where(Invitation.public_id == public_id)) or abort(404)
    try:
        members.resend(inv, actor=current_user)
        db.session.commit()
        flash(f"Invitation sent again to {inv.email}.", "success")
    except members.MemberError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.tenant_detail", code=inv.tenant.code))


@bp.get("/users")
def users():
    q = (request.args.get("q") or "").strip()
    stmt = select(User).order_by(User.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.full_name.ilike(like) | User.email.ilike(like))
    if request.args.get("filter") == "admins":
        stmt = stmt.where(User.is_platform_admin.is_(True))
    elif request.args.get("filter") == "locked":
        stmt = stmt.where(User.locked_until.is_not(None))
    rows = db.session.scalars(stmt.limit(300)).all()
    counts = dict(db.session.execute(
        select(Membership.user_id, func.count(func.distinct(Membership.tenant_id))).group_by(Membership.user_id)
    ).all())
    return render_template("admin/users.html", users=rows, counts=counts, q=q, filter=request.args.get("filter", ""))


@bp.get("/users/<public_id>")
def user_detail(public_id: str):
    user = db.session.scalar(select(User).where(User.public_id == public_id)) or abort(404)
    ms = db.session.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    by_tenant: dict[int, dict] = {}
    for m in ms:
        row = by_tenant.setdefault(m.tenant_id, {"tenant": m.tenant, "roles": [], "active": False, "owner": False})
        row["roles"].append(m.role_label)
        row["active"] = row["active"] or m.is_active
        row["owner"] = row["owner"] or m.is_owner
    events = db.session.scalars(select(AuditLog).where(AuditLog.actor_user_id == user.id)
                                .order_by(AuditLog.created_at.desc()).limit(25)).all()
    return render_template("admin/user_detail.html", person=user, workspaces=list(by_tenant.values()), events=events)


@bp.post("/users/<public_id>/action")
def user_action(public_id: str):
    user = db.session.scalar(select(User).where(User.public_id == public_id)) or abort(404)
    action = request.form.get("action")
    is_self = user.id == current_user.id
    if action == "deactivate":
        if is_self:
            flash("You can't deactivate your own account.", "error")
            return redirect(url_for("admin.user_detail", public_id=public_id))
        user.is_active_account = False
        user.rotate_session_token()
        msg, summary = f"{user.full_name}'s account is deactivated and signed out everywhere.", "Account deactivated"
    elif action == "reactivate":
        user.is_active_account = True
        msg, summary = f"{user.full_name}'s account is active again.", "Account reactivated"
    elif action == "unlock":
        user.locked_until = None
        user.failed_login_count = 0
        msg, summary = f"{user.full_name} can sign in again.", "Sign-in lock cleared"
    elif action == "reset_2fa":
        if not user.two_factor_enabled:
            flash("Two-step sign-in is already off for this person.", "info")
            return redirect(url_for("admin.user_detail", public_id=public_id))
        from app.services import twofactor
        twofactor.disable(user, "", actor=current_user)
        msg, summary = (f"Two-step sign-in is off for {user.full_name}. Ask them to set it up again.",
                        "Two-step sign-in switched off by administrator (lost phone)")
    elif action == "send_reset":
        link = url_for("auth.reset_password", token=auth_service.make_reset_token(user), _external=True)
        mailer.send(user.email, "Reset your PORTIA SkillBridge password",
                    f"Hello {user.first_name},\n\nThe PORTIA SkillBridge team sent you this link to choose a new "
                    f"password. It works for one hour:\n{link}\n\nPORTIA SkillBridge")
        msg, summary = f"Password reset link emailed to {user.email}.", "Password reset link sent by administrator"
    elif action in ("grant_admin", "revoke_admin"):
        if action == "revoke_admin":
            if is_self:
                flash("You can't remove your own Super Administrator access.", "error")
                return redirect(url_for("admin.user_detail", public_id=public_id))
            remaining = db.session.scalar(select(func.count(User.id)).where(User.is_platform_admin.is_(True),
                                                                             User.is_active_account.is_(True)))
            if remaining <= 1:
                flash("The platform must keep at least one Super Administrator.", "error")
                return redirect(url_for("admin.user_detail", public_id=public_id))
        user.is_platform_admin = action == "grant_admin"
        if action == "revoke_admin":
            user.rotate_session_token()
        msg = f"{user.full_name} {'is now' if user.is_platform_admin else 'is no longer'} a Super Administrator."
        summary = "Super Administrator access " + ("granted" if user.is_platform_admin else "removed")
    else:
        abort(400)
    audit.record(f"platform.user_{action}", actor=current_user, entity_type="user", entity_id=user.id,
                 summary=f"{summary}: {user.email}")
    db.session.commit()
    flash(msg, "success")
    return redirect(url_for("admin.user_detail", public_id=public_id))


@bp.route("/plans/<code>/edit", methods=["GET", "POST"])
def plan_edit(code: str):
    plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code)) or abort(404)
    if request.method == "GET":
        form = PlanForm(obj=plan, data={
            "monthly_price": Decimal(plan.monthly_price_minor) / 100,
            "annual_price": Decimal(plan.annual_price_minor) / 100,
            "features": "\n".join(plan.features or []),
        })
    else:
        form = PlanForm()
    if form.validate_on_submit():
        before = plan.to_dict()
        plan.name, plan.tagline = form.name.data, form.tagline.data or None
        plan.monthly_price_minor = int(round(form.monthly_price.data * 100))
        plan.annual_price_minor = int(round(form.annual_price.data * 100))
        plan.trial_days = form.trial_days.data
        for f in ("max_learners", "max_staff", "max_courses", "max_programmes", "max_vacancies", "storage_mb"):
            setattr(plan, f, getattr(form, f).data)
        plan.features = [line.strip() for line in (form.features.data or "").splitlines() if line.strip()]
        plan.is_public, plan.is_active, plan.is_featured = (bool(form.is_public.data), bool(form.is_active.data),
                                                            bool(form.is_featured.data))
        after = plan.to_dict()
        changed = sorted(k for k in after if after[k] != before[k])
        audit.record("platform.plan_updated", actor=current_user, entity_type="plan", entity_id=plan.id,
                     summary=f"{plan.name}: changed {', '.join(changed) or 'nothing'}")
        db.session.commit()
        flash(f"{plan.name} saved. New prices apply to new subscriptions and the next renewal.", "success")
        return redirect(url_for("admin.plans"))
    return render_template("admin/plan_edit.html", form=form, plan=plan)


# ---- System status and backups -----------------------------------------------------------------------

@bp.get("/system")
def system_status():
    from app.services import system
    results = system.checks()
    return render_template("admin/system.html", checks=results,
                           bad=sum(1 for c in results if c["state"] == "bad"),
                           warn=sum(1 for c in results if c["state"] == "warn"))


@bp.get("/backups")
def backups_page():
    from app.models import JobRun
    from app.services import backups
    try:
        files, error = backups.listing(), None
    except Exception as exc:  # noqa: BLE001
        files, error = [], str(exc)
    return render_template("admin/backups.html", files=files, error=error, run=db.session.get(JobRun, "backup"),
                           keyed=bool(current_app.config.get("BACKUP_ENCRYPTION_KEY")),
                           retention=current_app.config.get("BACKUP_RETENTION_DAYS", 30))


@bp.post("/backups")
@limiter.limit("3 per minute")
def backup_now():
    from app import jobs
    from app.services import backups
    db.session.commit()
    try:
        result = jobs.run("backup", backups.create)
        flash(f"Backup made: {result['key'].split('/')[-1]} ({result['rows']} rows).", "success")
    except backups.BackupError as exc:
        flash(str(exc), "error")
    except Exception:  # noqa: BLE001 — shown on the page; details are in the log
        flash("The backup failed. See the System status page and the server log.", "error")
    with platform_scope():
        audit_service.record("system.backup", actor=current_user, entity_type="system", summary="Backup run by hand")
        db.session.commit()
    return redirect(url_for("admin.backups_page"))


@bp.get("/backups/<name>")
def backup_download(name: str):
    from app.services import backups
    if "/" in name or not name.endswith(".psb"):
        abort(404)
    try:
        blob = backups.read(name)
    except backups.BackupError:
        abort(404)
    with platform_scope():
        audit_service.record("system.backup_downloaded", actor=current_user, entity_type="system",
                     summary=f"Downloaded encrypted backup {name}")
        db.session.commit()
    resp = Response(blob, mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
