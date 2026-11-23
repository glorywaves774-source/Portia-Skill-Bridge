"""Tenant workspaces: organizations and individual subscribers."""
from __future__ import annotations

import re
import secrets

from app.extensions import db
from app.models.base import TimestampMixin, UTCDateTime, new_public_id


class TenantKind:
    ORGANIZATION = "organization"
    INDIVIDUAL = "individual"
    ALL = (ORGANIZATION, INDIVIDUAL)


class OrganizationType:
    TRAINING_INSTITUTION = "training_institution"
    VOCATIONAL_INSTITUTE = "vocational_institute"
    TRAINING_CENTRE = "training_centre"
    EDUCATIONAL_INSTITUTION = "educational_institution"
    NGO = "ngo"
    COMPANY = "company"
    GOVERNMENT = "government"
    OTHER = "other"

    LABELS = {
        TRAINING_INSTITUTION: "Training institution",
        VOCATIONAL_INSTITUTE: "Vocational institute",
        TRAINING_CENTRE: "Training centre",
        EDUCATIONAL_INSTITUTION: "Educational institution",
        NGO: "NGO / donor-funded project",
        COMPANY: "Company / employer",
        GOVERNMENT: "Government agency",
        OTHER: "Other",
    }
    ALL = tuple(LABELS)


class TenantStatus:
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    ALL = (ACTIVE, SUSPENDED, CLOSED)


_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L confusion


def generate_tenant_code(kind: str) -> str:
    prefix = "ORG" if kind == TenantKind.ORGANIZATION else "IND"
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
    return f"PSB-{prefix}-{body}"


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:60] or "workspace"


class Tenant(TimestampMixin, db.Model):
    """An independent workspace. Every tenant-scoped record points at one of these."""

    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    code = db.Column(db.String(20), nullable=False, unique=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    organization_type = db.Column(db.String(40))
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(64), nullable=False, unique=True)

    email = db.Column(db.String(255))
    billing_email = db.Column(db.String(255))  # where Paystack receipts and billing notices go
    phone = db.Column(db.String(40))
    website = db.Column(db.String(255))
    country = db.Column(db.String(2), nullable=False, default="GH")
    region = db.Column(db.String(80))
    city = db.Column(db.String(80))
    address = db.Column(db.String(255))
    registration_number = db.Column(db.String(80))

    timezone = db.Column(db.String(64), nullable=False, default="Africa/Accra")
    currency = db.Column(db.String(3), nullable=False, default="GHS")
    logo_path = db.Column(db.String(255))

    status = db.Column(db.String(20), nullable=False, default=TenantStatus.ACTIVE, index=True)
    suspended_at = db.Column(UTCDateTime())
    suspension_reason = db.Column(db.String(255))

    subscriptions = db.relationship(
        "Subscription", back_populates="tenant", order_by="Subscription.id.desc()", lazy="select",
        cascade="all, delete-orphan",
    )
    memberships = db.relationship("Membership", back_populates="tenant", cascade="all, delete-orphan")

    __table_args__ = (
        db.CheckConstraint("kind IN ('organization', 'individual')", name="kind_valid"),
        db.CheckConstraint("status IN ('active', 'suspended', 'closed')", name="status_valid"),
    )

    @property
    def is_individual(self) -> bool:
        return self.kind == TenantKind.INDIVIDUAL

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE

    @property
    def organization_type_label(self) -> str:
        if self.is_individual:
            return "Individual"
        return OrganizationType.LABELS.get(self.organization_type or "", "Organization")

    @property
    def current_subscription(self):
        from app.models.subscription import SubscriptionStatus

        for sub in self.subscriptions:
            if sub.status in SubscriptionStatus.LIVE:
                return sub
        return self.subscriptions[0] if self.subscriptions else None

    @property
    def has_access(self) -> bool:
        """True when the workspace may be used: active and on a usable subscription."""
        sub = self.current_subscription
        return self.is_active and sub is not None and sub.grants_access()

    def __repr__(self) -> str:
        return f"<Tenant {self.code} {self.name!r}>"
