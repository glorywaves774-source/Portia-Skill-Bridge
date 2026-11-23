"""Who may open what: active workspace, permission checks and access gates."""
from __future__ import annotations

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user

from app.rbac import permissions_for
from app.services.auth import workspaces_for
from app.tenancy import use_platform_scope_for_request, use_tenant_for_request

SESSION_WORKSPACE_KEY = "workspace"

# Pages a workspace may still open when its subscription has lapsed (so it can see why and renew).
ALLOWED_WHEN_LAPSED = {"workspace.subscription", "workspace.dashboard", "billing.checkout", "billing.start",
                       "billing.paystack_return", "billing.change", "billing.invoices", "billing.invoice",
                       "billing.invoice_pdf", "billing.remove_card", "notifications.index",
                       "notifications.open_", "notifications.read_all"}


def safe_next(target: str | None, default: str) -> str:
    """Only allow redirects to paths on this site (no open redirects)."""
    if not target or "\\" in target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return default
    return target


def landing_url(user) -> str:
    options = workspaces_for(user)
    if not options:
        return url_for("admin.overview") if user.is_platform_admin else url_for("auth.no_workspace")
    if len(options) == 1:
        session[SESSION_WORKSPACE_KEY] = options[0][0].public_id
        return url_for("workspace.dashboard")
    return url_for("auth.choose_workspace")


def load_workspace(*, required: bool = True):
    """Attach the active workspace to the request and pin tenant isolation to it.

    Returns a response to short-circuit the request (redirect / blocked page) or None.
    """
    g.workspace = None
    g.memberships = []
    g.permissions = frozenset()
    g.workspace_count = 0
    if not current_user.is_authenticated:
        return None
    options = workspaces_for(current_user)
    g.workspace_count = len(options)
    wanted = session.get(SESSION_WORKSPACE_KEY)
    match = next(((t, ms) for t, ms in options if t.public_id == wanted), None)
    if match is None and len(options) == 1:
        match = options[0]
        session[SESSION_WORKSPACE_KEY] = match[0].public_id
    if match is None:
        session.pop(SESSION_WORKSPACE_KEY, None)  # never trust a workspace the person doesn't belong to
        if not required:
            return None
        if not options:
            return redirect(url_for("auth.no_workspace"))
        return redirect(url_for("auth.choose_workspace", next=request.full_path))

    tenant, memberships = match
    use_tenant_for_request(tenant.id)
    g.workspace = tenant
    g.memberships = memberships
    g.roles = sorted({m.role for m in memberships})
    g.is_owner = any(m.is_owner for m in memberships)
    g.permissions = permissions_for(g.roles, is_owner=g.is_owner)

    if not required:
        return None
    if not tenant.is_active:
        return render_template("workspace/blocked.html", reason="suspended"), 403
    if not tenant.has_access and request.endpoint not in ALLOWED_WHEN_LAPSED:
        return render_template("workspace/blocked.html", reason="lapsed"), 402
    return None


def can(permission: str) -> bool:
    return permission in getattr(g, "permissions", frozenset())


def permission_required(*permissions: str):
    """Allow the view only if the person holds every listed permission in the active workspace."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.get("workspace") is None:
                abort(403)
            missing = [p for p in permissions if not can(p)]
            if missing:
                if request.path.startswith("/api/"):
                    return jsonify(error="forbidden", message="You do not have permission for this."), 403
                abort(403)
            return view(*args, **kwargs)
        wrapped.required_permissions = permissions  # used by tests and navigation
        return wrapped
    return decorator


def require_platform_admin():
    """before_request guard for the Super Administrator console."""
    if not current_user.is_authenticated:
        from app.extensions import login_manager
        return login_manager.unauthorized()
    if not current_user.is_platform_admin:
        abort(403)
    use_platform_scope_for_request()
    return None
