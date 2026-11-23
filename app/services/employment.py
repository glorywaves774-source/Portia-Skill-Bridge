"""Recruitment and employment: employers, vacancies, matching, applications, placements and follow-ups.

Employer partner accounts (people who hold only the Employer role) are *scoped* to the employer their
membership is linked to: they see that employer's vacancies, the candidates the centre has shortlisted for
them, and their placements. Everyone else with the right permissions sees the whole workspace.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    Application,
    ApplicationStatus,
    Assessment,
    AssessmentStatus,
    Certificate,
    Competency,
    Employer,
    Enrolment,
    EnrolmentStatus,
    Learner,
    LearnerEmployment,
    LearnerSkill,
    LearnerStatus,
    Membership,
    Placement,
    PlacementFollowUp,
    PlacementStatus,
    Programme,
    Role,
    Tenant,
    TenantCounter,
    User,
    Vacancy,
    VacancyStatus,
)
from app.models.assessment import AssessmentRating
from app.models.base import utcnow
from app.models.employment import FOLLOW_UP_MONTHS, SECTORS
from app.models.learner import EMPLOYMENT_TYPES
from app.services import audit, mailer
from app.services import notifications as notes

EMPLOYER_FIELDS = ("name", "sector", "contact_name", "contact_role", "phone", "email", "website", "region", "city",
                   "address", "notes")
VACANCY_FIELDS = ("title", "trade", "employment_type", "positions", "region", "location", "salary_min_minor",
                  "salary_max_minor", "description", "requirements", "closing_date", "open_to_learners")
CANDIDATE_STATUSES = (LearnerStatus.ACTIVE, LearnerStatus.COMPLETED)
# How much each part of a match counts. Only the parts a vacancy asks for are used.
WEIGHTS = {"competencies": 60, "trade": 25, "region": 15}


class EmploymentError(ValueError):
    pass


# ---- Who sees what ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Scope:
    """restricted=True for employer partner accounts; employer is the employer they're linked to (or None)."""

    restricted: bool = False
    employer: Employer | None = None

    def allows_employer(self, employer_id: int | None) -> bool:
        return not self.restricted or (self.employer is not None and employer_id == self.employer.id)

    def allows_application(self, a: Application) -> bool:
        return not self.restricted or (self.allows_employer(a.vacancy.employer_id) and a.shared_with_employer)

    def vacancy_filter(self, stmt):
        if self.restricted:
            return stmt.where(Vacancy.employer_id == (self.employer.id if self.employer else -1))
        return stmt


def scope_for(user: User, tenant: Tenant, permissions) -> Scope:
    if "learners.view" in permissions:
        return Scope()
    ms = db.session.scalars(select(Membership).where(
        Membership.tenant_id == tenant.id, Membership.user_id == user.id, Membership.role == Role.EMPLOYER,
        Membership.is_active.is_(True))).all()
    emp = next((m.employer for m in ms if m.employer_id and m.employer and m.employer.is_active), None)
    return Scope(restricted=True, employer=emp)


def _clean(data: dict, fields) -> dict:
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in data.items() if k in fields}


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


# ---- Employers ----------------------------------------------------------------------------------

