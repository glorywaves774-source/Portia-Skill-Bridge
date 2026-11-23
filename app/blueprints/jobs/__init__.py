"""Recruitment and employment.

/app/employers   employer records and their sign-in contacts
/app/jobs        vacancies, matching, applicants, interviews and hiring
/app/placements  people in work and their 3/6/12-month follow-ups
/app/my-jobs     the learner's own job search and applications
"""
from __future__ import annotations

from datetime import date, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.access import can, load_workspace, permission_required
from app.auth.forms import _c
from app.blueprints.jobs import forms as F
from app.extensions import db
from app.models import (
    Application,
    ApplicationStatus,
    Certificate,
    Competency,
    Employer,
    Framework,
    FrameworkStatus,
    Learner,
    Membership,
    Placement,
    PlacementFollowUp,
    PlacementStatus,
    Vacancy,
    VacancyStatus,
)
from app.services import employment as svc

employers_bp = Blueprint("employers", __name__)
jobs_bp = Blueprint("jobs", __name__)
placements_bp = Blueprint("placements", __name__)
myjobs_bp = Blueprint("myjobs", __name__)


def _guard():
    resp = load_workspace(required=True)
    if resp is not None:
        return resp
    g.job_scope = svc.scope_for(current_user, g.workspace, g.permissions)
    return None


for _bp in (employers_bp, jobs_bp, placements_bp, myjobs_bp):
    _bp.before_request(login_required(_guard))


@jobs_bp.app_context_processor
def _ctx():
    return {"job_scope": g.get("job_scope")}


def _errors(form) -> str:
    return " ".join(e for f in form for e in f.errors) or "Check the form and try again."


def _one(model, public_id):
    obj = db.session.scalar(select(model).where(model.public_id == public_id))
    if obj is None:
        abort(404)
    return obj


def _staff_only():
    """Pages only the training centre uses (not employer partner accounts)."""
    if g.job_scope.restricted:
        abort(403)


# ==== Employers ==================================================================================

def _employer(public_id: str) -> Employer:
    e = _one(Employer, public_id)
    if not g.job_scope.allows_employer(e.id):
        abort(404)
    return e


@employers_bp.get("/")
@permission_required("vacancies.view")
def index():
    if g.job_scope.restricted:
        if g.job_scope.employer:
            return redirect(url_for("employers.detail", public_id=g.job_scope.employer.public_id))
        return render_template("jobs/not_linked.html")
    q = (request.args.get("q") or "").strip()
    show = request.args.get("show", "active")
    stmt = select(Employer).order_by(Employer.name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Employer.name.ilike(like) | Employer.city.ilike(like) | Employer.contact_name.ilike(like))
    if show != "all":
        stmt = stmt.where(Employer.is_active.is_(show != "inactive"))
    employers = db.session.scalars(stmt.options(selectinload(Employer.vacancies),
                                                selectinload(Employer.placements))).all()
    return render_template("jobs/employers.html", employers=employers, q=q, show=show)


def _employer_data(form) -> dict:
    return {k: form[k].data for k in svc.EMPLOYER_FIELDS}


@employers_bp.route("/new", methods=["GET", "POST"])
@permission_required("vacancies.manage")
def new():
    _staff_only()
    form = F.EmployerForm()
    if form.validate_on_submit():
        try:
            e = svc.create_employer(g.workspace, _employer_data(form), actor=current_user)
            db.session.commit()
            flash(f"{e.name} added. Next: post a vacancy.", "success")
            return redirect(url_for("employers.detail", public_id=e.public_id))
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("jobs/employer_form.html", form=form, employer=None)


@employers_bp.route("/<public_id>/edit", methods=["GET", "POST"])
@permission_required("vacancies.manage")
def edit(public_id):
    e = _employer(public_id)
    form = F.EmployerForm(obj=e)
    if form.validate_on_submit():
        try:
            changed = svc.update_employer(e, _employer_data(form), actor=current_user)
            db.session.commit()
            flash("Saved." if changed else "Nothing to save: no changes.", "success" if changed else "info")
            return redirect(url_for("employers.detail", public_id=e.public_id))
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("jobs/employer_form.html", form=form, employer=e)


