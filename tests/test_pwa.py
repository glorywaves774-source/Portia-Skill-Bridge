"""Installable app (PWA): manifest, icons, service worker, offline page and install help."""
import re
from pathlib import Path

import pytest
from PIL import Image

from app.demo import build
from tests.conftest import sign_in

PW = "Demo-pass-2026"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def _static_file(url):
    path = url.split("?")[0]
    assert path.startswith("/static/")
    return STATIC / path[len("/static/"):]


def test_manifest_has_what_browsers_need_to_install(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.mimetype == "application/manifest+json"
    m = r.get_json()
    assert m["name"] == "PORTIA SkillBridge" and m["short_name"]
    assert m["display"] == "standalone" and m["scope"] == "/"
    assert m["start_url"].startswith("/app/") and m["id"]
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", m["theme_color"])
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    for need in [("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")]:
        assert need in sizes
    # Every icon and screenshot exists and is really the size it claims.
    for item in m["icons"] + m["screenshots"]:
        f = _static_file(item["src"])
        assert f.exists(), item["src"]
        w, h = (int(x) for x in item["sizes"].split("x"))
        assert Image.open(f).size == (w, h), item["src"]
    assert {s["form_factor"] for s in m["screenshots"]} == {"wide", "narrow"}
    for s in m["shortcuts"]:
        assert s["url"].startswith("/app/") or s["url"].startswith("/notifications")


def test_service_worker_is_served_from_the_root_and_never_caches_private_pages(app, client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.mimetype in ("application/javascript", "text/javascript")
    assert r.headers["Service-Worker-Allowed"] == "/"
    assert "no-cache" in r.headers["Cache-Control"]
    body = r.get_data(as_text=True)
    assert body.startswith(f'const VERSION = "{app.config["APP_VERSION"]}";')
    precache = re.search(r"const PRECACHE\s*=\s*(\[.*?\]);", body, re.S).group(1)
    assert '"/offline"' in precache
    assert "/app/" not in precache and "/account" not in precache and "/admin" not in precache
    # Only static files are written to the cache; pages always go to the network.
    assert "/static/" in body and "caches.match(OFFLINE)" in body and 'OFFLINE = "/offline"' in body


def test_offline_page_carries_no_personal_details(app, client):
    build(password=PW)
    sign_in(client, "abena.darko@attc.example", PW)
    r = client.get("/offline")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "You're offline" in html
    assert "Abena" not in html and "abena.darko" not in html
    assert "csrf" not in html.lower()
    assert "<script>" not in html  # strict CSP: no inline scripts


def test_every_page_links_the_manifest_and_home_screen_icon(client):
    html = client.get("/signin").get_data(as_text=True)
    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'name="theme-color"' in html


def test_install_button_and_iphone_help_link_in_the_menu(app, client):
    build(password=PW)
    sign_in(client, "abena.darko@attc.example", PW)
    html = client.get("/app/").get_data(as_text=True)
    assert "data-install-app" in html and "data-install-ios" in html
    help_page = client.get("/help/?open=how-do-i-install-the-app-on-my-phone").get_data(as_text=True)
    m = re.search(r'<details[^>]*id="topic-how-do-i-install-the-app-on-my-phone"[^>]*>', help_page)
    assert m and " open" in m.group(0)
    other = re.search(r'<details[^>]*id="topic-how-do-i-use-dark-mode"[^>]*>', help_page)
    assert other and " open" not in other.group(0)


@pytest.mark.parametrize("path", ["/manifest.webmanifest", "/sw.js", "/offline"])
def test_app_files_work_without_signing_in(client, path):
    assert client.get(path).status_code == 200
