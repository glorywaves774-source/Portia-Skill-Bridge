"""Competency frameworks, assessments, competency profiles and skills-gap analysis."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.extensions import db
from app.models import (
    Assessment,
    AssessmentEvidence,
    AssessmentRating,
    AssessmentStatus,
    Competency,
    Framework,
    FrameworkStatus,
    Learner,
    LearnerStatus,
    Tenant,
)
from app.models.assessment import ASSESSMENT_KINDS, LEVELS
from app.models.base import utcnow
from app.services import audit, files


class AssessmentError(ValueError):
    pass


# ---- Frameworks -------------------------------------------------------------------------------

def create_framework(tenant: Tenant, data: dict, *, actor) -> Framework:
    name = (data.get("name") or "").strip()
    if not name:
        raise AssessmentError("Give the framework a name.")
    fw = Framework(tenant_id=tenant.id, name=name, trade=(data.get("trade") or "").strip() or None,
                   level=(data.get("level") or "").strip() or None,
                   description=(data.get("description") or "").strip() or None, created_by_id=getattr(actor, "id", None))
    db.session.add(fw)
    db.session.flush()
    audit.record("framework.created", tenant_id=tenant.id, actor=actor, entity_type="framework", entity_id=fw.id,
                 summary=f"Created competency framework “{fw.name}”")
    return fw


def update_framework(fw: Framework, data: dict, *, actor) -> None:
    for key in ("name", "trade", "level", "description"):
        if key in data:
            value = (data[key] or "").strip() or None
            if key == "name" and not value:
                raise AssessmentError("Give the framework a name.")
            setattr(fw, key, value)


def set_framework_status(fw: Framework, status: str, *, actor) -> None:
    if status not in FrameworkStatus.LABELS:
        raise AssessmentError("Unknown status.")
    if status == FrameworkStatus.ACTIVE and not fw.competencies:
        raise AssessmentError("Add at least one competency before making the framework active.")
    fw.status = status
    audit.record("framework.status_changed", tenant_id=fw.tenant_id, actor=actor, entity_type="framework",
                 entity_id=fw.id, summary=f"“{fw.name}” is now {fw.status_label.lower()}")


def _criteria(text: str | list | None) -> list[str]:
    if isinstance(text, list):
        lines = text
    else:
        lines = (text or "").splitlines()
    return [ln.strip().lstrip("-•* ").strip()[:300] for ln in lines if ln.strip()][:20]


def add_competency(fw: Framework, *, code: str, title: str, description: str | None = None, criteria=None,
                   required_level: int = 3, actor=None) -> Competency:
    code = (code or "").strip().upper()
    title = (title or "").strip()
    if not code or not title:
        raise AssessmentError("A competency needs a code and a title.")
    if any(c.code == code for c in fw.competencies):
        raise AssessmentError(f"The code {code} is already used in this framework.")
    if required_level not in LEVELS:
        raise AssessmentError("Choose the level needed to be competent.")
    pos = max([c.position for c in fw.competencies], default=0) + 1
    c = Competency(tenant_id=fw.tenant_id, framework=fw, code=code[:20], title=title[:200],
                   description=(description or "").strip() or None, criteria=_criteria(criteria),
                   required_level=required_level, position=pos)
    db.session.add(c)
    db.session.flush()
    return c


def update_competency(c: Competency, *, title: str, description: str | None, criteria, required_level: int) -> None:
    if not (title or "").strip():
        raise AssessmentError("A competency needs a title.")
    if required_level not in LEVELS:
        raise AssessmentError("Choose the level needed to be competent.")
    c.title, c.description = title.strip()[:200], (description or "").strip() or None
    c.criteria, c.required_level = _criteria(criteria), required_level


def delete_competency(c: Competency) -> None:
    used = db.session.scalar(select(AssessmentRating.id).where(AssessmentRating.competency_id == c.id).limit(1))
    if used:
        raise AssessmentError("Learners have been assessed on this competency, so it can't be deleted. "
                              "Retire the framework and create a new version instead.")
    db.session.delete(c)


# ---- Assessments --------------------------------------------------------------------------------

def create_assessment(tenant: Tenant, *, learner: Learner, framework: Framework, kind: str, assessed_on: date,
                      programme=None, location: str | None = None, actor) -> Assessment:
    if not framework.is_active:
        raise AssessmentError("Only active frameworks can be used for assessments.")
    if learner.status == LearnerStatus.ARCHIVED:
        raise AssessmentError("This learner's record is archived.")
    if kind not in ASSESSMENT_KINDS:
        raise AssessmentError("Choose the type of assessment.")
    if assessed_on > date.today():
        raise AssessmentError("The assessment date can't be in the future.")
    a = Assessment(tenant_id=tenant.id, framework=framework, learner=learner, programme=programme,
                   assessor_id=actor.id, kind=kind, assessed_on=assessed_on,
                   location=(location or "").strip() or None)
    db.session.add(a)
    db.session.flush()
    audit.record("assessment.started", tenant_id=tenant.id, actor=actor, entity_type="assessment", entity_id=a.id,
                 summary=f"{learner.learner_no}: {framework.name} ({a.kind_label.lower()})")
    return a


def can_edit(a: Assessment, user, permissions) -> bool:
    """Drafts are edited by the assessor who started them (or an Organization Administrator)."""
    if a.is_final:
        return False
    return "assessments.conduct" in permissions and (a.assessor_id == user.id or "members.manage" in permissions)


def save_ratings(a: Assessment, ratings: dict[int, dict], *, comments: str | None, actor) -> None:
    if a.is_final:
        raise AssessmentError("This assessment has been finalised and can't be changed.")
    valid = {c.id: c for c in a.framework.competencies}
    for comp_id, r in ratings.items():
        c = valid.get(comp_id)
        level = r.get("level")
        if c is None or level is None:
            continue
        if level not in LEVELS:
            raise AssessmentError("Ratings go from 1 to 4.")
        met = sorted({int(i) for i in r.get("criteria", []) if 0 <= int(i) < len(c.criteria)})
        existing = a.rating_for(comp_id)
        if existing is None:
            a.ratings.append(AssessmentRating(tenant_id=a.tenant_id, competency_id=comp_id, level=level,
                                              criteria_met=met, comment=(r.get("comment") or "").strip()[:500] or None))
        else:
            existing.level, existing.criteria_met = level, met
            existing.comment = (r.get("comment") or "").strip()[:500] or None
    a.comments = (comments or "").strip() or None
    db.session.flush()


def finalise(a: Assessment, *, actor) -> None:
    if a.is_final:
        raise AssessmentError("Already finalised.")
    rated = {r.competency_id for r in a.ratings}
    missing = [c.code for c in a.framework.competencies if c.id not in rated]
    if missing:
        raise AssessmentError(f"Rate every competency before finalising (missing: {', '.join(missing)}).")
    db.session.expire(a, ["ratings"])
    a.competent = all(r.achieved for r in a.ratings)
    a.status = AssessmentStatus.FINAL
    a.finalised_at = utcnow()
    audit.record("assessment.finalised", tenant_id=a.tenant_id, actor=actor, entity_type="assessment",
                 entity_id=a.id, summary=f"{a.learner.learner_no}: {a.framework.name} — {a.result_label}")


def add_evidence(a: Assessment, upload, *, title: str, actor) -> AssessmentEvidence:
    if a.is_final:
        raise AssessmentError("Evidence can't be added after finalising.")
    sf = files.store(a.framework.tenant, upload, kind="document", area="assessment-evidence", actor=actor,
                     allowed=files.COURSE_FILES)
    ev = AssessmentEvidence(tenant_id=a.tenant_id, assessment=a, file=sf,
                            title=(title or "").strip()[:160] or sf.original_name)
    db.session.add(ev)
    db.session.flush()
    return ev


def delete_evidence(ev: AssessmentEvidence) -> None:
    if ev.assessment.is_final:
        raise AssessmentError("Evidence on a finalised assessment is kept as part of the record.")
    sf = ev.file
    ev.assessment.evidence.remove(ev)  # delete-orphan removes the row
    db.session.flush()
    files.delete(sf)


def delete_draft(a: Assessment) -> None:
    if a.is_final:
        raise AssessmentError("Finalised assessments are part of the learner's record and can't be deleted.")
    for ev in list(a.evidence):
        delete_evidence(ev)
    db.session.delete(a)


# ---- Profiles and gaps ---------------------------------------------------------------------------

def final_assessments(learner: Learner, framework: Framework | None = None) -> list[Assessment]:
    stmt = select(Assessment).where(Assessment.learner_id == learner.id,
                                    Assessment.status == AssessmentStatus.FINAL)
    if framework is not None:
        stmt = stmt.where(Assessment.framework_id == framework.id)
    return db.session.scalars(stmt.order_by(Assessment.finalised_at)).all()


def profile(learner: Learner, framework: Framework) -> list[dict]:
    """The learner's current standing on each competency: the latest finalised rating wins."""
    latest: dict[int, AssessmentRating] = {}
    for a in final_assessments(learner, framework):
        for r in a.ratings:
            latest[r.competency_id] = r
    rows = []
    for c in framework.competencies:
        r = latest.get(c.id)
        level = r.level if r else None
        rows.append({"competency": c, "level": level, "required": c.required_level,
                     "achieved": bool(level and level >= c.required_level),
                     "gap": max(0, c.required_level - (level or 0)), "assessed_on": r.assessment.assessed_on if r else None})
    return rows


