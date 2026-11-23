"""Files uploaded to a workspace (photos, documents). The bytes live in object storage."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TenantScopedMixin, TimestampMixin, new_public_id


class StoredFile(TenantScopedMixin, TimestampMixin, db.Model):
    __tablename__ = "stored_files"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False, unique=True, default=new_public_id)
    # tenants/<tenant public id>/<area>/<random>.<ext> — never derived from the uploaded name.
    key = db.Column(db.String(255), nullable=False, unique=True)
    kind = db.Column(db.String(20), nullable=False)  # photo | document
    original_name = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    uploaded_by = db.relationship("User")

    @property
    def size_label(self) -> str:
        size = self.size_bytes
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

    @property
    def extension(self) -> str:
        return self.key.rsplit(".", 1)[-1].upper()
