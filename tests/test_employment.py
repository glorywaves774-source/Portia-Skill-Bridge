"""Recruitment and employment: employers, vacancies, matching, applications, hiring, placements and follow-ups."""
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Application,
    AuditLog,
    Employer,
    Learner,
    LearnerEmployment,
    Membership,
    Placement,
    Role,
    User,
    Vacancy,
)
from app.services import employment as svc
from app.services import mailer, members
from app.tenancy import platform_scope, tenant_context
from tests.conftest import PASSWORD, sign_in
from tests.test_assessment import _start, fw_setup  # noqa: F401  (fixture)

TODAY = date.today()


@pytest.fixture()
def jobs(app, fw_setup, org, make_person):
    """Abena is assessed competent in GC-01 and GC-02; Kofi isn't assessed. Both live in Ashanti."""
    a, comps = fw_setup["assessor"], fw_setup["comps"]
    url = _start(a, fw_setup)
    a.post(url, data={f"level-{comps['GC-01']}": "3", f"level-{comps['GC-02']}": "4", "action": "finalise"})
    make_person("admin@attc.example", [Role.ORG_ADMIN], org)
    admin = app.test_client()
    sign_in(admin, "admin@attc.example")
    r = admin.post("/app/employers/new", data={"name": "Kente Couture Ltd", "sector": "fashion", "region": "Ashanti",
                                               "city": "Kumasi", "contact_name": "Yaa Asantewaa",
                                               "phone": "+233 24 000 1111"})
    assert r.status_code == 302, r.data[:400]
    admin.post("/app/employers/new", data={"name": "Volta Garments"})
    with platform_scope():
        kente = db.session.scalar(select(Employer).where(Employer.name == "Kente Couture Ltd"))
        volta = db.session.scalar(select(Employer).where(Employer.name == "Volta Garments"))
    return {**fw_setup, "admin": admin, "kente": kente, "volta": volta}


def _vacancy(client, employer, **extra):
    data = {"employer": employer.public_id, "title": "Junior machinist", "employment_type": "full_time",
            "positions": "1", "region": "Ashanti", "open_to_learners": "y", "description": "## Duties\n- Sewing",
            **extra}
    r = client.post("/app/jobs/new", data=data)
    assert r.status_code == 302, r.data[:600]
    with platform_scope():
        return db.session.scalar(select(Vacancy).order_by(Vacancy.id.desc()))


def _open(client, v):
    return client.post(f"/app/jobs/{v.public_id}/status", data={"st-status": "open"}, follow_redirects=True)


def flashes(r):
    return re.findall(r'class="flash flash-[a-z]+">.*?<span>(.*?)</span>', r.get_data(as_text=True), re.S)


def _get(model, **kw):
    with platform_scope():
        return db.session.scalar(select(model).filter_by(**kw))


def test_employers_and_vacancy_lifecycle(jobs):
    admin, comps = jobs["admin"], jobs["comps"]
    r = admin.post("/app/employers/new", data={"name": "kente couture ltd"}, follow_redirects=True)
    assert b"already on your list" in r.data
    v = _vacancy(admin, jobs["kente"], competencies=[str(comps["GC-01"]), str(comps["GC-02"])],
                 salary_min="1200", salary_max="1800")
    assert v.code == f"J{TODAY.year}-001" and v.status == "draft"
    assert v.salary_min_minor == 120000 and v.salary_max_minor == 180000
    with platform_scope():
        assert sorted(c.code for c in db.session.get(Vacancy, v.id).competencies) == ["GC-01", "GC-02"]
    r = admin.post("/app/jobs/new", data={"employer": jobs["kente"].public_id, "title": "X", "employment_type": "full_time",
                                          "positions": "1", "salary_min": "500", "salary_max": "100"})
    assert b"below the lowest pay" in r.data
    admin.post(f"/app/jobs/{v.public_id}/edit", data={"employer": jobs["kente"].public_id, "title": "Junior machinist",
                                                      "employment_type": "full_time", "positions": "1",
                                                      "closing_date": (TODAY - timedelta(days=1)).isoformat()})
    r = _open(admin, v)
    assert b"closing date has passed" in r.data
    admin.post(f"/app/jobs/{v.public_id}/edit", data={"employer": jobs["kente"].public_id, "title": "Junior machinist",
                                                      "employment_type": "full_time", "positions": "2",
                                                      "region": "Ashanti", "open_to_learners": "y", "closing_date": "",
                                                      "salary_min": "", "salary_max": ""})
    r = _open(admin, v)
    assert b"is now open" in r.data, flashes(r)
    page = admin.get(f"/app/jobs/{v.public_id}").get_data(as_text=True)
    assert "Duties</h3>" in page or "Duties" in page
    assert "GHS 1,200 – GHS 1,800 a month" not in page  # pay was cleared by the edit
    assert _get(AuditLog, action="vacancy.status") is not None