def frameworks_for(learner: Learner) -> list[Framework]:
    ids = {a.framework_id for a in final_assessments(learner)}
    return [fw for fw in db.session.scalars(select(Framework).where(Framework.id.in_(ids))).all()] if ids else []


def cohort_gap(framework: Framework, learners: list[Learner]) -> dict:
    """For a group of learners: how many are competent in each competency, and the average level."""
    rows = []
    profiles = {l.id: profile(l, framework) for l in learners}
    assessed_learners = [l for l in learners if any(row["level"] for row in profiles[l.id])]
    for i, c in enumerate(framework.competencies):
        levels = [profiles[l.id][i]["level"] for l in assessed_learners if profiles[l.id][i]["level"]]
        achieved = sum(1 for l in assessed_learners if profiles[l.id][i]["achieved"])
        rows.append({"competency": c, "assessed": len(levels), "achieved": achieved,
                     "percent": round(achieved * 100 / len(levels)) if levels else None,
                     "average": round(sum(levels) / len(levels), 1) if levels else None})
    fully = sum(1 for l in assessed_learners if all(r["achieved"] for r in profiles[l.id]))
    return {"rows": rows, "learners": len(learners), "assessed": len(assessed_learners), "fully_competent": fully,
            "weakest": sorted([r for r in rows if r["percent"] is not None], key=lambda r: r["percent"])[:3]}
