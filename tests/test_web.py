from app import create_app
from app.config import ProductionConfig, normalise_database_url
from app.extensions import db
from app.models import Tenant, TenantStatus
from app.tenancy import platform_scope

import pytest

from tests.conftest import sign_in


def test_home_and_pricing_pages(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Bridging skills" in r.data
    r = client.get("/pricing")
    assert b"GHS 150" in r.data and b"GHS 350" in r.data and b"GHS 750" in r.data and b"Custom" in r.data
    r = client.get("/pricing?for=individual&billing=annual")
    assert b"Learner Plus" in r.data and b"GHS 350" in r.data  # 35 x 10 per year
    assert client.get("/pricing?for=hacker&billing=weird").status_code == 200


def test_every_page_has_viewport_theme_toggle_and_no_inline_script(client, make_tenant, make_person):
    t = make_tenant()
    make_person("boss@kayron.example", platform_admin=True)
    public = ["/", "/pricing", "/signin", "/signup", "/signup?for=individual", "/forgot-password", "/does-not-exist"]
    for url in public:
        _check_page(client.get(url).get_data(as_text=True), url)
    sign_in(client, "boss@kayron.example")
    for url in ["/admin/", "/admin/tenants", f"/admin/tenants/{t.code}", "/admin/plans", "/admin/audit", "/account/"]:
        _check_page(client.get(url).get_data(as_text=True), url)


def _check_page(html, url):
    assert 'name="viewport" content="width=device-width, initial-scale=1' in html, url
    assert "data-theme-toggle" in html, url
    assert "<script>" not in html and "onclick=" not in html and " style=" not in html, url
    assert html.index("theme-init.js") < html.index("</head>"), url
    assert html.rindex("mobile.css") > html.rindex("app.css"), url


def test_security_headers(client):
    r = client.get("/")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_api_plans_and_health(client):
    data = client.get("/api/v1/plans?audience=organization").get_json()["data"]
    assert [p["code"] for p in data] == ["org-starter", "org-professional", "org-business", "org-enterprise"]
    assert data[0]["monthly_price"] == 150 and data[-1]["monthly_price"] is None
    assert client.get("/api/v1/plans?audience=x").status_code == 400
    h = client.get("/api/v1/health")
    assert h.status_code == 200 and h.get_json()["database"] is True
    assert client.get("/api/v1/nope").get_json()["error"] == "Page not found"


def test_api_is_rate_limited(client):
    codes = [client.get("/api/v1/plans").status_code for _ in range(61)]
    assert codes[-1] == 429 and codes.count(200) == 60


@pytest.fixture()
def admin_client(client, make_person):
    make_person("boss@kayron.example", platform_admin=True)
    sign_in(client, "boss@kayron.example")
    return client


def test_admin_pages_render_real_data(admin_client, make_tenant):
    client = admin_client
    t = make_tenant("Tamale Skills Hub", plan="org-business")
    assert b"Tamale Skills Hub" in client.get("/admin/tenants?q=tamale").data
    assert b"Tamale Skills Hub" not in client.get("/admin/tenants?kind=individual").data
    page = client.get(f"/admin/tenants/{t.code}")
    assert page.status_code == 200 and b"Business" in page.data
    assert client.get("/admin/tenants/PSB-ORG-NOPE00").status_code == 404
    assert b"tenant.created" in client.get("/admin/audit").data


def test_admin_suspend_and_reactivate(admin_client, make_tenant):
    client = admin_client
    t = make_tenant()
    r = client.post(f"/admin/tenants/{t.code}/status", data={"action": "suspend", "reason": "Unpaid"},
                    follow_redirects=True)
    assert b"has been suspended" in r.data
    with platform_scope():
        assert db.session.get(Tenant, t.id).status == TenantStatus.SUSPENDED
    r = client.post(f"/admin/tenants/{t.code}/status", data={"action": "suspend", "reason": "x"},
                    follow_redirects=True)
    assert b"Only active workspaces" in r.data
    client.post(f"/admin/tenants/{t.code}/status", data={"action": "reactivate"})
    with platform_scope():
        db.session.expire_all()
        t2 = db.session.get(Tenant, t.id)
        assert t2.status == TenantStatus.ACTIVE
    assert client.post(f"/admin/tenants/{t.code}/status", data={"action": "delete"}).status_code == 400


def test_admin_forms_require_csrf_token(app, make_tenant, make_person):
    t = make_tenant()
    make_person("boss@kayron.example", platform_admin=True)
    c = app.test_client()
    sign_in(c, "boss@kayron.example")
    app.config["WTF_CSRF_ENABLED"] = True
    r = c.post(f"/admin/tenants/{t.code}/status", data={"action": "suspend", "reason": "x"})
    assert r.status_code == 400


def test_admin_console_requires_platform_admin(client, make_tenant, make_person):
    t = make_tenant()
    assert client.get("/admin/").status_code == 302  # not signed in
    make_person("esi@attc.example", ["org_admin"], t, owner=True)
    sign_in(client, "esi@attc.example")
    for url in ["/admin/", "/admin/tenants", f"/admin/tenants/{t.code}", "/admin/audit"]:
        assert client.get(url).status_code == 403, url
    assert client.post(f"/admin/tenants/{t.code}/status", data={"action": "suspend", "reason": "x"}).status_code == 403


def test_admin_console_works_in_production(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    app = create_app("production")
    assert "admin" in app.blueprints
    assert app.config["SESSION_COOKIE_SECURE"] is True
    with app.test_client() as c:
        assert c.get("/admin/").status_code == 302
        assert "Strict-Transport-Security" in c.get("/api/v1/plans").headers


def test_production_requires_strong_secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    with pytest.raises(RuntimeError):
        ProductionConfig.validate()


def test_database_url_normalisation():
    assert normalise_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url("postgresql://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalise_database_url("sqlite://") == "sqlite://"
    assert normalise_database_url(None) is None
