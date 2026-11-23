"""Monitoring and evaluation: indicators, disaggregation, trends and programme results.

Every figure is worked out from the workspace's own records for a reporting period, so it can be checked by
opening the underlying pages. Definitions (also shown on the report and in exports):

* Registered       learners whose registration date falls in the period
* Enrolled         learners who started a training programme in the period
* Completed        learners who completed a programme in the period
* Completion rate  of enrolments that started in the period and have finished, the share completed
* Attendance rate  present or late, out of all attendance marks for sessions in the period (excused left out)
* Assessed         learners with a finalised assessment in the period
* Competent        learners judged competent in at least one finalised assessment in the period
* Certified        learners issued a certificate in the period (revoked certificates are left out)
* Placed           learners who started a job, internship, apprenticeship or their own business in the period
* Placement rate   of learners who completed in the period, the share with a placement starting on or after
                   the day they enrolled
* Still in work    of follow-up checks recorded in the period, the share where the person was still working

Filters narrow the learners counted (programme, gender, region, disability, beneficiary category).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import (
    Assessment,
    AssessmentStatus,
    Attendance,
    AttendanceStatus,
    Certificate,
    Enrolment,
    EnrolmentStatus,
    Learner,
    LearnerStatus,
    Placement,
    PlacementFollowUp,
    Programme,
    TrainingSession,
)
from app.models.learner import CATEGORIES, GHANA_REGIONS, Gender

MAX_DAYS = 366 * 5
TREND_MONTHS = 24

INDICATORS = [
    # key, label, definition
    ("registered", "Registered", "Learners whose registration date falls in the period"),
    ("enrolled", "Enrolled in training", "Learners who started a training programme in the period"),
    ("completed", "Completed training", "Learners who completed a programme in the period"),
    ("assessed", "Assessed", "Learners with a finalised assessment in the period"),
    ("competent", "Assessed competent", "Learners judged competent in at least one finalised assessment in the period"),
    ("certified", "Certified", "Learners issued a certificate in the period (revoked certificates left out)"),
    ("placed", "Placed in work", "Learners who started a job, internship, apprenticeship or own business in the period"),
]
RATES = [
    ("completion_rate", "Completion rate",
     "Of enrolments that started in the period and have finished, the share completed"),
    ("attendance_rate", "Attendance rate",
     "Present or late, out of all attendance marks for sessions in the period (excused absences left out)"),
    ("competent_rate", "Competence rate", "Of learners assessed in the period, the share judged competent"),
    ("placement_rate", "Placement rate",
     "Of learners who completed training in the period, the share who started work or a business after enrolling"),
    ("retention_rate", "Still in work at follow-up",
     "Of follow-up checks recorded in the period, the share where the person was still working"),
]
GROUPS = [("total", "Total"), ("female", "Female"), ("male", "Male"), ("youth", "Youth 15–35"),
          ("pwd", "With a disability")]
AGE_BANDS = ["Under 15", "15–24", "25–35", "36–49", "50+", "Unknown"]
PLACEMENT_GROUPS = {
    "full_time": "Wage employment", "part_time": "Wage employment", "casual": "Wage employment",
    "self_employed": "Self-employment", "internship": "Internship or apprenticeship",
    "apprenticeship": "Internship or apprenticeship",
}
PLACEMENT_GROUP_ORDER = ["Wage employment", "Self-employment", "Internship or apprenticeship"]
CERT_KINDS = {"course": "Course", "programme": "Programme", "competency": "Competency"}


class ReportError(ValueError):
    pass


# ---- Filters ------------------------------------------------------------------------------------

def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@dataclass
class Filters:
    start: date
    end: date
    programme: Programme | None = None
    gender: str = ""
    region: str = ""
    disability: str = ""   # "", "yes", "no"
    category: str = ""
    notes: list[str] = field(default_factory=list)  # problems found while reading the filters

    def args(self) -> dict:
        out = {"start": self.start.isoformat(), "end": self.end.isoformat()}
        if self.programme:
            out["programme"] = self.programme.public_id
        for k in ("gender", "region", "disability", "category"):
            if getattr(self, k):
                out[k] = getattr(self, k)
        return out

    def describe(self) -> list[tuple[str, str]]:
        rows = [("Period", f"{self.start:%d %b %Y} to {self.end:%d %b %Y}")]
        rows.append(("Programme", f"{self.programme.code} · {self.programme.name}" if self.programme else "All programmes"))
        rows.append(("Gender", Gender.LABELS.get(self.gender, "Everyone")))
        rows.append(("Region", self.region or "All regions"))
        rows.append(("Disability", {"yes": "With a disability", "no": "Without a disability"}.get(self.disability,
                                                                                              "Everyone")))
        rows.append(("Beneficiary group", CATEGORIES.get(self.category, "Everyone")))
        return rows

    @property
    def narrowed(self) -> bool:
        return bool(self.programme or self.gender or self.region or self.disability or self.category)


def parse_filters(args, *, today: date | None = None) -> Filters:
    today = today or date.today()
    notes = []
    start = _parse_date(args.get("start"))
    end = _parse_date(args.get("end"))
    if args.get("start") and start is None or args.get("end") and end is None:
        notes.append("A date wasn't understood, so the default period is shown.")
    end = end or today
    start = start or date(end.year, 1, 1)
    if start > end:
        start, end = end, start
        notes.append("The start date was after the end date, so they were swapped.")
    if (end - start).days > MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS)
        notes.append("Reports cover at most five years, so the start date was moved.")
    prog = None
    if args.get("programme"):
        prog = db.session.scalar(select(Programme).where(Programme.public_id == args.get("programme")))
        if prog is None:
            notes.append("That programme wasn't found, so all programmes are shown.")
    gender = args.get("gender") if args.get("gender") in Gender.LABELS else ""
    region = args.get("region") if args.get("region") in GHANA_REGIONS else ""
    disability = args.get("disability") if args.get("disability") in ("yes", "no") else ""
    category = args.get("category") if args.get("category") in CATEGORIES else ""
    return Filters(start, end, prog, gender, region, disability, category, notes)


def period_presets(today: date | None = None) -> list[tuple[str, date, date]]:
    today = today or date.today()
    q = (today.month - 1) // 3
    q_start = date(today.year, q * 3 + 1, 1)
    last_q_end = q_start - timedelta(days=1)
    last_q_start = date(last_q_end.year, ((last_q_end.month - 1) // 3) * 3 + 1, 1)
    return [
        ("This year", date(today.year, 1, 1), today),
        ("This quarter", q_start, today),
        ("Last quarter", last_q_start, last_q_end),
        ("Last year", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
        ("Last 12 months", today - timedelta(days=365), today),
    ]


# ---- Helpers ------------------------------------------------------------------------------------

def _in(d, f: Filters) -> bool:
    if isinstance(d, datetime):
        d = d.date()
    return d is not None and f.start <= d <= f.end


def _pct(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def _months(start: date, end: date) -> list[date]:
    first = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    out = []
    while first <= last:
        out.append(first)
        first = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return out[-TREND_MONTHS:]


def _band(age: int | None) -> str:
    if age is None:
        return "Unknown"
    for upper, label in ((14, "Under 15"), (24, "15–24"), (35, "25–35"), (49, "36–49")):
        if age <= upper:
            return label
    return "50+"


@dataclass
class Person:
    id: int
    gender: str
    age: int | None
    band: str
    region: str
    pwd: bool

    def groups(self) -> set[str]:
        g = {"total"}
        if self.gender in ("female", "male"):
            g.add(self.gender)
        if self.age is not None and 15 <= self.age <= 35:
            g.add("youth")
        if self.pwd:
            g.add("pwd")
        return g


def _cohort(f: Filters) -> dict[int, Person]:
    stmt = select(Learner).where(Learner.status != LearnerStatus.ARCHIVED)
    if f.gender:
        stmt = stmt.where(Learner.gender == f.gender)
    if f.region:
        stmt = stmt.where(Learner.region == f.region)
    if f.disability:
        stmt = stmt.where(Learner.has_disability.is_(f.disability == "yes"))
    if f.programme:
        stmt = stmt.where(Learner.id.in_(select(Enrolment.learner_id).where(Enrolment.programme_id == f.programme.id)))
    people = {}
    for l in db.session.scalars(stmt):
        if f.category and f.category not in (l.categories or []):
            continue
        age = l.age(f.end)
        people[l.id] = Person(l.id, l.gender, age, _band(age), l.region or "Not recorded", bool(l.has_disability))
    return people


# ---- The report ---------------------------------------------------------------------------------

def build(f: Filters, *, with_learners: bool = False) -> dict:
    people = _cohort(f)
    ids = list(people)
    sets: dict[str, set[int]] = {k: set() for k, *_ in INDICATORS}
    trend_keys = ("enrolled", "completed", "certified", "placed")
    months = _months(f.start, f.end)
    trend = {k: Counter() for k in trend_keys}
    month_key = lambda d: date(d.year, d.month, 1)  # noqa: E731
    prog_rows: dict[int, dict] = {}

    enrolments, attendance, assessments, certificates, placements, follow_ups = [], [], [], [], [], []
    if ids:
        e_stmt = select(Enrolment).where(Enrolment.learner_id.in_(ids))
        if f.programme:
            e_stmt = e_stmt.where(Enrolment.programme_id == f.programme.id)
        enrolments = db.session.scalars(e_stmt).all()
        a_stmt = (select(Attendance.learner_id, Attendance.status, TrainingSession.programme_id)
                  .join(TrainingSession, TrainingSession.id == Attendance.session_id)
                  .where(Attendance.learner_id.in_(ids), TrainingSession.date >= f.start, TrainingSession.date <= f.end))
        if f.programme:
            a_stmt = a_stmt.where(TrainingSession.programme_id == f.programme.id)
        attendance = db.session.execute(a_stmt).all()
        s_stmt = select(Assessment).where(Assessment.learner_id.in_(ids), Assessment.status == AssessmentStatus.FINAL,
                                          Assessment.assessed_on >= f.start, Assessment.assessed_on <= f.end)
        if f.programme:
            s_stmt = s_stmt.where(Assessment.programme_id == f.programme.id)
        assessments = db.session.scalars(s_stmt).all()
        certificates = db.session.scalars(select(Certificate).where(
            Certificate.learner_id.in_(ids), Certificate.revoked_at.is_(None),
            Certificate.issued_on >= f.start, Certificate.issued_on <= f.end)).all()
        placements = db.session.scalars(select(Placement).where(Placement.learner_id.in_(ids))).all()
        follow_ups = db.session.execute(
            select(PlacementFollowUp, Placement.learner_id).join(Placement, Placement.id == PlacementFollowUp.placement_id)
            .where(Placement.learner_id.in_(ids), PlacementFollowUp.done_on >= f.start,
                   PlacementFollowUp.done_on <= f.end)).all()
        for l in db.session.execute(select(Learner.id, Learner.registered_on).where(Learner.id.in_(ids))).all():
            if _in(l.registered_on, f):
                sets["registered"].add(l.id)

    # Training
    finished = Counter()
    completed_starts: dict[int, list[date]] = defaultdict(list)  # enrolment dates of completions
    for e in enrolments:
        row = prog_rows.setdefault(e.programme_id, {"programme": e.programme, "enrolled": set(), "completed": set(),
                                                    "finished": 0, "done": 0, "present": 0, "marked": 0,
                                                    "competent": set(), "placed": set(), "learners": set()})
        row["learners"].add(e.learner_id)
        if _in(e.enrolled_on, f):
            sets["enrolled"].add(e.learner_id)
            row["enrolled"].add(e.learner_id)
            trend["enrolled"][month_key(e.enrolled_on)] += 1
            if e.status != EnrolmentStatus.ENROLLED:
                finished["all"] += 1
                row["finished"] += 1
                if e.status == EnrolmentStatus.COMPLETED:
                    finished["completed"] += 1
                    row["done"] += 1
        if e.status == EnrolmentStatus.COMPLETED and _in(e.completed_on, f):
            sets["completed"].add(e.learner_id)
            row["completed"].add(e.learner_id)
            completed_starts[e.learner_id].append(e.enrolled_on)
            trend["completed"][month_key(e.completed_on)] += 1

    present = marked = 0
    for learner_id, status, programme_id in attendance:
        if status == AttendanceStatus.EXCUSED:
            continue
        hit = status in AttendanceStatus.ATTENDED
        marked += 1
        present += hit
        if programme_id in prog_rows:
            prog_rows[programme_id]["marked"] += 1
            prog_rows[programme_id]["present"] += hit

    # Assessment and certification
    for a in assessments:
        sets["assessed"].add(a.learner_id)
        if a.competent:
            sets["competent"].add(a.learner_id)
            if a.programme_id in prog_rows:
                prog_rows[a.programme_id]["competent"].add(a.learner_id)
    cert_kinds = Counter()
    for c in certificates:
        sets["certified"].add(c.learner_id)
        cert_kinds[c.kind] += 1
        trend["certified"][month_key(c.issued_on)] += 1

    # Employment
    placed_types = Counter()
    pays = []
    placed_after: set[int] = set()
    for p in placements:
        if any(p.start_date >= s for s in completed_starts.get(p.learner_id, [])):
            placed_after.add(p.learner_id)
        if not _in(p.start_date, f):
            continue
        sets["placed"].add(p.learner_id)
        placed_types[PLACEMENT_GROUPS.get(p.employment_type, "Wage employment")] += 1
        trend["placed"][month_key(p.start_date)] += 1
        if p.monthly_pay_minor:
            pays.append(p.monthly_pay_minor)
        for row in prog_rows.values():
            if p.learner_id in row["learners"]:
                row["placed"].add(p.learner_id)
    retention = {m: {"done": 0, "working": 0} for m in (3, 6, 12)}
    fu_pays = []
    for fu, _learner_id in follow_ups:
        r = retention.setdefault(fu.months, {"done": 0, "working": 0})
        r["done"] += 1
        if fu.still_employed:
            r["working"] += 1
            if fu.monthly_pay_minor:
                fu_pays.append(fu.monthly_pay_minor)
    fu_done = sum(r["done"] for r in retention.values())
    fu_working = sum(r["working"] for r in retention.values())

    # Disaggregation
    def split(s: set[int]) -> dict[str, int]:
        out = {g: 0 for g, _ in GROUPS}
        for i in s:
            for g in people[i].groups():
                out[g] += 1
        return out

    counts = {k: len(sets[k]) for k, *_ in INDICATORS}
    disagg = [{"key": k, "label": label, "values": split(sets[k])} for k, label, _ in INDICATORS]
    by_band = []
    for band in AGE_BANDS:
        row = {k: sum(1 for i in sets[k] if people[i].band == band) for k in ("registered", "completed", "placed")}
        if any(row.values()) or band != "Unknown":
            by_band.append({"label": band, **row})
    regions = sorted({people[i].region for k in ("registered", "completed", "placed") for i in sets[k]},
                     key=lambda r: (r == "Not recorded", r))
    by_region = [{"label": r, **{k: sum(1 for i in sets[k] if people[i].region == r)
                                 for k in ("registered", "completed", "placed")}} for r in regions]
    by_region.sort(key=lambda r: (-(r["registered"] + r["completed"] + r["placed"]), r["label"]))

    completers = sets["completed"]
    rates = {
        "completion_rate": _pct(finished["completed"], finished["all"]),
        "attendance_rate": _pct(present, marked),
        "competent_rate": _pct(len(sets["competent"]), len(sets["assessed"])),
        "placement_rate": _pct(len(completers & placed_after), len(completers)),
        "retention_rate": _pct(fu_working, fu_done),
    }
    rate_basis = {
        "completion_rate": (finished["completed"], finished["all"], "enrolments"),
        "attendance_rate": (present, marked, "marks"),
        "competent_rate": (len(sets["competent"]), len(sets["assessed"]), "learners"),
        "placement_rate": (len(completers & placed_after), len(completers), "graduates"),
        "retention_rate": (fu_working, fu_done, "checks"),
    }

    programmes = []
    for row in prog_rows.values():
        if not (row["enrolled"] or row["completed"] or row["marked"]):
            continue
        programmes.append({
            "programme": row["programme"], "enrolled": len(row["enrolled"]), "completed": len(row["completed"]),
            "completion_rate": _pct(row["done"], row["finished"]), "attendance_rate": _pct(row["present"], row["marked"]),
            "competent": len(row["competent"]), "placed": len(row["placed"]),
        })
    programmes.sort(key=lambda r: (-r["enrolled"], r["programme"].code))

    report = {
        "filters": f, "learners_counted": len(people), "counts": counts, "rates": rates, "rate_basis": rate_basis,
        "disaggregation": disagg, "by_band": by_band, "by_region": by_region, "programmes": programmes,
        "certificates_by_kind": [(CERT_KINDS.get(k, k.title()), cert_kinds[k]) for k in CERT_KINDS if cert_kinds[k]],
        "placements_by_type": [(g, placed_types[g]) for g in PLACEMENT_GROUP_ORDER if placed_types[g]],
        "retention": [{"months": m, "done": r["done"], "working": r["working"],
                       "rate": _pct(r["working"], r["done"])} for m, r in sorted(retention.items())],
        "average_pay_minor": round(sum(pays) / len(pays)) if pays else None,
        "average_followup_pay_minor": round(sum(fu_pays) / len(fu_pays)) if fu_pays else None,
        "trend": {"months": months, "series": {k: [trend[k][m] for m in months] for k in trend_keys}},
        "generated_at": datetime.now(),
    }
    if with_learners:
        report["learners"] = _learner_rows(people, sets, f)
    return report


def _learner_rows(people: dict[int, Person], sets: dict[str, set[int]], f: Filters) -> list[dict]:
    """One row per learner with any activity in the period, for the Excel export."""
    active = set().union(*sets.values()) if sets else set()
    if not active:
        return []
    learners = db.session.scalars(select(Learner).where(Learner.id.in_(active))
                                  .order_by(Learner.learner_no)).all()
    return [{
        "learner": l, "age": people[l.id].age, "band": people[l.id].band,
        **{k: l.id in sets[k] for k, *_ in INDICATORS},
    } for l in learners]


# ---- Chart data (for the page's charts; numbers only, no personal details) -------------------------

def chart_data(r: dict) -> dict:
    labels = {k: label for k, label, _ in INDICATORS}
    chain = [k for k, *_ in INDICATORS if k != "assessed"]
    female = {d["key"]: d["values"]["female"] for d in r["disaggregation"]}
    male = {d["key"]: d["values"]["male"] for d in r["disaggregation"]}
    gender_keys = ["registered", "enrolled", "completed", "certified", "placed"]
    return {
        "chain": {"labels": [labels[k] for k in chain], "values": [r["counts"][k] for k in chain]},
        "trend": {"labels": [m.strftime("%b %Y") for m in r["trend"]["months"]],
                  "series": [{"name": n, "values": r["trend"]["series"][k]} for k, n in
                             (("enrolled", "Enrolled"), ("completed", "Completed"), ("certified", "Certificates"),
                              ("placed", "Placed"))]},
        "gender": {"labels": [labels[k] for k in gender_keys],
                   "series": [{"name": "Female", "values": [female[k] for k in gender_keys]},
                              {"name": "Male", "values": [male[k] for k in gender_keys]}]},
        "placements": {"labels": [g for g, _ in r["placements_by_type"]], "values": [n for _, n in r["placements_by_type"]]},
        "retention": {"labels": [f"{x['months']} months" for x in r["retention"]],
                      "values": [x["rate"] for x in r["retention"]],
                      "notes": [f"{x['working']} of {x['done']} checks" for x in r["retention"]]},
    }
