"""Stage 10: two-step sign-in with an authenticator app, recovery codes and resets."""
import re
import time

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Role, User
from app.services import twofactor
from app.tenancy import platform_scope
from tests.conftest import PASSWORD, sign_in


def test_codes_match_the_rfc_6238_test_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert twofactor.code_at(secret, 59 // 30) == "287082"
    assert twofactor.code_at(secret, 1111111109 // 30) == "081804"
    assert twofactor.check_code(secret, "287 082", now=59) == 1
    assert twofactor.check_code(secret, "287082", now=59 + 30) == 1          # one step of clock drift is fine
    assert twofactor.check_code(secret, "287082", now=59 + 90) is None       # older isn't
    assert twofactor.check_code(secret, "287082", last_step=1, now=59) is None  # and never twice


def _user(email):
    with platform_scope():
        return db.session.scalar(select(User).where(User.email == email))


@pytest.fixture()
def person(app, org, make_person):
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    c = app.test_client()
    sign_in(c, "esi@attc.example")
    return c


def _turn_on(client):
    page = client.get("/account/two-step").get_data(as_text=True)
    assert 'src="data:image/png;base64,' in page  # QR drawn here, not by another service
    with client.session_transaction() as s:
        secret = s["2fa_setup_secret"]
    bad = client.post("/account/two-step", data={"code": "000000"})
    assert bad.status_code == 400
    r = client.post("/account/two-step", data={"code": twofactor.code_at(secret, twofactor.current_step())})
    codes = re.findall(r"<li>([A-Z2-9]{4}-[A-Z2-9]{4})</li>", r.get_data(as_text=True))
    assert r.status_code == 200 and len(codes) == 8
    return secret, codes


def _password_step(client, email="esi@attc.example"):
    r = sign_in(client, email)
    assert r.status_code == 302 and r.headers["Location"].endswith("/signin/code")
    return r


def test_turning_it_on_and_signing_in_with_a_code(app, person):
    secret, _ = _turn_on(person)
    assert _user("esi@attc.example").two_factor_enabled
    c = app.test_client()
    _password_step(c)
    assert c.get("/app/").status_code == 302  # not signed in yet
    assert c.post("/signin/code", data={"code": "123456"}).status_code == 401
    # The code used to switch it on can't be replayed; the next one works.
    step = twofactor.current_step() + 1
    r = c.post("/signin/code", data={"code": twofactor.code_at(secret, step)})
    assert r.status_code == 302 and c.get("/app/").status_code == 200
    c2 = app.test_client()
    _password_step(c2)
    assert c2.post("/signin/code", data={"code": twofactor.code_at(secret, step)}).status_code == 401
    with platform_scope():
        actions = [a.action for a in db.session.scalars(select(AuditLog))]
    assert "auth.2fa_enabled" in actions and "auth.2fa_failed" in actions and "auth.login_password_ok" in actions


def test_recovery_codes_work_once(app, person):
    _, codes = _turn_on(person)
    c = app.test_client()
    _password_step(c)
    r = c.post("/signin/code", data={"code": codes[0].lower()}, follow_redirects=True)
    assert b"You used a recovery code. 7 left" in r.data
    c2 = app.test_client()
    _password_step(c2)
    assert c2.post("/signin/code", data={"code": codes[0]}).status_code == 401


def test_too_many_wrong_codes_and_slow_sign_in_start_over(app, person, monkeypatch):
    _turn_on(person)
    c = app.test_client()
    _password_step(c)
    for _ in range(4):
        assert c.post("/signin/code", data={"code": "000000"}).status_code == 401
    r = c.post("/signin/code", data={"code": "000000"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/signin")
    assert _user("esi@attc.example").is_locked()  # wrong codes count towards the normal lock-out
    assert sign_in(app.test_client(), "esi@attc.example").status_code == 401
    with platform_scope():
        u = db.session.scalar(select(User).where(User.email == "esi@attc.example"))
        u.locked_until = None
        db.session.commit()
    c2 = app.test_client()
    _password_step(c2)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 400)
    assert c2.get("/signin/code").headers["Location"].endswith("/signin")


def test_new_codes_and_turning_off_need_the_password(app, person):
    _turn_on(person)
    r = person.post("/account/two-step/codes", data={"codes-password": "wrong"}, follow_redirects=True)
    assert b"password wasn" in r.data
    r = person.post("/account/two-step/codes", data={"codes-password": PASSWORD})
    assert len(re.findall(r"<li>[A-Z2-9]{4}-[A-Z2-9]{4}</li>", r.get_data(as_text=True))) == 8
    person.post("/account/two-step/off", data={"off-password": PASSWORD})
    assert not _user("esi@attc.example").two_factor_enabled
    assert person.get("/account/").status_code == 200  # still signed in on this device
    c = app.test_client()
    assert sign_in(c, "esi@attc.example").headers["Location"].endswith("/app/")


def test_super_admin_can_reset_a_lost_phone(app, person, make_person):
    _turn_on(person)
    make_person("boss@kayron.example", platform_admin=True)
    boss = app.test_client()
    sign_in(boss, "boss@kayron.example")
    uid = _user("esi@attc.example").public_id
    r = boss.post(f"/admin/users/{uid}/action", data={"action": "reset_2fa"}, follow_redirects=True)
    assert b"Two-step sign-in is off" in r.data
    assert not _user("esi@attc.example").two_factor_enabled
    assert person.get("/account/").status_code == 302  # their other sessions were signed out
