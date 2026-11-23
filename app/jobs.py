"""Running scheduled jobs: each run is recorded in job_runs (shown on the admin System status page)."""
from __future__ import annotations

import json

from flask import current_app

from app.extensions import db
from app.models import JobRun
from app.models.base import utcnow
from app.tenancy import platform_scope


def run(name: str, fn) -> dict:
    """Run fn() in platform scope, commit, and record the outcome. Re-raises after recording a failure."""
    started = utcnow()
    with platform_scope():
        try:
            result = fn() or {}
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Job %s failed", name)
            JobRun.record(name, ok=False, result=f"{type(exc).__name__}: {exc}", started=started)
            db.session.commit()
            raise
        JobRun.record(name, ok=True, result=json.dumps(result, default=str), started=started)
        db.session.commit()
    return result


def daily() -> dict:
    from datetime import date

    from app.services import billing, employment, notifications
    counts = billing.run_daily()
    counts["follow_up_alerts"] = employment.notify_overdue_follow_ups(date.today())
    counts["notifications_pruned"] = notifications.prune()
    counts.update({f"emails_{k}": v for k, v in notifications.send_pending().items()})
    return counts
