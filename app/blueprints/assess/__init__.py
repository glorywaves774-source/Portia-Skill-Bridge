"""Competency frameworks (/app/competencies), assessments (/app/assessments) and certificates (/app/certificates)."""
from __future__ import annotations

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.access import can, load_workspace, permission_required
from app.auth.forms import _c
from app.blueprints.assess.forms import (
    BulkIssueForm,
    CompetencyForm,
    EvidenceForm,
    FrameworkForm,
    FrameworkStatusForm,
    IssueForm,
    NewAssessmentForm,
    RevokeForm,
)
from app.extensions import db, limiter
from app.models import (
    Assessment,
    AssessmentEvidence,
    AssessmentStatus,
    Certificate,
    Competency,
    Course,
    EnrolmentStatus,
    Framework,
    FrameworkStatus,
    Learner,
    LearnerStatus,
    Programme,
    ProgrammeStatus,
)
from app.models.assessment import LEVELS
from app.services import assessments as svc
from app.services import certificates as certs
from app.services import files, limits

frameworks_bp = Blueprint("frameworks", __name__)
assessments_bp = Blueprint("assessments", __name__)
certificates_bp = Blueprint("certificates", __name__)


def _guard():
    return load_workspace(required=True)


for _bp in (frameworks_bp, assessments_bp, certificates_bp):
    _bp.before_request(login_required(_guard))


def _errors(form) -> str:
    return " ".join(e for f in form for e in f.errors) or "Check the form and try again."


# =============================== Frameworks ===============================

def _framework(public_id: str) -> Framework:
    return db.session.scalar(select(Framework).where(Framework.public_id == public_id)) or abort(404)


@frameworks_bp.get("/")
@permission_required("assessments.view")
def index():
    rows = db.session.scalars(select(Framework).order_by(Framework.status, Framework.name)).all()
    return render_template("assess/frameworks.html", frameworks=rows)


@frameworks_bp.route("/new", methods=["GET", "POST"])
@permission_required("competencies.manage")
def new():
    form = FrameworkForm()
    if form.validate_on_submit():
        fw = svc.create_framework(g.workspace, form.data, actor=current_user)
        db.session.commit()
        flash("Framework created. Add its competencies (units), then make it active.", "success")
        return redirect(url_for("frameworks.detail", public_id=fw.public_id))
    return render_template("assess/framework_form.html", form=form, fw=None)


@frameworks_bp.route("/<public_id>", methods=["GET", "POST"])
@permission_required("assessments.view")
def detail(public_id):
    fw = _framework(public_id)
    manage = can("competencies.manage")
    form = FrameworkForm(prefix="fw", obj=fw) if manage else None
    if request.method == "POST":
        if not manage:
            abort(403)
        if form.validate_on_submit():
            try:
                svc.update_framework(fw, {k: form[k].data for k in ("name", "trade", "level", "description")},
                                     actor=current_user)
                db.session.commit()
                flash("Saved.", "success")
            except svc.AssessmentError as exc:
                db.session.rollback()
                flash(str(exc), "error")
            return redirect(url_for("frameworks.detail", public_id=public_id))
    used = {cid for (cid,) in db.session.execute(select(Assessment.framework_id).where(Assessment.framework_id == fw.id))}
    return render_template("assess/framework.html", fw=fw, manage=manage, form=form,
                           comp_form=CompetencyForm(prefix="c") if manage else None,
                           status_form=FrameworkStatusForm(prefix="st", data={"status": fw.status}) if manage else None,
                           levels=LEVELS, in_use=bool(used))


@frameworks_bp.post("/<public_id>/status")
@permission_required("competencies.manage")
def status(public_id):
    fw = _framework(public_id)
    form = FrameworkStatusForm(prefix="st")
    if form.validate_on_submit():
        try:
            svc.set_framework_status(fw, form.status.data, actor=current_user)
            db.session.commit()
            flash(f"The framework is now {fw.status_label.lower()}.", "success")
        except svc.AssessmentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("frameworks.detail", public_id=public_id))


