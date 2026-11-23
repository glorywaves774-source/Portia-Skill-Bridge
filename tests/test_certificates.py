"""Certificates: eligibility, numbering, PDF with QR code, revocation and public verification."""
import re
from datetime import date

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Certificate, Enrolment, Learner, LearnerSkill, Programme, Role
from app.services import certificates as certs
from app.services import mailer
from app.tenancy import platform_scope
from tests.conftest import sign_in
from tests.test_assessment import _start, fw_setup  # noqa: F401  (fixture)


@pytest.fixture()
def competent(fw_setup):
    a, s, comps = fw_setup["assessor"], fw_setup, fw_setup["comps"]
    url = _start(a, s)
    a.post(url, data={f"level-{comps['GC-01']}": "3", f"level-{comps['GC-02']}": "4", "action": "finalise"})
    return fw_setup


def _issue(client, learner, **extra):
    page = client.get(f"/app/certificates/issue/{learner.public_id}").get_data(as_text=True)
    basis = re.search(r'<option value="(competency:\d+)"', page)
    assert basis, page[:2000]
    return client.post(f"/app/certificates/issue/{learner.public_id}",
                       data={"basis": basis.group(1), "issued_on": date.today().isoformat(),
                             "signatory_name": "Esi Boateng", "signatory_title": "Director", **extra})


def test_issue_competency_certificate(competent, org):
    a = competent["assessor"]
    abena = competent["learners"]["Abena"]
    r = _issue(a, abena)
    assert r.status_code == 302
    with platform_scope():
        cert = db.session.scalar(select(Certificate))
        assert cert.number == f"{org.code.rsplit('-', 1)[-1]}-{date.today().year}-00001"
        assert re.fullmatch(r"[A-Z2-9]{5}-[A-Z2-9]{5}", cert.verification_code)
        skill = db.session.scalar(select(LearnerSkill))
        assert skill.certified and "Garment Construction" in skill.name
    # nothing left to issue for this assessment
    page = a.get(f"/app/certificates/issue/{abena.public_id}").get_data(as_text=True)
    assert "nothing to certify yet" in page


def test_not_yet_competent_is_not_eligible(fw_setup):
    a, s, comps = fw_setup["assessor"], fw_setup, fw_setup["comps"]
    url = _start(a, s)
    a.post(url, data={f"level-{comps['GC-01']}": "1", f"level-{comps['GC-02']}": "4", "action": "finalise"})
    page = a.get(f"/app/certificates/issue/{s['learners']['Abena'].public_id}").get_data(as_text=True)
    assert "nothing to certify yet" in page
    with platform_scope():
        with pytest.raises(certs.CertificateError):
            certs.issue(org_tenant(), db.session.get(Learner, s["learners"]["Abena"].id), kind="competency",
                        source_id=1, actor=None)


def org_tenant():
    from app.models import Tenant
    return db.session.scalar(select(Tenant))


def test_pdf_has_qr_and_details(competent):
    a = competent["assessor"]
    _issue(a, competent["learners"]["Abena"])
    with platform_scope():
        cert = db.session.scalar(select(Certificate))
    r = a.get(f"/app/certificates/{cert.public_id}.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")
    assert b"/Image" in r.data or b"/XObject" in r.data  # the QR code image
    assert cert.number.encode() not in b"" and len(r.data) > 2000


