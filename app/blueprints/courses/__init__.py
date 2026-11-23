"""Course builder and course management (/app/courses)."""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.access import can, load_workspace, permission_required
from app.auth.forms import _c
from app.blueprints.courses.forms import (
    CourseForm,
    CourseStatusForm,
    GradeForm,
    LessonFileForm,
    LessonForm,
    ModuleForm,
    NewLessonForm,
)
from app.extensions import db
from app.models import (
    AssignmentSubmission,
    Course,
    CourseEnrolment,
    CourseModule,
    CourseStatus,
    Learner,
    LearnerStatus,
    Lesson,
    LessonFile,
    Programme,
    ProgrammeStatus,
    QuizQuestion,
)
from app.services import files, limits
from app.services import lms as svc

bp = Blueprint("courses", __name__)
TABS = ("content", "learners", "submissions", "settings")
UPLOAD_ERRORS = (files.UploadError, limits.UsageLimitExceeded)


@bp.before_request
@login_required
def _workspace():
    return load_workspace(required=True)


def _course(public_id: str) -> Course:
    return db.session.scalar(select(Course).where(Course.public_id == public_id)) or abort(404)


def _module(c: Course, module_id: int) -> CourseModule:
    return db.session.scalar(select(CourseModule).where(CourseModule.id == module_id,
                                                        CourseModule.course_id == c.id)) or abort(404)


def _lesson(c: Course, lesson_id: str) -> Lesson:
    lesson = db.session.scalar(select(Lesson).where(Lesson.public_id == lesson_id))
    if lesson is None or lesson.module.course_id != c.id:
        abort(404)
    return lesson


def _errors(form) -> str:
    return " ".join(e for f in form for e in f.errors) or "Check the form and try again."


def _back(c: Course, tab: str = "content"):
    return redirect(url_for("courses.detail", public_id=c.public_id, tab=tab))


def _programme_choices():
    progs = db.session.scalars(select(Programme).where(Programme.status != ProgrammeStatus.CANCELLED)
                               .order_by(Programme.code.desc())).all()
    return _c([("", "None: a standalone course")] + [(p.public_id, f"{p.code} · {p.name}") for p in progs])


def _course_data(form: CourseForm) -> dict:
    prog = None
    if form.programme.data:
        prog = db.session.scalar(select(Programme).where(Programme.public_id == form.programme.data))
    return {"title": form.title.data, "summary": form.summary.data, "category": form.category.data,
            "level": form.level.data, "estimated_hours": form.estimated_hours.data,
            "programme_id": prog.id if prog else None}


@bp.get("/")
@permission_required("courses.view")
def index():
    status = request.args.get("status") or ""
    stmt = select(Course).order_by(Course.updated_at.desc())
    if status in CourseStatus.LABELS:
        stmt = stmt.where(Course.status == status)
    elif status != "all":
        stmt = stmt.where(Course.status != CourseStatus.ARCHIVED)
    courses = db.session.scalars(stmt.options(
        selectinload(Course.enrolments), selectinload(Course.programme),
        selectinload(Course.modules).selectinload(CourseModule.lessons))).all()
    return render_template("courses/index.html", courses=courses, status=status, statuses=CourseStatus.LABELS,
                           usage={"used": limits.usage(g.workspace, "courses"),
                                  "limit": limits.limit(g.workspace, "courses")})