@frameworks_bp.post("/<public_id>/competencies")
@permission_required("competencies.manage")
def add_competency(public_id):
    fw = _framework(public_id)
    form = CompetencyForm(prefix="c")
    if form.validate_on_submit():
        try:
            svc.add_competency(fw, code=form.code.data, title=form.title.data, description=form.description.data,
                               criteria=form.criteria.data, required_level=int(form.required_level.data))
            db.session.commit()
            flash("Competency added.", "success")
        except svc.AssessmentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    else:
        flash(_errors(form), "error")
    return redirect(url_for("frameworks.detail", public_id=public_id) + "#competencies")


def _competency(fw: Framework, comp_id: int) -> Competency:
    return db.session.scalar(select(Competency).where(Competency.id == comp_id,
                                                      Competency.framework_id == fw.id)) or abort(404)


@frameworks_bp.route("/<public_id>/competencies/<int:comp_id>", methods=["GET", "POST"])
@permission_required("competencies.manage")
def edit_competency(public_id, comp_id):
    fw = _framework(public_id)
    c = _competency(fw, comp_id)
    form = CompetencyForm(obj=c, data={"criteria": "\n".join(c.criteria or []),
                                       "required_level": str(c.required_level)})
    if form.validate_on_submit():
        try:
            svc.update_competency(c, title=form.title.data, description=form.description.data,
                                  criteria=form.criteria.data, required_level=int(form.required_level.data))
            db.session.commit()
            flash("Competency saved.", "success")
            return redirect(url_for("frameworks.detail", public_id=public_id) + "#competencies")
        except svc.AssessmentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("assess/competency_form.html", fw=fw, c=c, form=form)


@frameworks_bp.post("/<public_id>/competencies/<int:comp_id>/delete")
@permission_required("competencies.manage")
def delete_competency(public_id, comp_id):
    fw = _framework(public_id)
    try:
        svc.delete_competency(_competency(fw, comp_id))
        db.session.commit()
        flash("Competency deleted.", "success")
    except svc.AssessmentError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("frameworks.detail", public_id=public_id) + "#competencies")


# =============================== Assessments ===============================

def _assessment(public_id: str) -> Assessment:
    return db.session.scalar(select(Assessment).where(Assessment.public_id == public_id)) or abort(404)


@assessments_bp.get("/")
@permission_required("assessments.view")
def index():
    stmt = select(Assessment).order_by(Assessment.status, Assessment.assessed_on.desc()).options(
        selectinload(Assessment.learner), selectinload(Assessment.framework), selectinload(Assessment.assessor))
    status = request.args.get("status") or ""
    if status in AssessmentStatus.LABELS:
        stmt = stmt.where(Assessment.status == status)
    fw_id = request.args.get("framework")
    frameworks = db.session.scalars(select(Framework).order_by(Framework.name)).all()
    fw = next((f for f in frameworks if f.public_id == fw_id), None)
    if fw:
        stmt = stmt.where(Assessment.framework_id == fw.id)
    if request.args.get("mine") == "1":
        stmt = stmt.where(Assessment.assessor_id == current_user.id)
    rows = db.session.scalars(stmt.limit(300)).all()
    return render_template("assess/assessments.html", assessments=rows, frameworks=frameworks, status=status,
                           fw=fw, mine=request.args.get("mine") == "1")


def _learner_choices():
    learners = db.session.scalars(select(Learner).where(Learner.status != LearnerStatus.ARCHIVED)
                                  .order_by(Learner.last_name, Learner.first_name).limit(2000)).all()
    return _c([("", "Choose…")] + [(l.public_id, f"{l.full_name} · {l.learner_no}") for l in learners])