def test_public_verification(app, competent):
    a = competent["assessor"]
    _issue(a, competent["learners"]["Abena"])
    with platform_scope():
        cert = db.session.scalar(select(Certificate))
        code = cert.verification_code
    public = app.test_client()
    r = public.get(f"/verify/{code}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Valid certificate" in html and "Abena Darko" in html
    assert "Accra Technical Training Centre" in html and "GC-01 Take body measurements" in html
    assert "GHA-" not in html and "learner_no" not in html  # no ID numbers or internal data
    assert public.get(f"/verify?code={code.lower().replace('-', '')}").headers["Location"].endswith(f"/verify/{code}")
    assert public.get("/verify/AAAAA-BBBBB").status_code == 404
    assert public.get("/verify/../../etc").status_code == 404


def test_revocation_shows_on_verify_page(app, competent):
    a = competent["assessor"]
    _issue(a, competent["learners"]["Abena"])
    with platform_scope():
        cert = db.session.scalar(select(Certificate))
    r = a.post(f"/app/certificates/{cert.public_id}/revoke", data={"rv-reason": ""}, follow_redirects=True)
    assert b"Give a reason" in r.data
    a.post(f"/app/certificates/{cert.public_id}/revoke", data={"rv-reason": "Issued in error"})
    html = app.test_client().get(f"/verify/{cert.verification_code}").get_data(as_text=True)
    assert "has been revoked" in html and "Issued in error" not in html  # reason stays internal
    # A new certificate can be issued after revocation
    assert _issue(a, competent["learners"]["Abena"]).status_code == 302


def test_programme_bulk_issue(client, competent, org):
    with platform_scope():
        abena = db.session.get(Learner, competent["learners"]["Abena"].id)
    client.post("/app/programmes/new", data={"name": "Cohort 3", "delivery_mode": "in_person"})
    with platform_scope():
        p = db.session.scalar(select(Programme))
    client.post(f"/app/programmes/{p.public_id}/enrol", data={"learner": [abena.public_id]})
    with platform_scope():
        e = db.session.scalar(select(Enrolment))
    client.post(f"/app/programmes/{p.public_id}/enrolments/{e.id}/status", data={"en-status": "completed"})
    page = client.get("/app/certificates/").get_data(as_text=True)
    assert "Ready to issue" in page and "Cohort 3" in page
    r = client.post(f"/app/certificates/bulk/programme/{p.public_id}", data={"b-signatory_name": "Esi"},
                    follow_redirects=True)
    assert b"Issued 1 certificate" in r.data
    r = client.post(f"/app/certificates/bulk/programme/{p.public_id}", data={}, follow_redirects=True)
    assert b"Issued 0 certificates" in r.data


def test_learner_downloads_own_certificate_only(app, client, competent, org):
    mailer.outbox.clear()
    a = competent["assessor"]
    _issue(a, competent["learners"]["Abena"])
    abena = competent["learners"]["Abena"]
    client.post(f"/app/learners/{abena.public_id}/invite", data={"inv-email": "abena@example.com"})
    link = re.search(r"http://localhost(/invite/\S+)", mailer.outbox[-1].get_content()).group(1)
    learner = app.test_client()
    learner.post(link, data={"full_name": "Abena Darko", "password": "Abena-pass-2026", "confirm": "Abena-pass-2026",
                             "accept_terms": "y"})
    with platform_scope():
        cert = db.session.scalar(select(Certificate))
    home = learner.get("/app/learn/").get_data(as_text=True)
    assert "My certificates" in home and cert.verification_code in home and "My skills" in home
    assert learner.get(f"/app/learn/certificates/{cert.public_id}.pdf").data.startswith(b"%PDF")
    assert learner.get(f"/app/certificates/{cert.public_id}").status_code == 403
    assert learner.get(f"/app/certificates/{cert.public_id}.pdf").status_code == 403


def test_certificates_require_permission_and_are_isolated(app, competent, make_person, make_tenant, org):
    a = competent["assessor"]
    _issue(a, competent["learners"]["Abena"])
    with platform_scope():
        cert = db.session.scalar(select(Certificate))
    make_person("t@attc.example", [Role.TRAINER], org)
    t = app.test_client()
    sign_in(t, "t@attc.example")
    assert t.get("/app/certificates/").status_code == 403
    other = make_tenant("Beta NGO", organization_type="ngo")
    make_person("b@beta.example", [Role.ORG_ADMIN], other)
    b = app.test_client()
    sign_in(b, "b@beta.example")
    assert b.get(f"/app/certificates/{cert.public_id}").status_code == 404
    assert b.post(f"/app/certificates/{cert.public_id}/revoke", data={"rv-reason": "x"}).status_code == 404


def test_verify_is_rate_limited(app):
    c = app.test_client()
    codes = [c.get("/verify/AAAAA-BBBBB").status_code for _ in range(31)]
    assert codes[-1] == 429
