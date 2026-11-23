"""Recruitment and employment: employers, vacancies, applications, placements and follow-ups."""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id
from app.models.learner import EMPLOYMENT_TYPES

SECTORS = {
    "agriculture": "Agriculture and agro-processing", "automotive": "Automotive and mechanics",
    "beauty": "Beauty and cosmetology", "construction": "Construction and building",
    "creative": "Creative arts and media", "education": "Education", "electrical": "Electrical and electronics",
    "energy": "Energy and solar", "fashion": "Fashion and garments", "finance": "Finance and insurance",
    "health": "Health and care", "hospitality": "Hospitality, catering and tourism", "ict": "ICT and digital",
    "logistics": "Logistics and transport", "manufacturing": "Manufacturing", "mining": "Mining",
    "public": "Public sector", "retail": "Retail and trade", "welding": "Welding and fabrication",
    "other": "Other",
}


class VacancyStatus:
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"
    FILLED = "filled"
    LABELS = {DRAFT: "Draft", OPEN: "Open", CLOSED: "Closed", FILLED: "Filled"}


class ApplicationStatus:
    APPLIED = "applied"          # the learner applied themselves
    NOMINATED = "nominated"      # put forward by the training centre
    SHORTLISTED = "shortlisted"
    INTERVIEW = "interview"
    OFFERED = "offered"
    HIRED = "hired"
    NOT_SELECTED = "not_selected"
    WITHDRAWN = "withdrawn"
    LABELS = {APPLIED: "Applied", NOMINATED: "Nominated", SHORTLISTED: "Shortlisted", INTERVIEW: "Interview",
              OFFERED: "Offer made", HIRED: "Hired", NOT_SELECTED: "Not selected", WITHDRAWN: "Withdrawn"}
    PIPELINE = (APPLIED, NOMINATED, SHORTLISTED, INTERVIEW, OFFERED, HIRED)
    CLOSED = (HIRED, NOT_SELECTED, WITHDRAWN)
    # Statuses the employer's own team can see: the centre has screened the candidate.
    SHARED = (SHORTLISTED, INTERVIEW, OFFERED, HIRED, NOT_SELECTED)
    # Statuses staff can choose from the status menu (Hired goes through the hire form).
    SETTABLE = (APPLIED, NOMINATED, SHORTLISTED, INTERVIEW, OFFERED, NOT_SELECTED, WITHDRAWN)
    EMPLOYER_SETTABLE = (SHORTLISTED, INTERVIEW, OFFERED, NOT_SELECTED)


class PlacementStatus:
    ACTIVE = "active"
    ENDED = "ended"
    LABELS = {ACTIVE: "In work", ENDED: "Ended"}


FOLLOW_UP_MONTHS = (3, 6, 12)


class Employer(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "employers"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    name = db.Column(db.String(160), nullable=False)
    sector = db.Column(db.String(30))
    contact_name = db.Column(db.String(160))
    contact_role = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(255))
    website = db.Column(db.String(255))
    region = db.Column(db.String(40))
    city = db.Column(db.String(80))
    address = db.Column(db.String(255))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    vacancies = db.relationship("Vacancy", back_populates="employer", order_by="Vacancy.created_at.desc()")
    placements = db.relationship("Placement", back_populates="employer", order_by="Placement.start_date.desc()")

    __table_args__ = (db.UniqueConstraint("tenant_id", "name", name="uq_employers_tenant_name"),)

    @property
    def sector_label(self) -> str:
        return SECTORS.get(self.sector, self.sector or "—")

    @property
    def place(self) -> str:
        return ", ".join(p for p in (self.city, self.region) if p) or "—"


