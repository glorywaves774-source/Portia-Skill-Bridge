from app.extensions import db
from app.models import Tenant, User
from app.tenancy import platform_scope
from tests.conftest import sign_in


def test_cli_workflow(app):
    runner = app.test_cli_runner()
    assert "0 created" in runner.invoke(args=["seed-plans"]).output
    r = runner.invoke(args=["create-tenant", "--name", "Ho Vocational Institute", "--plan", "org-starter",
                            "--org-type", "vocational_institute", "--city", "Ho"])
    assert r.exit_code == 0 and "Trial" in r.output
    bad = runner.invoke(args=["create-tenant", "--name", "Wrong", "--kind", "individual", "--plan", "org-starter"])
    assert bad.exit_code != 0 and "organization accounts" in bad.output
    with platform_scope():
        code = Tenant.query.one().code
    assert code in runner.invoke(args=["tenants", "list"]).output
    assert "Suspended" in runner.invoke(args=["tenants", "suspend", code, "--reason", "Test"]).output
    assert "Reactivated" in runner.invoke(args=["tenants", "reactivate", code]).output
    assert '"expired": 0' in runner.invoke(args=["subscriptions", "process"]).output


def test_create_superadmin(app):
    runner = app.test_cli_runner()
    r = runner.invoke(args=["create-superadmin", "--email", "Boss@Kayron.example", "--name", "Boss"],
                      input="averylongpassword\naverylongpassword\n")
    assert r.exit_code == 0, r.output
    user = db.session.scalar(db.select(User).where(User.email == "boss@kayron.example"))
    assert user.is_platform_admin and user.check_password("averylongpassword")
    short = runner.invoke(args=["create-superadmin", "--email", "x@y.example", "--name", "X"], input="short\nshort\n")
    assert short.exit_code != 0


def test_demo_data_builds_a_complete_workspace(app, client):
    from app.models import Application, Certificate, Learner, Placement, Vacancy
    from app.tenancy import tenant_context

    runner = app.test_cli_runner()
    r = runner.invoke(args=["demo-data", "--password", "Demo-pass-2026"])
    assert r.exit_code == 0, r.output
    assert "yaa.asantewaa@kentecouture.example" in r.output
    with platform_scope():
        t = db.session.scalar(db.select(Tenant).where(Tenant.name == "Accra Technical Training Centre"))
    with tenant_context(t.id):
        assert db.session.scalar(db.select(db.func.count()).select_from(Learner)) == 12
        assert db.session.scalar(db.select(db.func.count()).select_from(Vacancy)) == 3
        assert db.session.scalar(db.select(db.func.count()).select_from(Application)) == 6
        assert db.session.scalar(db.select(db.func.count()).select_from(Placement)) == 3
        assert db.session.scalar(db.select(db.func.count()).select_from(Certificate)) >= 1
    # The demo accounts can sign in, and running it twice is refused.
    resp = sign_in(client, "abena.darko@attc.example", "Demo-pass-2026")
    assert resp.status_code == 302 and "/app" in resp.headers["Location"]
    assert client.get("/app/my-jobs/").status_code == 200
    again = runner.invoke(args=["demo-data", "--password", "Demo-pass-2026"])
    assert again.exit_code != 0 and "already exists" in again.output
