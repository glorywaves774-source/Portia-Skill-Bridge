from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import select, text

from app.extensions import db, limiter
from app.access import load_workspace, permission_required
from app.models import PlanAudience, Role, SubscriptionPlan

bp = Blueprint("api", __name__)


@bp.get("/health")
@limiter.exempt
def health():
    try:
        db.session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # pragma: no cover - exercised only when the DB is down
        current_app.logger.exception("Health check: database unreachable")
        db_ok = False
    body = {"status": "ok" if db_ok else "degraded", "database": db_ok,
            "version": current_app.config["APP_VERSION"]}
    return jsonify(body), (200 if db_ok else 503)


@bp.get("/plans")
@limiter.limit("60 per minute")
def plans():
    audience = request.args.get("audience")
    query = select(SubscriptionPlan).where(SubscriptionPlan.is_public.is_(True), SubscriptionPlan.is_active.is_(True))
    if audience:
        if audience not in PlanAudience.ALL:
            return jsonify(error="invalid_audience", message=f"audience must be one of {list(PlanAudience.ALL)}"), 400
        query = query.where(SubscriptionPlan.audience == audience)
    rows = db.session.scalars(query.order_by(SubscriptionPlan.sort_order)).all()
    return jsonify(data=[p.to_dict() for p in rows])


@bp.get("/me")
@limiter.limit("60 per minute")
@login_required
def me():
    """The signed-in person, their active workspace and what they may do there."""
    blocked = load_workspace(required=False)
    if blocked is not None:  # pragma: no cover - required=False never blocks
        return blocked
    ws = g.workspace
    sub = ws.current_subscription if ws else None
    return jsonify(
        user={"id": current_user.public_id, "name": current_user.full_name, "email": current_user.email,
              "platform_admin": current_user.is_platform_admin},
        workspace=None if ws is None else {
            "id": ws.public_id, "code": ws.code, "name": ws.name, "kind": ws.kind, "status": ws.status,
            "roles": [Role.LABELS.get(r, r) for r in g.roles], "role_codes": list(g.roles),
            "subscription": None if sub is None else {
                "plan": sub.plan.code, "status": sub.status,
                "current_period_end": sub.current_period_end.isoformat(),
            },
        },
        permissions=sorted(g.permissions),
    )


@bp.get("/workspace/members")
@limiter.limit("60 per minute")
@login_required
def workspace_members():
    blocked = load_workspace(required=True)
    if blocked is not None:
        return jsonify(error="workspace_unavailable", message="This workspace cannot be used right now."), 403
    return _members()


@permission_required("members.view")
def _members():
    from sqlalchemy import select as _select

    from app.models import Membership, User
    rows = db.session.execute(_select(User, Membership).join(Membership, Membership.user_id == User.id)
                              .order_by(User.full_name)).all()
    out: dict[str, dict] = {}
    for user, m in rows:
        item = out.setdefault(user.public_id, {"id": user.public_id, "name": user.full_name, "email": user.email,
                                               "roles": []})
        item["roles"].append(m.role)
    return jsonify(data=list(out.values()))