def _name_taken(name: str, exclude_id: int | None = None) -> bool:
    stmt = select(Employer.id).where(func.lower(Employer.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Employer.id != exclude_id)
    return db.session.scalar(stmt) is not None


def create_employer(tenant: Tenant, data: dict, *, actor) -> Employer:
    data = _clean(data, EMPLOYER_FIELDS)
    if not data.get("name"):
        raise EmploymentError("Give the employer a name.")
    if data.get("sector") and data["sector"] not in SECTORS:
        raise EmploymentError("Choose a sector from the list.")
    if _name_taken(data["name"]):
        raise EmploymentError(f"{data['name']} is already on your list of employers.")
    e = Employer(tenant_id=tenant.id, created_by_id=getattr(actor, "id", None), **data)
    db.session.add(e)
    db.session.flush()
    audit.record("employer.created", tenant_id=tenant.id, actor=actor, entity_type="employer", entity_id=e.id,
                 summary=f"Added employer {e.name}")
    return e


def update_employer(e: Employer, data: dict, *, actor) -> list[str]:
    data = _clean(data, EMPLOYER_FIELDS)
    if not data.get("name"):
        raise EmploymentError("Give the employer a name.")
    if data.get("sector") and data["sector"] not in SECTORS:
        raise EmploymentError("Choose a sector from the list.")
    if _name_taken(data["name"], exclude_id=e.id):
        raise EmploymentError(f"Another employer is already called {data['name']}.")
    changed = [k for k, v in data.items() if getattr(e, k) != v]
    for k in changed:
        setattr(e, k, data[k])
    if changed:
        audit.record("employer.updated", tenant_id=e.tenant_id, actor=actor, entity_type="employer", entity_id=e.id,
                     summary=f"{e.name}: changed {', '.join(c.replace('_', ' ') for c in changed)}")
    return changed


def set_employer_active(e: Employer, active: bool, *, actor) -> None:
    if e.is_active == active:
        return
    if not active and any(v.status == VacancyStatus.OPEN for v in e.vacancies):
        raise EmploymentError("Close this employer's open vacancies first.")
    e.is_active = active
    audit.record("employer.reactivated" if active else "employer.deactivated", tenant_id=e.tenant_id, actor=actor,
                 entity_type="employer", entity_id=e.id,
                 summary=f"{'Reactivated' if active else 'Deactivated'} employer {e.name}")


def employer_members(e: Employer) -> list[Membership]:
    return db.session.scalars(select(Membership).where(
        Membership.employer_id == e.id, Membership.role == Role.EMPLOYER)).all()


def linkable_members(tenant: Tenant) -> list[Membership]:
    """Active Employer-role memberships not yet linked to any employer."""
    return db.session.scalars(select(Membership).join(User, User.id == Membership.user_id).where(
        Membership.tenant_id == tenant.id, Membership.role == Role.EMPLOYER, Membership.is_active.is_(True),
        Membership.employer_id.is_(None)).order_by(User.full_name)).all()


def link_member(e: Employer, membership: Membership, *, actor) -> None:
    if membership.role != Role.EMPLOYER or not membership.is_active:
        raise EmploymentError("Only active people with the Employer role can be linked to an employer.")
    if membership.employer_id not in (None, e.id):
        raise EmploymentError("This person is already linked to another employer.")
    membership.employer_id = e.id
    audit.record("employer.member_linked", tenant_id=e.tenant_id, actor=actor, entity_type="employer", entity_id=e.id,
                 summary=f"Linked {membership.user.full_name} to {e.name}")


def unlink_member(e: Employer, membership: Membership, *, actor) -> None:
    if membership.employer_id != e.id:
        raise EmploymentError("This person isn't linked to this employer.")
    membership.employer_id = None
    audit.record("employer.member_unlinked", tenant_id=e.tenant_id, actor=actor, entity_type="employer",
                 entity_id=e.id, summary=f"Unlinked {membership.user.full_name} from {e.name}")


def invite_contact(tenant: Tenant, e: Employer, *, email: str, full_name: str | None, actor):
    from app.services import members
    if not e.is_active:
        raise EmploymentError("Reactivate this employer before inviting their staff.")
    try:
        return members.invite(tenant, email=email, roles=[Role.EMPLOYER], actor=actor, full_name=full_name,
                              employer=e)
    except members.MemberError as exc:
        raise EmploymentError(str(exc)) from exc


# ---- Vacancies ----------------------------------------------------------------------------------

def _vacancy_data(data: dict) -> dict:
    data = _clean(data, VACANCY_FIELDS)
    if not data.get("title"):
        raise EmploymentError("Give the vacancy a job title.")
    if data.get("employment_type") not in EMPLOYMENT_TYPES:
        raise EmploymentError("Choose the type of work.")
    if (data.get("positions") or 0) < 1:
        raise EmploymentError("There must be at least one position.")
    lo, hi = data.get("salary_min_minor"), data.get("salary_max_minor")
    if (lo is not None and lo < 0) or (hi is not None and hi < 0):
        raise EmploymentError("Pay can't be negative.")
    if lo is not None and hi is not None and hi < lo:
        raise EmploymentError("The highest pay is below the lowest pay.")
    data["open_to_learners"] = bool(data.get("open_to_learners"))
    return data


def _competencies(ids) -> list[Competency]:
    ids = [int(i) for i in ids or [] if str(i).isdigit()]
    if not ids:
        return []
    found = db.session.scalars(select(Competency).where(Competency.id.in_(ids))).all()
    if len(found) != len(set(ids)):
        raise EmploymentError("One of the chosen competencies doesn't exist.")
    return list(found)


def create_vacancy(tenant: Tenant, employer: Employer, data: dict, *, competency_ids=(), actor) -> Vacancy:
    if not employer.is_active:
        raise EmploymentError("This employer is inactive.")
    data = _vacancy_data(data)
    comps = _competencies(competency_ids)
    n = TenantCounter.next(tenant.id, "vacancy")
    v = Vacancy(tenant_id=tenant.id, employer_id=employer.id, code=f"J{date.today().year}-{n:03d}",
                created_by_id=getattr(actor, "id", None), competencies=comps, **data)
    db.session.add(v)
    db.session.flush()
    audit.record("vacancy.created", tenant_id=tenant.id, actor=actor, entity_type="vacancy", entity_id=v.id,
                 summary=f"Created vacancy {v.code} {v.title} at {employer.name}")
    db.session.refresh(v)
    return v


def update_vacancy(v: Vacancy, data: dict, *, competency_ids=(), actor) -> list[str]:
    data = _vacancy_data(data)
    if data["positions"] < v.hired_count:
        raise EmploymentError(f"{v.hired_count} people have already been hired; positions can't be fewer.")
    changed = [k for k, val in data.items() if getattr(v, k) != val]
    for k in changed:
        setattr(v, k, data[k])
    comps = _competencies(competency_ids)
    if {c.id for c in comps} != {c.id for c in v.competencies}:
        v.competencies = comps
        changed.append("competencies")
    if changed:
        audit.record("vacancy.updated", tenant_id=v.tenant_id, actor=actor, entity_type="vacancy", entity_id=v.id,
                     summary=f"{v.code}: changed {', '.join(c.replace('_', ' ') for c in changed)}")
    return changed


def set_vacancy_status(v: Vacancy, status: str, *, actor) -> None:
    if status not in VacancyStatus.LABELS:
        raise EmploymentError("Unknown status.")
    if status == v.status:
        return
    if status == VacancyStatus.OPEN:
        if not v.employer.is_active:
            raise EmploymentError("This employer is inactive.")
        if v.closing_date and v.closing_date < date.today():
            raise EmploymentError("The closing date has passed. Change it before opening the vacancy.")
        if v.hired_count >= v.positions:
            raise EmploymentError("Every position is filled. Add positions before opening it again.")
        v.opened_on = v.opened_on or date.today()
    old = v.status_label
    v.status = status
    audit.record("vacancy.status", tenant_id=v.tenant_id, actor=actor, entity_type="vacancy", entity_id=v.id,
                 summary=f"{v.code}: {old} → {v.status_label}")


def visible_to_learners_stmt():
    today = date.today()
    return (select(Vacancy).join(Employer, Employer.id == Vacancy.employer_id)
            .where(Vacancy.status == VacancyStatus.OPEN, Vacancy.open_to_learners.is_(True),
                   Employer.is_active.is_(True),
                   (Vacancy.closing_date.is_(None)) | (Vacancy.closing_date >= today)))


# ---- Matching -----------------------------------------------------------------------------------

def _latest_levels(learner_ids, competency_ids) -> dict[tuple[int, int], int]:
    if not learner_ids or not competency_ids:
        return {}
    rows = db.session.execute(
        select(Assessment.learner_id, AssessmentRating.competency_id, AssessmentRating.level)
        .join(Assessment, Assessment.id == AssessmentRating.assessment_id)
        .where(Assessment.status == AssessmentStatus.FINAL, Assessment.learner_id.in_(learner_ids),
               AssessmentRating.competency_id.in_(competency_ids))
        .order_by(Assessment.finalised_at, Assessment.id)).all()
    out: dict[tuple[int, int], int] = {}
    for learner_id, comp_id, level in rows:
        out[(learner_id, comp_id)] = level  # later finalised results overwrite earlier ones
    return out


def _words(text: str | None) -> set[str]:
    return {w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in (text or "")).split() if len(w) > 2}


