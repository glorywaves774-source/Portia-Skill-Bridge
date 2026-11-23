"""Online learning: courses, modules, lessons, quizzes/exams, assignments and progress."""
from __future__ import annotations

import re

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id


class CourseStatus:
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    LABELS = {DRAFT: "Draft", PUBLISHED: "Published", ARCHIVED: "Archived"}


COURSE_LEVELS = {"beginner": "Beginner", "intermediate": "Intermediate", "advanced": "Advanced"}


class LessonKind:
    TEXT = "text"
    VIDEO = "video"
    FILES = "files"
    QUIZ = "quiz"
    EXAM = "exam"
    ASSIGNMENT = "assignment"
    LABELS = {TEXT: "Reading", VIDEO: "Video", FILES: "Downloads", QUIZ: "Quiz", EXAM: "Exam",
              ASSIGNMENT: "Assignment"}
    ICONS = {TEXT: "file-text", VIDEO: "play", FILES: "file-text", QUIZ: "clipboard-check", EXAM: "award",
             ASSIGNMENT: "briefcase"}
    ASSESSED = (QUIZ, EXAM)


class Course(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    title = db.Column(db.String(160), nullable=False)
    summary = db.Column(db.Text)
    category = db.Column(db.String(80))
    level = db.Column(db.String(20), nullable=False, default="beginner")
    estimated_hours = db.Column(db.Integer)
    status = db.Column(db.String(20), nullable=False, default=CourseStatus.DRAFT, index=True)
    programme_id = db.Column(db.Integer, db.ForeignKey("programmes.id", ondelete="SET NULL"), index=True)
    published_at = db.Column(UTCDateTime())
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    tenant = db.relationship("Tenant")
    programme = db.relationship("Programme", back_populates="courses")
    modules = db.relationship("CourseModule", back_populates="course", cascade="all, delete-orphan",
                              order_by="CourseModule.position")
    enrolments = db.relationship("CourseEnrolment", back_populates="course", cascade="all, delete-orphan")

    __table_args__ = (db.CheckConstraint("status IN ('draft', 'published', 'archived')", name="status_valid"),)

    @property
    def status_label(self) -> str:
        return CourseStatus.LABELS.get(self.status, self.status)

    @property
    def is_published(self) -> bool:
        return self.status == CourseStatus.PUBLISHED

    @property
    def lessons(self):
        return [lesson for m in self.modules for lesson in m.lessons]

    @property
    def level_label(self) -> str:
        return COURSE_LEVELS.get(self.level, self.level)


class CourseModule(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "course_modules"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    course = db.relationship("Course", back_populates="modules")
    lessons = db.relationship("Lesson", back_populates="module", cascade="all, delete-orphan",
                              order_by="Lesson.position")


class Lesson(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "lessons"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    module_id = db.Column(db.Integer, db.ForeignKey("course_modules.id", ondelete="CASCADE"), nullable=False,
                          index=True)
    title = db.Column(db.String(160), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default=LessonKind.TEXT)
    position = db.Column(db.Integer, nullable=False, default=0)
    body = db.Column(db.Text)                  # reading text / instructions (plain text, simple formatting)
    video_url = db.Column(db.String(500))      # YouTube or Vimeo link
    duration_minutes = db.Column(db.Integer)
    # Quizzes and exams
    pass_mark = db.Column(db.Integer, nullable=False, default=50)   # percent
    max_attempts = db.Column(db.Integer, nullable=False, default=0)  # 0 = unlimited
    time_limit_minutes = db.Column(db.Integer)                       # exams
    show_answers = db.Column(db.Boolean, nullable=False, default=True)
    # Assignments
    due_date = db.Column(db.Date)
    max_score = db.Column(db.Integer, nullable=False, default=100)

    module = db.relationship("CourseModule", back_populates="lessons")
    files = db.relationship("LessonFile", back_populates="lesson", cascade="all, delete-orphan")
    questions = db.relationship("QuizQuestion", back_populates="lesson", cascade="all, delete-orphan",
                                order_by="QuizQuestion.position")

    @property
    def kind_label(self) -> str:
        return LessonKind.LABELS.get(self.kind, self.kind)

    @property
    def icon(self) -> str:
        return LessonKind.ICONS.get(self.kind, "file-text")

    @property
    def course(self):
        return self.module.course

    @property
    def is_assessed(self) -> bool:
        return self.kind in LessonKind.ASSESSED

    @property
    def total_points(self) -> int:
        return sum(q.points for q in self.questions)

    @property
    def embed_url(self) -> str | None:
        return video_embed_url(self.video_url)


_YT = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})")
_VIMEO = re.compile(r"vimeo\.com/(?:video/)?(\d{6,12})")


def video_embed_url(url: str | None) -> str | None:
    """Only YouTube and Vimeo links are embedded (privacy-friendly players, allowed by the CSP)."""
    if not url:
        return None
    m = _YT.search(url)
    if m:
        return f"https://www.youtube-nocookie.com/embed/{m.group(1)}?rel=0"
    m = _VIMEO.search(url)
    if m:
        return f"https://player.vimeo.com/video/{m.group(1)}?dnt=1"
    return None


class LessonFile(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "lesson_files"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    lesson = db.relationship("Lesson", back_populates="files")
    file = db.relationship("StoredFile")


class QuizQuestion(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "quiz_questions"
    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    prompt = db.Column(db.Text, nullable=False)
    multiple = db.Column(db.Boolean, nullable=False, default=False)  # more than one correct answer
    points = db.Column(db.Integer, nullable=False, default=1)
    position = db.Column(db.Integer, nullable=False, default=0)
    explanation = db.Column(db.Text)
    lesson = db.relationship("Lesson", back_populates="questions")
    options = db.relationship("QuizOption", back_populates="question", cascade="all, delete-orphan",
                              order_by="QuizOption.position")

    @property
    def correct_ids(self) -> set[int]:
        return {o.id for o in self.options if o.is_correct}


class QuizOption(TenantScopedMixin, db.Model):
    __tablename__ = "quiz_options"
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("quiz_questions.id", ondelete="CASCADE"), nullable=False,
                            index=True)
    text = db.Column(db.String(500), nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    question = db.relationship("QuizQuestion", back_populates="options")


class QuizAttempt(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "quiz_attempts"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at = db.Column(UTCDateTime(), nullable=False)
    submitted_at = db.Column(UTCDateTime())
    deadline_at = db.Column(UTCDateTime())
    answers = db.Column(db.JSON, nullable=False, default=dict)
    score = db.Column(db.Integer)
    max_score = db.Column(db.Integer)
    percent = db.Column(db.Integer)
    passed = db.Column(db.Boolean)
    late = db.Column(db.Boolean, nullable=False, default=False)
    lesson = db.relationship("Lesson")
    learner = db.relationship("Learner")


class AssignmentSubmission(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "assignment_submissions"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    text = db.Column(db.Text)
    file_id = db.Column(db.Integer, db.ForeignKey("stored_files.id", ondelete="SET NULL"))
    submitted_at = db.Column(UTCDateTime(), nullable=False)
    score = db.Column(db.Integer)
    feedback = db.Column(db.Text)
    graded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    graded_at = db.Column(UTCDateTime())
    lesson = db.relationship("Lesson")
    learner = db.relationship("Learner")
    file = db.relationship("StoredFile")
    graded_by = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("lesson_id", "learner_id", name="uq_assignment_submissions_lesson_learner"),)

    @property
    def is_graded(self) -> bool:
        return self.graded_at is not None


class CourseEnrolment(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "course_enrolments"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    progress = db.Column(db.Integer, nullable=False, default=0)  # percent of lessons completed
    completed_at = db.Column(UTCDateTime())
    last_activity_at = db.Column(UTCDateTime())
    course = db.relationship("Course", back_populates="enrolments")
    learner = db.relationship("Learner")
    __table_args__ = (db.UniqueConstraint("course_id", "learner_id", name="uq_course_enrolments_course_learner"),)


class LessonProgress(TenantScopedMixin, db.Model):
    __tablename__ = "lesson_progress"
    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    completed_at = db.Column(UTCDateTime(), nullable=False)
    __table_args__ = (db.UniqueConstraint("lesson_id", "learner_id", name="uq_lesson_progress_lesson_learner"),)