@employers_bp.get("/<public_id>")
@permission_required("vacancies.view")
def detail(public_id):
    e = _employer(public_id)
    team = svc.employer_members(e)
    forms = {}
    if can("members.manage") and not g.job_scope.restricted:
        lf = F.LinkMemberForm(prefix="ln")
        lf.member.choices = _c([("", "Choose…")] + [(str(m.id), m.user.full_name) for m in svc.linkable_members(g.workspace)])
        forms = {"invite": F.InviteContactForm(prefix="inv"), "link": lf}
    return render_template("jobs/employer.html", e=e, team=team, forms=forms,
                           vacancies=e.vacancies, placements=e.placements)


@employers_bp.post("/<public_id>/active")
@permission_required("vacancies.manage")
def set_active(public_id):
    _staff_only()
    e = _employer(public_id)
    try:
        svc.set_employer_active(e, request.form.get("active") == "1", actor=current_user)
        db.session.commit()
        flash(f"{e.name} is now {'active' if e.is_active else 'inactive'}.", "success")
    except svc.EmploymentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("employers.detail", public_id=e.public_id))


@employers_bp.post("/<public_id>/invite")
@permission_required("members.manage")
def invite(public_id):
    _staff_only()
    e = _employer(public_id)
    form = F.InviteContactForm(prefix="inv")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
    else:
        try:
            svc.invite_contact(g.workspace, e, email=form.email.data, full_name=form.full_name.data, actor=current_user)
            db.session.commit()
            flash(f"Invitation sent to {form.email.data}. Once they accept, they'll see {e.name}'s jobs and "
                  "shortlisted candidates.", "success")
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("employers.detail", public_id=e.public_id) + "#team")


def _membership(mid: str) -> Membership:
    m = db.session.get(Membership, int(mid)) if str(mid).isdigit() else None
    if m is None or m.tenant_id != g.workspace.id:
        abort(404)
    return m


@employers_bp.post("/<public_id>/link")
@permission_required("members.manage")
def link(public_id):
    _staff_only()
    e = _employer(public_id)
    form = F.LinkMemberForm(prefix="ln")
    form.member.choices = _c([(str(m.id), m.user.full_name) for m in svc.linkable_members(g.workspace)])
    if not form.validate_on_submit():
        flash(_errors(form), "error")
    else:
        try:
            svc.link_member(e, _membership(form.member.data), actor=current_user)
            db.session.commit()
            flash("Linked.", "success")
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("employers.detail", public_id=e.public_id) + "#team")


@employers_bp.post("/<public_id>/unlink/<mid>")
@permission_required("members.manage")
def unlink(public_id, mid):
    _staff_only()
    e = _employer(public_id)
    try:
        svc.unlink_member(e, _membership(mid), actor=current_user)
        db.session.commit()
        flash("Unlinked. They no longer see this employer's jobs.", "success")
    except svc.EmploymentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("employers.detail", public_id=e.public_id) + "#team")


# ==== Vacancies ==================================================================================

def _vacancy(public_id: str) -> Vacancy:
    v = _one(Vacancy, public_id)
    if not g.job_scope.allows_employer(v.employer_id):
        abort(404)
    return v


