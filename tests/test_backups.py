"""Stage 10: encrypted backups, restore, retention, the System status page and security hardening."""
import logging
import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import create_app
from app.demo import build
from app.extensions import db
from app.models import Invoice, Learner, Notification, User
from app.models.base import utcnow
from app.services import backups
from app.storage import storage
from app.tenancy import platform_scope
from tests.conftest import sign_in


def _counts():
    with platform_scope():
        return {m.__tablename__: db.session.scalar(select(func.count()).select_from(m))
                for m in (User, Learner, Invoice, Notification)}


@pytest.fixture()
def keyed(app):
    app.config["BACKUP_ENCRYPTION_KEY"] = backups.make_key()
    build(password="Demo-pass-2026")
    yield app
    app.config["BACKUP_ENCRYPTION_KEY"] = None


def test_backup_is_encrypted_and_tamper_proof(keyed):
    with platform_scope():
        blob, manifest = backups.build()
    assert blob.startswith(b"PSB1\n") and b"abena" not in blob.lower()  # nothing readable inside
    assert manifest["tables"]["learners"]["rows"] == 12
    assert backups.verify(blob)["format"] == 1
    tampered = blob[:-10] + (b"A" if blob[-10:-9] != b"A" else b"B") + blob[-9:]
    with pytest.raises(backups.BackupError, match="changed"):
        backups.verify(tampered)
    good_key = keyed.config["BACKUP_ENCRYPTION_KEY"]
    keyed.config["BACKUP_ENCRYPTION_KEY"] = backups.make_key()
    with pytest.raises(backups.BackupError, match="key doesn't match"):
        backups.verify(blob)
    keyed.config["BACKUP_ENCRYPTION_KEY"] = None
    with pytest.raises(backups.BackupError, match="encrypted"):
        backups.verify(blob)
    keyed.config["BACKUP_ENCRYPTION_KEY"] = good_key
    with pytest.raises(backups.BackupError, match="isn't a PORTIA"):
        backups.verify(b"hello")


def test_restore_into_an_empty_database_gives_the_same_data(keyed, tmp_path):
    with platform_scope():
        blob, _ = backups.build()
        abena = db.session.scalar(select(User).where(User.email == "abena.darko@attc.example"))
        before = _counts(), abena.password_hash, abena.last_login_at
    key = keyed.config["BACKUP_ENCRYPTION_KEY"]
    other = create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'restored.db'}",
                                   "UPLOAD_ROOT": str(tmp_path / "up2"), "BACKUP_ENCRYPTION_KEY": key})
    with other.app_context():
        db.create_all()
        with platform_scope():
            result = backups.restore(blob)
            db.session.commit()
        assert result["rows"] > 200
        with platform_scope():
            copy = db.session.scalar(select(User).where(User.email == "abena.darko@attc.example"))
            assert (_counts(), copy.password_hash, copy.last_login_at) == before
            with pytest.raises(backups.BackupError, match="already has data"):
                backups.restore(blob)
            db.session.rollback()
            backups.restore(blob, wipe=True)
            db.session.commit()
            assert _counts() == before[0]
            # New records carry on from the restored ids.
            u = User(email="new@example.com", full_name="New Person")
            db.session.add(u)
            db.session.commit()
            assert u.id > copy.id
        db.session.remove()
        db.drop_all()


def test_create_list_and_retention(keyed):
    with platform_scope():
        made = backups.create()
    assert made["encrypted"] and made["key"].startswith("backups/portia-")
    root = storage().root
    old = utcnow() - timedelta(days=60)
    for i in range(9):
        p = root / "backups" / f"portia-2025010{i}-000000.psb"
        p.write_bytes(b"PSB1\nold")
        os.utime(p, (old.timestamp(), old.timestamp()))
    with platform_scope():
        assert len(backups.listing()) == 10
        removed = backups.prune()
    assert removed == 3 and len(backups.listing()) == 7  # old ones go, but the newest 7 always stay
    assert backups.listing()[0]["key"] == made["key"]


