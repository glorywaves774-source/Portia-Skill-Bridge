"""Digital certificates: eligibility, issuing, revoking, PDF with QR code, and public verification."""
from __future__ import annotations

import io
from datetime import date

from flask import url_for
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Assessment,
    AssessmentStatus,
    Certificate,
    CertificateKind,
    CourseEnrolment,
    Enrolment,
    EnrolmentStatus,
    Learner,
    LearnerSkill,
    Tenant,
    TenantCounter,
)
from app.services import audit
from app.tenancy import platform_scope


class CertificateError(ValueError):
    pass


def _valid_for(learner: Learner, **source) -> Certificate | None:
    stmt = select(Certificate).where(Certificate.learner_id == learner.id, Certificate.revoked_at.is_(None))
    for key, value in source.items():
        stmt = stmt.where(getattr(Certificate, key) == value)
    return db.session.scalar(stmt)


def eligible(learner: Learner) -> list[dict]:
    """Everything this learner has earned that doesn't have a valid certificate yet."""
    out = []
    for ce in db.session.scalars(select(CourseEnrolment).where(CourseEnrolment.learner_id == learner.id,
                                                               CourseEnrolment.completed_at.is_not(None))):
        if not _valid_for(learner, course_id=ce.course_id):
            out.append({"kind": CertificateKind.COURSE, "id": ce.course_id, "title": ce.course.title,
                        "detail": f"Course completed {ce.completed_at:%d %b %Y}"})
    for e in db.session.scalars(select(Enrolment).where(Enrolment.learner_id == learner.id,
                                                        Enrolment.status == EnrolmentStatus.COMPLETED)):
        if not _valid_for(learner, enrolment_id=e.id):
            out.append({"kind": CertificateKind.PROGRAMME, "id": e.id, "title": e.programme.name,
                        "detail": f"Programme {e.programme.code} completed"
                                  + (f" {e.completed_on:%d %b %Y}" if e.completed_on else "")})
    for a in db.session.scalars(select(Assessment).where(Assessment.learner_id == learner.id,
                                                         Assessment.status == AssessmentStatus.FINAL,
                                                         Assessment.competent.is_(True))):
        if not _valid_for(learner, assessment_id=a.id):
            out.append({"kind": CertificateKind.COMPETENCY, "id": a.id,
                        "title": a.framework.name + (f" ({a.framework.level})" if a.framework.level else ""),
                        "detail": f"Assessed competent {a.assessed_on:%d %b %Y}"})
    return out


def _number(tenant: Tenant, issued_on: date) -> str:
    n = TenantCounter.next(tenant.id, "certificate")
    return f"{tenant.code.rsplit('-', 1)[-1]}-{issued_on.year}-{n:05d}"


def issue(tenant: Tenant, learner: Learner, *, kind: str, source_id: int, actor, issued_on: date | None = None,
          expires_on: date | None = None, signatory_name: str | None = None,
          signatory_title: str | None = None) -> Certificate:
    issued_on = issued_on or date.today()
    if issued_on > date.today():
        raise CertificateError("The issue date can't be in the future.")
    if expires_on and expires_on <= issued_on:
        raise CertificateError("The expiry date must be after the issue date.")
    option = next((o for o in eligible(learner) if o["kind"] == kind and o["id"] == source_id), None)
    if option is None:
        raise CertificateError("This learner hasn't earned that certificate, or already has a valid one for it.")
    cert = Certificate(tenant_id=tenant.id, learner=learner, kind=kind, title=option["title"][:200],
                       number=_number(tenant, issued_on), issued_on=issued_on, expires_on=expires_on,
                       issued_by_id=actor.id, signatory_name=(signatory_name or "").strip() or None,
                       signatory_title=(signatory_title or "").strip() or None)
    setattr(cert, {"course": "course_id", "programme": "enrolment_id", "competency": "assessment_id"}[kind], source_id)
    db.session.add(cert)
    db.session.flush()
    if kind == CertificateKind.COMPETENCY:
        _record_skill(learner, cert, tenant)
    if learner.user_id:
        from app.services import notifications
        notifications.notify(learner.user_id, tenant.id, category="learning", title="Your certificate is ready",
                             body=f"{cert.title}. Download it, or share its verification code {cert.verification_code} "
                                  "with employers.", link="/app/learn/")
    audit.record("certificate.issued", tenant_id=tenant.id, actor=actor, entity_type="certificate",
                 entity_id=cert.id, summary=f"{cert.number}: {cert.title} for {learner.full_name}")
    return cert


def _record_skill(learner: Learner, cert: Certificate, tenant: Tenant) -> None:
    """A certificate of competence also appears in the learner's skills list."""
    name = cert.title[:120]
    skill = next((s for s in learner.skills if s.name == name), None)
    if skill is None:
        db.session.add(LearnerSkill(tenant_id=learner.tenant_id, learner=learner, name=name, level="advanced",
                                    certified=True, certificate_body=tenant.name[:160], year=cert.issued_on.year))
    else:
        skill.certified, skill.certificate_body, skill.year = True, tenant.name[:160], cert.issued_on.year


def revoke(cert: Certificate, *, reason: str, actor) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise CertificateError("Give a reason for revoking the certificate.")
    if cert.is_revoked:
        raise CertificateError("This certificate is already revoked.")
    from app.models.base import utcnow
    cert.revoked_at, cert.revoked_reason = utcnow(), reason[:255]
    audit.record("certificate.revoked", tenant_id=cert.tenant_id, actor=actor, entity_type="certificate",
                 entity_id=cert.id, summary=f"{cert.number} revoked: {reason[:120]}")