def test_matching_ranks_competent_learner_first(jobs):
    admin, comps = jobs["admin"], jobs["comps"]
    v = _vacancy(admin, jobs["kente"], competencies=[str(comps["GC-01"]), str(comps["GC-02"])])
    with tenant_context(v.tenant_id):
        vac = db.session.get(Vacancy, v.id)
        rows = svc.candidates(vac)
        names = [r["learner"].first_name for r in rows]
        assert names == ["Abena", "Kofi"]
        assert rows[0]["score"] == 100 and rows[1]["score"] == 20  # Kofi only matches the region (15 of 75)
        assert [c.code for c in rows[1]["missing"]] == ["GC-01", "GC-02"]
    page = admin.get(f"/app/jobs/{v.public_id}?tab=matches").get_data(as_text=True)
    assert page.index("Abena Darko") < page.index("Kofi Mensah")
    assert "2 of 2 required competencies" in page


def test_nominate_shortlist_interview_and_hire(jobs, app):
    admin, learners = jobs["admin"], jobs["learners"]
    v = _vacancy(admin, jobs["kente"])
    r = admin.post(f"/app/jobs/{v.public_id}/nominate", data={"learner": [learners["Abena"].public_id]},
                   follow_redirects=True)
    assert b"Open the vacancy" in r.data
    _open(admin, v)
    tm = app.test_client()
    sign_in(tm, "tm@attc.example")  # training managers handle applicants and placements
    r = tm.post(f"/app/jobs/{v.public_id}/nominate",
                data={"learner": [learners["Abena"].public_id, learners["Kofi"].public_id]}, follow_redirects=True)
    assert b"Put forward 2 learners" in r.data
    abena_app = _get(Application, learner_id=learners["Abena"].id)
    assert abena_app.status == "nominated" and abena_app.source == "nominated"
    url = f"/app/jobs/applications/{abena_app.public_id}"
    r = tm.post(url + "/status", data={"ap-status": "hired"}, follow_redirects=True)
    assert b"Use Hire" in r.data
    tm.post(url + "/status", data={"ap-status": "shortlisted"})
    mailer.outbox.clear()
    with platform_scope():
        db.session.get(Learner, learners["Abena"].id).email = "abena@example.com"
        db.session.commit()
    when = (TODAY + timedelta(days=3)).isoformat() + "T10:30"
    r = tm.post(url + "/interview", data={"iv-when": when, "iv-location": "Kumasi workshop"}, follow_redirects=True)
    assert b"has been emailed" in r.data
    assert mailer.outbox and "Interview: Junior machinist at Kente Couture Ltd" in mailer.outbox[-1]["Subject"]
    assert _get(Application, id=abena_app.id).status == "interview"
    start = TODAY - timedelta(days=10)
    r = tm.post(url + "/hire", data={"hire-job_title": "Junior machinist", "hire-employment_type": "full_time",
                                     "hire-start_date": start.isoformat(), "hire-monthly_pay": "1500"})
    assert r.status_code == 302 and "/app/placements/" in r.headers["Location"]
    with platform_scope():
        a = db.session.get(Application, abena_app.id)
        p = db.session.scalar(select(Placement))
        assert a.status == "hired" and p.application_id == a.id and p.employer_id == jobs["kente"].id
        assert p.monthly_pay_minor == 150000 and [f.months for f in p.follow_ups] == [3, 6, 12]
        assert p.follow_ups[0].due_on == svc.add_months(start, 3)
        job = db.session.scalar(select(LearnerEmployment))
        assert job.employer == "Kente Couture Ltd" and job.position == "Junior machinist" and job.end_date is None
        assert db.session.get(Learner, learners["Abena"].id).employment_status == "wage_employed"
        assert db.session.get(Vacancy, v.id).status == "filled"  # one position, now filled
    r = tm.post(url + "/status", data={"ap-status": "not_selected"}, follow_redirects=True)
    assert b"was hired" in r.data
    kofi_app = _get(Application, learner_id=learners["Kofi"].id)
    r = tm.post(f"/app/jobs/applications/{kofi_app.public_id}/hire",
                data={"hire-job_title": "X", "hire-employment_type": "full_time", "hire-start_date": TODAY.isoformat()},
                follow_redirects=True)
    assert b"already filled" in r.data
    page = tm.get(f"/app/learners/{learners['Abena'].public_id}?tab=employment").get_data(as_text=True)
    assert "Placements and job applications" in page and "3-month check due" in page


