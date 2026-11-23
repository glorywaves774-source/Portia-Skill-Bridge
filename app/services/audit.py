"""Write audit trail entries."""
from __future__ import annotations

from flask import has_request_context, request

from app.extensions import db
from app.models.audit import AuditLog


def record(
    action: str,
    *,
    tenant_id: int | None = None,
    actor=None,
    actor_label: str | None = None,
    entity_type: str | None = None,
    entity_id=None,
    summary: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    """Add an audit entry to the current session (committed with the caller's work)."""
    ip = None
    if has_request_context():
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr)
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=getattr(actor, "id", None),
        actor_label=actor_label or getattr(actor, "email", None) or "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=(summary or "")[:255] or None,
        details=details or {},
        ip_address=ip,
    )
    db.session.add(entry)
    return entry
