"""Shared model building blocks."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator
from sqlalchemy.orm import declared_attr

from app.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_public_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every database (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TimestampMixin:
    created_at = db.Column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at = db.Column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


class TenantScopedMixin:
    """Marks a model as belonging to exactly one tenant workspace.

    Every query on a model with this mixin is automatically filtered to the
    active tenant (see `app.tenancy`), and writes for another tenant are refused.
    """

    @declared_attr
    def tenant_id(cls):
        return db.Column(
            db.Integer,
            db.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
