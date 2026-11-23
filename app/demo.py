"""Sample data for exploring PORTIA SkillBridge: `flask demo-data`.

Builds one training-centre workspace with staff, learners, a programme with attendance, an online course,
a competency framework with assessments and certificates, employers, vacancies, applications and
placements. Every record goes through the normal services, so it obeys the same rules as real data.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.extensions import db
from app.models import Membership, Role, Tenant, User
from app.services import assessments, certificates, employment, learners, lms, training
from app.tenancy import platform_scope, tenant_context

STAFF = [
    ("esi.boateng", "Esi Boateng", [Role.ORG_ADMIN], True),
    ("kwame.asare", "Kwame Asare", [Role.TRAINER, Role.ASSESSOR], False),
    ("adjoa.mensah", "Adjoa Mensah", [Role.TRAINING_MANAGER], False),
    ("yaw.darko", "Yaw Darko", [Role.ME_OFFICER], False),
]

LEARNERS = [
    # first, last, gender, birth year, region, city, disability
    ("Abena", "Darko", "female", 1995, "Greater Accra", "Accra", False),
    ("Kofi", "Mensah", "male", 1999, "Greater Accra", "Tema", False),
    ("Ama", "Owusu", "female", 2001, "Ashanti", "Kumasi", False),
    ("Yaw", "Boakye", "male", 2000, "Greater Accra", "Madina", True),
    ("Akosua", "Agyeman", "female", 2003, "Eastern", "Koforidua", False),
    ("Kwabena", "Osei", "male", 1998, "Greater Accra", "Accra", False),
    ("Efua", "Quansah", "female", 2002, "Central", "Cape Coast", False),
    ("Adwoa", "Tetteh", "female", 2004, "Greater Accra", "Ashaiman", False),
    ("Fuseini", "Abdulai", "male", 1997, "Northern", "Tamale", False),
    ("Akua", "Frimpong", "female", 2000, "Ashanti", "Obuasi", True),
    ("Nana", "Addo", "male", 2002, "Volta", "Ho", False),
    ("Esi", "Ankrah", "female", 1996, "Greater Accra", "Accra", False),
]

COMPETENCIES = [
    ("GC-01", "Take body measurements", ["Client stands naturally", "All 12 standard measurements recorded",
                                         "Within 0.5 cm of the assessor's check"]),
    ("GC-02", "Draft a basic bodice pattern", ["Uses the correct measurements", "Adds seam allowances",
                                               "Labels grainline and notches"]),
    ("GC-03", "Cut fabric accurately", ["Fabric is prepared and on grain", "Pattern pieces placed economically"]),
    ("GC-04", "Sew seams and finishes", ["Straight seams at 1.5 cm", "Neat seam finishes"]),
    ("GC-05", "Fit and finish a garment", ["Fitting adjustments made", "Hem and closures finished"]),
]

# Ratings per learner (GC-01..GC-05). Missing learners aren't assessed.
RATINGS = {
    "Abena": [3, 3, 4, 3, 3], "Kofi": [3, 2, 3, 3, 3], "Ama": [4, 3, 3, 4, 3], "Yaw": [2, 2, 3, 2, 2],
    "Akosua": [3, 1, 3, 3, 3], "Kwabena": [3, 2, 3, 2, 3], "Efua": [3, 3, 3, 3, 3],
}


def build(*, password: str | None = None) -> dict:
    password = password or ("Demo-" + secrets.token_urlsafe(9))
    today = date.today()
    with platform_scope():
        if db.session.scalar(select(Tenant).where(Tenant.name == "Accra Technical Training Centre")):
            raise RuntimeError("The demo workspace already exists. Use a fresh database.")
        from app.services.tenants import create_tenant
        t = create_tenant(name="Accra Technical Training Centre", kind="organization", plan_code="org-professional",
                          organization_type="training_centre", city="Accra", region="Greater Accra")
        db.session.flush()
        tid = t.id
        people = {}
        for login, name, roles, owner in STAFF:
            u = User(email=f"{login}@attc.example", full_name=name)
            u.set_password(password)
            db.session.add(u)
            for r in roles:
                db.session.add(Membership(tenant_id=tid, user=u, role=r, is_owner=owner))
            people[login] = u
        db.session.commit()

    with tenant_context(tid):
        t = db.session.get(Tenant, tid)
        admin, trainer, tm = (db.session.merge(people[k]) for k in ("esi.boateng", "kwame.asare", "adjoa.mensah"))
        ls = {}
        for i, (first, last, gender, year, region, city, pwd) in enumerate(LEARNERS):
            ls[first] = learners.create(t, {
                "first_name": first, "last_name": last, "gender": gender, "date_of_birth": date(year, 1 + i % 12, 3 + i),
                "phone": f"+233 24 555 {1000 + i:04d}", "region": region, "city": city, "education_level": "shs",
                "employment_status": "unemployed", "categories": ["youth"] + (["women"] if gender == "female" else []),
                "has_disability": pwd, "disability_details": "Hearing impairment" if pwd else None,
                "registered_on": today - timedelta(days=200),
            }, actor=admin)
        # A learner who can sign in
        lu = User(email="abena.darko@attc.example", full_name="Abena Darko")
        lu.set_password(password)
        db.session.add(lu)
        db.session.add(Membership(tenant_id=tid, user=lu, role=Role.LEARNER))
        db.session.flush()
        ls["Abena"].user_id = lu.id
        ls["Abena"].email = "abena.darko@attc.example"

        # Training programme with attendance
        p = training.create_programme(t, {"name": "Fashion Design & Garment Production — Cohort 3",
                                          "trade": "Fashion design", "delivery_mode": "in_person",
                                          "location": "Kaneshie workshop", "start_date": today - timedelta(days=150),
                                          "end_date": today - timedelta(days=20), "capacity": 15,
                                          "funder": "Youth Skills Fund"}, actor=admin)
        training.assign_trainer(p, trainer, lead=True, actor=admin)
        cohort = [ls[n] for n in ("Abena", "Kofi", "Ama", "Yaw", "Akosua", "Kwabena", "Efua", "Adwoa", "Fuseini", "Akua")]
        training.enrol(p, cohort, actor=admin)
        for w in range(4):
            s = training.add_session(p, {"title": f"Week {w + 1}: " + ("Measuring", "Pattern drafting", "Cutting",
                                                                         "Sewing seams")[w],
                                         "date": today - timedelta(days=140 - w * 7), "start_time": time(9),
                                         "end_time": time(13), "venue": "Workshop B", "trainer_id": trainer.id},
                                     actor=admin)
            marks = {l.id: ("absent" if (l.first_name == "Adwoa" and w % 2) else "late" if (l.first_name == "Yaw" and w == 2)
                            else "present") for l in cohort}
            training.record_attendance(s, marks, actor=trainer)
        training.set_status(p, "running", actor=admin)
        for e in p.enrolments:
            e.enrolled_on = p.start_date  # the cohort joined when the programme started
            if e.learner.first_name in ("Abena", "Kofi", "Ama", "Kwabena", "Efua", "Akosua"):
                training.set_enrolment_status(e, "completed", actor=admin)
                e.completed_on = p.end_date
            elif e.learner.first_name == "Fuseini":
                training.set_enrolment_status(e, "dropped_out", actor=admin)

        # A short online course
        c = lms.create_course(t, {"title": "Introduction to Garment Construction", "summary": "Tools, measuring, and your first stitched project.",
                                  "category": "Fashion", "level": "beginner", "estimated_hours": 6,
                                  "programme_id": p.id}, actor=trainer)
        m = c.modules[0]
        m.title = "Getting started"
        lms.add_lesson(m, {"title": "Workshop safety", "kind": "text",
                           "body": "## Before you start\n- Keep scissors closed\n- Switch off machines\n\n**Always** tidy your bench."},
                       actor=trainer)
        lms.add_lesson(m, {"title": "Measuring a client", "kind": "text",
                           "body": "Measure over light clothing. Record every measurement in centimetres."}, actor=trainer)
        lms.set_status(c, "published", actor=trainer)

        # Competency framework, assessments and certificates
        fw = assessments.create_framework(t, {"name": "Garment Construction", "trade": "Fashion design",
                                              "level": "CTVET Proficiency I"}, actor=trainer)
        for code, title, crit in COMPETENCIES:
            assessments.add_competency(fw, code=code, title=title, criteria=crit, required_level=3, actor=trainer)
        assessments.set_framework_status(fw, "active", actor=trainer)
        comps = list(fw.competencies)
        for name, levels in RATINGS.items():
            a = assessments.create_assessment(t, learner=ls[name], framework=fw, kind="practical",
                                              assessed_on=today - timedelta(days=25), programme=p,
                                              location="Workshop B", actor=trainer)
            assessments.save_ratings(a, {c.id: {"level": lv, "criteria": list(range(len(c.criteria)))}
                                         for c, lv in zip(comps, levels)}, comments=None, actor=trainer)
            assessments.finalise(a, actor=trainer)
            if a.competent:
                certificates.issue(t, ls[name], kind="competency", source_id=a.id, actor=admin,
                                   issued_on=today - timedelta(days=18), signatory_name="Esi Boateng",
                                   signatory_title="Centre Director")

        # Employers, vacancies, applications and placements
        kente = employment.create_employer(t, {"name": "Kente Couture Ltd", "sector": "fashion", "region": "Greater Accra",
                                               "city": "Accra", "contact_name": "Yaa Asantewaa", "contact_role": "HR Manager",
                                               "phone": "+233 30 222 4400", "email": "jobs@kentecouture.example"}, actor=admin)
        volta = employment.create_employer(t, {"name": "Volta Garments", "sector": "manufacturing", "region": "Volta",
                                               "city": "Ho", "contact_name": "Selorm Agbeko"}, actor=admin)
        hotel = employment.create_employer(t, {"name": "Labadi Coast Hotel", "sector": "hospitality",
                                               "region": "Greater Accra", "city": "Labadi"}, actor=admin)
        hr = User(email="yaa.asantewaa@kentecouture.example", full_name="Yaa Asantewaa")
        hr.set_password(password)
        db.session.add(hr)
        db.session.add(Membership(tenant_id=tid, user=hr, role=Role.EMPLOYER, employer_id=kente.id))
        db.session.flush()

        machinist = employment.create_vacancy(t, kente, {
            "title": "Junior machinist", "trade": "Fashion design", "employment_type": "full_time", "positions": 3,
            "region": "Greater Accra", "location": "North Industrial Area, Accra", "salary_min_minor": 120000,
            "salary_max_minor": 160000, "closing_date": today + timedelta(days=21), "open_to_learners": True,
            "description": "Join our production floor making ready-to-wear and kente-trim garments.\n\n## You'll\n- Sew garments to spec\n- Finish seams and hems\n- Keep your machine in good order",
            "requirements": "Competent in measuring, cutting and seams. Basic pattern reading."},
            competency_ids=[c.id for c in comps if c.code in ("GC-03", "GC-04", "GC-05")], actor=admin)
        employment.set_vacancy_status(machinist, "open", actor=admin)
        cutter = employment.create_vacancy(t, volta, {
            "title": "Pattern cutter", "trade": "Fashion design", "employment_type": "full_time", "positions": 1,
            "region": "Volta", "location": "Ho", "salary_min_minor": 150000, "closing_date": today + timedelta(days=10),
            "open_to_learners": True, "description": "Cut patterns and fabric for school uniforms."},
            competency_ids=[c.id for c in comps if c.code in ("GC-02", "GC-03")], actor=admin)
        employment.set_vacancy_status(cutter, "open", actor=admin)
        employment.create_vacancy(t, hotel, {
            "title": "Linen room attendant", "trade": "Fashion design", "employment_type": "internship", "positions": 2,
            "region": "Greater Accra", "location": "Labadi", "open_to_learners": False,
            "description": "Six-month internship repairing and maintaining hotel linen and uniforms."}, actor=admin)

        employment.nominate(machinist, [ls[n] for n in ("Abena", "Ama", "Efua", "Kofi", "Kwabena")], actor=tm)
        apps = {a.learner.first_name: a for a in machinist.applications}
        for n in ("Abena", "Ama", "Efua"):
            employment.set_status(apps[n], "shortlisted", actor=tm)
        employment.set_status(apps["Kwabena"], "not_selected", actor=tm)
        when = datetime.combine(today + timedelta(days=4), time(10, 30), tzinfo=timezone.utc)
        employment.schedule_interview(apps["Efua"], when=when, location="Kente Couture, North Industrial Area", actor=tm)
        employment.hire(apps["Abena"], {"start_date": today - timedelta(days=100), "monthly_pay_minor": 140000}, actor=tm)
        employment.hire(apps["Ama"], {"start_date": today - timedelta(days=12), "monthly_pay_minor": 130000}, actor=tm)
        employment.apply(cutter, ls["Akosua"], cover_note="I'm from the Eastern Region and happy to move to Ho.", user=None)

        own = employment.create_placement(ls["Kwabena"], {"employer_name": "Kwabena Osei Tailoring (own business)",
                                                          "job_title": "Owner", "employment_type": "self_employed",
                                                          "start_date": today - timedelta(days=120),
                                                          "monthly_pay_minor": 90000}, actor=tm)
        employment.record_follow_up(own.follow_ups[0], done_on=today - timedelta(days=28), still_employed=True,
                                    pay=95000, notes="Two regular school-uniform customers", actor=tm)
        db.session.commit()
    return {"password": password, "tenant": "Accra Technical Training Centre",
            "logins": [f"{k}@attc.example" for k, *_ in STAFF] + ["abena.darko@attc.example",
                                                                  "yaa.asantewaa@kentecouture.example"]}
