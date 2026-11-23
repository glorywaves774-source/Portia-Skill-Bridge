"""Encrypted database backups kept in the site's file storage, and restoring them.

A backup is a zip of every table (one JSON-lines file each) plus a manifest (app version, database revision, row
counts and a checksum per table), encrypted with BACKUP_ENCRYPTION_KEY (Fernet: AES with an HMAC, so any tampering
is detected). It works the same on PostgreSQL and SQLite and needs no database tools on the server.

* `create()`  makes one and saves it as backups/portia-YYYYMMDD-HHMMSS.psb, then applies the retention rule.
* `restore()` loads one into an empty database at the same revision (run `flask db upgrade` first).
* Retention: backups older than BACKUP_RETENTION_DAYS (30) are deleted, but the newest 7 are always kept.

Uploaded photos and documents live in the storage bucket itself, not in these backups: turn on versioning or the
provider's own backups for the bucket.
"""
from __future__ import annotations

import base64
import datetime as dt
import decimal
import hashlib
import io
import json
import zipfile

from flask import current_app
from sqlalchemy import JSON, bindparam, func, select, text
from sqlalchemy.types import TypeDecorator

from app.extensions import db
from app.models.base import utcnow
from app.storage import StorageError, storage

PREFIX = "backups/"
FORMAT = 1
MAGIC_ENCRYPTED = b"PSB1\n"
MAGIC_PLAIN = b"PSB0\n"
KEEP_AT_LEAST = 7
BATCH = 500


class BackupError(RuntimeError):
    pass


# ---- Encryption -----------------------------------------------------------------------------------

def make_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _fernet():
    key = current_app.config.get("BACKUP_ENCRYPTION_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise BackupError("BACKUP_ENCRYPTION_KEY isn't a valid key. Make one with `flask backup make-key`.") from exc


def _seal(payload: bytes) -> bytes:
    f = _fernet()
    if f is None:
        if current_app.config.get("ENV_NAME") == "production":
            raise BackupError("Set BACKUP_ENCRYPTION_KEY before making backups in production.")
        return MAGIC_PLAIN + payload
    return MAGIC_ENCRYPTED + f.encrypt(payload)


def _open(blob: bytes) -> bytes:
    if blob.startswith(MAGIC_PLAIN):
        return blob[len(MAGIC_PLAIN):]
    if not blob.startswith(MAGIC_ENCRYPTED):
        raise BackupError("This isn't a PORTIA SkillBridge backup.")
    f = _fernet()
    if f is None:
        raise BackupError("This backup is encrypted. Set BACKUP_ENCRYPTION_KEY to the key it was made with.")
    from cryptography.fernet import InvalidToken
    try:
        return f.decrypt(blob[len(MAGIC_ENCRYPTED):])
    except InvalidToken as exc:
        raise BackupError("The key doesn't match this backup, or the file has been changed.") from exc


# ---- Values to and from JSON -----------------------------------------------------------------------

def _dump(value):
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(value)).decode()}
    return value


def _loader(column):
    col_type = column.type
    if isinstance(col_type, TypeDecorator):  # e.g. UTCDateTime: look at the type underneath
        col_type = col_type.impl_instance
    if isinstance(col_type, JSON):
        return lambda v: v
    try:
        kind = col_type.python_type
    except NotImplementedError:
        return lambda v: v
    if kind is dt.datetime:
        return lambda v: None if v is None else dt.datetime.fromisoformat(v)
    if kind is dt.date:
        return lambda v: None if v is None else dt.date.fromisoformat(v)
    if kind is dt.time:
        return lambda v: None if v is None else dt.time.fromisoformat(v)
    if kind is decimal.Decimal:
        return lambda v: None if v is None else decimal.Decimal(v)
    if kind is bytes:
        return lambda v: None if v is None else base64.b64decode(v["__b64__"])
    return lambda v: v


def _tables():
    return list(db.metadata.sorted_tables)  # parents before children


def _revision() -> str | None:
    try:
        return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 — a database made with create_all() has no alembic table
        db.session.rollback()
        return None


# ---- Making a backup ---------------------------------------------------------------------------------

def build() -> tuple[bytes, dict]:
    """The sealed backup bytes and its manifest. Call in platform scope."""
    manifest = {"format": FORMAT, "app_version": current_app.config.get("APP_VERSION"),
                "revision": _revision(), "created_at": utcnow().isoformat(),
                "database": db.engine.dialect.name, "tables": {}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for table in _tables():
            digest = hashlib.sha256()
            count = 0
            names = [c.name for c in table.columns]
            lines = []
            order = [table.c[k] for k in (table.primary_key.columns.keys() or names[:1])]
            for row in db.session.execute(select(table).order_by(*order)).mappings():
                line = json.dumps({k: _dump(row[k]) for k in names}, ensure_ascii=False, sort_keys=True)
                digest.update(line.encode())
                lines.append(line)
                count += 1
            zf.writestr(f"data/{table.name}.jsonl", "\n".join(lines))
            manifest["tables"][table.name] = {"rows": count, "sha256": digest.hexdigest()}
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return _seal(buf.getvalue()), manifest


def create() -> dict:
    """Make a backup, save it to storage and apply retention. Returns a summary."""
    blob, manifest = build()
    key = f"{PREFIX}portia-{utcnow():%Y%m%d-%H%M%S}.psb"
    storage().save(key, blob, "application/octet-stream")
    removed = prune()
    rows = sum(t["rows"] for t in manifest["tables"].values())
    return {"key": key, "bytes": len(blob), "rows": rows, "tables": len(manifest["tables"]),
            "encrypted": blob.startswith(MAGIC_ENCRYPTED), "removed_old": removed}


def human_size(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} bytes"
    if n < 1_048_576:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1_048_576:.1f} MB"


