"""Monitoring and evaluation reports (/app/reports): indicators, charts and Excel/PDF exports."""
from __future__ import annotations

from flask import Blueprint, Response, g, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import select

from app.access import can, load_workspace, permission_required
from app.extensions import db, limiter
from app.models import Programme
from app.models.learner import CATEGORIES, GHANA_REGIONS, Gender
from app.services import audit, report_exports, reports

bp = Blueprint("reports", __name__)


def _guard():
    return load_workspace(required=True)


bp.before_request(login_required(_guard))


def _report(with_learners: bool = False):
    f = reports.parse_filters(request.args)
    return f, reports.build(f, with_learners=with_learners)


@bp.get("/")
@permission_required("reports.view")
def index():
    f, r = _report()
    programmes = db.session.scalars(select(Programme).order_by(Programme.code.desc())).all()
    return render_template("reports/index.html", f=f, r=r, charts=reports.chart_data(r), programmes=programmes,
                           presets=reports.period_presets(), genders=Gender.LABELS, regions=GHANA_REGIONS,
                           categories=CATEGORIES, indicators=reports.INDICATORS, rate_defs=reports.RATES,
                           groups=reports.GROUPS, args=f.args(), can_export=can("reports.export"))


def _download(body: bytes, mimetype: str, name: str) -> Response:
    resp = Response(body, mimetype=mimetype)
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.get("/export.xlsx")
@permission_required("reports.view", "reports.export")
@limiter.limit("20 per minute")
def export_xlsx():
    f, r = _report(with_learners=True)
    body = report_exports.to_xlsx(r, g.workspace)
    audit.record("report.exported", tenant_id=g.workspace.id, actor=current_user, entity_type="report",
                 summary=f"Exported the M&E report to Excel ({f.start:%d %b %Y} to {f.end:%d %b %Y}, "
                         f"{len(r['learners'])} learner rows)")
    db.session.commit()
    return _download(body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     report_exports.filename(g.workspace, r, "xlsx"))


@bp.get("/export.pdf")
@permission_required("reports.view", "reports.export")
@limiter.limit("20 per minute")
def export_pdf():
    f, r = _report()
    body = report_exports.to_pdf(r, g.workspace)
    audit.record("report.exported", tenant_id=g.workspace.id, actor=current_user, entity_type="report",
                 summary=f"Exported the M&E report to PDF ({f.start:%d %b %Y} to {f.end:%d %b %Y})")
    db.session.commit()
    return _download(body, "application/pdf", report_exports.filename(g.workspace, r, "pdf"))
