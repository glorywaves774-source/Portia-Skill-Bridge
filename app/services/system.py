"""Go-live readiness: checks shown on the admin System status page (and by `flask system check`)."""
from __future__ import annotations

from datetime import timedelta

from flask import current_app
from sqlalchemy import func, select, text

from app.extensions import db
from app.models import JobRun, User
from app.models.base import utcnow

OK, WARN, BAD = "ok", "warn", "bad"


def _head_revision() -> str | None:
    try:
        from alembic.script import ScriptDirectory
        cfg = current_app.extensions["migrate"].migrate.get_config()
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001
        return None


def _job(name: str, max_age: timedelta, label: str, how: str) -> dict:
    run = db.session.get(JobRun, name)
    if run is None or run.last_finished_at is None:
        return {"name": label, "state": WARN, "detail": f"Hasn't run yet. {how}"}
    age = utcnow() - run.last_finished_at
    when = f"Last ran {run.last_finished_at:%d %b %Y %H:%M} UTC"
    if not run.last_ok:
        return {"name": label, "state": BAD, "detail": f"{when} and failed: {run.last_result}"}
    if age > max_age:
        return {"name": label, "state": WARN, "detail": f"{when}, which is longer ago than expected. {how}"}
    return {"name": label, "state": OK, "detail": when + "."}


def checks() -> list[dict]:
    cfg = current_app.config
    prod = cfg.get("ENV_NAME") == "production"
    out = []

    try:
        db.session.execute(text("SELECT 1"))
        try:
            here = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            here = None
        head = _head_revision()
        if head and here and here != head:
            out.append({"name": "Database", "state": BAD,
                        "detail": f"Connected, but at revision {here}; run `flask db upgrade` (latest is {head})."})
        else:
            out.append({"name": "Database", "state": OK,
                        "detail": f"Connected ({db.engine.dialect.name}), revision {here or 'not tracked'}."})
    except Exception as exc:  # noqa: BLE001
        out.append({"name": "Database", "state": BAD, "detail": f"Can't connect: {type(exc).__name__}."})

    backend = cfg.get("STORAGE_BACKEND", "local")
    out.append({"name": "File storage", "state": (BAD if prod and backend != "s3" else OK),
                "detail": ("S3-compatible bucket " + str(cfg.get("STORAGE_BUCKET") or "?")) if backend == "s3"
                else "Local disk" + (" — files are lost on every Render deploy. Set STORAGE_BACKEND=s3." if prod
                                     else " (fine for development).")})

    mail = cfg.get("MAIL_BACKEND", "log")
    if mail == "smtp" and cfg.get("SMTP_HOST"):
        out.append({"name": "Email", "state": OK, "detail": f"SMTP via {cfg['SMTP_HOST']}, from {cfg.get('MAIL_FROM')}."})
    else:
        out.append({"name": "Email", "state": BAD if prod else WARN,
                    "detail": "Emails are only written to the log. Set MAIL_BACKEND=smtp and the SMTP_* settings."})

    from app.services import paystack
    b = paystack.backend()
    if b is None:
        out.append({"name": "Online payment", "state": WARN,
                    "detail": "Off. Set PAYSTACK_SECRET_KEY to take Mobile Money and card payments."})
    elif paystack.is_test_mode():
        out.append({"name": "Online payment", "state": WARN if prod else OK,
                    "detail": "Test mode (" + ("practice checkout" if b == "fake" else "Paystack test key") +
                              "): no real money moves."})
    else:
        out.append({"name": "Online payment", "state": OK, "detail": "Paystack live key set."})

    out.append({"name": "Public address", "state": OK if cfg.get("PUBLIC_BASE_URL") else WARN,
                "detail": cfg.get("PUBLIC_BASE_URL") or "PUBLIC_BASE_URL isn't set, so emails from scheduled jobs "
                                                        "won't include links."})

    if cfg.get("BACKUP_ENCRYPTION_KEY"):
        from app.services import backups
        try:
            files = backups.listing()
        except Exception as exc:  # noqa: BLE001
            files = None
            out.append({"name": "Backups", "state": BAD, "detail": f"Can't read the backup folder: {exc}"})
        if files is not None:
            if not files:
                out.append({"name": "Backups", "state": WARN, "detail": "Key set, but no backups yet."})
            else:
                age = utcnow() - files[0]["modified"]
                out.append({"name": "Backups", "state": OK if age < timedelta(hours=36) else BAD,
                            "detail": f"{len(files)} kept; newest {files[0]['modified']:%d %b %Y %H:%M} UTC "
                                      f"({backups.human_size(files[0]['size'])}), encrypted."})
    else:
        out.append({"name": "Backups", "state": BAD if prod else WARN,
                    "detail": "BACKUP_ENCRYPTION_KEY isn't set, so no backups are made. Run `flask backup make-key`."})

    out.append(_job("daily", timedelta(hours=36), "Daily job",
                    "Check the `flask subscriptions process` cron job on Render."))
    out.append(_job("notifications", timedelta(hours=2), "Notification emails",
                    "Check the `flask notifications send` cron job on Render."))
    out.append(_job("backup", timedelta(hours=36), "Backup job", "Check the `flask backup create` cron job on Render."))

    admins = db.session.scalars(select(User).where(User.is_platform_admin.is_(True),
                                                   User.is_active_account.is_(True))).all()
    without = [u for u in admins if not u.two_factor_enabled]
    out.append({"name": "Super Administrator sign-in", "state": OK if not without else WARN,
                "detail": "Every Super Administrator uses two-step sign-in." if not without else
                f"{len(without)} of {len(admins)} Super Administrator{'s' if len(admins) != 1 else ''} without "
                "two-step sign-in. Switch it on under My account."})
    out.append({"name": "Users", "state": OK,
                "detail": f"{db.session.scalar(select(func.count()).select_from(User))} accounts."})
    return out