def _trade_ok(trade_words: set[str], texts) -> bool:
    return bool(trade_words) and any(trade_words & _words(t) for t in texts)


def _context(learner_ids) -> dict:
    """Bulk-load what matching needs for many learners at once."""
    trades: dict[int, list[str]] = {i: [] for i in learner_ids}
    if learner_ids:
        for lid, trade in db.session.execute(
                select(Enrolment.learner_id, Programme.trade).join(Programme, Programme.id == Enrolment.programme_id)
                .where(Enrolment.learner_id.in_(learner_ids),
                       Enrolment.status.in_((EnrolmentStatus.ENROLLED, EnrolmentStatus.COMPLETED)))).all():
            trades[lid].append(trade or "")
        for lid, name in db.session.execute(select(LearnerSkill.learner_id, LearnerSkill.name)
                                            .where(LearnerSkill.learner_id.in_(learner_ids))).all():
            trades[lid].append(name)
    working = set(db.session.scalars(select(Placement.learner_id).where(
        Placement.learner_id.in_(learner_ids), Placement.status == PlacementStatus.ACTIVE)).all()) if learner_ids else set()
    certified = set(db.session.scalars(select(Certificate.learner_id).where(
        Certificate.learner_id.in_(learner_ids), Certificate.revoked_at.is_(None))).all()) if learner_ids else set()
    return {"trades": trades, "working": working, "certified": certified}