@jobs_bp.get("/")
@permission_required("vacancies.view")
def index():
    if g.job_scope.restricted and not g.job_scope.employer:
        return render_template("jobs/not_linked.html")
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    stmt = g.job_scope.vacancy_filter(select(Vacancy).join(Employer, Employer.id == Vacancy.employer_id))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Vacancy.title.ilike(like) | Vacancy.code.ilike(like) | Vacancy.trade.ilike(like)
                          | Employer.name.ilike(like))
    if status in VacancyStatus.LABELS:
        stmt = stmt.where(Vacancy.status == status)
    elif status != "all":
        stmt = stmt.where(Vacancy.status.in_((VacancyStatus.DRAFT, VacancyStatus.OPEN)))
    vacancies = db.session.scalars(stmt.order_by(Vacancy.created_at.desc()).options(
        selectinload(Vacancy.employer), selectinload(Vacancy.applications))).all()
    return render_template("jobs/vacancies.html", vacancies=vacancies, q=q, status=status,
                           statuses=VacancyStatus.LABELS, stats=svc.stats(g.job_scope))


def _competency_choices():
    rows = db.session.execute(select(Competency, Framework).join(Framework, Framework.id == Competency.framework_id)
                              .where(Framework.status == FrameworkStatus.ACTIVE)
                              .order_by(Framework.name, Competency.code)).all()
    return _c((str(c.id), f"{c.code} {c.title} ({fw.name})") for c, fw in rows)


def _vacancy_form(v: Vacancy | None = None, employer: Employer | None = None) -> F.VacancyForm:
    form = F.VacancyForm(obj=v)
    if g.job_scope.restricted:
        emps = [g.job_scope.employer]
    else:
        emps = db.session.scalars(select(Employer).where(Employer.is_active.is_(True)).order_by(Employer.name)).all()
        if v is not None and v.employer not in emps:
            emps.insert(0, v.employer)
    form.employer.choices = _c([("", "Choose…")] * (len(emps) != 1) + [(e.public_id, e.name) for e in emps])
    comps = _competency_choices()
    if v is not None:  # keep competencies from a retired framework that the vacancy already uses
        have = {c.value for c in comps}
        comps += _c((str(c.id), f"{c.code} {c.title}") for c in v.competencies if str(c.id) not in have)
    form.competencies.choices = comps
    if request.method == "GET":
        if v is not None:
            form.employer.data = v.employer.public_id
            form.competencies.data = [str(c.id) for c in v.competencies]
            form.salary_min.data = F.from_minor(v.salary_min_minor)
            form.salary_max.data = F.from_minor(v.salary_max_minor)
        elif employer is not None:
            form.employer.data = employer.public_id
        elif len(emps) == 1:
            form.employer.data = emps[0].public_id
    return form


def _vacancy_data(form) -> dict:
    data = {k: form[k].data for k in svc.VACANCY_FIELDS if k in form}
    data["salary_min_minor"] = F.to_minor(form.salary_min.data)
    data["salary_max_minor"] = F.to_minor(form.salary_max.data)
    return data


def _form_employer(form) -> Employer:
    e = db.session.scalar(select(Employer).where(Employer.public_id == form.employer.data))
    if e is None or not g.job_scope.allows_employer(e.id):
        abort(400)
    return e


@jobs_bp.route("/new", methods=["GET", "POST"])
@permission_required("vacancies.manage")
def new():
    if g.job_scope.restricted and not g.job_scope.employer:
        return render_template("jobs/not_linked.html")
    pre = request.args.get("employer")
    employer = db.session.scalar(select(Employer).where(Employer.public_id == pre)) if pre else None
    form = _vacancy_form(employer=employer)
    if not form.employer.choices:
        flash("Add an employer first.", "info")
        return redirect(url_for("employers.new"))
    if form.validate_on_submit():
        try:
            v = svc.create_vacancy(g.workspace, _form_employer(form), _vacancy_data(form),
                                   competency_ids=form.competencies.data, actor=current_user)
            db.session.commit()
            flash(f"Vacancy {v.code} saved as a draft. Open it when you're ready for applicants.", "success")
            return redirect(url_for("jobs.detail", public_id=v.public_id))
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("jobs/vacancy_form.html", form=form, v=None)


