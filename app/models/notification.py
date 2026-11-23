"""In-app notifications and the record of scheduled job runs."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id, utcnow


class NotificationCategory:
    LEARNING = "learning"      # enrolments, marked work, certificates
    JOBS = "jobs"              # applications, interviews, offers, placements, follow-ups
    BILLING = "billing"        # payments, failed renewals, trial ending
    PEOPLE = "people"          # someone joined the workspace
    LABELS = {LEARNING: "Training, marks and certificates", JOBS: "Jobs, applications and placements",
              BILLING: "Payments and subscription", PEOPLE: "People joining your workspace"}
    ALL = tuple(LABELS)


class Notification(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.String(500))
    link = db.Column(db.String(255))            # a path on this site, e.g. /app/my-jobs/…
    dedupe_key = db.Column(db.String(80))       # stops the same reminder being sent twice
    read_at = db.Column(UTCDateTime())
    email_status = db.Column(db.String(10), nullable=False, default="none")  # none | pending | sent | failed
    email_attempts = db.Column(db.Integer, nullable=False, default=0)
    emailed_at = db.Column(UTCDateTime())

    user = db.relationship("User")

    __table_args__ = (
        db.Index("ix_notifications_user_unread", "user_id", "tenant_id", "read_at"),
        db.Index("ix_notifications_email_pending", "email_status"),
        db.UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
        db.CheckConstraint("email_status IN ('none', 'pending', 'sent', 'failed')", name="email_status_valid"),
    )

    @property
    def is_unread(self) -> bool:
        return self.read_at is None

    @property
    def category_label(self) -> str:
        return NotificationCategory.LABELS.get(self.category, self.category.title())


class JobRun(db.Model):
    """When each scheduled job last ran, and how it went (shown on the System status page)."""
    __tablename__ = "job_runs"

    name = db.Column(db.String(40), primary_key=True)
    last_started_at = db.Column(UTCDateTime())
    last_finished_at = db.Column(UTCDateTime())
    last_ok = db.Column(db.Boolean)
    last_result = db.Column(db.String(500))

    @classmethod
    def record(cls, name: str, *, ok: bool, result: str, started) -> None:
        row = db.session.get(cls, name) or cls(name=name)
        row.last_started_at = started
        row.last_finished_at = utcnow()
        row.last_ok = ok
        row.last_result = (result or "")[:500]
        db.session.add(row)
