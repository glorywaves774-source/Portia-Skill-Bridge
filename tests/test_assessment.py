"""Competency frameworks, assessments, profiles and skills gaps."""
import io

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Assessment, AuditLog, Framework, Learner, Role
from app.services import assessments as svc
from app.tenancy import platform_scope
from tests.conftest import sign_in
from tests.test_files import PDF
from tests.test_learners import register


@pytest.fixture()
def fw_setup(app, client, org, make_person):
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    make_person("assessor@attc.example", [Role.ASSESSOR], org, name="Akua Assessor")
    sign_in(client, "tm@attc.example")
    register(client)
    register(client, first_name="Kofi", last_name="Mensah", gender="male", id_number="GHA-987654321-0",
             date_of_birth="1999-01-01")
    a = app.test_client()
    sign_in(a, "assessor@attc.example")
    r = a.post("/app/competencies/new", data={"name": "Garment Construction", "trade": "Fashion",
                                              "level": "CTVET Proficiency I"})
    assert r.status_code == 302
    with platform_scope():
        fw = db.session.scalar(select(Framework))
    url = f"/app/competencies/{fw.public_id}"
    r = a.post(url + "/status", data={"st-status": "active"}, follow_redirects=True)
    assert b"Add at least one competency" in r.data
    a.post(url + "/competencies", data={"c-code": "gc-01", "c-title": "Take body measurements",
                                       "c-criteria": "Client stands naturally\n- All 12 measurements recorded\n\n",
                                       "c-required_level": "3"})
    a.post(url + "/competencies", data={"c-code": "GC-02", "c-title": "Sew a straight seam", "c-required_level": "3"})
    r = a.post(url + "/competencies", data={"c-code": "GC-02", "c-title": "Duplicate", "c-required_level": "3"},
               follow_redirects=True)
    assert b"already used" in r.data
    a.post(url + "/status", data={"st-status": "active"})
    with platform_scope():
        fw = db.session.get(Framework, fw.id)
        learners = {l.first_name: l for l in db.session.scalars(select(Learner))}
        comps = {c.code: c.id for c in fw.competencies}
    return {"assessor": a, "fw": fw, "learners": learners, "comps": comps}


def _start(c, s, learner="Abena", kind="practical", date="2026-11-01"):
    r = c.post("/app/assessments/new", data={"learner": s["learners"][learner].public_id, "framework": s["fw"].public_id,
                                             "kind": kind, "assessed_on": date, "location": "Workshop B"})
    assert r.status_code == 302, r.data[:500]
    return r.headers["Location"]


def test_framework_codes_and_criteria(fw_setup):
    with platform_scope():
        fw = db.session.get(Framework, fw_setup["fw"].id)
        assert [c.code for c in fw.competencies] == ["GC-01", "GC-02"]
        assert fw.competencies[0].criteria == ["Client stands naturally", "All 12 measurements recorded"]
        assert fw.status == "active"


def test_rate_save_and_finalise(fw_setup):
    a, s, comps = fw_setup["assessor"], fw_setup, fw_setup["comps"]
    url = _start(a, s)
    a.post(url, data={f"level-{comps['GC-01']}": "3", f"crit-{comps['GC-01']}": ["0", "1", "9"],
                      "comments": "Good", "action": "save"})
    r = a.post(url, data={f"level-{comps['GC-01']}": "3", "action": "finalise"}, follow_redirects=True)
    assert b"missing: GC-02" in r.data
    r = a.post(url, data={f"level-{comps['GC-01']}": "4", f"crit-{comps['GC-01']}": ["0", "1", "9"],
                          f"level-{comps['GC-02']}": "2", "action": "finalise"}, follow_redirects=True)
    assert b"Not yet competent" in r.data
    with platform_scope():
        asm = db.session.scalar(select(Assessment))
        assert asm.is_final and asm.competent is False
        r1 = asm.rating_for(comps["GC-01"])
        assert r1.level == 4 and r1.criteria_met == [0, 1]  # out-of-range criterion ignored
    r = a.post(url, data={f"level-{comps['GC-02']}": "4", "action": "save"})
    assert r.status_code == 403  # finalised results are locked