@assessments_bp.route("/new", methods=["GET", "POST"])
@permission_required("assessments.conduct", "learners.view")
def new():
    form = NewAssessmentForm()
    form.learner.choices = _learner_choices()
    active = db.session.scalars(select(Framework).where(Framework.status == FrameworkStatus.ACTIVE)
                                .order_by(Framework.name)).all()
    form.framework.choices = _c([("", "Choose…")] + [(f.public_id, f.name + (f" ({f.level})" if f.level else ""))
                                                     for f in active])
    progs = db.session.scalars(select(Programme).where(Programme.status != ProgrammeStatus.CANCELLED)
                               .order_by(Programme.code.desc())).all()
    form.programme.choices = _c([("", "None")] + [(p.public_id, f"{p.code} · {p.name}") for p in progs])
    if request.method == "GET":
        form.learner.data = request.args.get("learner", "")
    if form.validate_on_submit():
        learner = db.session.scalar(select(Learner).where(Learner.public_id == form.learner.data))
        fw = next(f for f in active if f.public_id == form.framework.data)
        prog = next((p for p in progs if p.public_id == form.programme.data), None)
        try:
            a = svc.create_assessment(g.workspace, learner=learner, framework=fw, kind=form.kind.data,
                                      assessed_on=form.assessed_on.data, programme=prog, location=form.location.data,
                                      actor=current_user)
            db.session.commit()
            return redirect(url_for("assessments.detail", public_id=a.public_id))
        except svc.AssessmentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("assess/assessment_new.html", form=form, no_frameworks=not active)


@assessments_bp.route("/<public_id>", methods=["GET", "POST"])
@permission_required("assessments.view")
def detail(public_id):
    a = _assessment(public_id)
    editable = svc.can_edit(a, current_user, g.permissions)
    if request.method == "POST":
        if not editable:
            abort(403)
        ratings = {}
        for c in a.framework.competencies:
            raw = request.form.get(f"level-{c.id}")
            ratings[c.id] = {"level": int(raw) if raw and raw.isdigit() else None,
                             "criteria": [int(x) for x in request.form.getlist(f"crit-{c.id}") if x.isdigit()],
                             "comment": request.form.get(f"note-{c.id}", "")}
        try:
            svc.save_ratings(a, ratings, comments=request.form.get("comments"), actor=current_user)
            if request.form.get("action") == "finalise":
                svc.finalise(a, actor=current_user)
                db.session.commit()
                flash(f"Assessment finalised: {a.result_label}.", "success")
            else:
                db.session.commit()
                flash("Saved. You can come back and finish later.", "success")
        except svc.AssessmentError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("assessments.detail", public_id=public_id))
    return render_template("assess/assessment.html", a=a, editable=editable, levels=LEVELS,
                           evidence_form=EvidenceForm(prefix="ev") if editable else None)


