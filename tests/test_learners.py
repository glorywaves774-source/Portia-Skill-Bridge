"""Learner records: registration, validation, duplicates, limits, privacy and isolation."""
import csv
import io
import re
from datetime import date

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Learner, Role, SubscriptionPlan
from app.tenancy import platform_scope
from tests.conftest import sign_in

BASE = {"first_name": "Abena", "last_name": "Darko", "gender": "female", "date_of_birth": "2001-04-12",
        "phone": "+233 24 555 0101", "region": "Ashanti", "city": "Kumasi", "education_level": "shs",
        "employment_status": "unemployed", "categories": ["youth", "women"], "id_type": "ghana_card",
        "id_number": "GHA-123456789-0", "registered_on": date.today().isoformat()}


@pytest.fixture()
def manager(client, org, make_person):
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    sign_in(client, "tm@attc.example")
    return client


def register(client, **changes):
    return client.post("/app/learners/new", data={**BASE, **changes})


def _learner(email=None, **kw):
    with platform_scope():
        return db.session.scalar(select(Learner).filter_by(**kw))


def test_register_assigns_numbers(manager, org):
    r = register(manager)
    assert r.status_code == 302
    r2 = register(manager, first_name="Kofi", last_name="Mensah", gender="male", id_number="GHA-987654321-0",
                  date_of_birth="1999-01-01")
    assert r2.status_code == 302
    year = date.today().year
    with platform_scope():
        numbers = sorted(l.learner_no for l in Learner.query.filter_by(tenant_id=org.id))
        abena = Learner.query.filter_by(first_name="Abena").one()
    assert numbers == [f"L{year}-00001", f"L{year}-00002"]
    assert abena.categories == ["youth", "women"] and abena.id_number == "GHA-123456789-0"
    page = manager.get(r.headers["Location"]).get_data(as_text=True)
    assert "Abena Darko" in page and f"L{year}-00001" in page


@pytest.mark.parametrize("changes, message", [
    ({"first_name": ""}, b"This field is required"),
    ({"gender": ""}, b"Choose a gender option"),
    ({"date_of_birth": "2999-01-01"}, b"can&#39;t be in the future"),
    ({"date_of_birth": "1900-01-01"}, b"10 to 100 years old"),
    ({"id_number": "123"}, b"GHA-123456789-0"),
    ({"id_type": "", "id_number": "X1"}, b"type of ID document"),
    ({"phone": "call me"}, b"valid phone"),
    ({"region": "Atlantis"}, b"Not a valid choice"),
])
def test_validation(manager, changes, message):
    r = register(manager, **changes)
    assert r.status_code == 200 and message in r.data


def test_possible_duplicate_needs_confirmation(manager):
    register(manager)
    r = register(manager, id_number="", id_type="")  # same name + date of birth
    assert r.status_code == 200 and b"may already be registered" in r.data
    r = register(manager, id_number="", id_type="", confirm_duplicate="yes")
    assert r.status_code == 302


def test_same_id_document_is_refused(manager):
    register(manager)
    r = register(manager, first_name="Other", date_of_birth="1990-01-01")
    assert b"already registered" in r.data


def test_learner_limit_and_archived_records(manager, org):
    with platform_scope():
        db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-professional")).max_learners = 1
        db.session.commit()
    register(manager)
    r = register(manager, first_name="Kofi", id_number="GHA-987654321-0", date_of_birth="1999-01-01")
    assert b"Your plan allows 1 learners" in r.data
    abena = _learner(first_name="Abena")
    manager.post(f"/app/learners/{abena.public_id}/status", data={"st-status": "archived"})
    r = register(manager, first_name="Kofi", id_number="GHA-987654321-0", date_of_birth="1999-01-01")
    assert r.status_code == 302


def test_edit_audit_does_not_store_personal_values(manager):
    register(manager)
    abena = _learner(first_name="Abena")
    data = {**BASE, "phone": "+233 20 999 8888", "city": "Obuasi"}
    assert manager.post(f"/app/learners/{abena.public_id}/edit", data=data).status_code == 302
    with platform_scope():
        entry = AuditLog.query.filter_by(action="learner.updated").one()
    text = f"{entry.summary} {entry.details}"
    assert "999 8888" not in text and "phone" in text and "city" in text


def test_view_only_role_sees_masked_id_and_cannot_change(client, org, make_person, manager):
    register(manager)
    abena = _learner(first_name="Abena")
    client.post("/signout")
    make_person("me@attc.example", [Role.ME_OFFICER], org)
    sign_in(client, "me@attc.example")
    page = client.get(f"/app/learners/{abena.public_id}").get_data(as_text=True)
    assert "GHA-123456789-0" not in page and "6789-0" not in page and "•" in page
    assert "Edit" not in page and "Delete record" not in page
    assert client.post("/app/learners/new", data=BASE).status_code == 403
    assert client.post(f"/app/learners/{abena.public_id}/status", data={"st-status": "archived"}).status_code == 403


