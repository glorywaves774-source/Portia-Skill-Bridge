"""Invitations to join a workspace."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, UTCDateTime, new_public_id, utcnow

INVITATION_LIFETIME = timedelta(days=7)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


class InvitationStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"
    LABELS = {PENDING: "Pending", ACCEPTED: "Accepted", REVOKED: "Cancelled", EXPIRED: "Expired"}


class Invitation(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "invitations"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    email = db.Column(db.String(255), nullable=False, index=True)
    full_name = db.Column(db.String(160))
    roles = db.Column(db.JSON, nullable=False, default=list)
    make_owner = db.Column(db.Boolean, nullable=False, default=False)
    message = db.Column(db.String(500))
    # Only a hash is stored: a database leak does not reveal usable links.
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    invited_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    expires_at = db.Column(UTCDateTime(), nullable=False)
    accepted_at = db.Column(UTCDateTime())
    accepted_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at = db.Column(UTCDateTime())
    last_sent_at = db.Column(UTCDateTime())
    send_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    # Set when a learner record is invited to sign in: accepting links the account to the record.
    learner_id = db.Column(db.Integer, db.ForeignKey("learners.id", ondelete="SET NULL"))
    # Set when an employer's staff member is invited: accepting links their Employer role to that employer.
    employer_id = db.Column(db.Integer, db.ForeignKey("employers.id", ondelete="SET NULL"))

    tenant = db.relationship("Tenant")
    invited_by = db.relationship("User", foreign_keys=[invited_by_id])

    def status(self, now=None) -> str:
        if self.accepted_at:
            return InvitationStatus.ACCEPTED
        if self.revoked_at:
            return InvitationStatus.REVOKED
        if self.expires_at <= (now or utcnow()):
            return InvitationStatus.EXPIRED
        return InvitationStatus.PENDING

    @property
    def status_label(self) -> str:
        return InvitationStatus.LABELS[self.status()]

    @property
    def is_pending(self) -> bool:
        return self.status() == InvitationStatus.PENDING

    def issue_token(self) -> str:
        """Create a fresh link (old links stop working) and push the expiry out."""
        token = new_token()
        self.token_hash = hash_token(token)
        self.expires_at = utcnow() + INVITATION_LIFETIME
        return token