def test_follow_up_not_in_work_ends_placement(jobs, app):
    tm = app.test_client()
    sign_in(tm, "tm@attc.example")
    abena = jobs["learners"]["Abena"]
    start = TODAY - timedelta(days=100)
    r = tm.post("/app/placements/new", data={"learner": abena.public_id, "employer": jobs["volta"].public_id,
                                             "job_title": "Seamstress", "employment_type": "full_time",
                                             "start_date": start.isoformat(), "monthly_pay": "900"})
    assert r.status_code == 302, r.data[:500]
    p = _get(Placement, learner_id=abena.id)
    with platform_scope():
        fu3 = db.session.get(Placement, p.id).follow_ups[0]
    page = tm.get(f"/app/placements/").get_data(as_text=True)
    assert "Follow-up checks due" in page and "Overdue since" in page
    r = tm.post(f"/app/placements/{p.public_id}/follow-ups/{fu3.id}",
                data={"fu-done_on": TODAY.isoformat(), "fu-still_employed": "no", "fu-notes": "Moved to Accra"},
                follow_redirects=True)
    assert b"3-month check recorded" in r.data
    with platform_scope():
        p = db.session.get(Placement, p.id)
        assert p.status == "ended" and p.end_date == TODAY and "3-month check" in p.end_reason
        assert [f.months for f in p.follow_ups] == [3]  # later checks cancelled
        assert p.employment_record.end_date == TODAY
        assert p.learner.employment_status == "unemployed"
    r = tm.post(f"/app/placements/{p.public_id}/follow-ups/{fu3.id}",
                data={"fu-done_on": TODAY.isoformat(), "fu-still_employed": "yes"}, follow_redirects=True)
    assert b"already been recorded" in r.data


def test_self_employment_and_ending(jobs, app):
    tm = app.test_client()
    sign_in(tm, "tm@attc.example")
    kofi = jobs["learners"]["Kofi"]
    r = tm.post("/app/placements/new", data={"learner": kofi.public_id, "job_title": "Owner",
                                             "employment_type": "self_employed", "start_date": TODAY.isoformat()},
                follow_redirects=True)
    assert b"Choose an employer or type the business name" in r.data
    r = tm.post("/app/placements/new", data={"learner": kofi.public_id, "employer_name": "Kofi's Tailoring",
                                             "job_title": "Owner", "employment_type": "self_employed",
                                             "start_date": (TODAY + timedelta(days=200)).isoformat()},
                follow_redirects=True)
    assert b"more than three months away" in r.data
    r = tm.post("/app/placements/new", data={"learner": kofi.public_id, "employer_name": "Kofi's Tailoring",
                                             "job_title": "Owner", "employment_type": "self_employed",
                                             "start_date": (TODAY - timedelta(days=5)).isoformat()})
    assert r.status_code == 302
    p = _get(Placement, learner_id=kofi.id)
    assert p.employer_id is None and p.employer_name == "Kofi's Tailoring"
    assert _get(Learner, id=kofi.id).employment_status == "self_employed"
    r = tm.post(f"/app/placements/{p.public_id}/end", data={"end-end_date": (TODAY + timedelta(days=1)).isoformat()},
                follow_redirects=True)
    assert b"can&#39;t be in the future" in r.data or b"can't be in the future" in r.data
    tm.post(f"/app/placements/{p.public_id}/end", data={"end-end_date": TODAY.isoformat(), "end-reason": "Closed shop"})
    assert _get(Placement, id=p.id).status == "ended"
    assert _get(Learner, id=kofi.id).employment_status == "unemployed"


def _employer_user(app, make_person, org, email="hr@kente.example"):
    make_person(email, [Role.EMPLOYER], org, name="Yaa Asantewaa")
    c = app.test_client()
    sign_in(c, email)
    return c