@bp.route("/new", methods=["GET", "POST"])
@permission_required("courses.manage")
def new():
    form = CourseForm()
    form.programme.choices = _programme_choices()
    if request.method == "GET" and request.args.get("programme"):
        form.programme.data = request.args["programme"]
    if form.validate_on_submit():
        try:
            c = svc.create_course(g.workspace, _course_data(form), actor=current_user)
            db.session.commit()
            flash("Course created. Add lessons, then publish it.", "success")
            return _back(c)
        except (svc.CourseError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("courses/new.html", form=form)


@bp.get("/<public_id>")
@permission_required("courses.view")
def detail(public_id):
    c = _course(public_id)
    tab = request.args.get("tab", "content")
    tab = tab if tab in TABS else "content"
    manage = can("courses.manage")
    ctx = {"c": c, "tab": tab, "tabs": TABS, "manage": manage, "problems": svc.publish_problems(c)}
    if manage:
        cf = CourseForm(prefix="c", obj=c)
        cf.programme.choices = _programme_choices()
        if request.method == "GET":
            cf.programme.data = c.programme.public_id if c.programme else ""
        ctx.update(course_form=cf, status_form=CourseStatusForm(prefix="st", data={"status": c.status}),
                   module_form=ModuleForm(prefix="m"), lesson_form=NewLessonForm(prefix="l"))
    if tab == "submissions":
        ctx["submissions"] = db.session.scalars(
            select(AssignmentSubmission).join(Lesson).join(CourseModule)
            .where(CourseModule.course_id == c.id).order_by(AssignmentSubmission.graded_at.is_not(None),
                                                            AssignmentSubmission.submitted_at.desc())).all()
    return render_template("courses/detail.html", **ctx)


@bp.post("/<public_id>/settings")
@permission_required("courses.manage")
def settings(public_id):
    c = _course(public_id)
    form = CourseForm(prefix="c")
    form.programme.choices = _programme_choices()
    if form.validate_on_submit():
        changed = svc.update_course(c, _course_data(form), actor=current_user)
        db.session.commit()
        flash("Saved." if changed else "Nothing to save: no changes.", "success" if changed else "info")
    else:
        flash(_errors(form), "error")
    return _back(c, "settings")


@bp.post("/<public_id>/status")
@permission_required("courses.manage")
def status(public_id):
    c = _course(public_id)
    form = CourseStatusForm(prefix="st")
    if form.validate_on_submit():
        try:
            svc.set_status(c, form.status.data, actor=current_user)
            db.session.commit()
            flash(f"The course is now {c.status_label.lower()}.", "success")
        except (svc.CourseError, limits.UsageLimitExceeded) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return _back(c, request.form.get("back") or "settings")


# ---- Modules ------------------------------------------------------------------------------------

@bp.post("/<public_id>/modules")
@permission_required("courses.manage")
def add_module(public_id):
    c = _course(public_id)
    form = ModuleForm(prefix="m")
    if form.validate_on_submit():
        svc.add_module(c, form.title.data, actor=current_user)
        db.session.commit()
        flash("Module added.", "success")
    else:
        flash(_errors(form), "error")
    return _back(c)


@bp.post("/<public_id>/modules/<int:module_id>/rename")
@permission_required("courses.manage")
def rename_module(public_id, module_id):
    c = _course(public_id)
    m = _module(c, module_id)
    title = (request.form.get("title") or "").strip()
    if 1 <= len(title) <= 160:
        m.title = title
        db.session.commit()
    return _back(c)


@bp.post("/<public_id>/modules/<int:module_id>/move/<direction>")
@permission_required("courses.manage")
def move_module(public_id, module_id, direction):
    c = _course(public_id)
    svc.move_module(_module(c, module_id), -1 if direction == "up" else 1)
    db.session.commit()
    return _back(c)


@bp.post("/<public_id>/modules/<int:module_id>/delete")
@permission_required("courses.manage")
def delete_module(public_id, module_id):
    c = _course(public_id)
    try:
        svc.delete_module(_module(c, module_id), actor=current_user)
        db.session.commit()
        flash("Module deleted.", "success")
    except svc.CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(c)


# ---- Lessons ------------------------------------------------------------------------------------

@bp.post("/<public_id>/modules/<int:module_id>/lessons")
@permission_required("courses.manage")
def add_lesson(public_id, module_id):
    c = _course(public_id)
    m = _module(c, module_id)
    form = NewLessonForm(prefix="l")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
        return _back(c)
    try:
        lesson = svc.add_lesson(m, {"title": form.title.data, "kind": form.kind.data}, actor=current_user)
        db.session.commit()
        return redirect(url_for("courses.lesson", public_id=c.public_id, lesson_id=lesson.public_id))
    except svc.CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _back(c)


@bp.route("/<public_id>/lessons/<lesson_id>", methods=["GET", "POST"])
@permission_required("courses.view")
def lesson(public_id, lesson_id):
    c = _course(public_id)
    lesson = _lesson(c, lesson_id)
    manage = can("courses.manage")
    form = LessonForm(obj=lesson) if manage else None
    if request.method == "POST":
        if not manage:
            abort(403)
        if form.validate_on_submit():
            data = {k: form[k].data for k in ("title", "body", "video_url", "duration_minutes", "pass_mark",
                                              "max_attempts", "time_limit_minutes", "show_answers", "due_date",
                                              "max_score")}
            data = {k: v for k, v in data.items() if v is not None or k in ("video_url", "due_date",
                                                                             "time_limit_minutes", "body")}
            try:
                svc.update_lesson(lesson, data, actor=current_user)
                db.session.commit()
                flash("Lesson saved.", "success")
                return redirect(url_for("courses.lesson", public_id=public_id, lesson_id=lesson_id))
            except svc.CourseError as exc:
                db.session.rollback()
                flash(str(exc), "error")
    return render_template("courses/lesson.html", c=c, lesson=lesson, form=form, manage=manage,
                           file_form=LessonFileForm(prefix="f") if manage else None)


@bp.post("/<public_id>/lessons/<lesson_id>/move/<direction>")
@permission_required("courses.manage")
def move_lesson(public_id, lesson_id, direction):
    c = _course(public_id)
    svc.move_lesson(_lesson(c, lesson_id), -1 if direction == "up" else 1)
    db.session.commit()
    return _back(c)


@bp.post("/<public_id>/lessons/<lesson_id>/delete")
@permission_required("courses.manage")
def delete_lesson(public_id, lesson_id):
    c = _course(public_id)
    try:
        svc.delete_lesson(_lesson(c, lesson_id), actor=current_user)
        db.session.commit()
        flash("Lesson deleted.", "success")
    except svc.CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(c)


@bp.post("/<public_id>/lessons/<lesson_id>/files")
@permission_required("courses.manage")
def add_file(public_id, lesson_id):
    c = _course(public_id)
    lesson = _lesson(c, lesson_id)
    form = LessonFileForm(prefix="f")
    if not form.validate_on_submit():
        flash(_errors(form), "error")
    else:
        try:
            svc.add_lesson_file(lesson, form.file.data, title=form.title.data, actor=current_user)
            db.session.commit()
            flash("File added.", "success")
        except UPLOAD_ERRORS as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("courses.lesson", public_id=public_id, lesson_id=lesson_id))


