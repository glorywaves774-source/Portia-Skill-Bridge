"""Accepting uploads safely and serving them back.

* The file type is decided from the file's contents, never its name or the browser's claim.
* Images are re-drawn with Pillow: this removes hidden data (including GPS location)
  and anything that isn't a real picture. Passport photos are cropped to 3:4 and resized.
* Sizes are limited per file (MAX_UPLOAD_MB) and per workspace (the plan's storage).
"""
from __future__ import annotations

import hashlib
import io
import math
import secrets
import zipfile

from flask import Response, current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import StoredFile, Tenant
from app.services import audit, limits
from app.storage import StorageError, storage

Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse "decompression bomb" images

DOCUMENT_TYPES = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png",
                  "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
DEFAULT_DOCUMENTS = {"pdf", "docx", "jpg", "png"}
COURSE_FILES = {"pdf", "docx", "pptx", "xlsx", "jpg", "png"}
PHOTO_TYPES = {"jpg", "png", "webp"}


class UploadError(ValueError):
    pass


def sniff(data: bytes) -> str | None:
    """Identify a file by its first bytes."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                if "[Content_Types].xml" not in names:
                    return None
                if any(n.lower().endswith("vbaproject.bin") for n in names):
                    return None  # macro-enabled Office file: refuse
                for prefix, kind in (("word/", "docx"), ("ppt/", "pptx"), ("xl/", "xlsx")):
                    if any(n.startswith(prefix) for n in names):
                        return kind
        except zipfile.BadZipFile:
            return None
    return None


def _read(upload: FileStorage) -> bytes:
    if upload is None or not upload.filename:
        raise UploadError("Choose a file to upload.")
    limit = current_app.config["MAX_CONTENT_LENGTH"]
    data = upload.stream.read(limit + 1)
    if len(data) > limit:
        raise UploadError(f"That file is too big. The limit is {limit // (1024 * 1024)} MB.")
    if not data:
        raise UploadError("That file is empty.")
    return data


def _reencode(data: bytes, *, max_side: int, crop_portrait: bool) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise UploadError("That picture couldn't be read. Try a JPG or PNG photo.") from exc
    if min(img.size) < 120:
        raise UploadError("That picture is too small. Use a photo at least 120 pixels wide.")
    img = img.convert("RGB")
    if crop_portrait:  # passport photo: 3:4 portrait, centred
        img = ImageOps.fit(img, (600, 800), method=Image.Resampling.LANCZOS, centering=(0.5, 0.4))
    else:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True, progressive=True)  # no EXIF written
    return out.getvalue()


def store(tenant: Tenant, upload: FileStorage, *, kind: str, area: str, actor=None,
          allowed: set[str] | None = None) -> StoredFile:
    """Validate, clean and store an upload for this workspace. Caller commits."""
    data = _read(upload)
    detected = sniff(data)
    if kind == "photo":
        if detected not in PHOTO_TYPES:
            raise UploadError("Upload a photo as JPG, PNG or WEBP.")
        data, ext, content_type = _reencode(data, max_side=800, crop_portrait=True), "jpg", "image/jpeg"
    elif kind == "document":
        allowed = allowed or DEFAULT_DOCUMENTS
        if detected not in allowed:
            names = {"pdf": "PDF", "docx": "Word (.docx)", "pptx": "PowerPoint (.pptx)", "xlsx": "Excel (.xlsx)",
                     "jpg": "JPG", "png": "PNG"}
            raise UploadError("Upload a " + ", ".join(names[a] for a in sorted(allowed, key=list(names).index))
                              + " file.")
        ext, content_type = detected, DOCUMENT_TYPES[detected]
        if detected in ("jpg", "png"):
            data, ext, content_type = _reencode(data, max_side=2400, crop_portrait=False), "jpg", "image/jpeg"
    else:
        raise ValueError(kind)

    _check_storage_headroom(tenant, len(data))

    name = secure_filename(upload.filename) or f"upload.{ext}"
    base = name.rsplit(".", 1)[0][:100] or "file"
    key = f"tenants/{tenant.public_id}/{area}/{secrets.token_hex(16)}.{ext}"
    sf = StoredFile(tenant_id=tenant.id, key=key, kind=kind, original_name=f"{base}.{ext}",
                    content_type=content_type, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    uploaded_by_id=getattr(actor, "id", None))
    storage().save(key, data, content_type)
    db.session.add(sf)
    try:
        db.session.flush()
    except Exception:
        storage().delete(key)
        raise
    return sf


def _check_storage_headroom(tenant: Tenant, adding_bytes: int) -> None:
    cap_mb = limits.limit(tenant, "storage_mb")
    if cap_mb is None:
        return
    used = storage_bytes(tenant)
    if used + adding_bytes > cap_mb * 1024 * 1024:
        raise limits.UsageLimitExceeded("storage_mb", cap_mb, math.ceil(used / (1024 * 1024)))


def storage_bytes(tenant: Tenant) -> int:
    return db.session.scalar(select(func.coalesce(func.sum(StoredFile.size_bytes), 0))
                             .where(StoredFile.tenant_id == tenant.id)) or 0


@limits.register_counter("storage_mb")
def _count_storage(tenant: Tenant) -> int:
    return math.ceil(storage_bytes(tenant) / (1024 * 1024))


def delete(sf: StoredFile) -> None:
    key = sf.key
    db.session.delete(sf)
    db.session.flush()
    try:
        storage().delete(key)
    except StorageError:  # pragma: no cover
        current_app.logger.warning("Could not delete %s from storage", key)


def serve(sf: StoredFile, *, inline: bool = False, actor=None, log: bool = True) -> Response:
    """Send a stored file back with safe headers. Downloads of documents are audited."""
    try:
        data = storage().read(sf.key)
    except StorageError:
        return Response("This file is missing from storage.", status=404, mimetype="text/plain")
    disposition = "inline" if inline else "attachment"
    resp = Response(data, mimetype=sf.content_type)
    resp.headers["Content-Disposition"] = f'{disposition}; filename="{sf.original_name}"'
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    if log and sf.kind == "document":
        audit.record("file.downloaded", tenant_id=sf.tenant_id, actor=actor, entity_type="file", entity_id=sf.id,
                     summary=f"Downloaded {sf.original_name}")
        db.session.commit()
    return resp
