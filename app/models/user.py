"""People and their roles inside workspaces.

A `User` is one global identity (one email, one password). A `Membership` gives
that person a role inside a tenant workspace, so the same person can be a
learner at a training centre and also hold their own individual subscription.
Sign-in and permission checks are built on these tables in Stage 2.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id, utcnow


def new_session_token() -> str:
    return secrets.token_hex(16)


class Role:
    SUPER_ADMIN = "super_admin"  # platform-level, held via User.is_platform_admin
    ORG_ADMIN = "org_admin"
    TRAINING_MANAGER = "training_manager"
    TRAINER = "trainer"
    EMPLOYER = "employer"
    LEARNER = "learner"
    ASSESSOR = "assessor"
    ME_OFFICER = "me_officer"
    FINANCE_OFFICER = "finance_officer"

    LABELS = {
        SUPER_ADMIN: "SaaS Super Administrator",
        ORG_ADMIN: "Organization Administrator",
        TRAINING_MANAGER: "Training Manager",
        TRAINER: "Trainer / Instructor",
        EMPLOYER: "Employer",
        LEARNER: "Learner / Beneficiary",
        ASSESSOR: "Assessor",
        ME_OFFICER: "M&E Officer",
        FINANCE_OFFICER: "Finance Officer",
    }
    TENANT_ROLES = (ORG_ADMIN, TRAINING_MANAGER, TRAINER, EMPLOYER, LEARNER, ASSESSOR, ME_OFFICER, FINANCE_OFFICER)
    STAFF_ROLES = (ORG_ADMIN, TRAINING_MANAGER, TRAINER, ASSESSOR, ME_OFFICER, FINANCE_OFFICER)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    email = db.Column(db.String(255), nullable=False, unique=True)
    full_name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40))
    password_hash = db.Column(db.String(255))
    is_platform_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(UTCDateTime())
    preferred_theme = db.Column(db.String(10))

    # Sign-in security
    failed_login_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    locked_until = db.Column(UTCDateTime())
    password_changed_at = db.Column(UTCDateTime())
    # Part of the session identity: rotating it signs the person out everywhere.
    session_token = db.Column(db.String(32), nullable=False, default=new_session_token)
    # Two-step sign-in (authenticator app). The secret is only stored once confirmed with a code.
    totp_secret = db.Column(db.String(64))
    totp_enabled_at = db.Column(UTCDateTime())
    totp_last_step = db.Column(db.Integer)          # last accepted 30-second step: a code works once
    # JSONB on PostgreSQL: plain json can't be compared, which SELECT DISTINCT on users needs.
    recovery_codes = db.Column(db.JSON().with_variant(JSONB(), "postgresql"))  # hashes of unused recovery codes
    # Which notification categories also arrive by email (missing = yes).
    email_prefs = db.Column(db.JSON().with_variant(JSONB(), "postgresql"))

    MAX_FAILED_LOGINS = 5
    LOCKOUT = timedelta(minutes=15)

    memberships = db.relationship("Membership", back_populates="user", cascade="all, delete-orphan")

    @staticmethod
    def normalise_email(email: str) -> str:
        return (email or "").strip().lower()

    def set_password(self, password: str) -> None:
        if not password or len(password) < 10:
            raise ValueError("Password must be at least 10 characters long.")
        self.password_hash = generate_password_hash(password, method="scrypt")
        self.password_changed_at = utcnow()
        self.rotate_session_token()

    def rotate_session_token(self) -> None:
        """Invalidate every existing session and remember-me cookie for this person."""
        self.session_token = new_session_token()

    def is_locked(self, now=None) -> bool:
        return bool(self.locked_until and self.locked_until > (now or utcnow()))

    def register_failed_login(self, now=None) -> None:
        now = now or utcnow()
        self.failed_login_count = (self.failed_login_count or 0) + 1
        if self.failed_login_count >= self.MAX_FAILED_LOGINS:
            self.locked_until = now + self.LOCKOUT
            self.failed_login_count = 0

    def register_successful_login(self, now=None) -> None:
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = now or utcnow()

    def check_password(self, password: str) -> bool:
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # used by Flask-Login
        return bool(self.is_active_account)

    @property
    def two_factor_enabled(self) -> bool:
        return bool(self.totp_secret and self.totp_enabled_at)

    def wants_email(self, category: str) -> bool:
        return (self.email_prefs or {}).get(category, True) is not False

    def get_id(self) -> str:
        return f"{self.public_id}.{self.session_token}"

    @property
    def first_name(self) -> str:
        return (self.full_name or "").split(" ")[0]

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class Membership(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "memberships"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False)
    is_owner = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # For the Employer role: the employer this person works for. Their pages only show that employer's
    # vacancies, shortlisted candidates and placements.
    employer_id = db.Column(db.Integer, db.ForeignKey("employers.id", ondelete="SET NULL"), index=True)

    user = db.relationship("User", back_populates="memberships")
    tenant = db.relationship("Tenant", back_populates="memberships")
    employer = db.relationship("Employer")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "user_id", "role", name="uq_memberships_tenant_user_role"),
    )

    @property
    def role_label(self) -> str:
        return Role.LABELS.get(self.role, self.role)
