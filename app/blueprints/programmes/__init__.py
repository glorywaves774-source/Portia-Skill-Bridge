"""Training programmes (/app/programmes): trainers, enrolment, sessions and attendance."""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.access import can, load_workspace, permission_required
from app.auth.forms import _c
from app.blueprints.programmes.forms import (
    EnrolmentStatusForm,
    ProgrammeForm,
    ProgrammeStatusForm,
    SessionForm,
    TrainerForm,
)
from app.extensions import db
from app.models import (
    AttendanceStatus,
    Enrolment,
    EnrolmentStatus,
    Learner,
    LearnerStatus,
    Programme,
    ProgrammeStatus,
    ProgrammeTrainer,
    TrainingSession,
    User,
)
from app.services import audit, limits
from app.services import training as svc

bp = Blueprint("programmes", __name__)
TABS = ("overview", "learners", "sessions", "courses")


@bp.before_request
@login_required
def _workspace():
    return load_workspace(required=True)


def _programme(public_id: str) -> Programme:
    p = db.session.scalar(select(Programme).where(Programme.public_id == public_id))
    if p is None:
        abort(404)
    return p


def _errors(form) -> str:
    return " ".join(e for f in form for e in f.errors) or "Check the form and try again."


def _back(p: Programme, tab: str = "overview"):
    return redirect(url_for("programmes.detail", public_id=p.public_id, tab=tab))


@bp.get("/")
@permission_required("programmes.view")
def index():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    stmt = select(Programme).order_by(Programme.start_date.desc().nullslast(), Programme.code.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Programme.name.ilike(like) | Programme.code.ilike(like) | Programme.trade.ilike(like))
    if status in ProgrammeStatus.LABELS:
        stmt = stmt.where(Programme.status == status)
    elif status != "all":
        stmt = stmt.where(Programme.status != ProgrammeStatus.CANCELLED)
    programmes = db.session.scalars(stmt.options(selectinload(Programme.enrolments))).all()
    rates = {p.id: svc.overall_rate(p) for p in programmes}
    return render_template("programmes/index.html", programmes=programmes, rates=rates, q=q, status=status,
                           statuses=ProgrammeStatus.LABELS,
                           usage={"used": limits.usage(g.workspace, "programmes"),
                                  "limit": limits.limit(g.workspace, "programmes")})


def _data(form: ProgrammeForm) -> dict:
    return {k: form[k].data for k in svc.EDITABLE}