def test_only_assessor_edits_draft(app, fw_setup, make_person, org):
    a, s, comps = fw_setup["assessor"], fw_setup, fw_setup["comps"]
    url = _start(a, s)
    make_person("other@attc.example", [Role.ASSESSOR], org)
    other = app.test_client()
    sign_in(other, "other@attc.example")
    assert other.get(url).status_code == 200  # can view
    assert other.post(url, data={f"level-{comps['GC-01']}": "1", "action": "save"}).status_code == 403
    make_person("t@attc.example", [Role.TRAINER], org)
    trainer = app.test_client()
    sign_in(trainer, "t@attc.example")
    assert trainer.post("/app/assessments/new", data={}).status_code == 403  # trainers view, don't assess


def test_profile_uses_latest_result_and_gaps(fw_setup):
    a, s, comps = fw_setup["assessor"], fw_setup, fw_setup["comps"]
    first = _start(a, s, date="2026-10-01")
    a.post(first, data={f"level-{comps['GC-01']}": "2", f"level-{comps['GC-02']}": "3", "action": "finalise"})
    second = _start(a, s, date="2026-11-01")
    a.post(second, data={f"level-{comps['GC-01']}": "3", f"level-{comps['GC-02']}": "3", "action": "finalise"})
    kofi = _start(a, s, learner="Kofi")
    a.post(kofi, data={f"level-{comps['GC-01']}": "1", f"level-{comps['GC-02']}": "4", "action": "finalise"})
    with platform_scope():
        abena = s["learners"]["Abena"]
        fw = db.session.get(Framework, s["fw"].id)
        rows = svc.profile(db.session.get(Learner, abena.id), fw)
        assert [r["level"] for r in rows] == [3, 3] and all(r["achieved"] for r in rows)
    page = a.get(f"/app/assessments/gaps?framework={s['fw'].public_id}").get_data(as_text=True)
    assert "50%" in page and "100%" in page and "is-weak" in page
    tab = a.get(f"/app/learners/{s['learners']['Kofi'].public_id}?tab=competencies").get_data(as_text=True)
    assert "Gap: 2 levels" in tab


def test_evidence_and_draft_delete(fw_setup):
    a, s = fw_setup["assessor"], fw_setup
    url = _start(a, s)
    r = a.post(url + "/evidence", data={"ev-title": "Photo", "ev-file": (io.BytesIO(PDF), "e.pdf")},
               content_type="multipart/form-data", follow_redirects=True)
    assert b"Evidence added" in r.data
    a.post(url + "/delete")
    with platform_scope():
        assert Assessment.query.count() == 0


def test_cannot_use_draft_framework_or_future_date(fw_setup):
    a, s = fw_setup["assessor"], fw_setup
    r = a.post("/app/assessments/new", data={"learner": s["learners"]["Abena"].public_id,
                                             "framework": s["fw"].public_id, "kind": "practical",
                                             "assessed_on": "2999-01-01"})
    assert b"can&#39;t be in the future" in r.data
    a.post(f"/app/competencies/{s['fw'].public_id}/status", data={"st-status": "retired"})
    r = a.post("/app/assessments/new", data={"learner": s["learners"]["Abena"].public_id,
                                             "framework": s["fw"].public_id, "kind": "practical",
                                             "assessed_on": "2026-01-01"})
    assert r.status_code == 200  # retired framework isn't offered


def test_assessed_competency_cannot_be_deleted(fw_setup):
    a, s, comps = fw_setup["assessor"], fw_setup, fw_setup["comps"]
    url = _start(a, s)
    a.post(url, data={f"level-{comps['GC-01']}": "3", "action": "save"})
    r = a.post(f"/app/competencies/{s['fw'].public_id}/competencies/{comps['GC-01']}/delete", follow_redirects=True)
    assert b"can&#39;t be deleted" in r.data


def test_assessments_isolated(app, fw_setup, make_tenant, make_person):
    url = _start(fw_setup["assessor"], fw_setup)
    other = make_tenant("Beta NGO", organization_type="ngo")
    make_person("b@beta.example", [Role.ORG_ADMIN], other)
    c = app.test_client()
    sign_in(c, "b@beta.example")
    assert c.get(url).status_code == 404
    assert c.get(f"/app/competencies/{fw_setup['fw'].public_id}").status_code == 404
