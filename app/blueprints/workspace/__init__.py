"""A signed-in person's workspace (/app). Everything here runs inside tenant isolation."""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from app.access import can, load_workspace, permission_required
from app.auth.forms import InviteForm, RolesForm, WorkspaceSettingsForm, role_choices
from app.extensions import db
from app.models import AuditLog, Invitation, Membership, OrganizationType, Role, SubscriptionPlan, User
from app.rbac import PERMISSIONS
from app.services import audit, limits, members

bp = Blueprint("workspace", __name__)


@bp.before_request
@login_required
def _workspace():
    return load_workspace(required=True)


@bp.get("/")
@permission_required("workspace.view")
def dashboard():
    ws = g.workspace
    sub = ws.current_subscription
    people = None
    if "members.view" in g.permissions:
        rows = db.session.execute(
            select(Membership.role, func.count(func.distinct(Membership.user_id)))
            .where(Membership.is_active.is_(True)).group_by(Membership.role)
        ).all()
        people = {
            "total": db.session.scalar(select(func.count(func.distinct(Membership.user_id)))
                                       .where(Membership.is_active.is_(True))) or 0,
            "by_role": sorted(((Role.LABELS.get(r, r), n) for r, n in rows), key=lambda x: -x[1]),
        }
    recent = None
    if "audit.view" in g.permissions:
        recent = db.session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(5)).all()
    learner_stats = _learner_stats() if "learners.view" in g.permissions else None
    job_stats = None
    if "vacancies.view" in g.permissions:
        from app.services import employment
        scope = employment.scope_for(current_user, ws, g.permissions)
        job_stats = employment.stats(scope) if (not scope.restricted or scope.employer) else None
    return render_template(
        "workspace/dashboard.html", ws=ws, sub=sub, people=people, recent=recent, learner_stats=learner_stats,
        job_stats=job_stats,
        usage=limits.summary(ws) if "billing.view" in g.permissions else None,
        role_labels=[Role.LABELS.get(r, r) for r in g.roles],
    )


def _person_or_404(public_id: str) -> User:
    user = db.session.scalar(select(User).where(User.public_id == public_id))
    # Only people who are (or were) in this workspace exist here.
    if user is None or not members.memberships_of(g.workspace, user):
        abort(404)
    return user


def _learner_stats() -> dict:
    from datetime import date

    from app.models import Learner, LearnerStatus
    counted = Learner.status.in_(LearnerStatus.COUNTED)
    total = db.session.scalar(select(func.count(Learner.id)).where(counted)) or 0
    by_gender = dict(db.session.execute(select(Learner.gender, func.count(Learner.id)).where(counted)
                                        .group_by(Learner.gender)).all())
    month_start = date.today().replace(day=1)
    regions = db.session.execute(
        select(Learner.region, func.count(Learner.id)).where(counted, Learner.region.is_not(None))
        .group_by(Learner.region).order_by(func.count(Learner.id).desc()).limit(5)).all()
    return {
        "total": total,
        "active": db.session.scalar(select(func.count(Learner.id)).where(Learner.status == LearnerStatus.ACTIVE)) or 0,
        "female": by_gender.get("female", 0),
        "female_pct": round(by_gender.get("female", 0) * 100 / total) if total else 0,
        "pwd": db.session.scalar(select(func.count(Learner.id)).where(counted, Learner.has_disability.is_(True))) or 0,
        "new_this_month": db.session.scalar(select(func.count(Learner.id)).where(counted,
                                                                                Learner.registered_on >= month_start)) or 0,
        "regions": regions,
    }