def issue_for_programme(tenant: Tenant, programme, *, actor, **options) -> list[Certificate]:
    issued = []
    for e in programme.enrolments:
        if e.status == EnrolmentStatus.COMPLETED and not _valid_for(e.learner, enrolment_id=e.id):
            issued.append(issue(tenant, e.learner, kind=CertificateKind.PROGRAMME, source_id=e.id, actor=actor,
                                **options))
    return issued


def issue_for_course(tenant: Tenant, course, *, actor, **options) -> list[Certificate]:
    issued = []
    for ce in course.enrolments:
        if ce.completed_at and not _valid_for(ce.learner, course_id=course.id):
            issued.append(issue(tenant, ce.learner, kind=CertificateKind.COURSE, source_id=course.id, actor=actor,
                                **options))
    return issued


def normalise_code(code: str) -> str:
    raw = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return f"{raw[:5]}-{raw[5:]}" if len(raw) == 10 else ""


def find_by_code(code: str) -> Certificate | None:
    code = normalise_code(code)
    if not code:
        return None
    with platform_scope():
        return db.session.scalar(select(Certificate).where(Certificate.verification_code == code))


def verify_url(cert: Certificate) -> str:
    return url_for("verify.check", code=cert.verification_code, _external=True)


def competencies_for(cert: Certificate) -> list[str]:
    if cert.kind != CertificateKind.COMPETENCY or not cert.assessment_id:
        return []
    a = db.session.get(Assessment, cert.assessment_id)
    return [f"{r.competency.code} {r.competency.title}" for r in sorted(a.ratings, key=lambda r: r.competency.position)
            if r.achieved] if a else []


def pdf(cert: Certificate) -> bytes:
    """A4 landscape certificate with a QR code that opens the public verification page."""
    import segno
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    navy, teal, grey = colors.HexColor("#0B1B33"), colors.HexColor("#0D9488"), colors.HexColor("#51627A")
    buf = io.BytesIO()
    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"{cert.kind_label} — {cert.learner.full_name}")
    c.setAuthor(cert.tenant.name)
    c.setSubject(f"Certificate {cert.number}")

    # Frame
    c.setStrokeColor(navy)
    c.setLineWidth(3)
    c.roundRect(12 * mm, 12 * mm, w - 24 * mm, h - 24 * mm, 6 * mm)
    c.setStrokeColor(teal)
    c.setLineWidth(1)
    c.roundRect(16 * mm, 16 * mm, w - 32 * mm, h - 32 * mm, 4 * mm)

    def centre(text, y, font="Helvetica", size=12, color=navy):
        c.setFont(font, size)
        c.setFillColor(color)
        c.drawCentredString(w / 2, y, text)

    centre(cert.tenant.name.upper(), h - 38 * mm, "Helvetica-Bold", 14, teal)
    centre(cert.kind_label.upper(), h - 58 * mm, "Helvetica-Bold", 30)
    centre("This is to certify that", h - 75 * mm, "Helvetica", 13, grey)
    centre(cert.learner.full_name, h - 92 * mm, "Helvetica-Bold", 28)
    phrase = {"course": "has successfully completed the course",
              "programme": "has successfully completed the training programme",
              "competency": "has been assessed and found competent in"}[cert.kind]
    centre(phrase, h - 106 * mm, "Helvetica", 13, grey)
    title = cert.title if len(cert.title) <= 70 else cert.title[:67] + "…"
    centre(title, h - 119 * mm, "Helvetica-Bold", 18)
    comps = competencies_for(cert)
    if comps:
        # Wrap the competencies onto up to three centred lines.
        lines, current = [], ""
        for item in comps:
            candidate = f"{current} · {item}" if current else item
            if c.stringWidth(candidate, "Helvetica", 9) > w - 70 * mm and current:
                lines.append(current)
                current = item
            else:
                current = candidate
        lines.append(current)
        if len(lines) > 3:
            lines = lines[:3]
            lines[-1] += " · and more"
        for i, text in enumerate(lines):
            centre(text, h - 129 * mm - i * 5 * mm, "Helvetica", 9, grey)

    # Footer: date, signatory, number and QR code
    c.setFont("Helvetica", 11)
    c.setFillColor(navy)
    c.drawString(30 * mm, 42 * mm, f"Issued: {cert.issued_on:%d %B %Y}")
    if cert.expires_on:
        c.drawString(30 * mm, 36 * mm, f"Valid until: {cert.expires_on:%d %B %Y}")
    c.setFont("Helvetica", 9)
    c.setFillColor(grey)
    c.drawString(30 * mm, 28 * mm, f"Certificate number {cert.number}")
    c.drawString(30 * mm, 23 * mm, f"Verify at {verify_url(cert).split('/verify/')[0]}/verify  ·  code {cert.verification_code}")

    if cert.signatory_name:
        c.setStrokeColor(navy)
        c.line(w / 2 - 35 * mm, 44 * mm, w / 2 + 35 * mm, 44 * mm)
        centre(cert.signatory_name, 38 * mm, "Helvetica-Bold", 11)
        if cert.signatory_title:
            centre(cert.signatory_title, 33 * mm, "Helvetica", 9, grey)

    qr = segno.make(verify_url(cert), error="m")
    png = io.BytesIO()
    qr.save(png, kind="png", scale=6, border=1)
    png.seek(0)
    c.drawImage(ImageReader(png), w - 62 * mm, 22 * mm, 36 * mm, 36 * mm)
    c.setFont("Helvetica", 7)
    c.drawCentredString(w - 44 * mm, 19 * mm, "Scan to verify")
    if cert.is_revoked:
        c.saveState()
        c.setFillColor(colors.Color(0.8, 0.1, 0.1, alpha=0.25))
        c.setFont("Helvetica-Bold", 80)
        c.translate(w / 2, h / 2)
        c.rotate(25)
        c.drawCentredString(0, 0, "REVOKED")
        c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()
