from flask import Blueprint, Response, current_app, render_template, request, url_for
from sqlalchemy import select

from app.extensions import db
from app.models import PlanAudience, SubscriptionPlan

bp = Blueprint("public", __name__)


def _public_plans(audience: str):
    return db.session.scalars(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.audience == audience, SubscriptionPlan.is_public.is_(True),
               SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order)
    ).all()


@bp.get("/")
def index():
    return render_template("public/index.html", featured=_public_plans(PlanAudience.ORGANIZATION))


@bp.get("/pricing")
def pricing():
    audience = request.args.get("for", PlanAudience.ORGANIZATION)
    if audience not in PlanAudience.ALL:
        audience = PlanAudience.ORGANIZATION
    cycle = request.args.get("billing", "monthly")
    if cycle not in ("monthly", "annual"):
        cycle = "monthly"
    return render_template(
        "public/pricing.html",
        audience=audience,
        cycle=cycle,
        plans=_public_plans(audience),
    )


@bp.get("/robots.txt")
def robots():
    body = "User-agent: *\nAllow: /$\nAllow: /pricing\nAllow: /verify\nDisallow: /app/\nDisallow: /admin/\n" \
           "Disallow: /account/\nDisallow: /api/\nDisallow: /invite/\nDisallow: /reset-password/\n"
    return Response(body, mimetype="text/plain")


@bp.get("/.well-known/security.txt")
def security_txt():
    from datetime import datetime, timedelta, timezone
    contact = current_app.config.get("SECURITY_CONTACT") or current_app.config.get("SALES_EMAIL")
    lines = [f"Contact: mailto:{contact}" if contact else "Contact: " + url_for("public.index", _external=True),
             "Expires: " + (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z"),
             "Preferred-Languages: en",
             "Canonical: " + url_for("public.security_txt", _external=True)]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


# ---- Installable app (PWA) -----------------------------------------------------------------------------

def _icon(name: str) -> str:
    return url_for("static", filename=f"img/pwa/{name}")


@bp.get("/manifest.webmanifest")
def manifest():
    import json
    cfg = current_app.config
    icons = [{"src": _icon("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
             {"src": _icon("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
             {"src": _icon("maskable-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
             {"src": _icon("maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
    small = [icons[0]]
    data = {
        "id": "/app/",
        "name": cfg["APP_NAME"],
        "short_name": "SkillBridge",
        "description": f"{cfg['APP_TAGLINE']} Training, skills assessment, certificates and jobs in one place.",
        "start_url": "/app/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "orientation": "any",
        "background_color": "#0B1B33",
        "theme_color": "#0B1B33",
        "lang": "en",
        "dir": "ltr",
        "categories": ["education", "productivity", "business"],
        "icons": icons,
        "shortcuts": [
            {"name": "Dashboard", "url": "/app/", "icons": small},
            {"name": "Notifications", "url": "/app/notifications/", "icons": small},
            {"name": "My learning", "short_name": "Learn", "url": "/app/learn/", "icons": small},
            {"name": "Jobs for me", "short_name": "Jobs", "url": "/app/my-jobs/", "icons": small},
        ],
        "screenshots": [
            {"src": _icon("screenshot-wide.png"), "sizes": "1280x800", "type": "image/png", "form_factor": "wide",
             "label": "Dashboard with learners, training and jobs"},
            {"src": _icon("screenshot-narrow.png"), "sizes": "780x1688", "type": "image/png",
             "form_factor": "narrow", "label": "A learner's notifications on a phone"},
        ],
    }
    resp = Response(json.dumps(data, indent=1), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


SERVICE_WORKER = """/* PORTIA SkillBridge service worker. Caches only the app's own static files (styles, scripts, icons).
   Pages, files and API answers always come from the network and are never stored on the device. */
const CACHE = "portia-static-" + VERSION;
const OFFLINE = "/offline";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("portia-") && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (req.mode === "navigate") {
    // Pages: always the network. Only when there's no connection, show the offline page.
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    // Versioned static files: from the cache when we have them, otherwise fetch and keep.
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    })));
  }
  // Everything else (uploads, downloads, API): straight to the network, never stored.
});
"""


@bp.get("/sw.js")
def service_worker():
    import json
    v = current_app.config["APP_VERSION"]
    precache = ["/offline"] + [url_for("static", filename=f) for f in (
        "css/app.css", "css/dark.css", "css/mobile.css", "css/print.css", "js/theme-init.js", "js/app.js",
        "img/logo-mark.svg", "img/pwa/icon-192.png", "img/pwa/favicon-32.png")]
    body = f"const VERSION = {json.dumps(v)};\nconst PRECACHE = {json.dumps(precache)};\n" + SERVICE_WORKER
    resp = Response(body, mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"          # browsers check for a new version on every visit
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.get("/offline")
def offline():
    return render_template("public/offline.html")