def _lesson_file(lesson: Lesson, file_id: str) -> LessonFile:
    lf = db.session.scalar(select(LessonFile).where(LessonFile.public_id == file_id,
                                                    LessonFile.lesson_id == lesson.id))
    return lf or abort(404)


@bp.get("/<public_id>/lessons/<lesson_id>/files/<file_id>")
@permission_required("courses.view")
def download_file(public_id, lesson_id, file_id):
    c = _course(public_id)
    return files.serve(_lesson_file(_lesson(c, lesson_id), file_id).file, actor=current_user, log=False)


@bp.post("/<public_id>/lessons/<lesson_id>/files/<file_id>/delete")
@permission_required("courses.manage")
def delete_file(public_id, lesson_id, file_id):
    c = _course(public_id)
    lesson = _lesson(c, lesson_id)
    svc.delete_lesson_file(_lesson_file(lesson, file_id))
    db.session.commit()
    flash("File removed.", "success")
    return redirect(url_for("courses.lesson", public_id=public_id, lesson_id=lesson_id))


@bp.post("/<public_id>/lessons/<lesson_id>/questions")
@permission_required("courses.manage")
def add_question(public_id, lesson_id):
    c = _course(public_id)
    lesson = _lesson(c, lesson_id)
    options = [(request.form.get(f"opt{i}", ""), request.form.get(f"ok{i}") == "y") for i in range(1, 7)]
    try:
        svc.add_question(lesson, prompt=request.form.get("prompt", ""), options=options,
                         points=request.form.get("points", type=int) or 1,
                         explanation=request.form.get("explanation"))
        db.session.commit()
        flash("Question added.", "success")
    except svc.CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("courses.lesson", public_id=public_id, lesson_id=lesson_id) + "#questions")