@jobs_bp.route("/<public_id>/edit", methods=["GET", "POST"])
@permission_required("vacancies.manage")
def edit(public_id):
    v = _vacancy(public_id)
    form = _vacancy_form(v)
    if form.validate_on_submit():
        try:
            e = _form_employer(form)
            if e.id != v.employer_id:
                if v.applications:
                    raise svc.EmploymentError("People have applied already, so the employer can't be changed.")
                v.employer = e
            changed = svc.update_vacancy(v, _vacancy_data(form), competency_ids=form.competencies.data,
                                         actor=current_user)
            db.session.commit()
            flash("Saved." if changed else "Nothing to save: no changes.", "success" if changed else "info")
            return redirect(url_for("jobs.detail", public_id=v.public_id))
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("jobs/vacancy_form.html", form=form, v=v)


TABS = ("overview", "applicants", "matches")


@jobs_bp.get("/<public_id>")
@permission_required("vacancies.view")
def detail(public_id):
    v = _vacancy(public_id)
    tab = request.args.get("tab", "overview")
    can_match = can("applicants.manage") and not g.job_scope.restricted
    tabs = TABS if can_match else TABS[:2]
    tab = tab if tab in tabs else "overview"
    apps = [a for a in v.applications if g.job_scope.allows_application(a)]
    hidden = len(v.applications) - len(apps)
    matches, include_working, q = None, request.args.get("working") == "1", (request.args.get("q") or "").strip()
    if tab == "matches":
        matches = svc.candidates(v, include_working=include_working, q=q)
    order = {s: i for i, s in enumerate(ApplicationStatus.LABELS)}
    apps.sort(key=lambda a: (not a.is_open, order.get(a.status, 99), -(a.match_score or 0)))
    return render_template("jobs/vacancy.html", v=v, tab=tab, tabs=tabs, apps=apps, hidden=hidden, matches=matches,
                           include_working=include_working, q=q,
                           status_form=F.VacancyStatusForm(prefix="st", data={"status": v.status}))


@jobs_bp.post("/<public_id>/status")
@permission_required("vacancies.manage")
def status(public_id):
    v = _vacancy(public_id)
    form = F.VacancyStatusForm(prefix="st")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
    else:
        try:
            svc.set_vacancy_status(v, form.status.data, actor=current_user)
            db.session.commit()
            flash(f"{v.code} is now {v.status_label.lower()}.", "success")
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("jobs.detail", public_id=v.public_id))


@jobs_bp.post("/<public_id>/nominate")
@permission_required("applicants.manage")
def nominate(public_id):
    _staff_only()
    v = _vacancy(public_id)
    ids = request.form.getlist("learner")
    learners = db.session.scalars(select(Learner).where(Learner.public_id.in_(ids))).all() if ids else []
    if not learners:
        flash("Tick at least one learner.", "error")
        return redirect(url_for("jobs.detail", public_id=v.public_id, tab="matches"))
    try:
        created = svc.nominate(v, learners, actor=current_user)
        db.session.commit()
        flash(f"Put forward {len(created)} learner{'s' if len(created) != 1 else ''}. Shortlist them to share "
              f"them with {v.employer.name}.", "success")
    except svc.EmploymentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("jobs.detail", public_id=v.public_id, tab="matches"))
    return redirect(url_for("jobs.detail", public_id=v.public_id, tab="applicants"))


# ---- Applications -------------------------------------------------------------------------------

def _application(public_id: str) -> Application:
    a = _one(Application, public_id)
    if not g.job_scope.allows_application(a):
        abort(404)
    return a


def _app_back(a: Application):
    return redirect(url_for("jobs.application", public_id=a.public_id))


