"""Stage 8: monitoring and evaluation reports, charts data and Excel/PDF exports."""
import io
from datetime import date

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Learner, Role, Tenant
from app.services import certificates, employment, learners, reports, training
from app.tenancy import platform_scope, tenant_context
from tests.conftest import sign_in

PERIOD = {"start": "2026-01-01", "end": "2026-06-30"}


def _learner(t, admin, first, gender, dob, region, registered, *, pwd=False, categories=()):
    return learners.create(t, {"first_name": first, "last_name": "Test", "gender": gender, "date_of_birth": dob,
                               "region": region, "has_disability": pwd, "categories": list(categories),
                               "registered_on": registered}, actor=admin)


@pytest.fixture()
def me(app, org, make_person):
    """A hand-checkable workspace. Period of interest: 1 Jan to 30 Jun 2026.

    Ama   F 2000  Ashanti  registered Feb 2026, enrolled Jan 10, completed May 30, placed Jun 1 (GHS 1,500)
    Kofi  M 1980  Volta    PWD, registered Dec 2025, enrolled Jan 10, dropped out
    Efua  F 2003  Ashanti  'women', registered Mar 2026, enrolled Dec 15 2025, completed Jan 20,
                           own business from Dec 2025 (before enrolling), not in work at 3-month check (Mar 5)
    Yaw   M 2001  archived (never counted)
    """
    admin = make_person("esi@attc.example", [Role.ORG_ADMIN], org)
    with tenant_context(org.id):
        t = db.session.get(Tenant, org.id)
        admin = db.session.merge(admin)
        ama = _learner(t, admin, "Ama", "female", date(2000, 5, 1), "Ashanti", date(2026, 2, 1))
        kofi = _learner(t, admin, "Kofi", "male", date(1980, 1, 1), "Volta", date(2025, 12, 1), pwd=True)
        efua = _learner(t, admin, "Efua", "female", date(2003, 3, 3), "Ashanti", date(2026, 3, 1), categories=["women"])
        yaw = _learner(t, admin, "Yaw", "male", date(2001, 1, 1), "Ashanti", date(2026, 2, 1))
        yaw.status = "archived"
        p = training.create_programme(t, {"name": "Garment Production", "trade": "Fashion design",
                                          "start_date": date(2026, 1, 10), "end_date": date(2026, 5, 30)}, actor=admin)
        training.enrol(p, [ama, kofi, efua], actor=admin)
        e = {x.learner.first_name: x for x in p.enrolments}
        e["Ama"].enrolled_on = e["Kofi"].enrolled_on = date(2026, 1, 10)
        e["Efua"].enrolled_on = date(2025, 12, 15)
        s1 = training.add_session(p, {"title": "Week 1", "date": date(2026, 2, 1)}, actor=admin)
        training.record_attendance(s1, {ama.id: "present", kofi.id: "absent", efua.id: "excused"}, actor=admin)
        s0 = training.add_session(p, {"title": "Induction", "date": date(2025, 12, 20)}, actor=admin)
        training.record_attendance(s0, {ama.id: "present", kofi.id: "absent", efua.id: "absent"}, actor=admin)
        training.set_enrolment_status(e["Ama"], "completed", actor=admin)
        e["Ama"].completed_on = date(2026, 5, 30)
        training.set_enrolment_status(e["Efua"], "completed", actor=admin)
        e["Efua"].completed_on = date(2026, 1, 20)
        training.set_enrolment_status(e["Kofi"], "dropped_out", actor=admin)
        certificates.issue(t, ama, kind="programme", source_id=e["Ama"].id, actor=admin, issued_on=date(2026, 6, 5))
        employment.create_placement(ama, {"employer_name": "Kente Couture", "job_title": "Machinist",
                                          "employment_type": "full_time", "start_date": date(2026, 6, 1),
                                          "monthly_pay_minor": 150000}, actor=admin)
        own = employment.create_placement(efua, {"employer_name": "Efua Designs", "job_title": "Owner",
                                                 "employment_type": "self_employed", "start_date": date(2025, 12, 1),
                                                 "monthly_pay_minor": 50000}, actor=admin)
        employment.record_follow_up(own.follow_ups[0], done_on=date(2026, 3, 5), still_employed=False, pay=None,
                                    notes=None, actor=admin)
        db.session.commit()
        ids = {"programme": p.public_id, "org": org.id}
    db.session.expunge_all()
    client = app.test_client()
    sign_in(client, "esi@attc.example")
    return {**ids, "client": client}


