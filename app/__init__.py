"""PORTIA SkillBridge application factory."""
from __future__ import annotations

import hmac
import logging
import os

from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import get_config
from app.extensions import csrf, db, limiter, login_manager, migrate


def create_app(config_name: str | None = None, overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    config = get_config(config_name)
    if hasattr(config, "validate"):
        config.validate()
    app.config.from_object(config)
    if overrides:
        app.config.update(overrides)
    if app.config.get("ENV_NAME") != "testing":
        # Read secrets when the app starts, not when the module was first imported.
        from app.config import normalise_database_url
        if os.environ.get("SECRET_KEY"):
            app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
        if os.environ.get("DATABASE_URL"):
            app.config["SQLALCHEMY_DATABASE_URI"] = normalise_database_url(os.environ["DATABASE_URL"])

    if app.config.get("ENV_NAME") == "production":
        # Render terminates TLS at its proxy.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    limiter.default_limits = [app.config["RATELIMIT_DEFAULT"]] if app.config.get("RATELIMIT_DEFAULT") else []
    login_manager.init_app(app)
    login_manager.login_view = "auth.signin"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"
    login_manager.session_protection = "basic"

    from app import models  # noqa: F401  (register models with SQLAlchemy)
    from app import tenancy
    from app.security import init_security_headers

    @login_manager.user_loader
    def _load_user(session_id: str):
        public_id, _, token = (session_id or "").partition(".")
        if not public_id or not token:
            return None
        user = db.session.scalar(db.select(models.User).where(models.User.public_id == public_id))
        if user is None or not hmac.compare_digest(user.session_token or "", token):
            return None  # password changed or signed out everywhere
        if not user.is_active_account:
            return None  # deactivated: signed out on the next request
        return user

    @login_manager.unauthorized_handler
    def _unauthorized():
        from flask import flash, jsonify, redirect, url_for
        if request.path.startswith("/api/"):
            return jsonify(error="unauthorized", message="Sign in to use this endpoint."), 401
        flash(login_manager.login_message, "info")
        return redirect(url_for("auth.signin", next=request.full_path.rstrip("?")))

    @app.before_request
    def _reset_tenant_scope():
        tenancy.reset_scope()
        # Never reuse a signed-in user cached by an earlier request sharing this app context.
        from flask import g
        g.pop("_login_user", None)

    init_security_headers(app)
    _register_timing(app)
    from app.storage import init_storage
    init_storage(app)
    from app.services import files, lms, training  # noqa: F401  (register usage counters)
    _register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)

    from app.cli import register_cli
    register_cli(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.api import bp as api_bp
    from app.blueprints.public import bp as public_bp

    from app.auth import bp as auth_bp
    from app.blueprints.account import bp as account_bp
    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.invitations import bp as invitations_bp
    from app.help import bp as help_bp
    from app.blueprints.learners import bp as learners_bp, self_bp as me_bp
    from app.blueprints.programmes import bp as programmes_bp
    from app.blueprints.courses import bp as courses_bp
    from app.blueprints.learn import bp as learn_bp
    from app.blueprints.assess import assessments_bp, certificates_bp, frameworks_bp, verify_bp
    from app.blueprints.jobs import employers_bp, jobs_bp, myjobs_bp, placements_bp
    from app.blueprints.reports import bp as reports_bp
    from app.blueprints.notifications import bp as notifications_bp
    from app.blueprints.billing import bp as billing_bp, devpay_bp, webhooks_bp
    from app.blueprints.workspace import bp as workspace_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(invitations_bp)
    app.register_blueprint(workspace_bp, url_prefix="/app")
    app.register_blueprint(account_bp, url_prefix="/account")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(learners_bp, url_prefix="/app/learners")
    app.register_blueprint(me_bp, url_prefix="/app/me")
    app.register_blueprint(programmes_bp, url_prefix="/app/programmes")
    app.register_blueprint(courses_bp, url_prefix="/app/courses")
    app.register_blueprint(learn_bp, url_prefix="/app/learn")
    app.register_blueprint(frameworks_bp, url_prefix="/app/competencies")
    app.register_blueprint(assessments_bp, url_prefix="/app/assessments")
    app.register_blueprint(certificates_bp, url_prefix="/app/certificates")
    app.register_blueprint(verify_bp)
    app.register_blueprint(employers_bp, url_prefix="/app/employers")
    app.register_blueprint(jobs_bp, url_prefix="/app/jobs")
    app.register_blueprint(placements_bp, url_prefix="/app/placements")
    app.register_blueprint(myjobs_bp, url_prefix="/app/my-jobs")
    app.register_blueprint(reports_bp, url_prefix="/app/reports")
    app.register_blueprint(notifications_bp, url_prefix="/app/notifications")
    app.register_blueprint(billing_bp, url_prefix="/app/billing")
    app.register_blueprint(webhooks_bp, url_prefix="/webhooks")
    if app.config.get("ENV_NAME") != "production":
        app.register_blueprint(devpay_bp, url_prefix="/dev/paystack")
    app.register_blueprint(help_bp, url_prefix="/help")
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    # The API is read-only (GET) in Stage 2 and uses the browser session; CSRF applies to unsafe methods.


def _register_template_helpers(app: Flask) -> None:
    from app import navigation
    from app.help import suggested_questions
    from app.access import can
    from app.models.base import utcnow

    @app.template_filter("money")
    def money(minor: int | None, currency: str = "GHS") -> str:
        if minor is None:
            return "—"
        amount = minor / 100
        return f"{currency} {amount:,.0f}" if amount == int(amount) else f"{currency} {amount:,.2f}"

    @app.template_filter("filesize")
    def filesize(n) -> str:
        from app.services.backups import human_size
        return human_size(n)

    @app.template_filter("limit")
    def limit_label(value) -> str:
        return "Unlimited" if value is None else f"{value:,}"

    @app.template_filter("richtext")
    def richtext(text: str | None):
        """Plain text with simple formatting: blank-line paragraphs, '## ' headings, '- ' lists, **bold**.

        Everything is escaped first, so no HTML typed by a user ever reaches the page.
        """
        import re

        from markupsafe import Markup, escape
        if not text:
            return Markup("")
        out, items, para = [], [], []

        def inline(s):
            return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(escape(s)))

        def flush():
            if para:
                out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
                para.clear()
            if items:
                out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
                items.clear()
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip():
                flush()
            elif line.lstrip().startswith(("- ", "* ")):
                if para:
                    flush()
                items.append(line.lstrip()[2:])
            elif line.startswith("## ") or line.startswith("# "):
                flush()
                out.append(f"<h3>{inline(line.lstrip('# '))}</h3>")
            else:
                if items:
                    flush()
                para.append(line)
        flush()
        return Markup("".join(out))  # nosec B704 — every piece of user text was escaped above

    @app.template_filter("role_label")
    def role_label(code: str) -> str:
        from app.models import Role
        return Role.LABELS.get(code, code)

    @app.template_filter("date")
    def date_label(value, fmt: str = "%d %b %Y") -> str:
        return value.strftime(fmt) if value else "—"

    @app.context_processor
    def _globals():
        return {
            "app_name": app.config["APP_NAME"],
            "app_tagline": app.config["APP_TAGLINE"],
            "app_developer": app.config["APP_DEVELOPER"],
            "app_version": app.config["APP_VERSION"],
            "now": utcnow(),
            "navigation": navigation.build,
            "help_suggestions": suggested_questions,
            "can": can,
            "unread_notifications": _unread_notifications,
        }

    def _unread_notifications() -> int:
        from flask import g
        from flask_login import current_user as cu

        from app.services.notifications import unread_count
        ws = g.get("workspace")
        if ws is None or not cu.is_authenticated:
            return 0
        return unread_count(cu.id, ws.id)