def test_production_refuses_unencrypted_backups(app):
    app.config["ENV_NAME"] = "production"
    try:
        with platform_scope(), pytest.raises(backups.BackupError, match="BACKUP_ENCRYPTION_KEY"):
            backups.build()
    finally:
        app.config["ENV_NAME"] = "testing"


def test_backup_commands(keyed):
    runner = keyed.test_cli_runner()
    r = runner.invoke(args=["backup", "create"])
    assert r.exit_code == 0 and '"encrypted": true' in r.output
    name = runner.invoke(args=["backup", "list"]).output.split("\t")[0]
    assert name.startswith("portia-") and name.endswith(".psb")
    assert "OK:" in runner.invoke(args=["backup", "verify", name]).output
    r = runner.invoke(args=["backup", "restore", name])
    assert r.exit_code != 0 and "already has data" in r.output
    assert len(runner.invoke(args=["backup", "make-key"]).output.strip()) == 44


def test_admin_backup_and_system_pages(keyed, client, make_person):
    make_person("boss@kayron.example", platform_admin=True)
    sign_in(client, "boss@kayron.example")
    r = client.post("/admin/backups", follow_redirects=True)
    assert b"Backup made" in r.data
    name = backups.listing()[0]["key"].split("/")[-1]
    dl = client.get(f"/admin/backups/{name}")
    assert dl.status_code == 200 and dl.data.startswith(b"PSB1") and dl.headers["Cache-Control"] == "private, no-store"
    assert client.get("/admin/backups/..%2Fsecret.psb").status_code == 404
    page = client.get("/admin/system").get_data(as_text=True)
    assert "Backups" in page and "Super Administrator sign-in" in page and "without two-step sign-in" in page
    runner = keyed.test_cli_runner()
    assert "[OK  ] Database" in runner.invoke(args=["system", "check"]).output


def test_only_super_admins_see_backups(app, client, org, make_person):
    from app.models import Role
    make_person("esi@attc.example", [Role.ORG_ADMIN], org, owner=True)
    sign_in(client, "esi@attc.example")
    for url in ("/admin/backups", "/admin/system"):
        assert client.get(url).status_code == 403
    assert client.post("/admin/backups").status_code == 403


# ---- Hardening ------------------------------------------------------------------------------------------

def test_security_headers_and_public_files(client):
    r = client.get("/")
    h = r.headers
    assert h["Cross-Origin-Resource-Policy"] == "same-origin" and h["X-Permitted-Cross-Domain-Policies"] == "none"
    assert h["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert "?v=" in r.get_data(as_text=True)  # static files carry the version, so they can be cached long
    robots = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /app/" in robots and "Disallow: /admin/" in robots
    sec = client.get("/.well-known/security.txt").get_data(as_text=True)
    assert sec.startswith("Contact: ") and "Expires: " in sec


def test_slow_requests_are_logged_without_query_strings(app, client, caplog):
    fresh = create_app("testing", {"SLOW_REQUEST_MS": -1, "UPLOAD_ROOT": app.config["UPLOAD_ROOT"]})
    with caplog.at_level(logging.WARNING):
        fresh.test_client().get("/robots.txt?secret=abc")
    lines = [r.getMessage() for r in caplog.records if "Slow request" in r.getMessage()]
    assert lines and "secret" not in lines[0] and "/robots.txt" in lines[0]


def test_gunicorn_logs_hide_secret_links():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gconf", os.path.join(os.path.dirname(__file__), "..",
                                                                        "gunicorn.conf.py"))
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)
    assert conf._SECRET_PATH.sub(r"/\1/[hidden]", "GET /reset-password/eyJ.abc HTTP/1.1") == \
        "GET /reset-password/[hidden] HTTP/1.1"
    assert conf._SECRET_PATH.sub(r"/\1/[hidden]", "/invite/Tok3n?x=1") == "/invite/[hidden]?x=1"
    assert conf.workers >= 1 and conf.timeout == 60


def test_notification_emails_are_queued_not_sent_during_the_page(keyed):
    # The demo set-up enrolled, certified and hired people: their emails wait for the notifications job.
    with platform_scope():
        statuses = {s for (s,) in db.session.execute(select(Notification.email_status))}
    assert "pending" in statuses and "sent" not in statuses
