"""Training programmes, trainers, enrolments, sessions and attendance."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id


class ProgrammeStatus:
    DRAFT = "draft"
    OPEN = "open"            # taking enrolments
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    LABELS = {DRAFT: "Draft", OPEN: "Open for enrolment", RUNNING: "Running", COMPLETED: "Completed",
              CANCELLED: "Cancelled"}
    ACTIVE = (OPEN, RUNNING)
    COUNTED = (DRAFT, OPEN, RUNNING)  # count towards the plan's programme limit


DELIVERY_MODES = {"in_person": "In person", "online": "Online", "blended": "Blended"}


class EnrolmentStatus:
    ENROLLED = "enrolled"
    COMPLETED = "completed"
    DROPPED_OUT = "dropped_out"
    WITHDRAWN = "withdrawn"
    LABELS = {ENROLLED: "Enrolled", COMPLETED: "Completed", DROPPED_OUT: "Dropped out", WITHDRAWN: "Withdrawn"}


class AttendanceStatus:
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    EXCUSED = "excused"
    LABELS = {PRESENT: "Present", LATE: "Late", ABSENT: "Absent", EXCUSED: "Excused"}
    ATTENDED = (PRESENT, LATE)


class Programme(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "programmes"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    code = db.Column(db.String(30), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    trade = db.Column(db.String(120))          # e.g. Fashion design, Electrical installation
    delivery_mode = db.Column(db.String(20), nullable=False, default="in_person")
    location = db.Column(db.String(160))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    capacity = db.Column(db.Integer)            # None = no limit
    funder = db.Column(db.String(160))          # donor / project funding the programme
    status = db.Column(db.String(20), nullable=False, default=ProgrammeStatus.DRAFT, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    tenant = db.relationship("Tenant")
    trainers = db.relationship("ProgrammeTrainer", back_populates="programme", cascade="all, delete-orphan")
    enrolments = db.relationship("Enrolment", back_populates="programme", cascade="all, delete-orphan")
    sessions = db.relationship("TrainingSession", back_populates="programme", cascade="all, delete-orphan",
                               order_by="(TrainingSession.date, TrainingSession.start_time)")
    courses = db.relationship("Course", back_populates="programme")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "code", name="uq_programmes_tenant_code"),
        db.CheckConstraint("status IN ('draft', 'open', 'running', 'completed', 'cancelled')", name="status_valid"),
    )

    @property
    def status_label(self) -> str:
        return ProgrammeStatus.LABELS.get(self.status, self.status)

    @property
    def mode_label(self) -> str:
        return DELIVERY_MODES.get(self.delivery_mode, self.delivery_mode)

    @property
    def active_enrolments(self):
        return [e for e in self.enrolments if e.status in (EnrolmentStatus.ENROLLED, EnrolmentStatus.COMPLETED)]

    @property
    def seats_left(self) -> int | None:
        if self.capacity is None:
            return None
        return max(0, self.capacity - len([e for e in self.enrolments if e.status == EnrolmentStatus.ENROLLED]))

    def trainer_ids(self) -> set[int]:
        return {t.user_id for t in self.trainers}


class ProgrammeTrainer(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "programme_trainers"
    id = db.Column(db.Integer, primary_key=True)
    programme_id = db.Column(db.Integer, db.ForeignKey("programmes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_lead = db.Column(db.Boolean, nullable=False, default=False)
    programme = db.relationship("Programme", back_populates="trainers")
    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("programme_id", "user_id", name="uq_programme_trainers_programme_user"),)


class Enrolment(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "enrolments"
    id = db.Column(db.Integer, primary_key=True)
    programme_id = db.Column(db.Integer, db.ForeignKey("programmes.id", ondelete="CASCADE"), nullable=False, index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default=EnrolmentStatus.ENROLLED)
    enrolled_on = db.Column(db.Date, nullable=False)
    completed_on = db.Column(db.Date)
    note = db.Column(db.String(255))
    programme = db.relationship("Programme", back_populates="enrolments")
    learner = db.relationship("Learner")
    __table_args__ = (
        db.UniqueConstraint("programme_id", "learner_id", name="uq_enrolments_programme_learner"),
        db.CheckConstraint("status IN ('enrolled', 'completed', 'dropped_out', 'withdrawn')", name="status_valid"),
    )

    @property
    def status_label(self) -> str:
        return EnrolmentStatus.LABELS.get(self.status, self.status)


class TrainingSession(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "training_sessions"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    programme_id = db.Column(db.Integer, db.ForeignKey("programmes.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    venue = db.Column(db.String(160))
    trainer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    attendance_taken_at = db.Column(UTCDateTime())
    programme = db.relationship("Programme", back_populates="sessions")
    trainer = db.relationship("User")
    attendance = db.relationship("Attendance", back_populates="session", cascade="all, delete-orphan")


class Attendance(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("training_sessions.id", ondelete="CASCADE"), nullable=False,
                           index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False)
    note = db.Column(db.String(160))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    session = db.relationship("TrainingSession", back_populates="attendance")
    learner = db.relationship("Learner")
    __table_args__ = (
        db.UniqueConstraint("session_id", "learner_id", name="uq_attendance_session_learner"),
        db.CheckConstraint("status IN ('present', 'late', 'absent', 'excused')", name="status_valid"),
    )
