"""Training programmes: trainers, enrolment, sessions and attendance."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    Attendance,
    AttendanceStatus,
    Enrolment,
    EnrolmentStatus,
    Learner,
    LearnerStatus,
    Membership,
    Programme,
    ProgrammeStatus,
    ProgrammeTrainer,
    Role,
    Tenant,
    TenantCounter,
    TrainingSession,
    User,
)
from app.models.base import utcnow
from app.services import audit, limits

TRAINER_ROLES = (Role.TRAINER, Role.TRAINING_MANAGER, Role.ORG_ADMIN, Role.ASSESSOR)
EDITABLE = ("name", "description", "trade", "delivery_mode", "location", "start_date", "end_date", "capacity", "funder")


class TrainingError(ValueError):
    pass


@limits.register_counter("programmes")
def _count_programmes(tenant: Tenant) -> int:
    return db.session.scalar(select(func.count(Programme.id)).where(
        Programme.tenant_id == tenant.id, Programme.status.in_(ProgrammeStatus.COUNTED))) or 0


def _clean(data: dict) -> dict:
    out = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in data.items() if k in EDITABLE}
    if out.get("start_date") and out.get("end_date") and out["end_date"] < out["start_date"]:
        raise TrainingError("The end date is before the start date.")
    return out


def create_programme(tenant: Tenant, data: dict, *, actor) -> Programme:
    limits.check(tenant, "programmes")
    data = _clean(data)
    if not data.get("name"):
        raise TrainingError("Give the programme a name.")
    n = TenantCounter.next(tenant.id, "programme")
    year = (data.get("start_date") or date.today()).year
    p = Programme(tenant_id=tenant.id, code=f"P{year}-{n:03d}", created_by_id=getattr(actor, "id", None), **data)
    db.session.add(p)
    db.session.flush()
    audit.record("programme.created", tenant_id=tenant.id, actor=actor, entity_type="programme", entity_id=p.id,
                 summary=f"Created programme {p.code} {p.name}")
    return p


def update_programme(p: Programme, data: dict, *, actor) -> list[str]:
    data = _clean(data)
    if data.get("capacity") is not None:
        enrolled = len([e for e in p.enrolments if e.status == EnrolmentStatus.ENROLLED])
        if data["capacity"] < enrolled:
            raise TrainingError(f"{enrolled} learners are already enrolled; capacity can't be lower than that.")
    changed = [k for k, v in data.items() if getattr(p, k) != v]
    for k in changed:
        setattr(p, k, data[k])
    if changed:
        audit.record("programme.updated", tenant_id=p.tenant_id, actor=actor, entity_type="programme",
                     entity_id=p.id, summary=f"{p.code}: changed {', '.join(c.replace('_', ' ') for c in changed)}")
    return changed


def set_status(p: Programme, status: str, *, actor) -> None:
    if status not in ProgrammeStatus.LABELS:
        raise TrainingError("Unknown status.")
    if status == p.status:
        return
    if p.status not in ProgrammeStatus.COUNTED and status in ProgrammeStatus.COUNTED:
        limits.check(p.tenant, "programmes")
    old = p.status_label
    p.status = status
    audit.record("programme.status_changed", tenant_id=p.tenant_id, actor=actor, entity_type="programme",
                 entity_id=p.id, summary=f"{p.code}: {old} → {p.status_label}")


def eligible_trainers(tenant: Tenant) -> list[User]:
    return db.session.scalars(
        select(User).join(Membership, Membership.user_id == User.id).where(
            Membership.tenant_id == tenant.id, Membership.is_active.is_(True), Membership.role.in_(TRAINER_ROLES),
            User.is_active_account.is_(True)).distinct().order_by(User.full_name)
    ).all()


def assign_trainer(p: Programme, user: User, *, lead: bool, actor) -> None:
    if user.id not in {u.id for u in eligible_trainers(p.tenant)}:
        raise TrainingError("Only trainers, training managers, assessors or administrators can be assigned.")
    if user.id in p.trainer_ids():
        raise TrainingError(f"{user.full_name} is already assigned.")
    if lead:
        for t in p.trainers:
            t.is_lead = False
    db.session.add(ProgrammeTrainer(tenant_id=p.tenant_id, programme=p, user_id=user.id, is_lead=lead))
    audit.record("programme.trainer_assigned", tenant_id=p.tenant_id, actor=actor, entity_type="programme",
                 entity_id=p.id, summary=f"{p.code}: {user.full_name} assigned{' as lead trainer' if lead else ''}")


def remove_trainer(p: Programme, trainer: ProgrammeTrainer, *, actor) -> None:
    name = trainer.user.full_name
    db.session.delete(trainer)
    audit.record("programme.trainer_removed", tenant_id=p.tenant_id, actor=actor, entity_type="programme",
                 entity_id=p.id, summary=f"{p.code}: {name} removed")


def enrol(p: Programme, learners: list[Learner], *, actor) -> tuple[list[Learner], list[str]]:
    """Enrol learners. Returns (enrolled, reasons for those skipped)."""
    if p.status in (ProgrammeStatus.COMPLETED, ProgrammeStatus.CANCELLED):
        raise TrainingError(f"This programme is {p.status_label.lower()}; it can't take new learners.")
    existing = {e.learner_id: e for e in p.enrolments}
    todo, skipped = [], []
    for learner in learners:
        e = existing.get(learner.id)
        if learner.status == LearnerStatus.ARCHIVED:
            skipped.append(f"{learner.full_name} is archived")
        elif e and e.status in (EnrolmentStatus.ENROLLED, EnrolmentStatus.COMPLETED):
            skipped.append(f"{learner.full_name} is already enrolled")
        else:
            todo.append(learner)
    if p.seats_left is not None and len(todo) > p.seats_left:
        raise TrainingError(f"Only {p.seats_left} seat{'s' if p.seats_left != 1 else ''} left in this programme.")
    for learner in todo:
        e = existing.get(learner.id)
        if e:
            e.status, e.enrolled_on, e.completed_on = EnrolmentStatus.ENROLLED, date.today(), None
        else:
            db.session.add(Enrolment(tenant_id=p.tenant_id, programme=p, learner=learner, enrolled_on=date.today()))
    db.session.flush()
    if todo:
        from app.services import lms
        for course in p.courses:
            lms.enrol(course, todo, actor=actor, quiet=True)
        audit.record("programme.enrolled", tenant_id=p.tenant_id, actor=actor, entity_type="programme",
                     entity_id=p.id, summary=f"{p.code}: enrolled {len(todo)} learner{'s' if len(todo) != 1 else ''}")
        from app.services import notifications
        when = f" It starts on {p.start_date:%d %b %Y}." if p.start_date else ""
        for learner in todo:
            if learner.user_id:
                notifications.notify(learner.user_id, p.tenant_id, category="learning",
                                     title=f"You're enrolled: {p.name}", body=f"You've been enrolled in {p.name}.{when}",
                                     link="/app/learn/")
    return todo, skipped


def set_enrolment_status(e: Enrolment, status: str, *, actor) -> None:
    if status not in EnrolmentStatus.LABELS:
        raise TrainingError("Unknown status.")
    if status == EnrolmentStatus.ENROLLED and e.status != status and e.programme.seats_left == 0:
        raise TrainingError("The programme is full.")
    e.status = status
    e.completed_on = date.today() if status == EnrolmentStatus.COMPLETED else None
    audit.record("programme.enrolment_changed", tenant_id=e.tenant_id, actor=actor, entity_type="programme",
                 entity_id=e.programme_id, summary=f"{e.programme.code}: {e.learner.full_name} → {e.status_label}")


def add_session(p: Programme, data: dict, *, actor) -> TrainingSession:
    if data.get("start_time") and data.get("end_time") and data["end_time"] <= data["start_time"]:
        raise TrainingError("The session must end after it starts.")
    trainer_id = data.get("trainer_id") or None
    if trainer_id and trainer_id not in p.trainer_ids():
        raise TrainingError("Choose one of this programme's trainers.")
    s = TrainingSession(tenant_id=p.tenant_id, programme=p, title=data["title"].strip(), date=data["date"],
                        start_time=data.get("start_time"), end_time=data.get("end_time"),
                        venue=(data.get("venue") or "").strip() or None, trainer_id=trainer_id)
    db.session.add(s)
    db.session.flush()
    audit.record("programme.session_added", tenant_id=p.tenant_id, actor=actor, entity_type="programme",
                 entity_id=p.id, summary=f"{p.code}: session “{s.title}” on {s.date:%d %b %Y}")
    return s


def register_learners(s: TrainingSession) -> list[Learner]:
    """Learners on the attendance register: enrolled or completed (plus anyone already recorded)."""
    ids = {e.learner_id for e in s.programme.enrolments
           if e.status in (EnrolmentStatus.ENROLLED, EnrolmentStatus.COMPLETED)}
    ids |= {a.learner_id for a in s.attendance}
    learners = [e.learner for e in s.programme.enrolments if e.learner_id in ids]
    return sorted(learners, key=lambda l: (l.last_name.lower(), l.first_name.lower()))


def can_take_attendance(user: User, p: Programme, permissions) -> bool:
    return "programmes.manage" in permissions or ("attendance.record" in permissions and user.id in p.trainer_ids())


def record_attendance(s: TrainingSession, marks: dict[int, str], notes: dict[int, str] | None = None, *,
                      actor) -> int:
    allowed = {l.id for l in register_learners(s)}
    existing = {a.learner_id: a for a in s.attendance}
    count = 0
    for learner_id, status in marks.items():
        if learner_id not in allowed or status not in AttendanceStatus.LABELS:
            continue
        note = ((notes or {}).get(learner_id) or "").strip()[:160] or None
        a = existing.get(learner_id)
        if a is None:
            db.session.add(Attendance(tenant_id=s.tenant_id, session=s, learner_id=learner_id, status=status,
                                      note=note, recorded_by_id=actor.id))
        else:
            a.status, a.note, a.recorded_by_id = status, note, actor.id
        count += 1
    s.attendance_taken_at = utcnow()
    present = sum(1 for st in marks.values() if st in AttendanceStatus.ATTENDED)
    audit.record("programme.attendance_taken", tenant_id=s.tenant_id, actor=actor, entity_type="programme",
                 entity_id=s.programme_id, summary=f"{s.programme.code}: attendance for “{s.title}” — "
                                                   f"{present} of {count} present")
    return count


def attendance_rates(p: Programme) -> dict[int, dict]:
    """Per learner: sessions recorded, sessions attended, rate (%)."""
    out: dict[int, dict] = {}
    for s in p.sessions:
        for a in s.attendance:
            row = out.setdefault(a.learner_id, {"recorded": 0, "attended": 0})
            row["recorded"] += 1
            row["attended"] += a.status in AttendanceStatus.ATTENDED
    for row in out.values():
        row["rate"] = round(row["attended"] * 100 / row["recorded"]) if row["recorded"] else None
    return out


def overall_rate(p: Programme) -> int | None:
    rows = attendance_rates(p).values()
    recorded = sum(r["recorded"] for r in rows)
    return round(sum(r["attended"] for r in rows) * 100 / recorded) if recorded else None
