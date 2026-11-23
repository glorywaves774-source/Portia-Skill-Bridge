"""Stage 10: in-app notifications, their emails and email preferences."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.demo import build
from app.extensions import db
from app.models import Notification, PlacementFollowUp, Role, User
from app.services import employment, mailer, notifications
from app.tenancy import platform_scope, tenant_context
from tests.conftest import sign_in

PW = "Demo-pass-2026"


@pytest.fixture()
def demo(app):
    """The sample training centre: its set-up enrols, assesses, certifies, shortlists and hires people."""
    mailer.outbox.clear()
    build(password=PW)
    with platform_scope():
        users = {u.email.split("@")[0]: u.id for u in db.session.scalars(select(User))}
        tid = db.session.scalar(select(Notification.tenant_id))
    return {"users": users, "tenant": tid}


def _titles(user_id):
    with platform_scope():
        return [n.title for n in db.session.scalars(select(Notification).where(Notification.user_id == user_id)
                                                   .order_by(Notification.id))]


def test_people_hear_about_what_concerns_them(demo):
    u = demo["users"]
    learner = _titles(u["abena.darko"])
    assert "You're enrolled: Fashion Design & Garment Production — Cohort 3" in learner
    assert "Your certificate is ready" in learner
    assert "You've been shortlisted" in learner and "Congratulations on your new job" in learner
    # Staff who screen applications hear about the self-application; the employer's own staff don't.
    assert "New application: Akosua Agyeman" in _titles(u["esi.boateng"])
    assert "New application: Akosua Agyeman" in _titles(u["adjoa.mensah"])
    employer = _titles(u["yaa.asantewaa"])
    assert "New application: Akosua Agyeman" not in employer
    assert employer.count("New candidate for Junior machinist") == 3  # Abena, Ama and Efua were shortlisted
    # Roles that have nothing to do with jobs aren't bothered.
    assert not any("application" in t.lower() for t in _titles(u["yaw.darko"]))


def test_bell_page_open_and_mark_all_read(app, demo):
    c = app.test_client()
    sign_in(c, "abena.darko@attc.example", PW)
    home = c.get("/app/").get_data(as_text=True)
    assert 'class="btn-icon bell has-unread"' in home and "unread" in home
    page = c.get("/app/notifications/").get_data(as_text=True)
    assert "Your certificate is ready" in page
    with platform_scope():
        n = db.session.scalar(select(Notification).where(Notification.user_id == demo["users"]["abena.darko"],
                                                         Notification.title == "Your certificate is ready"))
    r = c.get(f"/app/notifications/{n.public_id}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/app/learn/")
    with platform_scope():
        assert db.session.get(Notification, n.id).read_at is not None
    c.post("/app/notifications/read-all")
    assert notifications.unread_count(demo["users"]["abena.darko"], demo["tenant"]) == 0
    assert 'has-unread' not in c.get("/app/").get_data(as_text=True)


def test_notifications_are_private(app, demo):
    with platform_scope():
        n = db.session.scalar(select(Notification).where(Notification.user_id == demo["users"]["abena.darko"]))
    c = app.test_client()
    sign_in(c, "esi.boateng@attc.example", PW)
    assert c.get(f"/app/notifications/{n.public_id}").status_code == 404
    assert "Your certificate is ready" not in c.get("/app/notifications/").get_data(as_text=True)


def test_emails_follow_preferences_and_retry(app, demo):
    uid = demo["users"]["abena.darko"]
    with platform_scope():
        pending = db.session.scalars(select(Notification).where(Notification.user_id == uid,
                                                                Notification.email_status == "pending")).all()
        assert pending
        notifications.send_pending()
        db.session.commit()
    sent = [m for m in mailer.outbox if m["To"] == "abena.darko@attc.example"]
    assert any(m["Subject"] == "Your certificate is ready" for m in sent)
    # Switch jobs emails off: later job notifications stay in the app only.
    c = app.test_client()
    sign_in(c, "abena.darko@attc.example", PW)
    c.post("/account/notifications", data={"email-learning": "1"})
    with tenant_context(demo["tenant"]):
        n = notifications.notify(uid, demo["tenant"], category="jobs", title="Jobs test")
        m = notifications.notify(uid, demo["tenant"], category="learning", title="Learning test")
        assert (n.email_status, m.email_status) == ("none", "pending")
        db.session.commit()
    # A failing mail server is retried, then given up after three tries.
    with platform_scope():
        original = mailer.send
        mailer.send = lambda *a, **k: (_ for _ in ()).throw(OSError("SMTP down"))
        try:
            for _ in range(3):
                notifications.send_pending()
        finally:
            mailer.send = original
        db.session.commit()
        assert db.session.get(Notification, m.id).email_status == "failed"


def test_links_stay_on_this_site_and_duplicates_are_skipped(app, demo):
    uid, tid = demo["users"]["esi.boateng"], demo["tenant"]
    with tenant_context(tid):
        for bad in ("https://evil.example/x", "//evil.example", "/\\evil.example"):
            n = notifications.notify(uid, tid, category="people", title="t", link=bad, email=False)
            assert n.link is None
        assert notifications.notify(uid, tid, category="people", title="a", dedupe_key="k1") is not None
        assert notifications.notify(uid, tid, category="people", title="a", dedupe_key="k1") is None
        assert notifications.notify(999999, tid, category="people", title="nobody") is None  # not a member
        with pytest.raises(ValueError):
            notifications.notify(uid, tid, category="spam", title="x")


def test_overdue_follow_ups_alert_once(app, demo):
    with platform_scope():
        fu = db.session.scalar(select(PlacementFollowUp).where(PlacementFollowUp.done_on.is_(None))
                               .order_by(PlacementFollowUp.due_on))
        due = fu.due_on
        first = employment.notify_overdue_follow_ups(due + timedelta(days=1))
        db.session.commit()
        again = employment.notify_overdue_follow_ups(due + timedelta(days=2))
    assert first >= 2 and again == 0  # admin and training manager, once
    assert any("check due" in t for t in _titles(demo["users"]["adjoa.mensah"]))
    assert not any("check due" in t for t in _titles(demo["users"]["yaa.asantewaa"]))


def test_daily_job_runs_everything_and_is_recorded(app, demo):
    runner = app.test_cli_runner()
    r = runner.invoke(args=["subscriptions", "process"])
    assert r.exit_code == 0, r.output
    assert "follow_up_alerts" in r.output and "emails_sent" in r.output
    r = runner.invoke(args=["notifications", "send"])
    assert r.exit_code == 0 and '"sent"' in r.output
    from app.models import JobRun
    with platform_scope():
        assert db.session.get(JobRun, "daily").last_ok and db.session.get(JobRun, "notifications").last_ok


def test_someone_joining_tells_the_people_managers(app, org, make_person):
    import re
    admin = make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    c = app.test_client()
    sign_in(c, "esi@attc.example")
    c.post("/app/people/invite", data={"email": "kofi@attc.example", "roles": ["trainer"]})
    link = re.search(r"http://localhost(/invite/\S+)", mailer.outbox[-1].get_content()).group(1)
    newcomer = app.test_client()
    r = newcomer.post(link, data={"full_name": "Kofi Mensah", "password": "Kofi-pass-2026",
                                  "confirm": "Kofi-pass-2026", "accept_terms": "y"})
    assert r.status_code == 302
    assert "Kofi Mensah joined" in _titles(admin.id)


def test_old_read_notifications_are_pruned(app, demo):
    from app.models.base import utcnow
    with platform_scope():
        n = db.session.scalar(select(Notification))
        n.read_at = utcnow()
        n.created_at = utcnow() - timedelta(days=400)
        db.session.commit()
        assert notifications.prune() == 1
