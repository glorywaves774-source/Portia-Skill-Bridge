"""Learners and beneficiaries, with education, skills, employment history and documents."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, new_public_id


class Gender:
    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    UNDISCLOSED = "undisclosed"
    LABELS = {FEMALE: "Female", MALE: "Male", OTHER: "Other", UNDISCLOSED: "Prefer not to say"}


class LearnerStatus:
    ACTIVE = "active"
    COMPLETED = "completed"
    DROPPED_OUT = "dropped_out"
    INACTIVE = "inactive"
    ARCHIVED = "archived"
    LABELS = {ACTIVE: "Active", COMPLETED: "Completed", DROPPED_OUT: "Dropped out", INACTIVE: "Inactive",
              ARCHIVED: "Archived"}
    COUNTED = (ACTIVE, COMPLETED, DROPPED_OUT, INACTIVE)  # count towards plan limits


IDENTITY_TYPES = {
    "ghana_card": "Ghana Card", "passport": "Passport", "voter_id": "Voter ID", "birth_certificate": "Birth certificate",
    "drivers_licence": "Driver's licence", "nhis": "NHIS card", "other": "Other",
}

EDUCATION_LEVELS = {
    "none": "No formal education", "primary": "Primary", "jhs": "Junior High (JHS)", "shs": "Senior High (SHS)",
    "tvet": "TVET certificate", "diploma": "Diploma / HND", "degree": "Bachelor's degree",
    "postgraduate": "Postgraduate",
}

CATEGORIES = {
    "youth": "Youth (15–35)", "women": "Women", "pwd": "Person with disability", "rural": "Rural resident",
    "returnee": "Returnee / migrant", "refugee": "Refugee", "school_leaver": "School leaver",
    "nss": "National Service person", "low_income": "Low-income household",
}

EMPLOYMENT_STATUSES = {
    "unemployed": "Unemployed", "wage_employed": "Wage employed", "self_employed": "Self-employed",
    "apprentice": "Apprentice", "student": "In school", "not_looking": "Not looking for work",
}

GHANA_REGIONS = [
    "Ahafo", "Ashanti", "Bono", "Bono East", "Central", "Eastern", "Greater Accra", "North East", "Northern",
    "Oti", "Savannah", "Upper East", "Upper West", "Volta", "Western", "Western North",
]

SKILL_LEVELS = {"basic": "Basic", "intermediate": "Intermediate", "advanced": "Advanced", "expert": "Expert"}

EMPLOYMENT_TYPES = {
    "full_time": "Full-time", "part_time": "Part-time", "self_employed": "Self-employed",
    "internship": "Internship", "apprenticeship": "Apprenticeship", "casual": "Casual / seasonal",
}

DOCUMENT_TYPES = {
    "cv": "CV / résumé", "certificate": "Certificate", "id_document": "ID document", "transcript": "Transcript",
    "recommendation": "Reference letter", "consent": "Consent form", "other": "Other",
}


class TenantCounter(TenantScopedMixin, db.Model):
    """Per-workspace number sequences (learner numbers and, later, certificates)."""

    __tablename__ = "tenant_counters"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False)
    value = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint("tenant_id", "name", name="uq_tenant_counters_tenant_name"),)

    @classmethod
    def next(cls, tenant_id: int, name: str) -> int:
        row = db.session.scalar(select(cls).where(cls.tenant_id == tenant_id, cls.name == name).with_for_update())
        if row is None:
            row = cls(tenant_id=tenant_id, name=name, value=0)
            db.session.add(row)
        row.value += 1
        db.session.flush()
        return row.value


class Learner(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "learners"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    learner_no = db.Column(db.String(20), nullable=False)

    first_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80), nullable=False)
    gender = db.Column(db.String(20), nullable=False)
    date_of_birth = db.Column(db.Date)
    nationality = db.Column(db.String(60), default="Ghanaian")
    id_type = db.Column(db.String(30))
    id_number = db.Column(db.String(40))

    phone = db.Column(db.String(40))
    alt_phone = db.Column(db.String(40))
    email = db.Column(db.String(255))
    address = db.Column(db.String(255))
    city = db.Column(db.String(80))
    district = db.Column(db.String(80))
    region = db.Column(db.String(40))

    has_disability = db.Column(db.Boolean, nullable=False, default=False)
    disability_details = db.Column(db.String(255))
    education_level = db.Column(db.String(20))
    employment_status = db.Column(db.String(20))
    categories = db.Column(db.JSON, nullable=False, default=list)

    emergency_name = db.Column(db.String(160))
    emergency_phone = db.Column(db.String(40))
    emergency_relation = db.Column(db.String(60))
    notes = db.Column(db.Text)

    status = db.Column(db.String(20), nullable=False, default=LearnerStatus.ACTIVE, index=True)
    registered_on = db.Column(db.Date, nullable=False, default=date.today)
    photo_file_id = db.Column(db.Integer, db.ForeignKey("stored_files.id", ondelete="SET NULL"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    tenant = db.relationship("Tenant")
    photo = db.relationship("StoredFile", foreign_keys=[photo_file_id])
    user = db.relationship("User", foreign_keys=[user_id])
    education = db.relationship("LearnerEducation", back_populates="learner", cascade="all, delete-orphan",
                                order_by="LearnerEducation.end_year.desc().nullsfirst()")
    skills = db.relationship("LearnerSkill", back_populates="learner", cascade="all, delete-orphan",
                             order_by="LearnerSkill.name")
    employment = db.relationship("LearnerEmployment", back_populates="learner", cascade="all, delete-orphan",
                                 order_by="LearnerEmployment.start_date.desc()")
    documents = db.relationship("LearnerDocument", back_populates="learner", cascade="all, delete-orphan",
                                order_by="LearnerDocument.created_at.desc()")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "learner_no", name="uq_learners_tenant_learner_no"),
        db.Index("uq_learners_tenant_id_number", "tenant_id", "id_type", "id_number", unique=True,
                 postgresql_where=db.text("id_number IS NOT NULL"), sqlite_where=db.text("id_number IS NOT NULL")),
        db.Index("ix_learners_tenant_name", "tenant_id", "last_name", "first_name"),
        db.CheckConstraint("gender IN ('female', 'male', 'other', 'undisclosed')", name="gender_valid"),
        db.CheckConstraint("status IN ('active', 'completed', 'dropped_out', 'inactive', 'archived')",
                           name="status_valid"),
    )

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)

    @property
    def initials(self) -> str:
        return (self.first_name[:1] + self.last_name[:1]).upper()

    def age(self, on: date | None = None) -> int | None:
        if not self.date_of_birth:
            return None
        on = on or date.today()
        dob = self.date_of_birth
        return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))

    @property
    def age_band(self) -> str:
        a = self.age()
        if a is None:
            return "Unknown"
        for upper, label in ((14, "Under 15"), (24, "15–24"), (35, "25–35"), (49, "36–49")):
            if a <= upper:
                return label
        return "50+"

    @property
    def masked_id_number(self) -> str | None:
        if not self.id_number:
            return None
        n = self.id_number
        return "•" * max(0, len(n) - 4) + n[-4:]

    @property
    def status_label(self) -> str:
        return LearnerStatus.LABELS.get(self.status, self.status)

    @property
    def gender_label(self) -> str:
        return Gender.LABELS.get(self.gender, self.gender)


class LearnerEducation(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "learner_education"
    id = db.Column(db.Integer, primary_key=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    institution = db.Column(db.String(160), nullable=False)
    qualification = db.Column(db.String(160), nullable=False)
    field_of_study = db.Column(db.String(120))
    level = db.Column(db.String(20))
    start_year = db.Column(db.Integer)
    end_year = db.Column(db.Integer)
    learner = db.relationship("Learner", back_populates="education")


class LearnerSkill(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "learner_skills"
    id = db.Column(db.Integer, primary_key=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    level = db.Column(db.String(20), nullable=False, default="basic")
    certified = db.Column(db.Boolean, nullable=False, default=False)
    certificate_body = db.Column(db.String(160))
    year = db.Column(db.Integer)
    learner = db.relationship("Learner", back_populates="skills")

    @property
    def level_label(self) -> str:
        return SKILL_LEVELS.get(self.level, self.level)


class LearnerEmployment(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "learner_employment"
    id = db.Column(db.Integer, primary_key=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    employer = db.Column(db.String(160), nullable=False)
    position = db.Column(db.String(120), nullable=False)
    employment_type = db.Column(db.String(20), nullable=False, default="full_time")
    sector = db.Column(db.String(80))
    location = db.Column(db.String(120))
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    monthly_income_minor = db.Column(db.Integer)  # optional, for M&E income outcomes
    learner = db.relationship("Learner", back_populates="employment")

    @property
    def is_current(self) -> bool:
        return self.end_date is None

    @property
    def type_label(self) -> str:
        return EMPLOYMENT_TYPES.get(self.employment_type, self.employment_type)


class LearnerDocument(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "learner_documents"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=False)
    doc_type = db.Column(db.String(20), nullable=False, default="other")
    title = db.Column(db.String(160), nullable=False)
    uploaded_by_learner = db.Column(db.Boolean, nullable=False, default=False)
    learner = db.relationship("Learner", back_populates="documents")
    file = db.relationship("StoredFile")

    @property
    def type_label(self) -> str:
        return DOCUMENT_TYPES.get(self.doc_type, self.doc_type)
