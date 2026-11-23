"""Competency frameworks, skills assessments and certificates."""
from __future__ import annotations

import secrets

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id

# Four-point proficiency scale used for every rating.
LEVELS = {1: "Not yet competent", 2: "Developing", 3: "Competent", 4: "Proficient"}
LEVEL_SHORT = {1: "NYC", 2: "Developing", 3: "Competent", 4: "Proficient"}


class FrameworkStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"
    LABELS = {DRAFT: "Draft", ACTIVE: "Active", RETIRED: "Retired"}


ASSESSMENT_KINDS = {
    "practical": "Practical demonstration", "observation": "Workplace observation", "written": "Written / oral test",
    "portfolio": "Portfolio of evidence", "rpl": "Recognition of prior learning",
}


class Framework(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "competency_frameworks"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    name = db.Column(db.String(160), nullable=False)
    trade = db.Column(db.String(120))
    level = db.Column(db.String(60))           # e.g. "CTVET Proficiency 1", "NVTI Grade 2"
    description = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default=FrameworkStatus.DRAFT)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    competencies = db.relationship("Competency", back_populates="framework", cascade="all, delete-orphan",
                                   order_by="Competency.position")
    tenant = db.relationship("Tenant")

    @property
    def status_label(self) -> str:
        return FrameworkStatus.LABELS.get(self.status, self.status)

    @property
    def is_active(self) -> bool:
        return self.status == FrameworkStatus.ACTIVE


class Competency(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "competencies"
    id = db.Column(db.Integer, primary_key=True)
    framework_id = db.Column(db.Integer, db.ForeignKey("competency_frameworks.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    code = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    criteria = db.Column(db.JSON, nullable=False, default=list)  # performance criteria (list of text)
    required_level = db.Column(db.Integer, nullable=False, default=3)
    position = db.Column(db.Integer, nullable=False, default=0)
    framework = db.relationship("Framework", back_populates="competencies")
    __table_args__ = (db.UniqueConstraint("framework_id", "code", name="uq_competencies_framework_code"),)


class AssessmentStatus:
    DRAFT = "draft"
    FINAL = "final"
    LABELS = {DRAFT: "In progress", FINAL: "Finalised"}


class Assessment(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "assessments"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    framework_id = db.Column(db.Integer, db.ForeignKey("competency_frameworks.id", ondelete="RESTRICT"),
                             nullable=False, index=True)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    programme_id = db.Column(db.Integer, db.ForeignKey("programmes.id", ondelete="SET NULL"), index=True)
    assessor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    kind = db.Column(db.String(20), nullable=False, default="practical")
    assessed_on = db.Column(db.Date, nullable=False)
    location = db.Column(db.String(160))
    comments = db.Column(db.Text)
    status = db.Column(db.String(10), nullable=False, default=AssessmentStatus.DRAFT, index=True)
    competent = db.Column(db.Boolean)          # overall result, set when finalised
    finalised_at = db.Column(UTCDateTime())
    framework = db.relationship("Framework")
    learner = db.relationship("Learner")
    programme = db.relationship("Programme")
    assessor = db.relationship("User")
    ratings = db.relationship("AssessmentRating", back_populates="assessment", cascade="all, delete-orphan")
    evidence = db.relationship("AssessmentEvidence", back_populates="assessment", cascade="all, delete-orphan")

    @property
    def is_final(self) -> bool:
        return self.status == AssessmentStatus.FINAL

    @property
    def kind_label(self) -> str:
        return ASSESSMENT_KINDS.get(self.kind, self.kind)

    @property
    def status_label(self) -> str:
        return AssessmentStatus.LABELS.get(self.status, self.status)

    def rating_for(self, competency_id: int):
        return next((r for r in self.ratings if r.competency_id == competency_id), None)

    @property
    def result_label(self) -> str:
        if not self.is_final:
            return "In progress"
        return "Competent" if self.competent else "Not yet competent"


class AssessmentRating(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "assessment_ratings"
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False,
                              index=True)
    competency_id = db.Column(db.Integer, db.ForeignKey("competencies.id", ondelete="CASCADE"), nullable=False)
    level = db.Column(db.Integer, nullable=False)
    criteria_met = db.Column(db.JSON, nullable=False, default=list)  # indexes of criteria observed
    comment = db.Column(db.String(500))
    assessment = db.relationship("Assessment", back_populates="ratings")
    competency = db.relationship("Competency")
    __table_args__ = (
        db.UniqueConstraint("assessment_id", "competency_id", name="uq_assessment_ratings_assessment_competency"),
        db.CheckConstraint("level BETWEEN 1 AND 4", name="level_valid"),
    )

    @property
    def achieved(self) -> bool:
        return self.level >= self.competency.required_level


class AssessmentEvidence(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "assessment_evidence"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False,
                              index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    assessment = db.relationship("Assessment", back_populates="evidence")
    file = db.relationship("StoredFile")


class CertificateKind:
    COURSE = "course"
    PROGRAMME = "programme"
    COMPETENCY = "competency"
    LABELS = {COURSE: "Course completion", PROGRAMME: "Programme completion", COMPETENCY: "Certificate of competence"}


def new_verification_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


class Certificate(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "certificates"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    number = db.Column(db.String(40), nullable=False)
    verification_code = db.Column(db.String(12), nullable=False, unique=True, default=new_verification_code)
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    # What the certificate was earned for (one of these is set).
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="SET NULL"))
    enrolment_id = db.Column(db.Integer, db.ForeignKey("enrolments.id", ondelete="SET NULL"))
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id", ondelete="SET NULL"))
    issued_on = db.Column(db.Date, nullable=False)
    expires_on = db.Column(db.Date)
    issued_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    signatory_name = db.Column(db.String(160))
    signatory_title = db.Column(db.String(160))
    revoked_at = db.Column(UTCDateTime())
    revoked_reason = db.Column(db.String(255))
    learner = db.relationship("Learner")
    issued_by = db.relationship("User")
    tenant = db.relationship("Tenant")
    __table_args__ = (db.UniqueConstraint("tenant_id", "number", name="uq_certificates_tenant_number"),)

    @property
    def kind_label(self) -> str:
        return CertificateKind.LABELS.get(self.kind, self.kind)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, today=None) -> bool:
        from datetime import date
        return bool(self.expires_on and self.expires_on < (today or date.today()))

    @property
    def status_label(self) -> str:
        if self.is_revoked:
            return "Revoked"
        if self.is_expired():
            return "Expired"
        return "Valid"
