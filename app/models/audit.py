"""Append-only audit trail."""
from __future__ import annotations

from app.extensions import db
from app.models.base import UTCDateTime, utcnow


class AuditLog(db.Model):
    """Who did what, when. Tenant events carry a tenant_id; platform events do not.

    Isolation: inside a tenant context only that tenant's rows are visible
    (see `app.tenancy`); platform-wide rows are visible only in platform scope.
    """

    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="SET NULL"), index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_label = db.Column(db.String(160))
    action = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(60))
    entity_id = db.Column(db.String(64))
    summary = db.Column(db.String(255))
    details = db.Column(db.JSON, nullable=False, default=dict)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(UTCDateTime(), nullable=False, default=utcnow, index=True)

    tenant = db.relationship("Tenant")