def test_csv_export_respects_permissions_and_escapes_formulas(client, org, make_person, manager):
    register(manager, first_name="=HYPERLINK(\"http://evil\")", last_name="Darko")
    client.post("/signout")
    make_person("me@attc.example", [Role.ME_OFFICER], org)
    sign_in(client, "me@attc.example")
    r = client.get("/app/learners/export.csv?gender=female")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True).lstrip("﻿"))))
    assert rows[0][:3] == ["Learner number", "First name", "Middle name"] and "Phone" not in rows[0]
    assert rows[1][1].startswith("'=")
    with platform_scope():
        assert AuditLog.query.filter_by(action="learner.exported").count() == 1
    client.post("/signout")
    make_person("t@attc.example", [Role.TRAINER], org)  # can view learners but not export
    sign_in(client, "t@attc.example")
    assert client.get("/app/learners/").status_code == 200
    assert client.get("/app/learners/export.csv").status_code == 403


def test_learners_hidden_from_learner_role(client, org, make_person):
    make_person("l@attc.example", [Role.LEARNER], org)
    sign_in(client, "l@attc.example")
    assert client.get("/app/learners/").status_code == 403


def test_other_workspaces_learners_do_not_exist(client, manager, make_tenant, make_person):
    other = make_tenant("Beta NGO", organization_type="ngo")
    make_person("b@beta.example", [Role.TRAINING_MANAGER], other)
    c2 = client.application.test_client()
    sign_in(c2, "b@beta.example")
    register(c2, first_name="Secret")
    secret = _learner(first_name="Secret")
    for url in [f"/app/learners/{secret.public_id}", f"/app/learners/{secret.public_id}/photo",
                f"/app/learners/{secret.public_id}/edit"]:
        assert manager.get(url).status_code == 404, url
    assert manager.post(f"/app/learners/{secret.public_id}/status", data={"st-status": "archived"}).status_code == 404
    assert b"Secret Darko" not in manager.get("/app/learners/?q=Secret").data


def test_search_and_filters(manager):
    register(manager)
    register(manager, first_name="Kofi", last_name="Mensah", gender="male", id_number="GHA-987654321-0",
             date_of_birth="1999-01-01", region="Volta", categories=["pwd"], has_disability="y")
    assert b"Kofi" in manager.get("/app/learners/?q=mensah").data
    assert b"Kofi" not in manager.get("/app/learners/?gender=female").data
    assert b"Abena" not in manager.get("/app/learners/?category=pwd").data
    assert b"Abena" not in manager.get("/app/learners/?disability=yes").data
    assert b"Kofi" in manager.get("/app/learners/?region=Volta").data


def test_history_records(manager):
    register(manager)
    pid = _learner(first_name="Abena").public_id
    manager.post(f"/app/learners/{pid}/education", data={"edu-institution": "Prempeh College",
                                                         "edu-qualification": "WASSCE", "edu-start_year": "2016",
                                                         "edu-end_year": "2019"})
    manager.post(f"/app/learners/{pid}/skills", data={"skill-name": "Tailoring", "skill-level": "advanced",
                                                      "skill-certified": "y", "skill-certificate_body": "NVTI"})
    manager.post(f"/app/learners/{pid}/employment", data={"job-employer": "Kente Hub", "job-position": "Weaver",
                                                          "job-employment_type": "self_employed",
                                                          "job-start_date": "2022-01-10", "job-monthly_income": "1200"})
    bad = manager.post(f"/app/learners/{pid}/education", data={"edu-institution": "X", "edu-qualification": "Y",
                                                               "edu-start_year": "2020", "edu-end_year": "2010"},
                       follow_redirects=True)
    assert b"end year is before" in bad.data
    l = _learner(first_name="Abena")
    with platform_scope():
        l = db.session.get(Learner, l.id)
        assert [e.institution for e in l.education] == ["Prempeh College"]
        assert l.skills[0].certified and l.employment[0].monthly_income_minor == 120000
        rid = l.skills[0].id
    assert b"<strong>Tailoring</strong>" in manager.get(f"/app/learners/{pid}?tab=skills").data
    manager.post(f"/app/learners/{pid}/skills/{rid}/delete")
    assert b"<strong>Tailoring</strong>" not in manager.get(f"/app/learners/{pid}?tab=skills").data


def test_dashboard_learner_figures(manager):
    register(manager)
    html = manager.get("/app/").get_data(as_text=True)
    assert "Learners &amp; beneficiaries" in html and "100%" in html