@bp.post("/<public_id>/lessons/<lesson_id>/questions/<int:question_id>/delete")
@permission_required("courses.manage")
def delete_question(public_id, lesson_id, question_id):
    c = _course(public_id)
    lesson = _lesson(c, lesson_id)
    q = db.session.scalar(select(QuizQuestion).where(QuizQuestion.id == question_id,
                                                     QuizQuestion.lesson_id == lesson.id)) or abort(404)
    db.session.delete(q)
    db.session.commit()
    flash("Question deleted.", "success")
    return redirect(url_for("courses.lesson", public_id=public_id, lesson_id=lesson_id) + "#questions")


# ---- Learners and marking ----------------------------------------------------------------------------

@bp.route("/<public_id>/enrol", methods=["GET", "POST"])
@permission_required("courses.manage", "learners.view")
def enrol(public_id):
    c = _course(public_id)
    if request.method == "POST":
        ids = request.form.getlist("learner")
        learners = db.session.scalars(select(Learner).where(Learner.public_id.in_(ids))).all() if ids else []
        added = svc.enrol(c, learners, actor=current_user)
        db.session.commit()
        flash(f"Enrolled {len(added)} learner{'s' if len(added) != 1 else ''}.", "success" if added else "info")
        return _back(c, "learners")
    q = (request.args.get("q") or "").strip()
    taken = {e.learner_id for e in c.enrolments}
    stmt = select(Learner).where(Learner.status != LearnerStatus.ARCHIVED).order_by(Learner.last_name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Learner.first_name.ilike(like) | Learner.last_name.ilike(like) |
                          Learner.learner_no.ilike(like))
    candidates = [l for l in db.session.scalars(stmt.limit(300)) if l.id not in taken]
    return render_template("courses/enrol.html", c=c, candidates=candidates, q=q)


@bp.post("/<public_id>/enrolments/<int:enrolment_id>/remove")
@permission_required("courses.manage")
def remove_enrolment(public_id, enrolment_id):
    c = _course(public_id)
    ce = db.session.scalar(select(CourseEnrolment).where(CourseEnrolment.id == enrolment_id,
                                                         CourseEnrolment.course_id == c.id)) or abort(404)
    svc.unenrol(ce, actor=current_user)
    db.session.commit()
    flash("Removed from the course. Their progress is kept if they're enrolled again.", "success")
    return _back(c, "learners")


def _submission(c: Course, submission_id: str) -> AssignmentSubmission:
    sub = db.session.scalar(select(AssignmentSubmission).where(AssignmentSubmission.public_id == submission_id))
    if sub is None or sub.lesson.module.course_id != c.id:
        abort(404)
    return sub


@bp.route("/<public_id>/submissions/<submission_id>", methods=["GET", "POST"])
@permission_required("courses.manage")
def submission(public_id, submission_id):
    c = _course(public_id)
    sub = _submission(c, submission_id)
    form = GradeForm(obj=sub)
    if form.validate_on_submit():
        try:
            svc.grade(sub, score=form.score.data, feedback=form.feedback.data, actor=current_user)
            db.session.commit()
            flash(f"Marked: {sub.score}/{sub.lesson.max_score}.", "success")
            return _back(c, "submissions")
        except svc.CourseError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("courses/submission.html", c=c, sub=sub, form=form)


@bp.get("/<public_id>/submissions/<submission_id>/file")
@permission_required("courses.manage")
def submission_file(public_id, submission_id):
    c = _course(public_id)
    sub = _submission(c, submission_id)
    if sub.file is None:
        abort(404)
    return files.serve(sub.file, actor=current_user)