class Vacancy(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "vacancies"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    code = db.Column(db.String(20), nullable=False)
    employer_id = db.Column(db.Integer, db.ForeignKey("employers.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    trade = db.Column(db.String(120))
    employment_type = db.Column(db.String(20), nullable=False, default="full_time")
    positions = db.Column(db.Integer, nullable=False, default=1)
    region = db.Column(db.String(40))
    location = db.Column(db.String(160))
    salary_min_minor = db.Column(db.Integer)   # monthly, in pesewas
    salary_max_minor = db.Column(db.Integer)
    description = db.Column(db.Text)
    requirements = db.Column(db.Text)
    closing_date = db.Column(db.Date)
    open_to_learners = db.Column(db.Boolean, nullable=False, default=True)  # learners can see it and apply
    status = db.Column(db.String(20), nullable=False, default=VacancyStatus.DRAFT, index=True)
    opened_on = db.Column(db.Date)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    employer = db.relationship("Employer", back_populates="vacancies")
    competencies = db.relationship("Competency", secondary="vacancy_competencies", order_by="Competency.code")
    applications = db.relationship("Application", back_populates="vacancy", cascade="all, delete-orphan",
                                   order_by="Application.created_at")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "code", name="uq_vacancies_tenant_code"),
        db.CheckConstraint("status IN ('draft', 'open', 'closed', 'filled')", name="status_valid"),
        db.CheckConstraint("positions >= 1", name="positions_positive"),
    )

    @property
    def status_label(self) -> str:
        return VacancyStatus.LABELS.get(self.status, self.status)

    @property
    def type_label(self) -> str:
        return EMPLOYMENT_TYPES.get(self.employment_type, self.employment_type)

    @property
    def hired_count(self) -> int:
        return sum(1 for a in self.applications if a.status == ApplicationStatus.HIRED)

    @property
    def is_accepting(self) -> bool:
        """Open, and the closing date (if any) hasn't passed."""
        return self.status == VacancyStatus.OPEN and (self.closing_date is None or self.closing_date >= date.today())

    @property
    def place(self) -> str:
        return ", ".join(p for p in (self.location, self.region) if p) or "—"


vacancy_competencies = db.Table(
    "vacancy_competencies",
    db.Column("vacancy_id", db.Integer, db.ForeignKey("vacancies.id", ondelete="CASCADE"), primary_key=True),
    db.Column("competency_id", db.Integer, db.ForeignKey("competencies.id", ondelete="CASCADE"), primary_key=True),
)


class Application(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "applications"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    vacancy_id = db.Column(db.Integer, db.ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False)          # applied | nominated
    status = db.Column(db.String(20), nullable=False, index=True)
    match_score = db.Column(db.Integer)                         # 0-100 when the application was made
    cover_note = db.Column(db.Text)                             # written by the learner
    notes = db.Column(db.Text)                                  # internal: centre and employer only
    interview_at = db.Column(UTCDateTime())
    interview_location = db.Column(db.String(160))
    decided_at = db.Column(UTCDateTime())
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    vacancy = db.relationship("Vacancy", back_populates="applications")
    learner = db.relationship("Learner")
    placement = db.relationship("Placement", back_populates="application", uselist=False)

    __table_args__ = (
        db.UniqueConstraint("vacancy_id", "learner_id", name="uq_applications_vacancy_learner"),
        db.CheckConstraint("source IN ('applied', 'nominated')", name="source_valid"),
        db.CheckConstraint("status IN ('applied', 'nominated', 'shortlisted', 'interview', 'offered', 'hired', "
                           "'not_selected', 'withdrawn')", name="status_valid"),
    )

    @property
    def status_label(self) -> str:
        return ApplicationStatus.LABELS.get(self.status, self.status)

    @property
    def is_open(self) -> bool:
        return self.status not in ApplicationStatus.CLOSED

    @property
    def shared_with_employer(self) -> bool:
        return self.status in ApplicationStatus.SHARED


class Placement(TenantScopedMixin, TimestampMixin, db.Model):
    """A learner in work: wage employment, apprenticeship, internship or their own business."""

    __tablename__ = "placements"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    employer_id = db.Column(db.Integer, db.ForeignKey("employers.id", ondelete="SET NULL"), index=True)
    employer_name = db.Column(db.String(160), nullable=False)  # kept even for self-employment or unlisted employers
    vacancy_id = db.Column(db.Integer, db.ForeignKey("vacancies.id", ondelete="SET NULL"))
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id", ondelete="SET NULL"), unique=True)
    employment_record_id = db.Column(db.Integer, db.ForeignKey("learner_employment.id", ondelete="SET NULL"))
    job_title = db.Column(db.String(160), nullable=False)
    employment_type = db.Column(db.String(20), nullable=False, default="full_time")
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    monthly_pay_minor = db.Column(db.Integer)
    status = db.Column(db.String(20), nullable=False, default=PlacementStatus.ACTIVE, index=True)
    end_reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    learner = db.relationship("Learner")
    employer = db.relationship("Employer", back_populates="placements")
    vacancy = db.relationship("Vacancy")
    application = db.relationship("Application", back_populates="placement")
    employment_record = db.relationship("LearnerEmployment")
    follow_ups = db.relationship("PlacementFollowUp", back_populates="placement", cascade="all, delete-orphan",
                                 order_by="PlacementFollowUp.due_on")

    __table_args__ = (
        db.CheckConstraint("status IN ('active', 'ended')", name="status_valid"),
    )

    @property
    def status_label(self) -> str:
        return PlacementStatus.LABELS.get(self.status, self.status)

    @property
    def type_label(self) -> str:
        return EMPLOYMENT_TYPES.get(self.employment_type, self.employment_type)

    @property
    def is_active(self) -> bool:
        return self.status == PlacementStatus.ACTIVE


class PlacementFollowUp(TenantScopedMixin, TimestampMixin, db.Model):
    """Checks at 3, 6 and 12 months: is the person still in work, and what do they earn?"""

    __tablename__ = "placement_follow_ups"

    id = db.Column(db.Integer, primary_key=True)
    placement_id = db.Column(db.Integer, db.ForeignKey("placements.id", ondelete="CASCADE"), nullable=False,
                             index=True)
    months = db.Column(db.Integer, nullable=False)
    due_on = db.Column(db.Date, nullable=False, index=True)
    done_on = db.Column(db.Date)
    still_employed = db.Column(db.Boolean)
    monthly_pay_minor = db.Column(db.Integer)
    notes = db.Column(db.String(500))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    placement = db.relationship("Placement", back_populates="follow_ups")
    recorded_by = db.relationship("User")

    __table_args__ = (db.UniqueConstraint("placement_id", "months", name="uq_follow_ups_placement_months"),)

    @property
    def is_done(self) -> bool:
        return self.done_on is not None

    def is_overdue(self, today: date | None = None) -> bool:
        return not self.is_done and self.due_on < (today or date.today())