@bp.get("/people")
@permission_required("members.view")
def people():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    stmt = (select(User, Membership).join(Membership, Membership.user_id == User.id)
            .order_by(User.full_name, Membership.role))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.full_name.ilike(like) | User.email.ilike(like))
    grouped: dict[int, dict] = {}
    for user, m in db.session.execute(stmt).all():
        row = grouped.setdefault(user.id, {"user": user, "roles": [], "owner": False, "active": False,
                                           "since": m.created_at})
        row["roles"].append(m.role_label)
        row["owner"] = row["owner"] or m.is_owner
        row["active"] = row["active"] or (m.is_active and user.is_active_account)
        row["since"] = min(row["since"], m.created_at)
    rows = list(grouped.values())
    if status == "active":
        rows = [r for r in rows if r["active"]]
    elif status == "inactive":
        rows = [r for r in rows if not r["active"]]
    invitations = []
    form = None
    if can("members.manage"):
        invitations = [i for i in db.session.scalars(select(Invitation).order_by(Invitation.created_at.desc()))
                       if i.is_pending]
        form = InviteForm()
        form.roles.choices = role_choices(g.workspace.kind)
    return render_template("workspace/people.html", rows=rows, q=q, status=status, invitations=invitations,
                           form=form, staff=limits_row("staff"), learners=limits_row("learners"))


def limits_row(resource: str) -> dict:
    return {"used": limits.usage(g.workspace, resource), "limit": limits.limit(g.workspace, resource)}


@bp.post("/people/invite")
@permission_required("members.manage")
def invite():
    form = InviteForm()
    form.roles.choices = role_choices(g.workspace.kind)
    if form.validate_on_submit():
        try:
            inv, _ = members.invite(g.workspace, email=form.email.data, roles=form.roles.data,
                                    full_name=form.full_name.data, message=form.message.data, actor=current_user)
            db.session.commit()
            flash(f"Invitation sent to {inv.email}.", "success")
        except (members.MemberError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    else:
        errors = [e for f in form for e in f.errors]
        flash(" ".join(errors) or "Check the invitation details.", "error")
    return redirect(url_for("workspace.people"))


def _invitation_or_404(public_id: str) -> Invitation:
    return db.session.scalar(select(Invitation).where(Invitation.public_id == public_id)) or abort(404)


@bp.post("/invitations/<public_id>/resend")
@permission_required("members.manage")
def resend_invitation(public_id: str):
    inv = _invitation_or_404(public_id)
    try:
        members.resend(inv, actor=current_user)
        db.session.commit()
        flash(f"Invitation sent again to {inv.email}.", "success")
    except members.MemberError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("workspace.people"))


@bp.post("/invitations/<public_id>/revoke")
@permission_required("members.manage")
def revoke_invitation(public_id: str):
    inv = _invitation_or_404(public_id)
    try:
        members.revoke(inv, actor=current_user)
        db.session.commit()
        flash(f"Invitation for {inv.email} cancelled.", "success")
    except members.MemberError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("workspace.people"))


@bp.get("/people/<public_id>")
@permission_required("members.view")
def person(public_id: str):
    user = _person_or_404(public_id)
    ms = members.memberships_of(g.workspace, user)
    form = None
    if can("members.manage"):
        form = RolesForm(data={"roles": [m.role for m in ms]})
        form.roles.choices = role_choices(g.workspace.kind)
    events = db.session.scalars(
        select(AuditLog).where((AuditLog.actor_user_id == user.id) |
                               ((AuditLog.entity_type == "user") & (AuditLog.entity_id == str(user.id))))
        .order_by(AuditLog.created_at.desc()).limit(20)
    ).all()
    return render_template("workspace/person.html", person=user, memberships=ms, form=form, events=events,
                           active=any(m.is_active for m in ms) and user.is_active_account,
                           owner=any(m.is_owner for m in ms), is_self=user.id == current_user.id)


