"""Learners and beneficiaries (/app/learners), plus the learner's own profile (/app/me)."""
from __future__ import annotations

import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from app.access import can, load_workspace, permission_required
from app.blueprints.learners.forms import (
    DeleteLearnerForm,
    DocumentForm,
    EducationForm,
    EmploymentForm,
    InviteLearnerForm,
    LearnerForm,
    PhotoForm,
    SelfProfileForm,
    SkillForm,
    StatusForm,
)
from app.extensions import db
from app.models import (
    Learner,
    LearnerDocument,
    LearnerEducation,
    LearnerEmployment,
    LearnerSkill,
    LearnerStatus,
    Role,
)
from app.models.learner import CATEGORIES, EDUCATION_LEVELS, EMPLOYMENT_STATUSES, GHANA_REGIONS, IDENTITY_TYPES, Gender
from app.services import audit, files, limits, members
from app.services import learners as svc

bp = Blueprint("learners", __name__)
PAGE_SIZE = 50
UPLOAD_ERRORS = (files.UploadError, limits.UsageLimitExceeded)


@bp.before_request
@login_required
def _workspace():
    return load_workspace(required=True)


@bp.app_context_processor
def _labels():
    return {"LEARNER_LABELS": {"id_types": IDENTITY_TYPES, "education": EDUCATION_LEVELS,
                               "employment": EMPLOYMENT_STATUSES, "categories": CATEGORIES}}


def _learner(public_id: str) -> Learner:
    learner = db.session.scalar(select(Learner).where(Learner.public_id == public_id))
    if learner is None:
        abort(404)
    return learner


def _form_errors(form) -> str:
    return " ".join(e for f in form for e in f.errors) or "Check the form and try again."


def _filtered(args):
    stmt = select(Learner)
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        full = func.lower(Learner.first_name + " " + Learner.last_name)
        stmt = stmt.where(Learner.first_name.ilike(like) | Learner.last_name.ilike(like) | full.like(like.lower()) |
                          Learner.learner_no.ilike(like) | Learner.phone.ilike(like) | Learner.email.ilike(like))
    status = args.get("status") or ""
    if status in LearnerStatus.LABELS:
        stmt = stmt.where(Learner.status == status)
    elif status != "all":
        stmt = stmt.where(Learner.status != LearnerStatus.ARCHIVED)
    if args.get("gender") in Gender.LABELS:
        stmt = stmt.where(Learner.gender == args["gender"])
    if args.get("region") in GHANA_REGIONS:
        stmt = stmt.where(Learner.region == args["region"])
    if args.get("disability") == "yes":
        stmt = stmt.where(Learner.has_disability.is_(True))
    category = args.get("category")
    rows_filter = category if category in CATEGORIES else None
    return stmt, rows_filter


# ---- List, export ------------------------------------------------------------------------