@bp.route("/new", methods=["GET", "POST"])
@permission_required("programmes.manage")
def new():
    form = ProgrammeForm()
    if form.validate_on_submit():
        try:
            p = svc.create_programme(g.workspace, _data(form), actor=current_user)
            db.session.commit()
            flash(f"Programme {p.code} created. Next: assign trainers and enrol learners.", "success")
            return _back(p)
        except (svc.TrainingError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("programmes/form.html", form=form, programme=None)


@bp.route("/<public_id>/edit", methods=["GET", "POST"])
@permission_required("programmes.manage")
def edit(public_id):
    p = _programme(public_id)
    form = ProgrammeForm(obj=p)
    if form.validate_on_submit():
        try:
            changed = svc.update_programme(p, _data(form), actor=current_user)
            db.session.commit()
            flash("Saved." if changed else "Nothing to save: no changes.", "success" if changed else "info")
            return _back(p)
        except svc.TrainingError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("programmes/form.html", form=form, programme=p)


@bp.get("/<public_id>")
@permission_required("programmes.view")
def detail(public_id):
    p = _programme(public_id)
    tab = request.args.get("tab", "overview")
    tab = tab if tab in TABS else "overview"
    manage = can("programmes.manage")
    forms = {}
    if manage:
        trainers = svc.eligible_trainers(g.workspace)
        tf = TrainerForm(prefix="tr")
        tf.user.choices = _c([("", "Choose…")] + [(u.public_id, u.full_name) for u in trainers
                                                  if u.id not in p.trainer_ids()])
        sf = SessionForm(prefix="ses")
        sf.trainer.choices = _c([("", "—")] + [(t.user.public_id, t.user.full_name) for t in p.trainers])
        forms = {"trainer": tf, "session": sf, "status": ProgrammeStatusForm(prefix="st", data={"status": p.status}),
                 "enrolment": EnrolmentStatusForm(prefix="en")}
    rates = svc.attendance_rates(p)
    return render_template("programmes/detail.html", p=p, tab=tab, tabs=TABS, manage=manage, forms=forms,
                           rates=rates, overall=svc.overall_rate(p),
                           can_attend=svc.can_take_attendance(current_user, p, g.permissions),
                           enrolment_labels=EnrolmentStatus.LABELS)


@bp.post("/<public_id>/status")
@permission_required("programmes.manage")
def status(public_id):
    p = _programme(public_id)
    form = ProgrammeStatusForm(prefix="st")
    if form.validate_on_submit():
        try:
            svc.set_status(p, form.status.data, actor=current_user)
            db.session.commit()
            flash(f"Status is now {p.status_label}.", "success")
        except (svc.TrainingError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return _back(p)


@bp.post("/<public_id>/trainers")
@permission_required("programmes.manage")
def add_trainer(public_id):
    p = _programme(public_id)
    form = TrainerForm(prefix="tr")
    form.user.choices = _c((u.public_id, u.full_name) for u in svc.eligible_trainers(g.workspace))
    if not form.validate_on_submit():
        flash(_errors(form), "error")
        return _back(p)
    user = db.session.scalar(select(User).where(User.public_id == form.user.data))
    try:
        svc.assign_trainer(p, user, lead=bool(form.lead.data), actor=current_user)
        db.session.commit()
        flash(f"{user.full_name} assigned.", "success")
    except svc.TrainingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(p)


@bp.post("/<public_id>/trainers/<int:trainer_id>/remove")
@permission_required("programmes.manage")
def remove_trainer(public_id, trainer_id):
    p = _programme(public_id)
    t = db.session.scalar(select(ProgrammeTrainer).where(ProgrammeTrainer.id == trainer_id,
                                                         ProgrammeTrainer.programme_id == p.id)) or abort(404)
    svc.remove_trainer(p, t, actor=current_user)
    db.session.commit()
    flash("Trainer removed.", "success")
    return _back(p)


@bp.route("/<public_id>/enrol", methods=["GET", "POST"])
@permission_required("programmes.manage", "learners.view")
def enrol(public_id):
    p = _programme(public_id)
    if request.method == "POST":
        ids = request.form.getlist("learner")
        learners = db.session.scalars(select(Learner).where(Learner.public_id.in_(ids))).all() if ids else []
        if not learners:
            flash("Tick at least one learner.", "error")
            return redirect(url_for("programmes.enrol", public_id=public_id))
        try:
            added, skipped = svc.enrol(p, learners, actor=current_user)
            db.session.commit()
            msg = f"Enrolled {len(added)} learner{'s' if len(added) != 1 else ''}."
            if skipped:
                msg += " Skipped: " + "; ".join(skipped) + "."
            flash(msg, "success" if added else "info")
            return _back(p, "learners")
        except svc.TrainingError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("programmes.enrol", public_id=public_id))
    q = (request.args.get("q") or "").strip()
    taken = {e.learner_id for e in p.enrolments if e.status in (EnrolmentStatus.ENROLLED, EnrolmentStatus.COMPLETED)}
    stmt = select(Learner).where(Learner.status != LearnerStatus.ARCHIVED).order_by(Learner.last_name,
                                                                                    Learner.first_name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Learner.first_name.ilike(like) | Learner.last_name.ilike(like) |
                          Learner.learner_no.ilike(like) | Learner.city.ilike(like))
    candidates = [l for l in db.session.scalars(stmt.limit(300)) if l.id not in taken]
    return render_template("programmes/enrol.html", p=p, candidates=candidates, q=q)


@bp.post("/<public_id>/enrolments/<int:enrolment_id>/status")
@permission_required("programmes.manage")
def enrolment_status(public_id, enrolment_id):
    p = _programme(public_id)
    e = db.session.scalar(select(Enrolment).where(Enrolment.id == enrolment_id, Enrolment.programme_id == p.id)) \
        or abort(404)
    form = EnrolmentStatusForm(prefix="en")
    if form.validate_on_submit():
        try:
            svc.set_enrolment_status(e, form.status.data, actor=current_user)
            db.session.commit()
            flash(f"{e.learner.full_name}: {e.status_label}.", "success")
        except svc.TrainingError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return _back(p, "learners")


@bp.post("/<public_id>/sessions")
@permission_required("programmes.manage")
def add_session(public_id):
    p = _programme(public_id)
    form = SessionForm(prefix="ses")
    form.trainer.choices = _c([("", "—")] + [(t.user.public_id, t.user.full_name) for t in p.trainers])
    if not form.validate_on_submit():
        flash(_errors(form), "error")
        return _back(p, "sessions")
    trainer = next((t.user for t in p.trainers if t.user.public_id == form.trainer.data), None)
    try:
        svc.add_session(p, {"title": form.title.data, "date": form.date.data, "start_time": form.start_time.data,
                            "end_time": form.end_time.data, "venue": form.venue.data,
                            "trainer_id": trainer.id if trainer else None}, actor=current_user)
        db.session.commit()
        flash("Session added.", "success")
    except svc.TrainingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(p, "sessions")


def _session(p: Programme, session_id: str) -> TrainingSession:
    return db.session.scalar(select(TrainingSession).where(TrainingSession.public_id == session_id,
                                                           TrainingSession.programme_id == p.id)) or abort(404)


@bp.post("/<public_id>/sessions/<session_id>/delete")
@permission_required("programmes.manage")
def delete_session(public_id, session_id):
    p = _programme(public_id)
    s = _session(p, session_id)
    title = s.title
    db.session.delete(s)
    audit.record("programme.session_deleted", tenant_id=p.tenant_id, actor=current_user, entity_type="programme",
                 entity_id=p.id, summary=f"{p.code}: session “{title}” deleted")
    db.session.commit()
    flash("Session deleted.", "success")
    return _back(p, "sessions")


@bp.route("/<public_id>/sessions/<session_id>/attendance", methods=["GET", "POST"])
@permission_required("programmes.view")
def attendance(public_id, session_id):
    p = _programme(public_id)
    s = _session(p, session_id)
    if not svc.can_take_attendance(current_user, p, g.permissions):
        abort(403)
    learners = svc.register_learners(s)
    if request.method == "POST":
        marks, notes = {}, {}  # CSRF is checked globally by CSRFProtect
        for l in learners:
            value = request.form.get(f"a-{l.public_id}")
            if value:
                marks[l.id] = value
                notes[l.id] = request.form.get(f"n-{l.public_id}", "")
        if not marks:
            flash("Mark at least one learner.", "error")
        else:
            svc.record_attendance(s, marks, notes, actor=current_user)
            db.session.commit()
            flash(f"Attendance saved for {len(marks)} learner{'s' if len(marks) != 1 else ''}.", "success")
            return _back(p, "sessions")
    current = {a.learner_id: a for a in s.attendance}
    return render_template("programmes/attendance.html", p=p, s=s, learners=learners, current=current,
                           statuses=AttendanceStatus.LABELS)