def score(v: Vacancy, learner: Learner, *, levels=None, ctx=None) -> dict:
    """How well a learner fits a vacancy, 0-100, with the reasons."""
    comps = list(v.competencies)
    if levels is None:
        levels = _latest_levels([learner.id], [c.id for c in comps])
    if ctx is None:
        ctx = _context([learner.id])
    achieved = [c for c in comps if levels.get((learner.id, c.id), 0) >= c.required_level]
    missing = [c for c in comps if c not in achieved]
    parts, got, total = [], 0.0, 0
    if comps:
        total += WEIGHTS["competencies"]
        got += WEIGHTS["competencies"] * len(achieved) / len(comps)
        parts.append((f"{len(achieved)} of {len(comps)} required competencies", len(achieved) == len(comps)))
    trade_words = _words(v.trade)
    if trade_words:
        ok = _trade_ok(trade_words, ctx["trades"].get(learner.id, []))
        total += WEIGHTS["trade"]
        got += WEIGHTS["trade"] if ok else 0
        parts.append(("Trained in " + v.trade if ok else "No training in " + v.trade, ok))
    if v.region:
        ok = (learner.region or "") == v.region
        total += WEIGHTS["region"]
        got += WEIGHTS["region"] if ok else 0
        parts.append((f"Lives in {v.region}" if ok else f"Lives outside {v.region}", ok))
    return {"learner": learner, "score": round(got * 100 / total) if total else None, "parts": parts,
            "achieved": achieved, "missing": missing, "working": learner.id in ctx["working"],
            "certified": learner.id in ctx["certified"]}


def candidates(v: Vacancy, *, include_working: bool = False, q: str | None = None, limit: int = 100) -> list[dict]:
    """Learners ranked by fit, excluding anyone already put forward for this vacancy."""
    already = {a.learner_id for a in v.applications}
    stmt = select(Learner).where(Learner.status.in_(CANDIDATE_STATUSES))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Learner.first_name.ilike(like) | Learner.last_name.ilike(like) | Learner.learner_no.ilike(like))
    learners = [l for l in db.session.scalars(stmt).all() if l.id not in already]
    ids = [l.id for l in learners]
    levels = _latest_levels(ids, [c.id for c in v.competencies])
    ctx = _context(ids)
    rows = [score(v, l, levels=levels, ctx=ctx) for l in learners]
    if not include_working:
        rows = [r for r in rows if not r["working"]]
    rows.sort(key=lambda r: (-(r["score"] or 0), not r["certified"], r["learner"].last_name, r["learner"].first_name))
    return rows[:limit]


# ---- Applications -------------------------------------------------------------------------------

def _check_learner(learner: Learner) -> None:
    if learner.status not in CANDIDATE_STATUSES:
        raise EmploymentError(f"{learner.full_name}'s record is {learner.status_label.lower()}.")


