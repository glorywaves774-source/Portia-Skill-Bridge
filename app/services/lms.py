"""Online courses: building, publishing, enrolment, progress, quizzes, exams and assignments."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

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
    LessonKind,
    LessonProgress,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    Tenant,
)
from app.models.base import utcnow
from app.services import audit, files, limits

COURSE_FIELDS = ("title", "summary", "category", "level", "estimated_hours", "programme_id")
LESSON_FIELDS = ("title", "kind", "body", "video_url", "duration_minutes", "pass_mark", "max_attempts",
                 "time_limit_minutes", "show_answers", "due_date", "max_score")
GRACE = timedelta(seconds=60)  # allowance for slow connections when an exam's time runs out


class CourseError(ValueError):
    pass


@limits.register_counter("courses")
def _count_courses(tenant: Tenant) -> int:
    return db.session.scalar(select(func.count(Course.id)).where(
        Course.tenant_id == tenant.id, Course.status != CourseStatus.ARCHIVED)) or 0


def _strip(data: dict, fields) -> dict:
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in data.items() if k in fields}


# ---- Courses -------------------------------------------------------------------------------

def create_course(tenant: Tenant, data: dict, *, actor) -> Course:
    limits.check(tenant, "courses")
    data = _strip(data, COURSE_FIELDS)
    if not data.get("title"):
        raise CourseError("Give the course a title.")
    c = Course(tenant_id=tenant.id, created_by_id=getattr(actor, "id", None), **data)
    db.session.add(c)
    db.session.flush()
    db.session.add(CourseModule(tenant_id=tenant.id, course=c, title="Module 1", position=1))
    audit.record("course.created", tenant_id=tenant.id, actor=actor, entity_type="course", entity_id=c.id,
                 summary=f"Created course “{c.title}”")
    return c


def update_course(c: Course, data: dict, *, actor) -> list[str]:
    data = _strip(data, COURSE_FIELDS)
    changed = [k for k, v in data.items() if getattr(c, k) != v]
    for k in changed:
        setattr(c, k, data[k])
    if "programme_id" in changed and c.is_published and c.programme is not None:
        _enrol_programme_learners(c, actor)
    if changed:
        audit.record("course.updated", tenant_id=c.tenant_id, actor=actor, entity_type="course", entity_id=c.id,
                     summary=f"“{c.title}”: changed {', '.join(k.replace('_', ' ') for k in changed)}")
    return changed


def publish_problems(c: Course) -> list[str]:
    problems = []
    if not c.lessons:
        problems.append("Add at least one lesson.")
    for lesson in c.lessons:
        if lesson.is_assessed:
            if not lesson.questions:
                problems.append(f"“{lesson.title}” has no questions.")
            for q in lesson.questions:
                if not q.correct_ids:
                    problems.append(f"A question in “{lesson.title}” has no correct answer.")
        if lesson.kind == LessonKind.VIDEO and not lesson.embed_url:
            problems.append(f"“{lesson.title}” needs a YouTube or Vimeo link.")
        if lesson.kind == LessonKind.FILES and not lesson.files:
            problems.append(f"“{lesson.title}” has no files to download.")
    return problems


def set_status(c: Course, status: str, *, actor) -> None:
    if status not in CourseStatus.LABELS:
        raise CourseError("Unknown status.")
    if status == c.status:
        return
    if status == CourseStatus.PUBLISHED:
        problems = publish_problems(c)
        if problems:
            raise CourseError("Before publishing: " + " ".join(problems))
        if c.status == CourseStatus.ARCHIVED:
            limits.check(c.tenant, "courses")
        c.published_at = c.published_at or utcnow()
    c.status = status
    if status == CourseStatus.PUBLISHED and c.programme is not None:
        _enrol_programme_learners(c, actor)
    audit.record(f"course.{status}", tenant_id=c.tenant_id, actor=actor, entity_type="course", entity_id=c.id,
                 summary=f"“{c.title}” is now {c.status_label.lower()}")


def _enrol_programme_learners(c: Course, actor) -> None:
    from app.models import EnrolmentStatus
    learners = [e.learner for e in c.programme.enrolments
                if e.status in (EnrolmentStatus.ENROLLED, EnrolmentStatus.COMPLETED)]
    enrol(c, learners, actor=actor, quiet=True)


def enrol(c: Course, learners: list[Learner], *, actor, quiet: bool = False) -> list[Learner]:
    have = {e.learner_id for e in c.enrolments}
    added = [l for l in learners if l.id not in have and l.status != LearnerStatus.ARCHIVED]
    for learner in added:
        db.session.add(CourseEnrolment(tenant_id=c.tenant_id, course=c, learner=learner))
    db.session.flush()
    for ce in c.enrolments:
        if ce.learner_id in {l.id for l in added}:
            recompute(ce)
    if added and not quiet:
        audit.record("course.enrolled", tenant_id=c.tenant_id, actor=actor, entity_type="course", entity_id=c.id,
                     summary=f"“{c.title}”: enrolled {len(added)} learner{'s' if len(added) != 1 else ''}")
    return added


def unenrol(ce: CourseEnrolment, *, actor) -> None:
    c, name = ce.course, ce.learner.full_name
    db.session.delete(ce)
    audit.record("course.unenrolled", tenant_id=c.tenant_id, actor=actor, entity_type="course", entity_id=c.id,
                 summary=f"“{c.title}”: {name} removed")


# ---- Modules and lessons ----------------------------------------------------------------------

def add_module(c: Course, title: str, *, actor) -> CourseModule:
    pos = max([m.position for m in c.modules], default=0) + 1
    m = CourseModule(tenant_id=c.tenant_id, course=c, title=title.strip(), position=pos)
    db.session.add(m)
    db.session.flush()
    return m


def _swap(items, item, direction: int) -> None:
    items = sorted(items, key=lambda x: x.position)
    for i, it in enumerate(items, start=1):
        it.position = i
    idx = items.index(item)
    other = idx + direction
    if 0 <= other < len(items):
        items[idx].position, items[other].position = items[other].position, items[idx].position


def move_module(m: CourseModule, direction: int) -> None:
    _swap(m.course.modules, m, direction)


def delete_module(m: CourseModule, *, actor) -> None:
    c = m.course
    if len(c.modules) == 1:
        raise CourseError("A course needs at least one module.")
    if c.is_published and len(c.lessons) - len(m.lessons) == 0:
        raise CourseError("A published course needs at least one lesson.")
    for lesson in m.lessons:
        _delete_lesson_files(lesson)
    db.session.delete(m)
    db.session.flush()
    _recompute_course(c)


def add_lesson(m: CourseModule, data: dict, *, actor) -> Lesson:
    data = _strip(data, LESSON_FIELDS)
    if data.get("kind") not in LessonKind.LABELS:
        raise CourseError("Choose a lesson type.")
    if data["kind"] == LessonKind.VIDEO and data.get("video_url") and not _embed_ok(data["video_url"]):
        raise CourseError("Use a YouTube or Vimeo link for videos.")
    pos = max([l.position for l in m.lessons], default=0) + 1
    lesson = Lesson(tenant_id=m.tenant_id, module=m, position=pos, **data)
    if lesson.kind == LessonKind.EXAM and not lesson.time_limit_minutes:
        lesson.time_limit_minutes = 30
    db.session.add(lesson)
    db.session.flush()
    _recompute_course(m.course)
    audit.record("course.lesson_added", tenant_id=m.tenant_id, actor=actor, entity_type="course",
                 entity_id=m.course_id, summary=f"“{m.course.title}”: added {lesson.kind_label.lower()} “{lesson.title}”")
    return lesson


def _embed_ok(url: str) -> bool:
    from app.models.lms import video_embed_url
    return video_embed_url(url) is not None


def update_lesson(lesson: Lesson, data: dict, *, actor) -> None:
    data = _strip(data, LESSON_FIELDS)
    data.pop("kind", None)  # the type of a lesson doesn't change once created
    if lesson.kind == LessonKind.VIDEO and data.get("video_url") and not _embed_ok(data["video_url"]):
        raise CourseError("Use a YouTube or Vimeo link for videos.")
    for k, v in data.items():
        setattr(lesson, k, v)
    if lesson.kind == LessonKind.EXAM and not lesson.time_limit_minutes:
        lesson.time_limit_minutes = 30


def move_lesson(lesson: Lesson, direction: int) -> None:
    _swap(lesson.module.lessons, lesson, direction)


def _delete_lesson_files(lesson: Lesson) -> None:
    stored = [lf.file for lf in lesson.files]
    subs = db.session.scalars(select(AssignmentSubmission).where(AssignmentSubmission.lesson_id == lesson.id)).all()
    stored += [s.file for s in subs if s.file is not None]
    for lf in list(lesson.files):
        db.session.delete(lf)
    for s in subs:
        s.file = None
    db.session.flush()
    for sf in stored:
        files.delete(sf)


def delete_lesson(lesson: Lesson, *, actor) -> None:
    c = lesson.course
    if c.is_published and len(c.lessons) == 1:
        raise CourseError("A published course needs at least one lesson.")
    _delete_lesson_files(lesson)
    title = lesson.title
    db.session.delete(lesson)
    db.session.flush()
    _recompute_course(c)
    audit.record("course.lesson_deleted", tenant_id=c.tenant_id, actor=actor, entity_type="course", entity_id=c.id,
                 summary=f"“{c.title}”: deleted “{title}”")


def add_lesson_file(lesson: Lesson, upload, *, title: str, actor) -> LessonFile:
    sf = files.store(lesson.course.tenant, upload, kind="document", area="course-files", actor=actor,
                     allowed=files.COURSE_FILES)
    lf = LessonFile(tenant_id=lesson.tenant_id, lesson=lesson, file=sf,
                    title=(title or "").strip()[:160] or sf.original_name)
    db.session.add(lf)
    db.session.flush()
    return lf


def delete_lesson_file(lf: LessonFile) -> None:
    sf = lf.file
    db.session.delete(lf)
    db.session.flush()
    files.delete(sf)


def add_question(lesson: Lesson, *, prompt: str, options: list[tuple[str, bool]], points: int = 1,
                 explanation: str | None = None) -> QuizQuestion:
    if not lesson.is_assessed:
        raise CourseError("Questions belong to quizzes and exams.")
    options = [(t.strip(), bool(ok)) for t, ok in options if t and t.strip()]
    if not (prompt or "").strip():
        raise CourseError("Write the question.")
    if len(options) < 2:
        raise CourseError("Give at least two answers.")
    correct = sum(ok for _, ok in options)
    if correct == 0:
        raise CourseError("Mark at least one answer as correct.")
    pos = max([q.position for q in lesson.questions], default=0) + 1
    q = QuizQuestion(tenant_id=lesson.tenant_id, lesson=lesson, prompt=prompt.strip(), multiple=correct > 1,
                     points=max(1, int(points or 1)), position=pos, explanation=(explanation or "").strip() or None)
    for i, (text, ok) in enumerate(options, start=1):
        q.options.append(QuizOption(tenant_id=lesson.tenant_id, text=text[:500], is_correct=ok, position=i))
    db.session.add(q)
    db.session.flush()
    return q


# ---- Learner access and progress -----------------------------------------------------------------

def enrolment_for(learner: Learner, course: Course) -> CourseEnrolment | None:
    if learner is None or not course.is_published:
        return None
    return db.session.scalar(select(CourseEnrolment).where(CourseEnrolment.course_id == course.id,
                                                           CourseEnrolment.learner_id == learner.id))


def completed_lesson_ids(learner: Learner, course: Course) -> set[int]:
    ids = [l.id for l in course.lessons]
    if not ids:
        return set()
    return set(db.session.scalars(select(LessonProgress.lesson_id).where(
        LessonProgress.learner_id == learner.id, LessonProgress.lesson_id.in_(ids))).all())


def recompute(ce: CourseEnrolment) -> None:
    lessons = ce.course.lessons
    done = completed_lesson_ids(ce.learner, ce.course)
    ce.progress = round(len(done) * 100 / len(lessons)) if lessons else 0
    if ce.progress == 100 and ce.completed_at is None:
        ce.completed_at = utcnow()
        audit.record("course.completed", tenant_id=ce.tenant_id, actor=ce.learner.user, entity_type="course",
                     entity_id=ce.course_id, summary=f"{ce.learner.full_name} completed “{ce.course.title}”")
    elif ce.progress < 100:
        ce.completed_at = None


def _recompute_course(c: Course) -> None:
    for ce in c.enrolments:
        recompute(ce)


def complete_lesson(learner: Learner, lesson: Lesson) -> None:
    exists = db.session.scalar(select(LessonProgress).where(LessonProgress.lesson_id == lesson.id,
                                                            LessonProgress.learner_id == learner.id))
    if exists is None:
        db.session.add(LessonProgress(tenant_id=lesson.tenant_id, lesson_id=lesson.id, learner_id=learner.id,
                                      completed_at=utcnow()))
        db.session.flush()
    ce = enrolment_for(learner, lesson.course)
    if ce is not None:
        ce.last_activity_at = utcnow()
        recompute(ce)


# ---- Quizzes and exams -------------------------------------------------------------------------

def attempts(learner: Learner, lesson: Lesson) -> list[QuizAttempt]:
    return db.session.scalars(select(QuizAttempt).where(QuizAttempt.lesson_id == lesson.id,
                                                        QuizAttempt.learner_id == learner.id)
                              .order_by(QuizAttempt.started_at)).all()


def open_attempt(learner: Learner, lesson: Lesson) -> QuizAttempt | None:
    return next((a for a in attempts(learner, lesson) if a.submitted_at is None), None)


def attempts_left(learner: Learner, lesson: Lesson) -> int | None:
    if not lesson.max_attempts:
        return None
    used = len([a for a in attempts(learner, lesson) if a.submitted_at is not None])
    return max(0, lesson.max_attempts - used)


def start_attempt(learner: Learner, lesson: Lesson) -> QuizAttempt:
    if not lesson.is_assessed:
        raise CourseError("This lesson isn't a quiz or exam.")
    current = open_attempt(learner, lesson)
    if current is not None:
        return current
    if attempts_left(learner, lesson) == 0:
        raise CourseError("You have used all your attempts.")
    now = utcnow()
    a = QuizAttempt(tenant_id=lesson.tenant_id, lesson=lesson, learner=learner, started_at=now,
                    deadline_at=now + timedelta(minutes=lesson.time_limit_minutes) if lesson.time_limit_minutes else None)
    db.session.add(a)
    db.session.flush()
    return a


def submit_attempt(a: QuizAttempt, answers: dict[int, list[int]]) -> QuizAttempt:
    if a.submitted_at is not None:
        raise CourseError("This attempt has already been submitted.")
    now = utcnow()
    lesson = a.lesson
    a.late = bool(a.deadline_at and now > a.deadline_at + GRACE)
    score, stored = 0, {}
    for q in lesson.questions:
        valid = {o.id for o in q.options}
        chosen = {int(x) for x in answers.get(q.id, []) if int(x) in valid}
        if not q.multiple and len(chosen) > 1:
            chosen = set()
        stored[str(q.id)] = sorted(chosen)
        if chosen and chosen == q.correct_ids:
            score += q.points
    total = lesson.total_points
    a.answers, a.score, a.max_score, a.submitted_at = stored, score, total, now
    a.percent = round(score * 100 / total) if total else 0
    a.passed = (not a.late) and a.percent >= lesson.pass_mark
    if a.passed:
        complete_lesson(a.learner, lesson)
    audit.record("course.quiz_submitted", tenant_id=a.tenant_id, actor=a.learner.user, entity_type="course",
                 entity_id=lesson.course.id,
                 summary=f"{a.learner.full_name}: {lesson.kind_label.lower()} “{lesson.title}” {a.percent}%"
                         f"{' (late)' if a.late else ''} — {'passed' if a.passed else 'not passed'}")
    return a


def best_attempt(learner: Learner, lesson: Lesson) -> QuizAttempt | None:
    done = [a for a in attempts(learner, lesson) if a.submitted_at is not None]
    return max(done, key=lambda a: (a.passed, a.percent or 0), default=None)


# ---- Assignments ---------------------------------------------------------------------------------

def submission_for(learner: Learner, lesson: Lesson) -> AssignmentSubmission | None:
    return db.session.scalar(select(AssignmentSubmission).where(AssignmentSubmission.lesson_id == lesson.id,
                                                                AssignmentSubmission.learner_id == learner.id))


def submit_assignment(learner: Learner, lesson: Lesson, *, text: str | None, upload=None) -> AssignmentSubmission:
    if lesson.kind != LessonKind.ASSIGNMENT:
        raise CourseError("This lesson isn't an assignment.")
    text = (text or "").strip() or None
    has_file = upload is not None and getattr(upload, "filename", "")
    if not text and not has_file:
        raise CourseError("Write your answer or attach a file.")
    sub = submission_for(learner, lesson)
    if sub is not None and sub.is_graded:
        raise CourseError("This assignment has already been marked.")
    new_file = None
    if has_file:
        new_file = files.store(lesson.course.tenant, upload, kind="document", area="submissions",
                               actor=learner.user, allowed=files.COURSE_FILES)
    if sub is None:
        sub = AssignmentSubmission(tenant_id=lesson.tenant_id, lesson=lesson, learner=learner, submitted_at=utcnow())
        db.session.add(sub)
    old_file = sub.file if new_file is not None else None
    sub.text = text
    if new_file is not None:
        sub.file = new_file
    sub.submitted_at = utcnow()
    db.session.flush()
    if old_file is not None:
        files.delete(old_file)
    complete_lesson(learner, lesson)
    audit.record("course.assignment_submitted", tenant_id=lesson.tenant_id, actor=learner.user, entity_type="course",
                 entity_id=lesson.course.id, summary=f"{learner.full_name} submitted “{lesson.title}”")
    return sub


def grade(sub: AssignmentSubmission, *, score: int, feedback: str | None, actor) -> None:
    if score is None or not 0 <= score <= sub.lesson.max_score:
        raise CourseError(f"Give a mark from 0 to {sub.lesson.max_score}.")
    sub.score = score
    sub.feedback = (feedback or "").strip() or None
    sub.graded_by_id = actor.id
    sub.graded_at = utcnow()
    audit.record("course.assignment_graded", tenant_id=sub.tenant_id, actor=actor, entity_type="course",
                 entity_id=sub.lesson.course.id,
                 summary=f"Marked {sub.learner.full_name}'s “{sub.lesson.title}”: {score}/{sub.lesson.max_score}")
    if sub.learner.user_id:
        from app.services import notifications
        course = sub.lesson.course
        notifications.notify(sub.learner.user_id, sub.tenant_id, category="learning",
                             title=f"Marked: {sub.lesson.title}",
                             body=f"You got {score}/{sub.lesson.max_score} in {course.title}."
                                  + (" Your trainer left feedback." if sub.feedback else ""),
                             link=f"/app/learn/courses/{course.public_id}/lessons/{sub.lesson.public_id}")
