"""A learner invited to sign in sees and updates only their own record."""
import io
import re

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Learner, LearnerDocument, Role
from app.services import mailer
from app.tenancy import platform_scope
from tests.conftest import sign_in
from tests.test_files import PDF, upload_doc
from tests.test_learners import register


@pytest.fixture()
def linked(app, client, org, make_person):
    mailer.outbox.clear()
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    sign_in(client, "tm@attc.example")
    register(client)
    register(client, first_name="Kofi", last_name="Mensah", gender="male", id_number="GHA-987654321-0",
             date_of_birth="1999-01-01")
    with platform_scope():
        abena = db.session.scalar(select(Learner).where(Learner.first_name == "Abena"))
        kofi = db.session.scalar(select(Learner).where(Learner.first_name == "Kofi"))
    upload_doc(client, kofi, PDF, title="Kofi private")
    upload_doc(client, abena, PDF, title="Staff upload")
    r = client.post(f"/app/learners/{abena.public_id}/invite", data={"inv-email": "abena@example.com"},
                    follow_redirects=True)
    assert b"Sign-in invitation sent" in r.data
    link = re.search(r"http://localhost(/invite/\S+)", mailer.outbox[-1].get_content()).group(1)
    learner_client = app.test_client()
    learner_client.post(link, data={"full_name": "Abena Darko", "password": "Abena-pass-2026",
                                    "confirm": "Abena-pass-2026", "accept_terms": "y"})
    return learner_client, abena, kofi


def test_invitation_links_account_to_record(linked):
    c, abena, _ = linked
    with platform_scope():
        rec = db.session.get(Learner, abena.id)
        assert rec.user is not None and rec.user.email == "abena@example.com"
    page = c.get("/app/me/").get_data(as_text=True)
    assert abena.learner_no in page and "Staff upload" in page


def test_learner_updates_contact_but_not_identity(linked):
    c, abena, _ = linked
    r = c.post("/app/me/", data={"phone": "+233 20 111 2222", "region": "Volta", "first_name": "Hacked",
                                 "id_number": "GHA-000000000-0"})
    assert r.status_code == 302
    with platform_scope():
        rec = db.session.get(Learner, abena.id)
        db.session.refresh(rec)
        assert rec.phone == "+233 20 111 2222" and rec.region == "Volta"
        assert rec.first_name == "Abena" and rec.id_number == "GHA-123456789-0"


def test_learner_uploads_and_manages_only_own_documents(linked):
    c, abena, kofi = linked
    r = c.post("/app/me/documents", data={"doc-file": (io.BytesIO(PDF), "cv.pdf"), "doc-doc_type": "cv",
                                          "doc-title": "My CV"}, content_type="multipart/form-data",
               follow_redirects=True)
    assert b"Document uploaded" in r.data
    with platform_scope():
        docs = {d.title: d.public_id for d in LearnerDocument.query}
    assert c.get(f"/app/me/documents/{docs['My CV']}").status_code == 200
    assert c.post(f"/app/me/documents/{docs['Staff upload']}/delete").status_code == 403
    assert c.get(f"/app/me/documents/{docs['Kofi private']}").status_code == 404
    assert c.post(f"/app/me/documents/{docs['My CV']}/delete").status_code == 302


def test_learner_cannot_open_staff_pages(linked):
    c, abena, kofi = linked
    assert c.get("/app/learners/").status_code == 403
    assert c.get(f"/app/learners/{kofi.public_id}").status_code == 403
    assert c.get(f"/app/learners/{abena.public_id}/photo").status_code == 403


def test_staff_without_record_see_explanation(client, org, make_person):
    make_person("t@attc.example", [Role.TRAINER], org)
    sign_in(client, "t@attc.example")
    assert b"hasn't been linked" in client.get("/app/me/").data