def nominate(v: Vacancy, learners, *, actor) -> list[Application]:
    if v.status != VacancyStatus.OPEN:
        raise EmploymentError("Open the vacancy before putting learners forward.")
    existing = {a.learner_id for a in v.applications}
    created = []
    for learner in learners:
        if learner.id in existing:
            continue
        _check_learner(learner)
        s = score(v, learner)
        a = Application(tenant_id=v.tenant_id, vacancy=v, learner=learner, source="nominated",
                        status=ApplicationStatus.NOMINATED, match_score=s["score"], created_by_id=getattr(actor, "id", None))
        db.session.add(a)
        created.append(a)
        existing.add(learner.id)
    db.session.flush()
    if created:
        audit.record("application.nominated", tenant_id=v.tenant_id, actor=actor, entity_type="vacancy", entity_id=v.id,
                     summary=f"Put forward {len(created)} learner{'s' if len(created) != 1 else ''} for {v.code} {v.title}")
    return created


def apply(v: Vacancy, learner: Learner, *, cover_note: str | None, user) -> Application:
    if not (v.is_accepting and v.open_to_learners and v.employer.is_active):
        raise EmploymentError("This vacancy isn't taking applications.")
    _check_learner(learner)
    if any(a.learner_id == learner.id for a in v.applications):
        raise EmploymentError("You've already applied for this job.")
    a = Application(tenant_id=v.tenant_id, vacancy=v, learner=learner, source="applied",
                    status=ApplicationStatus.APPLIED, match_score=score(v, learner)["score"],
                    cover_note=(cover_note or "").strip()[:2000] or None, created_by_id=getattr(user, "id", None))
    db.session.add(a)
    db.session.flush()
    audit.record("application.applied", tenant_id=v.tenant_id, actor=user, entity_type="application", entity_id=a.id,
                 summary=f"{learner.full_name} applied for {v.code} {v.title}")
    # Centre staff screen applications first; employer partner accounts only see shortlisted people.
    notes.notify_permission(v.tenant_id, "applicants.manage", "learners.view", category="jobs",
                            title=f"New application: {learner.full_name}",
                            body=f"{learner.full_name} applied for {v.title} at {v.employer.name} ({v.code}).",
                            link=f"/app/jobs/applications/{a.public_id}")
    return a


def withdraw(a: Application, *, user) -> None:
    if not a.is_open:
        raise EmploymentError("This application is already closed.")
    a.status = ApplicationStatus.WITHDRAWN
    a.decided_at = utcnow()
    audit.record("application.withdrawn", tenant_id=a.tenant_id, actor=user, entity_type="application",
                 entity_id=a.id, summary=f"{a.learner.full_name} withdrew from {a.vacancy.code}")


def set_status(a: Application, status: str, *, actor, scope: Scope = Scope()) -> None:
    allowed = ApplicationStatus.EMPLOYER_SETTABLE if scope.restricted else ApplicationStatus.SETTABLE
    if status not in allowed:
        raise EmploymentError("Choose a status from the list." if status != ApplicationStatus.HIRED
                              else "Use Hire to record a hire, so the placement is recorded too.")
    if a.status == ApplicationStatus.HIRED:
        raise EmploymentError("This person was hired. End the placement instead if things changed.")
    if not scope.allows_application(a):
        raise EmploymentError("You can't change this application.")
    if status == a.status:
        return
    old = a.status_label
    a.status = status
    a.decided_at = utcnow() if status in ApplicationStatus.CLOSED else None
    audit.record("application.status", tenant_id=a.tenant_id, actor=actor, entity_type="application", entity_id=a.id,
                 summary=f"{a.learner.full_name}, {a.vacancy.code}: {old} → {a.status_label}")
    _tell_about_status(a, actor)


def employer_users(employer_id: int, tenant_id: int) -> list[int]:
    return sorted(set(db.session.scalars(select(Membership.user_id).where(
        Membership.tenant_id == tenant_id, Membership.employer_id == employer_id, Membership.role == Role.EMPLOYER,
        Membership.is_active.is_(True))).all()))