@jobs_bp.get("/applications/<public_id>")
@permission_required("vacancies.view")
def application(public_id):
    a = _application(public_id)
    v = a.vacancy
    fit = svc.score(v, a.learner)
    from app.services import assessments
    profiles = [(fw, assessments.profile(a.learner, fw)) for fw in assessments.frameworks_for(a.learner)]
    certificates = [c for c in db.session.scalars(select(Certificate).where(
        Certificate.learner_id == a.learner_id).order_by(Certificate.issued_on.desc())).all() if not c.is_revoked]
    manage = can("applicants.manage") and a.status != ApplicationStatus.HIRED
    forms = {}
    if manage:
        allowed = ApplicationStatus.EMPLOYER_SETTABLE if g.job_scope.restricted else ApplicationStatus.SETTABLE
        sf = F.ApplicationStatusForm(prefix="ap", data={"status": a.status}).limit_to(
            allowed if a.status in allowed else (a.status, *allowed))
        hf = F.HireForm(prefix="hire", data={"job_title": v.title, "employment_type": v.employment_type,
                                              "start_date": date.today()})
        forms = {"status": sf, "interview": F.InterviewForm(prefix="iv", data={"location": a.interview_location}),
                 "hire": hf}
    notes_form = F.NotesForm(prefix="nt", data={"notes": a.notes}) if can("applicants.manage") else None
    # Contact details are shared with employers only once the centre has shortlisted the candidate.
    show_contact = not g.job_scope.restricted or a.shared_with_employer
    return render_template("jobs/application.html", a=a, v=v, fit=fit, profiles=profiles, certificates=certificates,
                           forms=forms, notes_form=notes_form, show_contact=show_contact,
                           can_view_learner=can("learners.view"))


@jobs_bp.post("/applications/<public_id>/status")
@permission_required("applicants.manage")
def application_status(public_id):
    a = _application(public_id)
    new = request.form.get("ap-status", "")
    form = F.ApplicationStatusForm(prefix="ap").limit_to(ApplicationStatus.LABELS)
    if not form.validate_on_submit():
        flash(_errors(form), "error")
        return _app_back(a)
    try:
        svc.set_status(a, new, actor=current_user, scope=g.job_scope)
        db.session.commit()
        flash(f"{a.learner.full_name}: {a.status_label}.", "success")
    except svc.EmploymentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    if g.job_scope.restricted and not g.job_scope.allows_application(a):
        return redirect(url_for("jobs.detail", public_id=a.vacancy.public_id, tab="applicants"))
    return _app_back(a)


@jobs_bp.post("/applications/<public_id>/interview")
@permission_required("applicants.manage")
def interview(public_id):
    a = _application(public_id)
    form = F.InterviewForm(prefix="iv")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
        return _app_back(a)
    try:
        when = form.when.data.replace(tzinfo=timezone.utc)  # Ghana time is GMT all year
        emailed = svc.schedule_interview(a, when=when, location=form.location.data, actor=current_user,
                                         scope=g.job_scope)
        db.session.commit()
        flash("Interview set." + (f" {a.learner.first_name} has been emailed the details." if emailed
                                  else " There's no email address for this learner, so tell them directly."), "success")
    except svc.EmploymentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _app_back(a)


@jobs_bp.post("/applications/<public_id>/notes")
@permission_required("applicants.manage")
def notes(public_id):
    a = _application(public_id)
    form = F.NotesForm(prefix="nt")
    if form.validate_on_submit():
        try:
            svc.save_notes(a, form.notes.data, actor=current_user, scope=g.job_scope)
            db.session.commit()
            flash("Notes saved.", "success")
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    else:
        flash(_errors(form), "error")
    return _app_back(a)


@jobs_bp.post("/applications/<public_id>/hire")
@permission_required("applicants.manage")
def hire(public_id):
    a = _application(public_id)
    form = F.HireForm(prefix="hire")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
        return _app_back(a)
    try:
        p = svc.hire(a, {"job_title": form.job_title.data, "employment_type": form.employment_type.data,
                         "start_date": form.start_date.data, "monthly_pay_minor": F.to_minor(form.monthly_pay.data)},
                     actor=current_user, scope=g.job_scope)
        db.session.commit()
        flash(f"{a.learner.full_name} hired. Follow-up checks are scheduled at 3, 6 and 12 months.", "success")
        return redirect(url_for("placements.detail", public_id=p.public_id))
    except svc.EmploymentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _app_back(a)


