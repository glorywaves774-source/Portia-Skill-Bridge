"""In-app notifications, with email copies queued for the people who want them.

Call `notify(...)` for one person or `notify_permission(...)` for everyone in a workspace who holds a permission.
Notifications only ever link to pages on this site. Emails are queued (email_status=pending) and sent by
`send_pending()`, which the notifications job runs every few minutes, so a slow mail server never slows a page.
Only people with an active membership in the workspace are notified.
"""
from __future__ import annotations

from datetime import timedelta

from flask import current_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Membership, Notification, NotificationCategory, User
from app.models.base import utcnow
from app.rbac import permissions_for
from app.services import mailer

MAX_EMAIL_ATTEMPTS = 3
KEEP_DAYS = 180


def _clean_link(link: str | None) -> str | None:
    if not link:
        return None
    link = link.strip()
    # Internal paths only: never "//host" or "https://…".
    if not link.startswith("/") or link.startswith("//") or "\\" in link:
        return None
    return link[:255]


def notify(user: User | int | None, tenant_id: int, *, category: str, title: str, body: str | None = None,
           link: str | None = None, email: bool = True, dedupe_key: str | None = None) -> Notification | None:
    """Notify one person in a workspace. Returns None if they aren't an active member or it was a duplicate."""
    if user is None:
        return None
    user_id = user if isinstance(user, int) else user.id
    if category not in NotificationCategory.ALL:
        raise ValueError(f"Unknown notification category {category}")
    member = db.session.scalar(select(Membership.id).where(
        Membership.user_id == user_id, Membership.tenant_id == tenant_id, Membership.is_active.is_(True)).limit(1))
    if member is None:
        return None
    person = db.session.get(User, user_id)
    if person is None or not person.is_active_account:
        return None
    if dedupe_key and db.session.scalar(select(Notification.id).where(
            Notification.user_id == user_id, Notification.dedupe_key == dedupe_key)):
        return None
    n = Notification(tenant_id=tenant_id, user_id=user_id, category=category, title=title[:160],
                     body=(body or "")[:500] or None, link=_clean_link(link), dedupe_key=dedupe_key,
                     email_status="pending" if email and person.wants_email(category) else "none")
    try:
        with db.session.begin_nested():
            db.session.add(n)
    except IntegrityError:  # the same reminder raced in from elsewhere
        return None
    return n


def holders(tenant_id: int, permission: str, *also: str) -> list[int]:
    """Ids of active members of a workspace who hold a permission (and every one in `also`)."""
    rows = db.session.execute(select(Membership.user_id, Membership.role, Membership.is_owner).where(
        Membership.tenant_id == tenant_id, Membership.is_active.is_(True))).all()
    roles: dict[int, set] = {}
    owner: dict[int, bool] = {}
    for uid, role, is_owner in rows:
        roles.setdefault(uid, set()).add(role)
        owner[uid] = owner.get(uid, False) or bool(is_owner)
    need = {permission, *also}
    return sorted(uid for uid, rs in roles.items() if need <= permissions_for(rs, is_owner=owner[uid]))


def notify_permission(tenant_id: int, permission: str, *also: str, exclude: int | None = None, **kwargs) -> int:
    """Notify everyone in the workspace who holds `permission` (and `also`). Returns how many were notified."""
    sent = 0
    for uid in holders(tenant_id, permission, *also):
        if uid != exclude and notify(uid, tenant_id, **kwargs) is not None:
            sent += 1
    return sent


def unread_count(user_id: int, tenant_id: int) -> int:
    return db.session.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user_id, Notification.tenant_id == tenant_id, Notification.read_at.is_(None))) or 0


def mark_read(n: Notification) -> None:
    if n.read_at is None:
        n.read_at = utcnow()


def mark_all_read(user_id: int, tenant_id: int) -> int:
    result = db.session.execute(update(Notification).where(
        Notification.user_id == user_id, Notification.tenant_id == tenant_id, Notification.read_at.is_(None))
        .values(read_at=utcnow()).execution_options(synchronize_session=False))
    return result.rowcount or 0


def send_pending(limit: int = 200) -> dict:
    """Email queued notifications (platform scope). Failures are retried on the next run, up to 3 times."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    counts = {"sent": 0, "failed": 0}
    pending = db.session.scalars(select(Notification).where(Notification.email_status == "pending")
                                 .order_by(Notification.id).limit(limit)).all()
    for n in pending:
        n.email_attempts += 1
        link = f"\n\nOpen it: {base}{n.link}" if (base and n.link) else ""
        body = f"{n.body or ''}{link}\n\nYou can choose which emails you get under My account → Notifications."
        try:
            mailer.send(n.user.email, n.title, body.strip() + "\n\nPORTIA SkillBridge")
            n.email_status, n.emailed_at = "sent", utcnow()
            counts["sent"] += 1
        except Exception:  # noqa: BLE001 — one bad address mustn't stop the others
            current_app.logger.exception("Notification email %s failed", n.id)
            if n.email_attempts >= MAX_EMAIL_ATTEMPTS:
                n.email_status = "failed"
            counts["failed"] += 1
    return counts


def prune(now=None) -> int:
    """Delete read notifications older than KEEP_DAYS (platform scope)."""
    cutoff = (now or utcnow()) - timedelta(days=KEEP_DAYS)
    result = db.session.execute(Notification.__table__.delete().where(
        Notification.read_at.is_not(None), Notification.created_at < cutoff))
    return result.rowcount or 0