def _tell_about_status(a: Application, actor) -> None:
    v, learner = a.vacancy, a.learner
    job = f"{v.title} at {v.employer.name}"
    messages = {
        ApplicationStatus.SHORTLISTED: ("You've been shortlisted", f"You're on the shortlist for {job}. "
                                        "The employer may contact you to arrange an interview."),
        ApplicationStatus.OFFERED: ("You've been offered a job", f"{v.employer.name} has offered you the {v.title} "
                                    "role. Contact your training centre to accept."),
        ApplicationStatus.NOT_SELECTED: ("Application update", f"You weren't selected for {job} this time. "
                                         "Keep an eye on Jobs for me for other openings."),
    }
    if a.status in messages and learner.user_id:
        title, body = messages[a.status]
        notes.notify(learner.user_id, a.tenant_id, category="jobs", title=title, body=body,
                     link=f"/app/my-jobs/{v.public_id}")
    if a.status == ApplicationStatus.SHORTLISTED:
        for uid in employer_users(v.employer_id, a.tenant_id):
            if uid != getattr(actor, "id", None):
                notes.notify(uid, a.tenant_id, category="jobs", title=f"New candidate for {v.title}",
                             body=f"{learner.full_name} has been shortlisted for {v.title} ({v.code}).",
                             link=f"/app/jobs/applications/{a.public_id}")


def schedule_interview(a: Application, *, when: datetime, location: str | None, actor,
                       scope: Scope = Scope()) -> bool:
    """Set the interview, move the application to Interview, and email the learner. Returns True if emailed."""
    if not a.is_open:
        raise EmploymentError("This application is closed.")
    if not scope.allows_application(a):
        raise EmploymentError("You can't change this application.")
    if when <= utcnow():
        raise EmploymentError("Choose a time in the future.")
    a.interview_at = when
    a.interview_location = (location or "").strip()[:160] or None
    a.status = ApplicationStatus.INTERVIEW
    audit.record("application.interview", tenant_id=a.tenant_id, actor=actor, entity_type="application",
                 entity_id=a.id, summary=f"Interview set for {a.learner.full_name}, {a.vacancy.code}")
    if a.learner.user_id:  # already emailed below, so in-app only
        notes.notify(a.learner.user_id, a.tenant_id, category="jobs", title=f"Interview: {a.vacancy.title}",
                     body=f"{when.strftime('%A %d %B %Y, %H:%M')} (GMT) · {a.interview_location or 'place to be confirmed'}",
                     link=f"/app/my-jobs/{a.vacancy.public_id}", email=False)
    email = a.learner.email or (a.learner.user.email if a.learner.user else None)
    if not email:
        return False
    v = a.vacancy
    body = (f"Hello {a.learner.first_name},\n\n"
            f"You have an interview for {v.title} at {v.employer.name}.\n\n"
            f"When: {when.strftime('%A %d %B %Y, %H:%M')} (GMT)\n"
            f"Where: {a.interview_location or 'to be confirmed'}\n\n"
            f"If you can't attend, please tell {a.learner.tenant.name} as soon as possible.\n\nPORTIA SkillBridge")
    mailer.send(email, f"Interview: {v.title} at {v.employer.name}", body)
    return True


def save_notes(a: Application, notes: str | None, *, actor, scope: Scope = Scope()) -> None:
    if not scope.allows_application(a):
        raise EmploymentError("You can't change this application.")
    a.notes = (notes or "").strip()[:4000] or None


def hire(a: Application, data: dict, *, actor, scope: Scope = Scope()) -> Placement:
    if not a.is_open:
        raise EmploymentError("This application is closed.")
    if not scope.allows_application(a):
        raise EmploymentError("You can't change this application.")
    v = a.vacancy
    if v.hired_count >= v.positions:
        raise EmploymentError("Every position on this vacancy is already filled. Add positions first.")
    p = create_placement(a.learner, {**data, "employment_type": data.get("employment_type") or v.employment_type,
                                     "job_title": data.get("job_title") or v.title},
                         employer=v.employer, vacancy=v, application=a, actor=actor)
    old = a.status_label
    a.status = ApplicationStatus.HIRED
    a.decided_at = utcnow()
    audit.record("application.hired", tenant_id=a.tenant_id, actor=actor, entity_type="application", entity_id=a.id,
                 summary=f"{a.learner.full_name} hired for {v.code} {v.title} ({old} → Hired)")
    if a.learner.user_id:
        notes.notify(a.learner.user_id, a.tenant_id, category="jobs", title="Congratulations on your new job",
                     body=f"You've been hired as {p.job_title} at {v.employer.name}. Your centre will check in after "
                          "3, 6 and 12 months.", link="/app/my-jobs/")
    if v.hired_count >= v.positions and v.status == VacancyStatus.OPEN:
        v.status = VacancyStatus.FILLED
        audit.record("vacancy.status", tenant_id=v.tenant_id, actor=actor, entity_type="vacancy", entity_id=v.id,
                     summary=f"{v.code}: every position filled")
    return p