def _build(org_id, **args):
    with tenant_context(org_id):
        return reports.build(reports.parse_filters({**PERIOD, **args}))


def test_indicators_follow_their_definitions(me):
    r = _build(me["org"])
    assert r["learners_counted"] == 3  # archived Yaw left out
    assert r["counts"] == {"registered": 2, "enrolled": 2, "completed": 2, "assessed": 0, "competent": 0,
                           "certified": 1, "placed": 1}
    assert r["rates"]["completion_rate"] == 50.0      # of Ama and Kofi (started in period): Ama completed
    assert r["rates"]["attendance_rate"] == 50.0      # Feb session: Ama present, Kofi absent, Efua excused
    assert r["rates"]["competent_rate"] is None       # nobody assessed
    assert r["rates"]["placement_rate"] == 50.0       # Ama placed after enrolling; Efua's business predates it
    assert r["rates"]["retention_rate"] == 0.0        # Efua's 3-month check: not in work
    assert r["rate_basis"]["completion_rate"] == (1, 2, "enrolments")
    assert r["placements_by_type"] == [("Wage employment", 1)]
    assert r["average_pay_minor"] == 150000
    assert r["certificates_by_kind"] == [("Programme", 1)]
    assert r["retention"][0] == {"months": 3, "done": 1, "working": 0, "rate": 0.0}
    prog = r["programmes"][0]
    assert (prog["enrolled"], prog["completed"], prog["completion_rate"], prog["attendance_rate"], prog["placed"]) \
        == (2, 2, 50.0, 50.0, 1)


def test_disaggregation_by_group_age_and_region(me):
    r = _build(me["org"])
    groups = {d["key"]: d["values"] for d in r["disaggregation"]}
    assert groups["registered"] == {"total": 2, "female": 2, "male": 0, "youth": 2, "pwd": 0}
    assert groups["enrolled"] == {"total": 2, "female": 1, "male": 1, "youth": 1, "pwd": 1}  # Kofi is 46 and PWD
    bands = {b["label"]: b for b in r["by_band"]}
    assert bands["25–35"]["registered"] == 1 and bands["15–24"]["registered"] == 1 and bands["36–49"]["registered"] == 0
    regions = {b["label"]: b for b in r["by_region"]}
    assert regions["Ashanti"] == {"label": "Ashanti", "registered": 2, "completed": 2, "placed": 1}
    months = [m.month for m in r["trend"]["months"]]
    assert months == [1, 2, 3, 4, 5, 6]
    assert r["trend"]["series"]["enrolled"] == [2, 0, 0, 0, 0, 0]
    assert r["trend"]["series"]["completed"] == [1, 0, 0, 0, 1, 0]
    data = reports.chart_data(r)
    assert data["chain"]["labels"][0] == "Registered" and "Assessed" not in data["chain"]["labels"]
    assert [s["name"] for s in data["gender"]["series"]] == ["Female", "Male"]


def test_filters_narrow_the_learners_counted(me):
    assert _build(me["org"], gender="female")["counts"]["enrolled"] == 1
    volta = _build(me["org"], region="Volta")
    assert volta["learners_counted"] == 1 and volta["counts"]["registered"] == 0
    assert _build(me["org"], category="women")["counts"]["completed"] == 1
    assert _build(me["org"], disability="yes")["learners_counted"] == 1
    assert _build(me["org"], programme=me["programme"])["learners_counted"] == 3
    # A period before anything happened is empty but still works.
    empty = _build(me["org"], start="2020-01-01", end="2020-12-31")
    assert set(empty["counts"].values()) == {0} and empty["rates"]["completion_rate"] is None


def test_filter_problems_are_explained(app):
    today = date(2026, 11, 22)
    f = reports.parse_filters({"start": "2026-09-01", "end": "2026-03-01"}, today=today)
    assert (f.start, f.end) == (date(2026, 3, 1), date(2026, 9, 1)) and "swapped" in f.notes[0]
    f = reports.parse_filters({"start": "not-a-date"}, today=today)
    assert f.start == date(2026, 1, 1) and f.end == today and "wasn't understood" in f.notes[0]
    f = reports.parse_filters({"start": "2000-01-01", "end": "2026-01-01", "gender": "robot", "region": "Mars"},
                              today=today)
    assert (f.end - f.start).days <= reports.MAX_DAYS and f.gender == "" and f.region == ""
    presets = dict((label, (s, e)) for label, s, e in reports.period_presets(today))
    assert presets["This quarter"] == (date(2026, 10, 1), today)
    assert presets["Last quarter"] == (date(2026, 7, 1), date(2026, 9, 30))