def _register_error_handlers(app: Flask) -> None:
    def _render(code: int, title: str, message: str):
        if request.path.startswith("/api/"):
            return {"error": title, "message": message}, code
        return render_template("errors/error.html", code=code, title=title, message=message), code

    app.register_error_handler(400, lambda e: _render(400, "Bad request", getattr(e, "description", "")))
    app.register_error_handler(403, lambda e: _render(403, "Not allowed", "You do not have access to this page."))
    app.register_error_handler(404, lambda e: _render(404, "Page not found", "We couldn't find that page."))
    app.register_error_handler(429, lambda e: _render(429, "Too many requests", "Please slow down and try again shortly."))
    app.register_error_handler(500, lambda e: _render(500, "Something went wrong", "The error has been logged."))


def _register_timing(app: Flask) -> None:
    """Log slow pages (no query strings, so no tokens or personal data end up in the log), and give static files
    a version in their address so browsers can cache them for a year and still get each new release."""
    import time

    from flask import g

    slow_ms = int(app.config.get("SLOW_REQUEST_MS", 1500))

    @app.before_request
    def _start_timer():
        g._t0 = time.perf_counter()

    @app.after_request
    def _log_slow(response):
        t0 = g.get("_t0")
        if t0 is not None:
            ms = (time.perf_counter() - t0) * 1000
            if ms > slow_ms:
                app.logger.warning("Slow request: %s %s -> %s in %.0f ms", request.method, request.url_rule or
                                   request.path.split("?")[0], response.status_code, ms)
        return response

    @app.url_defaults
    def _static_version(endpoint, values):
        if endpoint == "static" and "v" not in values:
            values["v"] = app.config["APP_VERSION"]