def test_employer_partner_sees_only_their_shortlisted_candidates(jobs, app, make_person, org):
    admin, learners = jobs["admin"], jobs["learners"]
    hr = _employer_user(app, make_person, org)
    # Not linked yet: nothing to see.
    assert b"isn't linked to an employer" in hr.get("/app/jobs/").data
    kente_job = _vacancy(admin, jobs["kente"], title="Machinist")
    volta_job = _vacancy(admin, jobs["volta"], title="Cutter")
    assert hr.get(f"/app/jobs/{kente_job.public_id}").status_code == 404
    with platform_scope():
        mid = db.session.scalar(select(Membership.id).join(User).where(User.email == "hr@kente.example"))
    page = admin.get(f"/app/employers/{jobs['kente'].public_id}").get_data(as_text=True)
    assert "Yaa Asantewaa" in page  # offered in the link list
    r = admin.post(f"/app/employers/{jobs['kente'].public_id}/link", data={"ln-member": str(mid)})
    assert r.status_code == 302
    for v in (kente_job, volta_job):
        _open(admin, v)
        admin.post(f"/app/jobs/{v.public_id}/nominate",
                   data={"learner": [learners["Abena"].public_id, learners["Kofi"].public_id]})
    abena_app = _get(Application, learner_id=learners["Abena"].id, vacancy_id=kente_job.id)
    kofi_app = _get(Application, learner_id=learners["Kofi"].id, vacancy_id=kente_job.id)
    admin.post(f"/app/jobs/applications/{abena_app.public_id}/status", data={"ap-status": "shortlisted"})

    listing = hr.get("/app/jobs/").get_data(as_text=True)
    assert "Machinist" in listing and "Cutter" not in listing
    assert hr.get(f"/app/jobs/{volta_job.public_id}").status_code == 404
    assert hr.get(f"/app/employers/{jobs['volta'].public_id}").status_code == 404
    assert hr.get("/app/employers/").status_code == 302  # straight to their own employer page
    applicants = hr.get(f"/app/jobs/{kente_job.public_id}?tab=applicants").get_data(as_text=True)
    assert "Abena Darko" in applicants and "Kofi Mensah" not in applicants and "still being screened" in applicants
    assert "Find candidates" not in hr.get(f"/app/jobs/{kente_job.public_id}").get_data(as_text=True)
    assert hr.get(f"/app/jobs/applications/{kofi_app.public_id}").status_code == 404
    detail = hr.get(f"/app/jobs/applications/{abena_app.public_id}").get_data(as_text=True)
    assert "+233 24 555 0101" in detail  # shortlisted: contact details shared
    assert hr.post(f"/app/jobs/{kente_job.public_id}/nominate",
                   data={"learner": [learners["Kofi"].public_id]}).status_code == 403
    r = hr.post(f"/app/jobs/applications/{abena_app.public_id}/status", data={"ap-status": "nominated"},
                follow_redirects=True)
    assert b"Choose a status from the list" in r.data
    hr.post(f"/app/jobs/applications/{abena_app.public_id}/status", data={"ap-status": "offered"})
    assert _get(Application, id=abena_app.id).status == "offered"
    assert hr.get("/app/learners/").status_code == 403
    assert hr.get("/app/placements/new").status_code == 403
    # They can post their own vacancy, but not for another employer.
    r = hr.post("/app/jobs/new", data={"employer": jobs["volta"].public_id, "title": "Sneaky",
                                       "employment_type": "full_time", "positions": "1"})
    assert r.status_code in (200, 400) and _get(Vacancy, title="Sneaky") is None
    r = hr.post("/app/jobs/new", data={"employer": jobs["kente"].public_id, "title": "Finisher",
                                       "employment_type": "part_time", "positions": "1"})
    assert r.status_code == 302 and _get(Vacancy, title="Finisher").employer_id == jobs["kente"].id
    # Unlinking removes their access straight away.
    admin.post(f"/app/employers/{jobs['kente'].public_id}/unlink/{mid}")
    assert hr.get(f"/app/jobs/{kente_job.public_id}").status_code == 404


def test_invitation_links_employer_on_accept(jobs, org, app):
    with app.test_request_context(), tenant_context(org.id):
        kente = db.session.get(Employer, jobs["kente"].id)
        inv, token = members.invite(org, email="boss@kente.example", roles=[Role.EMPLOYER], actor=None, employer=kente)
        db.session.commit()
        inv_id = inv.id
    user = User(email="boss@kente.example", full_name="Kwame Boss")
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.commit()
    with platform_scope():
        from app.models import Invitation
        members.accept(db.session.get(Invitation, inv_id), user)
    m = _get(Membership, user_id=user.id)
    assert m.role == Role.EMPLOYER and m.employer_id == jobs["kente"].id
    r = jobs["admin"].post(f"/app/employers/{jobs['kente'].public_id}/invite",
                           data={"inv-email": "second@kente.example", "inv-full_name": "Ama"}, follow_redirects=True)
    assert b"Invitation sent to second@kente.example" in r.data