@bp.get("/")
@permission_required("learners.view")
def index():
    stmt, category = _filtered(request.args)
    stmt = stmt.order_by(Learner.last_name, Learner.first_name)
    if category:  # JSON list: filter in Python (portable across databases)
        rows = [l for l in db.session.scalars(stmt) if category in (l.categories or [])]
        total = len(rows)
    else:
        total = db.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = None
    page = max(1, request.args.get("page", 1, type=int))
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(page, pages)
    if rows is None:
        rows = db.session.scalars(stmt.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    else:
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template("learners/index.html", learners=rows, total=total, page=page, pages=pages, args=args,
                           regions=GHANA_REGIONS, categories=CATEGORIES, genders=Gender.LABELS,
                           statuses=LearnerStatus.LABELS, usage={"used": limits.usage(g.workspace, "learners"),
                                                                 "limit": limits.limit(g.workspace, "learners")})


@bp.get("/export.csv")
@permission_required("learners.view", "reports.export")
def export():
    stmt, category = _filtered(request.args)
    rows = [l for l in db.session.scalars(stmt.order_by(Learner.learner_no))
            if not category or category in (l.categories or [])]
    contact = can("learners.manage")  # contact details only for people who manage records
    out = io.StringIO()
    w = csv.writer(out)
    header = ["Learner number", "First name", "Middle name", "Surname", "Gender", "Age", "Age band", "Region",
              "District", "Town", "Disability", "Highest education", "Employment at registration", "Categories",
              "Status", "Registered on"]
    if contact:
        header += ["Phone", "Email"]
    w.writerow(header)
    for l in rows:
        row = [l.learner_no, l.first_name, l.middle_name or "", l.last_name, l.gender_label, l.age() or "",
               l.age_band, l.region or "", l.district or "", l.city or "", "Yes" if l.has_disability else "No",
               EDUCATION_LEVELS.get(l.education_level or "", ""), EMPLOYMENT_STATUSES.get(l.employment_status or "", ""),
               "; ".join(CATEGORIES.get(c, c) for c in (l.categories or [])), l.status_label,
               l.registered_on.isoformat()]
        if contact:
            row += [l.phone or "", l.email or ""]
        w.writerow([_safe_cell(v) for v in row])
    audit.record("learner.exported", tenant_id=g.workspace.id, actor=current_user, entity_type="learner",
                 summary=f"Exported {len(rows)} learner records to CSV")
    db.session.commit()
    resp = Response("﻿" + out.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="learners-{date.today():%Y-%m-%d}.csv"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def _safe_cell(value):
    """Stop spreadsheet programs treating text as a formula (CSV injection)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


# ---- Register, edit ---------------------------------------------------------------------------

def _form_data(form: LearnerForm) -> dict:
    return {k: form[k].data for k in svc.EDITABLE if k in form}


@bp.route("/new", methods=["GET", "POST"])
@permission_required("learners.manage")
def new():
    form = LearnerForm()
    duplicates = []
    if form.validate_on_submit():
        try:
            learner = svc.create(g.workspace, _form_data(form), actor=current_user,
                                 allow_duplicate=form.confirm_duplicate.data == "yes")
            db.session.commit()
            flash(f"{learner.full_name} registered as {learner.learner_no}.", "success")
            return redirect(url_for("learners.profile", public_id=learner.public_id))
        except svc.LearnerError as exc:
            db.session.rollback()
            if str(exc) == "possible-duplicate":
                duplicates = svc.find_duplicates(g.workspace, _form_data(form))
                form.confirm_duplicate.data = "yes"
            else:
                flash(str(exc), "error")
        except limits.UsageLimitExceeded as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("learners/form.html", form=form, learner=None, duplicates=duplicates)


@bp.route("/<public_id>/edit", methods=["GET", "POST"])
@permission_required("learners.manage")
def edit(public_id: str):
    learner = _learner(public_id)
    form = LearnerForm(obj=learner)
    if request.method == "GET":
        form.categories.data = learner.categories or []
    if form.validate_on_submit():
        try:
            changed = svc.update(learner, _form_data(form), actor=current_user)
            db.session.commit()
            flash("Saved." if changed else "Nothing to save: no changes.", "success" if changed else "info")
            return redirect(url_for("learners.profile", public_id=public_id))
        except svc.LearnerError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("learners/form.html", form=form, learner=learner, duplicates=[])


# ---- Profile ----------------------------------------------------------------------------------

TABS = ("overview", "training", "competencies", "education", "skills", "employment", "documents")


@bp.get("/<public_id>")
@permission_required("learners.view")
def profile(public_id: str):
    learner = _learner(public_id)
    tab = request.args.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    manage = can("learners.manage")
    forms = {}
    if manage:
        forms = {"education": EducationForm(prefix="edu"), "skills": SkillForm(prefix="skill"),
                 "employment": EmploymentForm(prefix="job"), "documents": DocumentForm(prefix="doc"),
                 "photo": PhotoForm(prefix="photo"), "status": StatusForm(prefix="st", data={"status": learner.status}),
                 "delete": DeleteLearnerForm(prefix="del"),
                 "invite": InviteLearnerForm(prefix="inv", data={"email": learner.email})}
    training = _training_history(learner) if tab == "training" else None
    competence = _competence(learner) if tab == "competencies" else None
    jobs = _jobs(learner) if tab == "employment" else None
    return render_template("learners/profile.html", learner=learner, tab=tab, tabs=TABS, manage=manage, forms=forms,
                           show_id=manage, training=training, competence=competence, jobs=jobs)


def _jobs(learner: Learner) -> dict:
    from app.models import Application, Placement
    return {
        "placements": db.session.scalars(select(Placement).where(Placement.learner_id == learner.id)
                                         .order_by(Placement.start_date.desc())).all(),
        "applications": db.session.scalars(select(Application).where(Application.learner_id == learner.id)
                                           .order_by(Application.created_at.desc())).all(),
    }


def _competence(learner: Learner) -> dict:
    from app.models import Assessment, Certificate
    from app.services import assessments as assess_svc
    from app.services import certificates as cert_svc
    out = {"profiles": [], "assessments": [], "certificates": [], "eligible": []}
    if can("assessments.view"):
        out["profiles"] = [(fw, assess_svc.profile(learner, fw)) for fw in assess_svc.frameworks_for(learner)]
        out["assessments"] = db.session.scalars(select(Assessment).where(Assessment.learner_id == learner.id)
                                                .order_by(Assessment.assessed_on.desc())).all()
    out["certificates"] = db.session.scalars(select(Certificate).where(Certificate.learner_id == learner.id)
                                             .order_by(Certificate.issued_on.desc())).all()
    if can("certificates.issue"):
        out["eligible"] = cert_svc.eligible(learner)
    return out


def _training_history(learner: Learner) -> dict:
    from app.models import CourseEnrolment, Enrolment
    from app.services import training as training_svc
    enrolments = db.session.scalars(select(Enrolment).where(Enrolment.learner_id == learner.id)
                                    .order_by(Enrolment.enrolled_on.desc())).all()
    rates = {e.id: training_svc.attendance_rates(e.programme).get(learner.id) for e in enrolments}
    courses = db.session.scalars(select(CourseEnrolment).where(CourseEnrolment.learner_id == learner.id)).all()
    return {"enrolments": enrolments, "rates": rates, "courses": courses}


@bp.get("/<public_id>/photo")
@permission_required("learners.view")
def photo(public_id: str):
    learner = _learner(public_id)
    if learner.photo is None:
        abort(404)
    return files.serve(learner.photo, inline=True, log=False)


def _back(learner: Learner, tab: str = "overview"):
    return redirect(url_for("learners.profile", public_id=learner.public_id, tab=tab))


@bp.post("/<public_id>/photo")
@permission_required("learners.manage")
def upload_photo(public_id: str):
    learner = _learner(public_id)
    form = PhotoForm(prefix="photo")
    if not form.validate_on_submit():
        flash(_form_errors(form), "error")
        return _back(learner)
    try:
        svc.set_photo(learner, form.photo.data, actor=current_user)
        db.session.commit()
        flash("Passport photo saved.", "success")
    except UPLOAD_ERRORS as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(learner)


@bp.post("/<public_id>/photo/remove")
@permission_required("learners.manage")
def remove_photo(public_id: str):
    learner = _learner(public_id)
    svc.remove_photo(learner, actor=current_user)
    db.session.commit()
    flash("Photo removed.", "success")
    return _back(learner)


@bp.post("/<public_id>/status")
@permission_required("learners.manage")
def status(public_id: str):
    learner = _learner(public_id)
    form = StatusForm(prefix="st")
    if form.validate_on_submit():
        try:
            svc.set_status(learner, form.status.data, actor=current_user)
            db.session.commit()
            flash(f"Status is now {learner.status_label}.", "success")
        except (svc.LearnerError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return _back(learner)


@bp.post("/<public_id>/delete")
@permission_required("learners.manage", "members.manage")
def delete(public_id: str):
    learner = _learner(public_id)
    form = DeleteLearnerForm(prefix="del")
    if form.validate_on_submit() and form.confirm.data.upper() == learner.learner_no.upper():
        name = learner.full_name
        svc.delete_permanently(learner, actor=current_user)
        db.session.commit()
        flash(f"{name}'s record and files have been permanently deleted.", "success")
        return redirect(url_for("learners.index"))
    flash("Type the learner number exactly to confirm permanent deletion.", "error")
    return _back(learner)


@bp.post("/<public_id>/invite")
@permission_required("learners.manage")
def invite(public_id: str):
    learner = _learner(public_id)
    form = InviteLearnerForm(prefix="inv")
    if learner.user_id:
        flash("This learner can already sign in.", "info")
        return _back(learner)
    if form.validate_on_submit():
        try:
            members.invite(g.workspace, email=form.email.data, roles=[Role.LEARNER], full_name=learner.full_name,
                           actor=current_user, learner=learner)
            db.session.commit()
            flash(f"Sign-in invitation sent to {form.email.data}.", "success")
        except (members.MemberError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    else:
        flash(_form_errors(form), "error")
    return _back(learner)


# ---- Education, skills, employment ------------------------------------------------------------

def _add_record(public_id, form_cls, prefix, tab, build):
    learner = _learner(public_id)
    form = form_cls(prefix=prefix)
    if form.validate_on_submit():
        record = build(learner, form)
        db.session.add(record)
        audit.record(f"learner.{tab}_added", tenant_id=learner.tenant_id, actor=current_user, entity_type="learner",
                     entity_id=learner.id, summary=f"{learner.learner_no}: {tab} record added")
        db.session.commit()
        flash("Added.", "success")
    else:
        flash(_form_errors(form), "error")
    return _back(learner, tab)


def _delete_record(public_id, model, record_id, tab):
    learner = _learner(public_id)
    record = db.session.scalar(select(model).where(model.id == record_id, model.learner_id == learner.id))
    if record is None:
        abort(404)
    db.session.delete(record)
    audit.record(f"learner.{tab}_deleted", tenant_id=learner.tenant_id, actor=current_user, entity_type="learner",
                 entity_id=learner.id, summary=f"{learner.learner_no}: {tab} record deleted")
    db.session.commit()
    flash("Deleted.", "success")
    return _back(learner, tab)


@bp.post("/<public_id>/education")
@permission_required("learners.manage")
def add_education(public_id):
    return _add_record(public_id, EducationForm, "edu", "education", lambda l, f: LearnerEducation(
        tenant_id=l.tenant_id, learner=l, institution=f.institution.data, qualification=f.qualification.data,
        field_of_study=f.field_of_study.data or None, level=f.level.data or None, start_year=f.start_year.data,
        end_year=f.end_year.data))


@bp.post("/<public_id>/education/<int:record_id>/delete")
@permission_required("learners.manage")
def delete_education(public_id, record_id):
    return _delete_record(public_id, LearnerEducation, record_id, "education")


@bp.post("/<public_id>/skills")
@permission_required("learners.manage")
def add_skill(public_id):
    return _add_record(public_id, SkillForm, "skill", "skills", lambda l, f: LearnerSkill(
        tenant_id=l.tenant_id, learner=l, name=f.name.data, level=f.level.data, certified=bool(f.certified.data),
        certificate_body=f.certificate_body.data or None, year=f.year.data))


@bp.post("/<public_id>/skills/<int:record_id>/delete")
@permission_required("learners.manage")
def delete_skill(public_id, record_id):
    return _delete_record(public_id, LearnerSkill, record_id, "skills")


@bp.post("/<public_id>/employment")
@permission_required("learners.manage")
def add_employment(public_id):
    return _add_record(public_id, EmploymentForm, "job", "employment", lambda l, f: LearnerEmployment(
        tenant_id=l.tenant_id, learner=l, employer=f.employer.data, position=f.position.data,
        employment_type=f.employment_type.data, sector=f.sector.data or None, location=f.location.data or None,
        start_date=f.start_date.data, end_date=f.end_date.data,
        monthly_income_minor=f.monthly_income.data * 100 if f.monthly_income.data is not None else None))


@bp.post("/<public_id>/employment/<int:record_id>/delete")
@permission_required("learners.manage")
def delete_employment(public_id, record_id):
    return _delete_record(public_id, LearnerEmployment, record_id, "employment")


# ---- Documents ------------------------------------------------------------------------------------

@bp.post("/<public_id>/documents")
@permission_required("learners.manage")
def add_document(public_id):
    learner = _learner(public_id)
    form = DocumentForm(prefix="doc")
    if not form.validate_on_submit():
        flash(_form_errors(form), "error")
        return _back(learner, "documents")
    try:
        svc.add_document(learner, form.file.data, doc_type=form.doc_type.data, title=form.title.data,
                         actor=current_user)
        db.session.commit()
        flash("Document uploaded.", "success")
    except UPLOAD_ERRORS as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(learner, "documents")


def _document(learner: Learner, doc_id: str) -> LearnerDocument:
    doc = db.session.scalar(select(LearnerDocument).where(LearnerDocument.public_id == doc_id,
                                                          LearnerDocument.learner_id == learner.id))
    if doc is None:
        abort(404)
    return doc


@bp.get("/<public_id>/documents/<doc_id>")
@permission_required("learners.view")
def download(public_id, doc_id):
    learner = _learner(public_id)
    return files.serve(_document(learner, doc_id).file, actor=current_user)


@bp.post("/<public_id>/documents/<doc_id>/delete")
@permission_required("learners.manage")
def delete_document(public_id, doc_id):
    learner = _learner(public_id)
    svc.delete_document(_document(learner, doc_id), actor=current_user)
    db.session.commit()
    flash("Document deleted.", "success")
    return _back(learner, "documents")


# ---- The learner's own profile ------------------------------------------------------------------------

self_bp = Blueprint("me", __name__)


@self_bp.before_request
@login_required
def _me_workspace():
    return load_workspace(required=True)


def _my_record() -> Learner | None:
    return db.session.scalar(select(Learner).where(Learner.user_id == current_user.id))


@self_bp.route("/", methods=["GET", "POST"])
@permission_required("self.profile")
def my_profile():
    learner = _my_record()
    if learner is None:
        return render_template("learners/me.html", learner=None)
    form = SelfProfileForm(obj=learner)
    if form.validate_on_submit():
        changed = svc.update(learner, {k: form[k].data for k in svc.SELF_EDITABLE}, actor=current_user,
                             allowed=svc.SELF_EDITABLE, by_learner=True)
        db.session.commit()
        flash("Your details have been saved." if changed else "Nothing to save: no changes.",
              "success" if changed else "info")
        return redirect(url_for("me.my_profile"))
    return render_template("learners/me.html", learner=learner, form=form, doc_form=DocumentForm(prefix="doc"))


@self_bp.post("/documents")
@permission_required("self.profile")
def my_document_upload():
    learner = _my_record() or abort(404)
    form = DocumentForm(prefix="doc")
    if not form.validate_on_submit():
        flash(_form_errors(form), "error")
        return redirect(url_for("me.my_profile"))
    try:
        svc.add_document(learner, form.file.data, doc_type=form.doc_type.data, title=form.title.data,
                         actor=current_user, by_learner=True)
        db.session.commit()
        flash("Document uploaded.", "success")
    except UPLOAD_ERRORS as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("me.my_profile"))


@self_bp.get("/documents/<doc_id>")
@permission_required("self.profile")
def my_document(doc_id):
    learner = _my_record() or abort(404)
    return files.serve(_document(learner, doc_id).file, actor=current_user)


@self_bp.post("/documents/<doc_id>/delete")
@permission_required("self.profile")
def my_document_delete(doc_id):
    learner = _my_record() or abort(404)
    doc = _document(learner, doc_id)
    if not doc.uploaded_by_learner:
        abort(403)  # learners can remove only what they uploaded themselves
    svc.delete_document(doc, actor=current_user)
    db.session.commit()
    flash("Document deleted.", "success")
    return redirect(url_for("me.my_profile"))


@self_bp.get("/photo")
@permission_required("self.profile")
def my_photo():
    learner = _my_record()
    if learner is None or learner.photo is None:
        abort(404)
    return files.serve(learner.photo, inline=True, log=False)
