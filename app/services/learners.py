"""Registering and maintaining learner / beneficiary records."""
from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func, or_, select

from app.extensions import db
from app.models import Learner, LearnerDocument, LearnerStatus, Tenant, TenantCounter
from app.services import audit, files, limits

# Fields staff may set on a learner record (anything else in the form data is ignored).
EDITABLE = (
    "first_name", "middle_name", "last_name", "gender", "date_of_birth", "nationality", "id_type", "id_number",
    "phone", "alt_phone", "email", "address", "city", "district", "region", "has_disability", "disability_details",
    "education_level", "employment_status", "categories", "emergency_name", "emergency_phone", "emergency_relation",
    "notes", "registered_on",
)
# Fields learners may change themselves.
SELF_EDITABLE = ("phone", "alt_phone", "email", "address", "city", "district", "region",
                 "emergency_name", "emergency_phone", "emergency_relation")
# Personal identifiers: their values are never written to the audit log.
SENSITIVE = {"id_number", "date_of_birth", "phone", "alt_phone", "email", "address", "notes", "disability_details",
             "emergency_phone"}


class LearnerError(ValueError):
    pass


def _clean(data: dict, allowed) -> dict:
    out = {}
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str):
            value = value.strip() or None
        out[key] = value
    if out.get("id_number"):
        out["id_number"] = out["id_number"].upper().replace(" ", "")
    if "id_number" in out and not out.get("id_number") and "id_type" in out:
        out["id_type"] = None  # no number, no type
    if out.get("email"):
        out["email"] = out["email"].lower()
    if "has_disability" in out and not out["has_disability"]:
        out["disability_details"] = None
    return out


def find_duplicates(tenant: Tenant, data: dict, exclude_id: int | None = None) -> list[Learner]:
    """Possible duplicates: same ID document, or same name and date of birth, or same phone and date of birth."""
    data = _clean(data, EDITABLE)
    conds = []
    if data.get("id_number"):
        conds.append(and_(Learner.id_number == data["id_number"], Learner.id_type == data.get("id_type")))
    if data.get("date_of_birth"):
        if data.get("first_name") and data.get("last_name"):
            conds.append(and_(func.lower(Learner.first_name) == data["first_name"].lower(),
                              func.lower(Learner.last_name) == data["last_name"].lower(),
                              Learner.date_of_birth == data["date_of_birth"]))
        if data.get("phone"):
            conds.append(and_(Learner.phone == data["phone"], Learner.date_of_birth == data["date_of_birth"]))
    if not conds:
        return []
    stmt = select(Learner).where(Learner.tenant_id == tenant.id, or_(*conds))
    if exclude_id:
        stmt = stmt.where(Learner.id != exclude_id)
    return db.session.scalars(stmt.limit(5)).all()


def _id_taken(tenant: Tenant, data: dict, exclude_id: int | None = None) -> bool:
    if not data.get("id_number"):
        return False
    stmt = select(Learner.id).where(Learner.tenant_id == tenant.id, Learner.id_type == data.get("id_type"),
                                    Learner.id_number == data["id_number"])
    if exclude_id:
        stmt = stmt.where(Learner.id != exclude_id)
    return db.session.scalar(stmt) is not None


def create(tenant: Tenant, data: dict, *, actor, allow_duplicate: bool = False) -> Learner:
    data = _clean(data, EDITABLE)
    if not data.get("first_name") or not data.get("last_name") or not data.get("gender"):
        raise LearnerError("First name, last name and gender are required.")
    limits.check(tenant, "learners")
    if _id_taken(tenant, data):
        raise LearnerError("A learner with this ID document is already registered.")
    if not allow_duplicate and find_duplicates(tenant, data):
        raise LearnerError("possible-duplicate")
    registered = data.get("registered_on") or date.today()
    number = TenantCounter.next(tenant.id, "learner")
    learner = Learner(tenant_id=tenant.id, learner_no=f"L{registered.year}-{number:05d}",
                      created_by_id=getattr(actor, "id", None), **{**data, "registered_on": registered})
    learner.categories = list(data.get("categories") or [])
    db.session.add(learner)
    db.session.flush()
    audit.record("learner.created", tenant_id=tenant.id, actor=actor, entity_type="learner", entity_id=learner.id,
                 summary=f"Registered {learner.full_name} ({learner.learner_no})")
    return learner