def test_report_page_and_exports(me):
    c = me["client"]
    page = c.get("/app/reports/", query_string=PERIOD)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'id="report-data"' in html and "vendor/chart.umd.min.js" in html and "<script>" not in html
    assert "1 of 2 enrolments" in html and "PDF report" in html
    xlsx = c.get("/app/reports/export.xlsx", query_string=PERIOD)
    assert xlsx.status_code == 200 and xlsx.headers["Cache-Control"] == "private, no-store"
    assert "attachment" in xlsx.headers["Content-Disposition"]
    wb = load_workbook(io.BytesIO(xlsx.data))
    assert wb.sheetnames == ["Summary", "By group", "By programme", "Monthly", "Employment", "Learners"]
    names = [row[1] for row in wb["Learners"].iter_rows(min_row=2, values_only=True)]
    assert sorted(names) == ["Ama", "Efua", "Kofi"]
    pdf = c.get("/app/reports/export.pdf", query_string=PERIOD)
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF" and pdf.mimetype == "application/pdf"
    with platform_scope():
        logged = db.session.scalars(select(AuditLog.summary).where(AuditLog.action == "report.exported")).all()
    assert len(logged) == 2 and any("3 learner rows" in s for s in logged)


def test_spreadsheet_cells_cannot_run_formulas(me):
    with tenant_context(me["org"]):
        ama = db.session.scalar(select(Learner).where(Learner.first_name == "Ama"))
        ama.first_name = "=HYPERLINK(\"http://evil.example\")"
        db.session.commit()
    wb = load_workbook(io.BytesIO(me["client"].get("/app/reports/export.xlsx", query_string=PERIOD).data))
    firsts = [row[1] for row in wb["Learners"].iter_rows(min_row=2, values_only=True)]
    assert any(v.startswith("'=") for v in firsts)


@pytest.mark.parametrize("roles,view,export", [
    ([Role.ME_OFFICER], 200, 200),
    ([Role.TRAINING_MANAGER], 200, 200),
    ([Role.FINANCE_OFFICER], 200, 403),
    ([Role.TRAINER], 403, 403),
    ([Role.EMPLOYER], 403, 403),
    ([Role.LEARNER], 403, 403),
])
def test_who_can_see_and_export_reports(app, org, make_person, roles, view, export):
    make_person("someone@attc.example", roles, org)
    c = app.test_client()
    sign_in(c, "someone@attc.example")
    assert c.get("/app/reports/").status_code == view
    assert c.get("/app/reports/export.pdf").status_code == export
    assert c.get("/app/reports/export.xlsx").status_code == export


def test_me_officer_gets_reports_as_a_main_tab(app, org, make_person):
    make_person("me@attc.example", [Role.ME_OFFICER], org)
    c = app.test_client()
    sign_in(c, "me@attc.example")
    html = c.get("/app/").get_data(as_text=True)
    tabbar = html[html.index('class="tabbar"'):]
    assert tabbar.index("/app/reports/") < tabbar.index("/app/learners/")


def test_reports_only_count_your_own_workspace(me, app, make_tenant, make_person):
    other = make_tenant("Beta NGO", plan="org-professional", organization_type="ngo")
    make_person("beta@beta.example", [Role.ORG_ADMIN], other)
    c = app.test_client()
    sign_in(c, "beta@beta.example")
    html = c.get("/app/reports/", query_string=PERIOD).get_data(as_text=True)
    assert "0 learner records match" in html
    wb = load_workbook(io.BytesIO(c.get("/app/reports/export.xlsx", query_string=PERIOD).data))
    assert wb["Learners"].max_row == 1  # header only
    # Someone else's programme id is ignored, not used.
    with tenant_context(other.id):
        f = reports.parse_filters({**PERIOD, "programme": me["programme"]})
    assert f.programme is None and f.notes