@assessments_bp.post("/<public_id>/evidence")
@permission_required("assessments.conduct")
def add_evidence(public_id):
    a = _assessment(public_id)
    if not svc.can_edit(a, current_user, g.permissions):
        abort(403)
    form = EvidenceForm(prefix="ev")
    if form.validate_on_submit():
        try:
            svc.add_evidence(a, form.file.data, title=form.title.data, actor=current_user)
            db.session.commit()
            flash("Evidence added.", "success")
        except (svc.AssessmentError, files.UploadError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    else:
        flash(_errors(form), "error")
    return redirect(url_for("assessments.detail", public_id=public_id) + "#evidence")


def _evidence(a: Assessment, ev_id: str) -> AssessmentEvidence:
    return db.session.scalar(select(AssessmentEvidence).where(AssessmentEvidence.public_id == ev_id,
                                                              AssessmentEvidence.assessment_id == a.id)) or abort(404)


@assessments_bp.get("/<public_id>/evidence/<ev_id>")
@permission_required("assessments.view")
def evidence(public_id, ev_id):
    a = _assessment(public_id)
    return files.serve(_evidence(a, ev_id).file, actor=current_user)


@assessments_bp.post("/<public_id>/evidence/<ev_id>/delete")
@permission_required("assessments.conduct")
def delete_evidence(public_id, ev_id):
    a = _assessment(public_id)
    if not svc.can_edit(a, current_user, g.permissions):
        abort(403)
    svc.delete_evidence(_evidence(a, ev_id))
    db.session.commit()
    flash("Evidence removed.", "success")
    return redirect(url_for("assessments.detail", public_id=public_id) + "#evidence")


@assessments_bp.post("/<public_id>/delete")
@permission_required("assessments.conduct")
def delete(public_id):
    a = _assessment(public_id)
    if not svc.can_edit(a, current_user, g.permissions):
        abort(403)
    svc.delete_draft(a)
    db.session.commit()
    flash("Draft assessment deleted.", "success")
    return redirect(url_for("assessments.index"))


@assessments_bp.get("/gaps")
@permission_required("assessments.view")
def gaps():
    frameworks = db.session.scalars(select(Framework).where(Framework.status != FrameworkStatus.DRAFT)
                                    .order_by(Framework.name)).all()
    programmes = db.session.scalars(select(Programme).order_by(Programme.code.desc())).all()
    fw = next((f for f in frameworks if f.public_id == request.args.get("framework")), frameworks[0] if frameworks else None)
    prog = next((p for p in programmes if p.public_id == request.args.get("programme")), None)
    result = None
    if fw:
        if prog:
            learners = [e.learner for e in prog.enrolments if e.status in (EnrolmentStatus.ENROLLED,
                                                                           EnrolmentStatus.COMPLETED)]
        else:
            ids = {a.learner_id for a in db.session.scalars(select(Assessment).where(Assessment.framework_id == fw.id))}
            learners = db.session.scalars(select(Learner).where(Learner.id.in_(ids))).all() if ids else []
        result = svc.cohort_gap(fw, learners)
    return render_template("assess/gaps.html", frameworks=frameworks, programmes=programmes, fw=fw, prog=prog,
                           result=result)


# =============================== Certificates ===============================

def _certificate(public_id: str) -> Certificate:
    return db.session.scalar(select(Certificate).where(Certificate.public_id == public_id)) or abort(404)


@certificates_bp.get("/")
@permission_required("certificates.issue")
def index():
    q = (request.args.get("q") or "").strip()
    stmt = select(Certificate).join(Learner).order_by(Certificate.issued_on.desc(), Certificate.id.desc()).options(
        selectinload(Certificate.learner))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Certificate.number.ilike(like) | Certificate.title.ilike(like) |
                          Learner.first_name.ilike(like) | Learner.last_name.ilike(like) |
                          Certificate.verification_code.ilike(like))
    rows = db.session.scalars(stmt.limit(300)).all()
    programmes = db.session.scalars(select(Programme).order_by(Programme.code.desc())).all()
    courses = db.session.scalars(select(Course).order_by(Course.title)).all()
    ready_prog = [(p, sum(1 for e in p.enrolments if e.status == EnrolmentStatus.COMPLETED and
                          not certs._valid_for(e.learner, enrolment_id=e.id))) for p in programmes]
    ready_course = [(c, sum(1 for ce in c.enrolments if ce.completed_at and
                            not certs._valid_for(ce.learner, course_id=c.id))) for c in courses]
    return render_template("assess/certificates.html", certificates=rows, q=q,
                           ready_prog=[r for r in ready_prog if r[1]], ready_course=[r for r in ready_course if r[1]],
                           bulk_form=BulkIssueForm(prefix="b"))