# ---- Placements ---------------------------------------------------------------------------------

STATUS_FOR_TYPE = {"self_employed": "self_employed", "apprenticeship": "apprentice"}


def create_placement(learner: Learner, data: dict, *, employer: Employer | None = None, vacancy: Vacancy | None = None,
                     application: Application | None = None, actor) -> Placement:
    _check_learner(learner)
    start = data.get("start_date")
    if not isinstance(start, date):
        raise EmploymentError("Enter the start date.")
    if start > add_months(date.today(), 3):
        raise EmploymentError("The start date is more than three months away. Record the placement nearer the time.")
    etype = data.get("employment_type") or "full_time"
    if etype not in EMPLOYMENT_TYPES:
        raise EmploymentError("Choose the type of work.")
    title = (data.get("job_title") or "").strip()
    if not title:
        raise EmploymentError("Enter the job title.")
    name = employer.name if employer else (data.get("employer_name") or "").strip()
    if not name:
        raise EmploymentError("Enter the employer or business name." if etype != "self_employed"
                              else "Enter the business name.")
    pay = data.get("monthly_pay_minor")
    if pay is not None and pay < 0:
        raise EmploymentError("Pay can't be negative.")
    record = LearnerEmployment(tenant_id=learner.tenant_id, learner=learner, employer=name[:160], position=title[:120],
                               employment_type=etype, start_date=start, monthly_income_minor=pay,
                               sector=(SECTORS.get(employer.sector) if employer and employer.sector else None),
                               location=((vacancy.place if vacancy and vacancy.place != "—" else None)
                                         or (employer.place if employer and employer.place != "—" else None)))
    if record.sector:
        record.sector = record.sector[:80]
    if record.location:
        record.location = record.location[:120]
    db.session.add(record)
    p = Placement(tenant_id=learner.tenant_id, learner=learner, employer=employer, employer_name=name[:160],
                  vacancy=vacancy, application=application, employment_record=record, job_title=title[:160],
                  employment_type=etype, start_date=start, monthly_pay_minor=pay,
                  notes=(data.get("notes") or "").strip()[:2000] or None, created_by_id=getattr(actor, "id", None))
    for months in FOLLOW_UP_MONTHS:
        p.follow_ups.append(PlacementFollowUp(tenant_id=learner.tenant_id, months=months,
                                              due_on=add_months(start, months)))
    db.session.add(p)
    learner.employment_status = STATUS_FOR_TYPE.get(etype, "wage_employed")
    db.session.flush()
    audit.record("placement.created", tenant_id=learner.tenant_id, actor=actor, entity_type="placement",
                 entity_id=p.id, summary=f"{learner.full_name} started as {p.job_title} at {p.employer_name}")
    return p


def end_placement(p: Placement, *, end_date: date, reason: str | None, actor) -> None:
    if not p.is_active:
        raise EmploymentError("This placement has already ended.")
    if end_date < p.start_date:
        raise EmploymentError("The end date is before the start date.")
    if end_date > date.today():
        raise EmploymentError("The end date can't be in the future.")
    p.status = PlacementStatus.ENDED
    p.end_date = end_date
    p.end_reason = (reason or "").strip()[:255] or None
    if p.employment_record is not None:
        p.employment_record.end_date = end_date
    for fu in list(p.follow_ups):
        if not fu.is_done and fu.due_on > end_date:
            p.follow_ups.remove(fu)
    others = db.session.scalar(select(func.count(Placement.id)).where(
        Placement.learner_id == p.learner_id, Placement.id != p.id, Placement.status == PlacementStatus.ACTIVE))
    if not others:
        p.learner.employment_status = "unemployed"
    audit.record("placement.ended", tenant_id=p.tenant_id, actor=actor, entity_type="placement", entity_id=p.id,
                 summary=f"{p.learner.full_name}'s placement at {p.employer_name} ended")