def update(learner: Learner, data: dict, *, actor, allowed=EDITABLE, by_learner: bool = False) -> list[str]:
    data = _clean(data, allowed)
    if "id_number" in data and _id_taken(learner.tenant, {**{"id_type": learner.id_type}, **data},
                                            exclude_id=learner.id):
        raise LearnerError("Another learner is already registered with this ID document.")
    changed = []
    for key, value in data.items():
        if key == "categories":
            value = list(value or [])
        if getattr(learner, key) != value:
            setattr(learner, key, value)
            changed.append(key)
    if changed:
        visible = [c.replace("_", " ") for c in changed]
        audit.record("learner.updated_by_self" if by_learner else "learner.updated", tenant_id=learner.tenant_id,
                     actor=actor, entity_type="learner", entity_id=learner.id,
                     summary=f"{learner.learner_no}: changed {', '.join(visible)}",
                     details={"fields": changed, "sensitive_changed": sorted(set(changed) & SENSITIVE)})
    return changed


def set_status(learner: Learner, status: str, *, actor) -> None:
    if status not in LearnerStatus.LABELS:
        raise LearnerError("Unknown status.")
    if status == learner.status:
        return
    if learner.status == LearnerStatus.ARCHIVED and status in LearnerStatus.COUNTED:
        limits.check(learner.tenant, "learners")
    old = learner.status_label
    learner.status = status
    audit.record("learner.status_changed", tenant_id=learner.tenant_id, actor=actor, entity_type="learner",
                 entity_id=learner.id, summary=f"{learner.learner_no}: {old} → {learner.status_label}")


def set_photo(learner: Learner, upload, *, actor) -> None:
    sf = files.store(learner.tenant, upload, kind="photo", area="learner-photos", actor=actor)
    old = learner.photo
    learner.photo = sf
    db.session.flush()
    if old is not None:
        files.delete(old)
    audit.record("learner.photo_updated", tenant_id=learner.tenant_id, actor=actor, entity_type="learner",
                 entity_id=learner.id, summary=f"{learner.learner_no}: passport photo updated")


def remove_photo(learner: Learner, *, actor) -> None:
    if learner.photo is None:
        return
    old = learner.photo
    learner.photo = None
    db.session.flush()
    files.delete(old)
    audit.record("learner.photo_removed", tenant_id=learner.tenant_id, actor=actor, entity_type="learner",
                 entity_id=learner.id, summary=f"{learner.learner_no}: passport photo removed")


def add_document(learner: Learner, upload, *, doc_type: str, title: str, actor, by_learner: bool = False):
    sf = files.store(learner.tenant, upload, kind="document", area="learner-documents", actor=actor)
    doc = LearnerDocument(tenant_id=learner.tenant_id, learner=learner, file=sf, doc_type=doc_type,
                          title=(title or "").strip()[:160] or sf.original_name, uploaded_by_learner=by_learner)
    db.session.add(doc)
    db.session.flush()
    audit.record("learner.document_added", tenant_id=learner.tenant_id, actor=actor, entity_type="learner",
                 entity_id=learner.id, summary=f"{learner.learner_no}: added {doc.type_label.lower()} “{doc.title}”")
    return doc


def delete_document(doc: LearnerDocument, *, actor) -> None:
    learner, title = doc.learner, doc.title
    sf = doc.file
    db.session.delete(doc)
    db.session.flush()
    files.delete(sf)
    audit.record("learner.document_deleted", tenant_id=learner.tenant_id, actor=actor, entity_type="learner",
                 entity_id=learner.id, summary=f"{learner.learner_no}: deleted “{title}”")


def delete_permanently(learner: Learner, *, actor) -> None:
    """Erase a record and its files (e.g. a data-protection request). Cannot be undone."""
    number = learner.learner_no
    stored = [d.file for d in learner.documents] + ([learner.photo] if learner.photo else [])
    learner_id = learner.id
    tenant_id = learner.tenant_id
    learner.photo = None
    db.session.flush()
    db.session.delete(learner)
    db.session.flush()
    for sf in stored:
        files.delete(sf)
    audit.record("learner.deleted", tenant_id=tenant_id, actor=actor, entity_type="learner", entity_id=learner_id,
                 summary=f"{number}: record and files permanently deleted")