def test_learner_finds_applies_and_withdraws(jobs, app, make_person, org):
    admin, abena = jobs["admin"], jobs["learners"]["Abena"]
    me = make_person("abena@example.com", [Role.LEARNER], org, name="Abena Darko")
    with platform_scope():
        db.session.get(Learner, abena.id).user_id = me.id
        db.session.commit()
    shown = _vacancy(admin, jobs["kente"], title="Machinist")
    hidden = _vacancy(admin, jobs["kente"], title="Supervisor", open_to_learners="")
    draft = _vacancy(admin, jobs["volta"], title="Draft job")
    _open(admin, shown)
    _open(admin, hidden)
    c = app.test_client()
    sign_in(c, "abena@example.com")
    page = c.get("/app/my-jobs/").get_data(as_text=True)
    assert "Machinist" in page and "Supervisor" not in page and "Draft job" not in page
    assert c.get(f"/app/my-jobs/{hidden.public_id}").status_code == 404
    assert c.get(f"/app/my-jobs/{draft.public_id}").status_code == 404
    assert c.get("/app/jobs/").status_code == 403  # the staff side is off-limits
    r = c.post(f"/app/my-jobs/{shown.public_id}/apply", data={"cover_note": "I love sewing."}, follow_redirects=True)
    assert b"Application sent" in r.data
    a = _get(Application, learner_id=abena.id)
    assert a.source == "applied" and a.status == "applied" and a.cover_note == "I love sewing."
    r = c.post(f"/app/my-jobs/{shown.public_id}/apply", data={}, follow_redirects=True)
    assert b"already applied" in r.data
    page = c.get("/app/my-jobs/").get_data(as_text=True)
    assert "My applications" in page
    c.post(f"/app/my-jobs/{shown.public_id}/withdraw")
    assert _get(Application, id=a.id).status == "withdrawn"
    # internal notes never reach the learner
    admin.post(f"/app/jobs/applications/{a.public_id}/notes", data={"nt-notes": "Strong finisher, slow cutter"})
    assert "slow cutter" not in c.get(f"/app/my-jobs/{shown.public_id}").get_data(as_text=True)


def test_isolation_between_workspaces(jobs, app, make_tenant, make_person):
    other = make_tenant("Beta Institute", plan="org-professional", organization_type="training_centre")
    make_person("admin@beta.example", [Role.ORG_ADMIN], other)
    c = app.test_client()
    sign_in(c, "admin@beta.example")
    v = _vacancy(jobs["admin"], jobs["kente"])
    assert c.get(f"/app/employers/{jobs['kente'].public_id}").status_code == 404
    assert c.get(f"/app/jobs/{v.public_id}").status_code == 404
    assert "Kente" not in c.get("/app/employers/").get_data(as_text=True)
    # the same employer name can exist in another workspace
    assert c.post("/app/employers/new", data={"name": "Kente Couture Ltd"}).status_code == 302


def test_add_months_handles_short_months():
    assert svc.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert svc.add_months(date(2027, 11, 30), 3) == date(2028, 2, 29)
    assert svc.add_months(date(2026, 6, 15), 12) == date(2027, 6, 15)


def test_pages_render_for_each_role(jobs, app, make_person, org):
    admin = jobs["admin"]
    v = _vacancy(admin, jobs["kente"])
    for path in ("/app/jobs/", "/app/employers/", "/app/placements/", f"/app/jobs/{v.public_id}",
                 f"/app/jobs/{v.public_id}?tab=applicants", f"/app/jobs/{v.public_id}?tab=matches",
                 f"/app/jobs/{v.public_id}/edit", "/app/jobs/new", "/app/employers/new", "/app/placements/new",
                 f"/app/employers/{jobs['kente'].public_id}", f"/app/employers/{jobs['kente'].public_id}/edit", "/app/"):
        r = admin.get(path)
        assert r.status_code == 200, path
    make_person("me@attc.example", [Role.ME_OFFICER], org)
    me = app.test_client()
    sign_in(me, "me@attc.example")
    assert me.get("/app/jobs/").status_code == 200
    assert me.get("/app/jobs/new").status_code == 403
    assert me.get("/app/placements/new").status_code == 403
    assert "Find candidates" not in me.get(f"/app/jobs/{v.public_id}").get_data(as_text=True)
    page = admin.get("/app/").get_data(as_text=True)
    assert "Jobs and employment" in page
    assert re.search(r'href="/app/jobs/"', admin.get("/app/").get_data(as_text=True))