# ==== Placements =================================================================================

def _placement(public_id: str) -> Placement:
    p = _one(Placement, public_id)
    if g.job_scope.restricted and not g.job_scope.allows_employer(p.employer_id):
        abort(404)
    return p


@placements_bp.get("/")
@permission_required("vacancies.view")
def index():
    if g.job_scope.restricted and not g.job_scope.employer:
        return render_template("jobs/not_linked.html")
    show = request.args.get("show", "active")
    etype = request.args.get("type", "")
    stmt = select(Placement).order_by(Placement.start_date.desc()).options(selectinload(Placement.learner))
    if g.job_scope.restricted:
        stmt = stmt.where(Placement.employer_id == g.job_scope.employer.id)
    if show in PlacementStatus.LABELS:
        stmt = stmt.where(Placement.status == show)
    if etype in svc.EMPLOYMENT_TYPES:
        stmt = stmt.where(Placement.employment_type == etype)
    placements = db.session.scalars(stmt).all()
    return render_template("jobs/placements.html", placements=placements, show=show, etype=etype,
                           types=svc.EMPLOYMENT_TYPES, due=svc.follow_ups_due(g.job_scope),
                           stats=svc.stats(g.job_scope), today=date.today())


@placements_bp.route("/new", methods=["GET", "POST"])
@permission_required("placements.manage", "learners.view")
def new():
    _staff_only()
    form = F.PlacementForm()
    learners = db.session.scalars(select(Learner).where(Learner.status.in_(svc.CANDIDATE_STATUSES))
                                  .order_by(Learner.last_name, Learner.first_name)).all()
    form.learner.choices = _c([("", "Choose…")] + [(l.public_id, f"{l.full_name} ({l.learner_no})") for l in learners])
    emps = db.session.scalars(select(Employer).where(Employer.is_active.is_(True)).order_by(Employer.name)).all()
    form.employer.choices = _c([("", "Not on the list / own business")] + [(e.public_id, e.name) for e in emps])
    if request.method == "GET":
        form.learner.data = request.args.get("learner") or ""
        form.start_date.data = date.today()
    if form.validate_on_submit():
        learner = next((l for l in learners if l.public_id == form.learner.data), None)
        employer = next((e for e in emps if e.public_id == form.employer.data), None)
        if learner is None:
            abort(400)
        try:
            p = svc.create_placement(learner, {
                "job_title": form.job_title.data, "employment_type": form.employment_type.data,
                "start_date": form.start_date.data, "monthly_pay_minor": F.to_minor(form.monthly_pay.data),
                "employer_name": form.employer_name.data, "notes": form.notes.data,
            }, employer=employer, actor=current_user)
            db.session.commit()
            flash(f"Placement recorded for {learner.full_name}. Follow-up checks are scheduled at 3, 6 and 12 months.",
                  "success")
            return redirect(url_for("placements.detail", public_id=p.public_id))
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("jobs/placement_form.html", form=form)


@placements_bp.get("/<public_id>")
@permission_required("vacancies.view")
def detail(public_id):
    p = _placement(public_id)
    manage = can("placements.manage")
    forms = {}
    if manage and p.is_active:
        forms = {"end": F.EndPlacementForm(prefix="end", data={"end_date": date.today()}),
                 "follow": F.FollowUpForm(prefix="fu", data={"done_on": date.today()})}
    next_fu = next((f for f in p.follow_ups if not f.is_done), None)
    return render_template("jobs/placement.html", p=p, manage=manage, forms=forms, today=date.today(), next_fu=next_fu,
                           can_view_learner=can("learners.view"))


@placements_bp.post("/<public_id>/end")
@permission_required("placements.manage")
def end(public_id):
    p = _placement(public_id)
    form = F.EndPlacementForm(prefix="end")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
    else:
        try:
            svc.end_placement(p, end_date=form.end_date.data, reason=form.reason.data, actor=current_user)
            db.session.commit()
            flash("Placement ended. The learner's employment history has been updated.", "success")
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("placements.detail", public_id=p.public_id))