def record_follow_up(fu: PlacementFollowUp, *, done_on: date, still_employed: bool, pay: int | None,
                     notes: str | None, actor) -> None:
    p = fu.placement
    if done_on > date.today():
        raise EmploymentError("The check can't be dated in the future.")
    if done_on < p.start_date:
        raise EmploymentError("The check is dated before the placement started.")
    if pay is not None and pay < 0:
        raise EmploymentError("Pay can't be negative.")
    fu.done_on = done_on
    fu.still_employed = bool(still_employed)
    fu.monthly_pay_minor = pay
    fu.notes = (notes or "").strip()[:500] or None
    fu.recorded_by_id = getattr(actor, "id", None)
    if still_employed and pay is not None and p.employment_record is not None:
        p.employment_record.monthly_income_minor = pay
    audit.record("placement.follow_up", tenant_id=p.tenant_id, actor=actor, entity_type="placement", entity_id=p.id,
                 summary=f"{fu.months}-month check for {p.learner.full_name}: "
                         f"{'still in work' if still_employed else 'no longer in work'}")
    if not still_employed and p.is_active:
        end_placement(p, end_date=done_on, reason=f"Not in work at the {fu.months}-month check", actor=actor)


def follow_ups_due(scope: Scope, *, within_days: int = 14) -> list[PlacementFollowUp]:
    from datetime import timedelta
    stmt = (select(PlacementFollowUp).join(Placement, Placement.id == PlacementFollowUp.placement_id)
            .where(PlacementFollowUp.done_on.is_(None), Placement.status == PlacementStatus.ACTIVE,
                   PlacementFollowUp.due_on <= date.today() + timedelta(days=within_days))
            .order_by(PlacementFollowUp.due_on))
    if scope.restricted:
        stmt = stmt.where(Placement.employer_id == (scope.employer.id if scope.employer else -1))
    return db.session.scalars(stmt).all()


# ---- Figures ------------------------------------------------------------------------------------

def notify_overdue_follow_ups(today: date | None = None) -> int:
    """Tell placement staff about follow-up checks that have become due (platform scope; once per check)."""
    today = today or date.today()
    sent = 0
    due = db.session.scalars(select(PlacementFollowUp).join(Placement, Placement.id == PlacementFollowUp.placement_id)
                             .where(PlacementFollowUp.done_on.is_(None), PlacementFollowUp.due_on <= today,
                                    Placement.status == PlacementStatus.ACTIVE)).all()
    for fu in due:
        p = fu.placement
        sent += notes.notify_permission(
            p.tenant_id, "placements.manage", "learners.view", category="jobs",
            title=f"{fu.months}-month check due: {p.learner.full_name}",
            body=f"Ask {p.learner.first_name} whether they're still working at {p.employer_name}, and record it.",
            link=f"/app/placements/{p.public_id}", dedupe_key=f"followup:{fu.id}")
    return sent


def stats(scope: Scope) -> dict:
    vac = scope.vacancy_filter(select(Vacancy).where(Vacancy.status == VacancyStatus.OPEN))
    open_vacancies = db.session.scalars(vac).all()
    pl = select(Placement)
    if scope.restricted:
        pl = pl.where(Placement.employer_id == (scope.employer.id if scope.employer else -1))
    placements = db.session.scalars(pl).all()
    year = date.today().year
    out = {
        "open_vacancies": len(open_vacancies),
        "open_positions": sum(max(0, v.positions - v.hired_count) for v in open_vacancies),
        "in_work": sum(1 for p in placements if p.is_active),
        "placed_this_year": sum(1 for p in placements if p.start_date.year == year),
        "follow_ups_due": len(follow_ups_due(scope, within_days=0)),
    }
    done = [fu for p in placements for fu in p.follow_ups if fu.is_done and fu.months == 6]
    out["retention_6m"] = round(100 * sum(1 for fu in done if fu.still_employed) / len(done)) if done else None
    if not scope.restricted:
        completers = set(db.session.scalars(select(Enrolment.learner_id).where(
            Enrolment.status == EnrolmentStatus.COMPLETED)).all())
        placed = {p.learner_id for p in placements}
        out["completers"] = len(completers)
        out["completers_placed"] = len(completers & placed)
        out["placement_rate"] = round(100 * len(completers & placed) / len(completers)) if completers else None
    return out