def listing() -> list[dict]:
    return sorted([f for f in storage().list(PREFIX) if f["key"].endswith(".psb")],
                  key=lambda f: f["key"], reverse=True)


def prune(now: dt.datetime | None = None) -> int:
    days = int(current_app.config.get("BACKUP_RETENTION_DAYS", 30))
    cutoff = (now or utcnow()) - dt.timedelta(days=days)
    removed = 0
    for f in listing()[KEEP_AT_LEAST:]:
        if f["modified"] < cutoff:
            storage().delete(f["key"])
            removed += 1
    return removed


def read(name_or_path: str) -> bytes:
    """A backup from storage (by name) or from a file on disk."""
    import os
    if os.path.isfile(name_or_path):
        with open(name_or_path, "rb") as fh:
            return fh.read()
    key = name_or_path if name_or_path.startswith(PREFIX) else PREFIX + name_or_path
    try:
        return storage().read(key)
    except StorageError as exc:
        raise BackupError(f"No backup called {name_or_path}.") from exc


def inspect(blob: bytes) -> tuple[dict, zipfile.ZipFile]:
    payload = _open(blob)
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
        manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise BackupError("The backup is damaged.") from exc
    if manifest.get("format") != FORMAT:
        raise BackupError("This backup was made by a different version of PORTIA SkillBridge.")
    return manifest, zf


def verify(blob: bytes) -> dict:
    """Decrypt and check every table's checksum without touching the database."""
    manifest, zf = inspect(blob)
    for name, info in manifest["tables"].items():
        raw = zf.read(f"data/{name}.jsonl").decode()
        lines = raw.split("\n") if raw else []
        digest = hashlib.sha256()
        for line in lines:
            digest.update(line.encode())
        if len(lines) != info["rows"] or digest.hexdigest() != info["sha256"]:
            raise BackupError(f"The backup's {name} table is damaged.")
    return manifest


# ---- Restoring ------------------------------------------------------------------------------------------

def _row_count(table) -> int:
    return db.session.scalar(select(func.count()).select_from(table)) or 0


def restore(blob: bytes, *, wipe: bool = False) -> dict:
    """Load a backup into this database (platform scope). The database must be empty unless wipe=True."""
    manifest = verify(blob)
    _, zf = inspect(blob)
    here = _revision()
    if manifest.get("revision") and here and manifest["revision"] != here:
        raise BackupError(f"The backup is from database revision {manifest['revision']} but this database is at "
                          f"{here}. Run `flask db upgrade` (or check out the matching release) first.")
    tables = _tables()
    missing = [n for n in manifest["tables"] if n not in {t.name for t in tables}]
    if missing:
        raise BackupError("This database is missing tables the backup needs: " + ", ".join(missing))
    used = [t.name for t in tables if _row_count(t)]
    if used and not wipe:
        raise BackupError("This database already has data (" + ", ".join(used[:5]) + "…). Restore into a new, "
                          "empty database, or use --wipe to replace everything.")
    if used:
        for table in reversed(tables):
            db.session.execute(table.delete())
    counts = {}
    for table in tables:
        info = manifest["tables"].get(table.name)
        if not info or not info["rows"]:
            continue
        loaders = {c.name: _loader(c) for c in table.columns}
        rows = []
        for line in zf.read(f"data/{table.name}.jsonl").decode().split("\n"):
            data = json.loads(line)
            rows.append({k: loaders[k](v) for k, v in data.items() if k in loaders})
        stmt = table.insert()
        json_cols = [c.name for c in table.columns
                     if isinstance(c.type.impl_instance if isinstance(c.type, TypeDecorator) else c.type, JSON)]
        if json_cols:  # an empty (NULL) JSON value must come back as NULL, not the JSON value null
            stmt = stmt.values({n: bindparam(n, type_=JSON(none_as_null=True)) for n in json_cols})
        for i in range(0, len(rows), BATCH):
            db.session.execute(stmt, rows[i:i + BATCH])
        counts[table.name] = len(rows)
    if db.engine.dialect.name == "postgresql":
        for table in tables:
            pk = list(table.primary_key.columns)
            if len(pk) == 1 and pk[0].name == "id" and pk[0].autoincrement in (True, "auto"):
                quoted = db.engine.dialect.identifier_preparer.quote(table.name)  # our own table names, quoted
                db.session.execute(text(
                    "SELECT setval(pg_get_serial_sequence(:t, 'id'), "  # nosec B608
                    f"COALESCE((SELECT MAX(id) FROM {quoted}), 1), (SELECT MAX(id) FROM {quoted}) IS NOT NULL)"),
                    {"t": table.name})
    return {"rows": sum(counts.values()), "tables": len(counts), "backup_created_at": manifest["created_at"],
            "app_version": manifest.get("app_version")}