@certificates_bp.route("/issue/<learner_id>", methods=["GET", "POST"])
@permission_required("certificates.issue", "learners.view")
def issue(learner_id):
    learner = db.session.scalar(select(Learner).where(Learner.public_id == learner_id)) or abort(404)
    options = certs.eligible(learner)
    form = IssueForm(data={"signatory_name": current_user.full_name} if request.method == "GET" else None)
    form.basis.choices = _c((f"{o['kind']}:{o['id']}", f"{o['title']} — {o['detail']}") for o in options)
    if form.validate_on_submit():
        kind, _, source = form.basis.data.partition(":")
        try:
            cert = certs.issue(g.workspace, learner, kind=kind, source_id=int(source), actor=current_user,
                               issued_on=form.issued_on.data, expires_on=form.expires_on.data,
                               signatory_name=form.signatory_name.data, signatory_title=form.signatory_title.data)
            db.session.commit()
            flash(f"Certificate {cert.number} issued.", "success")
            return redirect(url_for("certificates.detail", public_id=cert.public_id))
        except certs.CertificateError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("assess/issue.html", learner=learner, options=options, form=form)


@certificates_bp.post("/bulk/<kind>/<public_id>")
@permission_required("certificates.issue")
def bulk(kind, public_id):
    form = BulkIssueForm(prefix="b")
    form.validate()
    options = {"signatory_name": form.signatory_name.data, "signatory_title": form.signatory_title.data}
    if kind == "programme":
        p = db.session.scalar(select(Programme).where(Programme.public_id == public_id)) or abort(404)
        issued = certs.issue_for_programme(g.workspace, p, actor=current_user, **options)
    elif kind == "course":
        c = db.session.scalar(select(Course).where(Course.public_id == public_id)) or abort(404)
        issued = certs.issue_for_course(g.workspace, c, actor=current_user, **options)
    else:
        abort(404)
    db.session.commit()
    flash(f"Issued {len(issued)} certificate{'s' if len(issued) != 1 else ''}.", "success" if issued else "info")
    return redirect(url_for("certificates.index"))


@certificates_bp.get("/<public_id>")
@permission_required("certificates.issue")
def detail(public_id):
    cert = _certificate(public_id)
    return render_template("assess/certificate.html", cert=cert, revoke_form=RevokeForm(prefix="rv"),
                           competencies=certs.competencies_for(cert), verify_url=certs.verify_url(cert))


@certificates_bp.get("/<public_id>.pdf")
def pdf(public_id):
    if not (can("certificates.issue") or can("learners.view")):
        abort(403)
    cert = _certificate(public_id)
    return _pdf_response(cert)


def _pdf_response(cert: Certificate) -> Response:
    resp = Response(certs.pdf(cert), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'inline; filename="certificate-{cert.number}.pdf"'
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@certificates_bp.post("/<public_id>/revoke")
@permission_required("certificates.issue")
def revoke(public_id):
    cert = _certificate(public_id)
    form = RevokeForm(prefix="rv")
    if form.validate_on_submit():
        try:
            certs.revoke(cert, reason=form.reason.data, actor=current_user)
            db.session.commit()
            flash(f"Certificate {cert.number} revoked. The verification page now shows it as revoked.", "success")
        except certs.CertificateError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    else:
        flash(_errors(form), "error")
    return redirect(url_for("certificates.detail", public_id=public_id))


# =============================== Public verification ===============================

verify_bp = Blueprint("verify", __name__)


@verify_bp.get("/verify")
@limiter.limit("30 per minute")
def form():
    code = request.args.get("code")
    if code:
        return redirect(url_for("verify.check", code=certs.normalise_code(code) or code.strip()[:20]))
    return render_template("assess/verify.html", cert=None, searched=False)


@verify_bp.get("/verify/<code>")
@limiter.limit("30 per minute")
def check(code):
    cert = certs.find_by_code(code)
    from app.tenancy import platform_scope
    with platform_scope():
        context = None
        if cert is not None:
            context = {"cert": cert, "tenant": cert.tenant, "learner_name": cert.learner.full_name,
                       "competencies": certs.competencies_for(cert)}
        resp = render_template("assess/verify.html", searched=True, code=code, **(context or {"cert": None}))
    return resp, (200 if cert else 404)