@placements_bp.post("/<public_id>/follow-ups/<int:fu_id>")
@permission_required("placements.manage")
def follow_up(public_id, fu_id):
    p = _placement(public_id)
    fu = db.session.get(PlacementFollowUp, fu_id)
    if fu is None or fu.placement_id != p.id:
        abort(404)
    if fu.is_done:
        flash("This check has already been recorded.", "info")
        return redirect(url_for("placements.detail", public_id=p.public_id))
    form = F.FollowUpForm(prefix="fu")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
    else:
        try:
            svc.record_follow_up(fu, done_on=form.done_on.data, still_employed=form.still_employed.data == "yes",
                                 pay=F.to_minor(form.monthly_pay.data), notes=form.notes.data, actor=current_user)
            db.session.commit()
            flash(f"{fu.months}-month check recorded.", "success")
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("placements.detail", public_id=p.public_id))


# ==== The learner's own job search ===============================================================

def _me() -> Learner | None:
    return db.session.scalar(select(Learner).where(Learner.user_id == current_user.id))


@myjobs_bp.get("/")
@permission_required("self.jobs")
def index():
    learner = _me()
    if learner is None:
        return render_template("jobs/my_jobs.html", learner=None, jobs=[], applications=[])
    vacancies = db.session.scalars(svc.visible_to_learners_stmt().order_by(Vacancy.closing_date.asc().nullslast(),
                                                                           Vacancy.created_at.desc())).all()
    applied = {a.vacancy_id: a for a in db.session.scalars(select(Application).where(
        Application.learner_id == learner.id).order_by(Application.created_at.desc())).all()}
    fits = {v.id: svc.score(v, learner) for v in vacancies}
    jobs = sorted(vacancies, key=lambda v: (v.id in applied, -(fits[v.id]["score"] or 0)))
    return render_template("jobs/my_jobs.html", learner=learner, jobs=jobs, fits=fits, applied=applied,
                           applications=list(applied.values()))


def _my_vacancy(public_id: str) -> tuple[Learner, Vacancy, Application | None]:
    learner = _me()
    if learner is None:
        abort(404)
    v = _one(Vacancy, public_id)
    mine = next((a for a in v.applications if a.learner_id == learner.id), None)
    visible = db.session.scalar(svc.visible_to_learners_stmt().where(Vacancy.id == v.id))
    if visible is None and mine is None:
        abort(404)
    return learner, v, mine


@myjobs_bp.get("/<public_id>")
@permission_required("self.jobs")
def job(public_id):
    learner, v, mine = _my_vacancy(public_id)
    can_apply = mine is None and v.is_accepting and v.open_to_learners and learner.status in svc.CANDIDATE_STATUSES
    return render_template("jobs/my_job.html", v=v, mine=mine, fit=svc.score(v, learner), learner=learner,
                           form=F.ApplyForm() if can_apply else None)


@myjobs_bp.post("/<public_id>/apply")
@permission_required("self.jobs")
def apply(public_id):
    learner, v, mine = _my_vacancy(public_id)
    form = F.ApplyForm()
    if not form.validate_on_submit():
        flash(_errors(form), "error")
    else:
        try:
            svc.apply(v, learner, cover_note=form.cover_note.data, user=current_user)
            db.session.commit()
            flash(f"Application sent. {g.workspace.name} will be in touch about next steps.", "success")
        except svc.EmploymentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("myjobs.job", public_id=v.public_id))


@myjobs_bp.post("/<public_id>/withdraw")
@permission_required("self.jobs")
def withdraw(public_id):
    learner, v, mine = _my_vacancy(public_id)
    if mine is None:
        abort(404)
    try:
        svc.withdraw(mine, user=current_user)
        db.session.commit()
        flash("Application withdrawn.", "success")
    except svc.EmploymentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("myjobs.job", public_id=v.public_id))
