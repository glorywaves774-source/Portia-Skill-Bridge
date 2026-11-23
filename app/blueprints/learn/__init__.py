"""The learner's learning space (/app/learn): courses, lessons, quizzes, exams, assignments, programmes."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.access import load_workspace, permission_required
from app.extensions import db
from app.models import (
    Course,
    CourseEnrolment,
    Enrolment,
    EnrolmentStatus,
    Learner,
    Lesson,
    LessonFile,
    LessonKind,
    QuizAttempt,
    TrainingSession,
)
from app.models.base import utcnow
from app.services import files, limits
from app.services import lms as svc
from app.services import training as training_svc

bp = Blueprint("learn", __name__)


@bp.before_request
@login_required
def _workspace():
    return load_workspace(required=True)


def _me() -> Learner | None:
    return db.session.scalar(select(Learner).where(Learner.user_id == current_user.id))


def _require_me() -> Learner:
    me = _me()
    if me is None:
        abort(404)
    return me


def _course_for(me: Learner, public_id: str) -> tuple[Course, CourseEnrolment]:
    c = db.session.scalar(select(Course).where(Course.public_id == public_id))
    ce = svc.enrolment_for(me, c) if c else None
    if ce is None:
        abort(404)  # not published, or not enrolled: behave as if it doesn't exist
    return c, ce


def _lesson_for(c: Course, lesson_id: str) -> Lesson:
    lesson = db.session.scalar(select(Lesson).where(Lesson.public_id == lesson_id))
    if lesson is None or lesson.module.course_id != c.id:
        abort(404)
    return lesson


@bp.get("/")
@permission_required("self.learning")
def home():
    me = _me()
    if me is None:
        return render_template("learn/home.html", me=None)
    courses = [ce for ce in db.session.scalars(select(CourseEnrolment).where(CourseEnrolment.learner_id == me.id))
               if ce.course.is_published]
    courses.sort(key=lambda ce: (ce.completed_at is not None, -(ce.last_activity_at.timestamp()
                                                                 if ce.last_activity_at else 0)))
    enrolments = db.session.scalars(select(Enrolment).where(Enrolment.learner_id == me.id)
                                    .order_by(Enrolment.enrolled_on.desc())).all()
    programme_ids = [e.programme_id for e in enrolments if e.status == EnrolmentStatus.ENROLLED]
    upcoming = db.session.scalars(select(TrainingSession).where(TrainingSession.programme_id.in_(programme_ids),
                                                                TrainingSession.date >= date.today())
                                  .order_by(TrainingSession.date, TrainingSession.start_time).limit(5)).all() \
        if programme_ids else []
    rates = {e.id: training_svc.attendance_rates(e.programme).get(me.id) for e in enrolments}
    from app.models import Certificate
    from app.services import assessments as assess_svc
    certificates = db.session.scalars(select(Certificate).where(Certificate.learner_id == me.id,
                                                                Certificate.revoked_at.is_(None))
                                      .order_by(Certificate.issued_on.desc())).all()
    profiles = [(fw, assess_svc.profile(me, fw)) for fw in assess_svc.frameworks_for(me)]
    return render_template("learn/home.html", me=me, courses=courses, enrolments=enrolments, rates=rates,
                           upcoming=upcoming, certificates=certificates, profiles=profiles)


@bp.get("/certificates/<public_id>.pdf")
@permission_required("self.learning")
def certificate_pdf(public_id):
    from app.models import Certificate
    from app.services import certificates as cert_svc
    me = _require_me()
    cert = db.session.scalar(select(Certificate).where(Certificate.public_id == public_id,
                                                       Certificate.learner_id == me.id)) or abort(404)
    from flask import Response
    resp = Response(cert_svc.pdf(cert), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'inline; filename="certificate-{cert.number}.pdf"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.get("/courses/<public_id>")
@permission_required("self.learning")
def course(public_id):
    me = _require_me()
    c, ce = _course_for(me, public_id)
    done = svc.completed_lesson_ids(me, c)
    nxt = next((l for l in c.lessons if l.id not in done), None)
    return render_template("learn/course.html", c=c, ce=ce, done=done, next_lesson=nxt)


@bp.get("/courses/<public_id>/lessons/<lesson_id>")
@permission_required("self.learning")
def lesson(public_id, lesson_id):
    me = _require_me()
    c, ce = _course_for(me, public_id)
    lesson = _lesson_for(c, lesson_id)
    lessons = c.lessons
    i = lessons.index(lesson)
    ctx = {"c": c, "ce": ce, "lesson": lesson, "done": svc.completed_lesson_ids(me, c),
           "prev": lessons[i - 1] if i > 0 else None, "next": lessons[i + 1] if i + 1 < len(lessons) else None}
    if lesson.is_assessed:
        ctx.update(attempts=[a for a in svc.attempts(me, lesson) if a.submitted_at],
                   open_attempt=svc.open_attempt(me, lesson), left=svc.attempts_left(me, lesson),
                   best=svc.best_attempt(me, lesson))
    if lesson.kind == LessonKind.ASSIGNMENT:
        ctx["submission"] = svc.submission_for(me, lesson)
    ce.last_activity_at = utcnow()
    db.session.commit()
    return render_template("learn/lesson.html", **ctx)


@bp.post("/courses/<public_id>/lessons/<lesson_id>/complete")
@permission_required("self.learning")
def complete(public_id, lesson_id):
    me = _require_me()
    c, _ = _course_for(me, public_id)
    lesson = _lesson_for(c, lesson_id)
    if lesson.kind in (LessonKind.QUIZ, LessonKind.EXAM, LessonKind.ASSIGNMENT):
        abort(400)  # these complete by passing or submitting
    svc.complete_lesson(me, lesson)
    db.session.commit()
    lessons = c.lessons
    i = lessons.index(lesson)
    if i + 1 < len(lessons):
        return redirect(url_for("learn.lesson", public_id=public_id, lesson_id=lessons[i + 1].public_id))
    flash("Well done! You've reached the end of the course.", "success")
    return redirect(url_for("learn.course", public_id=public_id))


@bp.get("/courses/<public_id>/lessons/<lesson_id>/files/<file_id>")
@permission_required("self.learning")
def download(public_id, lesson_id, file_id):
    me = _require_me()
    c, _ = _course_for(me, public_id)
    lesson = _lesson_for(c, lesson_id)
    lf = db.session.scalar(select(LessonFile).where(LessonFile.public_id == file_id,
                                                    LessonFile.lesson_id == lesson.id)) or abort(404)
    return files.serve(lf.file, actor=current_user, log=False)


# ---- Quizzes and exams ------------------------------------------------------------------------

@bp.post("/courses/<public_id>/lessons/<lesson_id>/start")
@permission_required("self.learning")
def start(public_id, lesson_id):
    me = _require_me()
    c, _ = _course_for(me, public_id)
    lesson = _lesson_for(c, lesson_id)
    try:
        a = svc.start_attempt(me, lesson)
        db.session.commit()
    except svc.CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("learn.lesson", public_id=public_id, lesson_id=lesson_id))
    return redirect(url_for("learn.attempt", public_id=public_id, attempt_id=a.public_id))


def _attempt(me: Learner, c: Course, attempt_id: str) -> QuizAttempt:
    a = db.session.scalar(select(QuizAttempt).where(QuizAttempt.public_id == attempt_id,
                                                    QuizAttempt.learner_id == me.id))
    if a is None or a.lesson.module.course_id != c.id:
        abort(404)
    return a


@bp.route("/courses/<public_id>/attempts/<attempt_id>", methods=["GET", "POST"])
@permission_required("self.learning")
def attempt(public_id, attempt_id):
    me = _require_me()
    c, _ = _course_for(me, public_id)
    a = _attempt(me, c, attempt_id)
    if a.submitted_at is not None:
        return render_template("learn/result.html", c=c, a=a, lesson=a.lesson)
    if request.method == "POST":
        answers = {q.id: request.form.getlist(f"q{q.id}") for q in a.lesson.questions}
        try:
            svc.submit_attempt(a, {k: [int(x) for x in v if x.isdigit()] for k, v in answers.items()})
            db.session.commit()
        except svc.CourseError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("learn.attempt", public_id=public_id, attempt_id=attempt_id))
    return render_template("learn/take.html", c=c, a=a, lesson=a.lesson)


# ---- Assignments ------------------------------------------------------------------------------

@bp.post("/courses/<public_id>/lessons/<lesson_id>/submit")
@permission_required("self.learning")
def submit(public_id, lesson_id):
    me = _require_me()
    c, _ = _course_for(me, public_id)
    lesson = _lesson_for(c, lesson_id)
    try:
        svc.submit_assignment(me, lesson, text=request.form.get("text"), upload=request.files.get("file"))
        db.session.commit()
        flash("Submitted. Your trainer will mark it.", "success")
    except (svc.CourseError, files.UploadError, limits.UsageLimitExceeded) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("learn.lesson", public_id=public_id, lesson_id=lesson_id))


@bp.get("/courses/<public_id>/lessons/<lesson_id>/my-file")
@permission_required("self.learning")
def my_submission_file(public_id, lesson_id):
    me = _require_me()
    c, _ = _course_for(me, public_id)
    sub = svc.submission_for(me, _lesson_for(c, lesson_id))
    if sub is None or sub.file is None:
        abort(404)
    return files.serve(sub.file, actor=current_user, log=False)
