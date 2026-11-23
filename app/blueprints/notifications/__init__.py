"""Notifications (/app/notifications): the bell's list, opening one, and marking them read."""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.access import load_workspace, permission_required, safe_next
from app.extensions import db
from app.models import Notification
from app.services import notifications as svc

bp = Blueprint("notifications", __name__)


def _guard():
    return load_workspace(required=True)


bp.before_request(login_required(_guard))


def _mine():
    return select(Notification).where(Notification.user_id == current_user.id,
                                      Notification.tenant_id == g.workspace.id)


@bp.get("/")
@permission_required("workspace.view")
def index():
    rows = db.session.scalars(_mine().order_by(Notification.created_at.desc()).limit(100)).all()
    return render_template("notifications/index.html", items=rows,
                           unread=sum(1 for n in rows if n.is_unread))


@bp.get("/<public_id>")
@permission_required("workspace.view")
def open_(public_id):
    n = db.session.scalar(_mine().where(Notification.public_id == public_id)) or abort(404)
    svc.mark_read(n)
    db.session.commit()
    return redirect(safe_next(n.link, url_for("notifications.index")))


@bp.post("/read-all")
@permission_required("workspace.view")
def read_all():
    n = svc.mark_all_read(current_user.id, g.workspace.id)
    db.session.commit()
    flash("All caught up." if n else "Nothing new.", "success")
    return redirect(url_for("notifications.index"))
