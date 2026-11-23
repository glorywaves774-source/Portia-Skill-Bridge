"""Training programmes: creation, trainers, enrolment, sessions and attendance."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Attendance, Enrolment, Learner, Programme, Role, SubscriptionPlan
from app.tenancy import platform_scope
from tests.conftest import sign_in
from tests.test_learners import register

PROG = {"name": "Fashion Design Cohort 3", "trade": "Fashion design", "delivery_mode": "in_person",
        "location": "Accra", "start_date": "2026-01-12", "end_date": "2026-06-30", "capacity": "3",
        "funder": "Skills Fund"}


@pytest.fixture()
def setup(client, org, make_person):
    make_person("tm@attc.example", [Role.TRAINING_MANAGER], org)
    trainer = make_person("yaw@attc.example", [Role.TRAINER], org, name="Yaw Asante")
    other_trainer = make_person("ama@attc.example", [Role.TRAINER], org, name="Ama Boateng")
    sign_in(client, "tm@attc.example")
    for i, (first, last) in enumerate([("Abena", "Darko"), ("Kofi", "Mensah"), ("Efua", "Quansah"), ("Yaa", "Asantewaa")]):
        register(client, first_name=first, last_name=last, id_number=f"GHA-12345678{i}-0", date_of_birth=f"199{i}-01-01")
    r = client.post("/app/programmes/new", data=PROG)
    assert r.status_code == 302
    with platform_scope():
        p = db.session.scalar(select(Programme))
        learners = db.session.scalars(select(Learner).order_by(Learner.learner_no)).all()
    return {"p": p, "learners": learners, "trainer": trainer, "other_trainer": other_trainer}


def _enrol(client, p, learners):
    return client.post(f"/app/programmes/{p.public_id}/enrol", data={"learner": [l.public_id for l in learners]},
                       follow_redirects=True)


def test_create_programme_with_code(client, setup):
    p = setup["p"]
    assert p.code == "P2026-001" and p.capacity == 3 and p.status == "draft"
    page = client.get(f"/app/programmes/{p.public_id}").get_data(as_text=True)
    assert "Fashion Design Cohort 3" in page and "Skills Fund" in page


def test_programme_validation(client, setup):
    r = client.post("/app/programmes/new", data={**PROG, "end_date": "2025-01-01"})
    assert b"end date is before" in r.data


def test_enrol_respects_capacity_and_duplicates(client, setup):
    p, ls = setup["p"], setup["learners"]
    r = _enrol(client, p, ls)
    assert b"Only 3 seats left" in r.data
    r = _enrol(client, p, ls[:2])
    assert b"Enrolled 2 learners" in r.data
    r = _enrol(client, p, ls[:3])
    assert b"Enrolled 1 learner" in r.data and b"already enrolled" in r.data
    with platform_scope():
        assert db.session.scalar(select(Programme)).seats_left == 0


def test_capacity_cannot_go_below_enrolled(client, setup):
    p, ls = setup["p"], setup["learners"]
    _enrol(client, p, ls[:3])
    r = client.post(f"/app/programmes/{p.public_id}/edit", data={**PROG, "capacity": "2"}, follow_redirects=True)
    assert b"3 learners are already enrolled" in r.data


def test_trainers_and_attendance_permissions(app, client, setup):
    p, ls = setup["p"], setup["learners"]
    _enrol(client, p, ls[:2])
    r = client.post(f"/app/programmes/{p.public_id}/trainers", data={"tr-user": setup["trainer"].public_id,
                                                                    "tr-lead": "y"}, follow_redirects=True)
    assert b"Yaw Asante assigned" in r.data
    client.post(f"/app/programmes/{p.public_id}/sessions", data={"ses-title": "Week 1", "ses-date": "2026-01-12",
                                                                "ses-start_time": "09:00", "ses-end_time": "12:00",
                                                                "ses-trainer": setup["trainer"].public_id})
    with platform_scope():
        sess = db.session.scalar(select(Programme)).sessions[0]
    url = f"/app/programmes/{p.public_id}/sessions/{sess.public_id}/attendance"

    assigned = app.test_client()
    sign_in(assigned, "yaw@attc.example")
    page = assigned.get(url)
    assert page.status_code == 200 and b"Abena Darko" in page.data and b"Efua" not in page.data
    r = assigned.post(url, data={f"a-{ls[0].public_id}": "present", f"a-{ls[1].public_id}": "late",
                                 f"n-{ls[1].public_id}": "Bus delay", f"a-{ls[2].public_id}": "present"})
    assert r.status_code == 302
    with platform_scope():
        rows = {a.learner_id: (a.status, a.note) for a in db.session.scalars(select(Attendance))}
    assert rows == {ls[0].id: ("present", None), ls[1].id: ("late", "Bus delay")}  # not-enrolled learner ignored

    unassigned = app.test_client()
    sign_in(unassigned, "ama@attc.example")
    assert unassigned.get(url).status_code == 403
    assert unassigned.post(url, data={f"a-{ls[0].public_id}": "absent"}).status_code == 403


def test_attendance_rates_and_training_tab(client, setup):
    p, ls = setup["p"], setup["learners"]
    _enrol(client, p, ls[:2])
    for i, d in enumerate(["2026-01-12", "2026-01-13"]):
        client.post(f"/app/programmes/{p.public_id}/sessions", data={"ses-title": f"Day {i + 1}", "ses-date": d})
    with platform_scope():
        sessions = db.session.scalar(select(Programme)).sessions
    for s, status in zip(sessions, ["present", "absent"]):
        client.post(f"/app/programmes/{p.public_id}/sessions/{s.public_id}/attendance",
                    data={f"a-{ls[0].public_id}": status, f"a-{ls[1].public_id}": "present"})
    html = client.get(f"/app/programmes/{p.public_id}?tab=learners").get_data(as_text=True)
    assert "50%" in html and "100%" in html and "rate-low" in html
    training = client.get(f"/app/learners/{ls[0].public_id}?tab=training").get_data(as_text=True)
    assert "Fashion Design Cohort 3" in training and "50%" in training


def test_enrolment_status_and_completed_programme(client, setup):
    p, ls = setup["p"], setup["learners"]
    _enrol(client, p, ls[:1])
    with platform_scope():
        e = db.session.scalar(select(Enrolment))
    client.post(f"/app/programmes/{p.public_id}/enrolments/{e.id}/status", data={"en-status": "completed"})
    with platform_scope():
        e = db.session.get(Enrolment, e.id)
        assert e.status == "completed" and e.completed_on == date.today()
    client.post(f"/app/programmes/{p.public_id}/status", data={"st-status": "completed"})
    r = _enrol(client, p, ls[1:2])
    assert b"completed; it can&#39;t take new learners" in r.data


def test_programme_limit(client, setup):
    with platform_scope():
        db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "org-professional")).max_programmes = 1
        db.session.commit()
    r = client.post("/app/programmes/new", data={**PROG, "name": "Second"}, follow_redirects=True)
    assert b"Your plan allows 1 programmes" in r.data


def test_view_only_roles_and_isolation(app, client, setup, make_person, make_tenant, org):
    p = setup["p"]
    make_person("me@attc.example", [Role.ME_OFFICER], org)
    c = app.test_client()
    sign_in(c, "me@attc.example")
    assert c.get(f"/app/programmes/{p.public_id}").status_code == 200
    assert c.post("/app/programmes/new", data=PROG).status_code == 403
    other = make_tenant("Beta NGO", organization_type="ngo")
    make_person("b@beta.example", [Role.ORG_ADMIN], other)
    c2 = app.test_client()
    sign_in(c2, "b@beta.example")
    assert c2.get(f"/app/programmes/{p.public_id}").status_code == 404
    assert b"Fashion Design" not in c2.get("/app/programmes/").data