@bp.post("/people/<public_id>/roles")
@permission_required("members.manage")
def person_roles(public_id: str):
    user = _person_or_404(public_id)
    form = RolesForm()
    form.roles.choices = role_choices(g.workspace.kind)
    if form.validate_on_submit():
        try:
            members.set_roles(g.workspace, user, form.roles.data, actor=current_user)
            db.session.commit()
            flash(f"Roles updated for {user.full_name}.", "success")
        except (members.MemberError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    else:
        flash(" ".join(e for f in form for e in f.errors) or "Choose at least one role.", "error")
    return redirect(url_for("workspace.person", public_id=public_id))


@bp.post("/people/<public_id>/status")
@permission_required("members.manage")
def person_status(public_id: str):
    user = _person_or_404(public_id)
    action = request.form.get("action")
    try:
        if action == "deactivate":
            members.deactivate(g.workspace, user, actor=current_user)
            message = f"{user.full_name} can no longer open this workspace."
        elif action == "reactivate":
            members.reactivate(g.workspace, user, actor=current_user)
            message = f"{user.full_name} can use this workspace again."
        elif action == "remove":
            members.remove(g.workspace, user, actor=current_user)
            db.session.commit()
            flash(f"{user.full_name} has been removed from the workspace.", "success")
            return redirect(url_for("workspace.people"))
        else:
            abort(400)
        db.session.commit()
        flash(message, "success")
    except (members.MemberError, limits.UsageLimitExceeded) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("workspace.person", public_id=public_id))


@bp.get("/subscription")
@permission_required("billing.view")
def subscription():
    ws = g.workspace
    plans = db.session.scalars(
        select(SubscriptionPlan).where(SubscriptionPlan.audience == ws.kind, SubscriptionPlan.is_public.is_(True),
                                       SubscriptionPlan.is_active.is_(True)).order_by(SubscriptionPlan.sort_order)
    ).all()
    from app.models import Invoice, InvoiceStatus
    from app.services import billing, paystack
    recent = db.session.scalars(select(Invoice).where(Invoice.status != InvoiceStatus.VOID)
                                .order_by(Invoice.created_at.desc()).limit(5)).all()
    return render_template("workspace/subscription.html", ws=ws, sub=ws.current_subscription,
                           live=billing.live_subscription(ws), usage=limits.summary(ws), plans=plans,
                           method=billing.saved_method(ws), invoices=recent, online=paystack.enabled(),
                           test_mode=paystack.is_test_mode())


@bp.get("/activity")
@permission_required("audit.view")
def activity():
    rows = db.session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all()
    return render_template("workspace/activity.html", events=rows)


@bp.route("/settings", methods=["GET", "POST"])
@permission_required("workspace.settings")
def settings():
    ws = g.workspace
    form = WorkspaceSettingsForm(obj=ws)
    if ws.is_individual:
        del form.organization_type
    if form.validate_on_submit():
        before = {f.name: getattr(ws, f.name) for f in form if f.name != "csrf_token"}
        for field in form:
            if field.name == "csrf_token":
                continue
            value = field.data.strip() if isinstance(field.data, str) else field.data
            setattr(ws, field.name, value or None if field.name != "name" else value)
        if not ws.is_individual and ws.organization_type not in OrganizationType.ALL:
            ws.organization_type = OrganizationType.OTHER
        changed = sorted(k for k, v in before.items() if getattr(ws, k) != v)
        if changed:
            audit.record("workspace.updated", tenant_id=ws.id, actor=current_user, entity_type="tenant",
                         entity_id=ws.id, summary="Changed " + ", ".join(c.replace("_", " ") for c in changed))
            db.session.commit()
            flash("Workspace details saved.", "success")
        else:
            flash("Nothing to save: no changes.", "info")
        return redirect(url_for("workspace.settings"))
    return render_template("workspace/settings.html", form=form, ws=ws)


@bp.get("/access")
@permission_required("workspace.view")
def my_access():
    """What this person can do here, in plain words."""
    return render_template("workspace/access.html",
                           allowed=[(p, PERMISSIONS[p]) for p in sorted(g.permissions)],
                           role_labels=[Role.LABELS.get(r, r) for r in g.roles])
