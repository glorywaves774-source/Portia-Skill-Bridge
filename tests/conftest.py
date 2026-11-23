import os

import pytest

from app import create_app
from app.extensions import db, limiter
from app.models import Membership, Role, User
from app.seeds import seed_plans
from app.services.tenants import create_tenant
from app.tenancy import platform_scope, reset_scope


@pytest.fixture()
def app(tmp_path):
    app = create_app("testing", {"UPLOAD_ROOT": str(tmp_path / "uploads")})
    with app.app_context():
        db.drop_all()
        db.create_all()
        seed_plans()
        limiter.reset()
        yield app
        db.session.remove()
        db.drop_all()
    reset_scope()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_tenant(app):
    def _make(name="Accra Training Centre", kind="organization", plan="org-starter", **kw):
        with platform_scope():
            tenant = create_tenant(name=name, kind=kind, plan_code=plan, **kw)
            db.session.commit()
            return tenant
    return _make


@pytest.fixture()
def two_tenants(app, make_tenant):
    """Two organizations, each with one learner membership."""
    a = make_tenant("Alpha Institute", plan="org-professional", organization_type="training_institution")
    b = make_tenant("Beta NGO", plan="org-starter", organization_type="ngo")
    with platform_scope():
        for tenant, email in ((a, "ama@alpha.example"), (b, "kofi@beta.example")):
            user = User(email=email, full_name=email.split("@")[0].title())
            db.session.add(user)
            db.session.add(Membership(tenant_id=tenant.id, user=user, role=Role.LEARNER))
        db.session.commit()
        ids = (a.id, b.id)
    db.session.expunge_all()
    return ids


PASSWORD = "Correct-Horse-42"


@pytest.fixture()
def make_person(app):
    """Create a person with given roles in a workspace (or none)."""
    def _make(email, roles=(), tenant=None, *, owner=False, platform_admin=False, name=None):
        user = User(email=email, full_name=name or email.split("@")[0].title(), is_platform_admin=platform_admin)
        user.set_password(PASSWORD)
        db.session.add(user)
        with platform_scope():
            for role in roles:
                db.session.add(Membership(tenant_id=tenant.id, user=user, role=role, is_owner=owner))
            db.session.commit()
        return user
    return _make


def sign_in(client, email, password=PASSWORD, **extra):
    return client.post("/signin", data={"email": email, "password": password, **extra})


@pytest.fixture()
def org(make_tenant):
    return make_tenant("Accra Technical Training Centre", plan="org-professional", organization_type="training_centre")


def pytest_report_header(config):
    url = os.environ.get("TEST_DATABASE_URL")
    return f"database: {'PostgreSQL ' + url.split('@')[-1] if url else 'SQLite (in memory)'}"
