"""PDF invoices and receipts."""
from __future__ import annotations

import io

from flask import current_app

from app.services.billing import money


def render(inv, tenant) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    navy, teal = colors.HexColor("#0F2744"), colors.HexColor("#0D9488")
    ink, muted, rule = colors.HexColor("#0F172A"), colors.HexColor("#51627A"), colors.HexColor("#D9E0EA")
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13, textColor=ink)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=11, textColor=muted)

    def esc(t):
        return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    cfg = current_app.config
    paid = inv.is_paid
    title = "Receipt" if paid else "Invoice"
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm,
                            bottomMargin=20 * mm, title=f"{title} {inv.number}", author=cfg["APP_DEVELOPER"])
    width = A4[0] - 40 * mm

    head = Table([[Paragraph(f"<b>{esc(cfg['APP_NAME'])}</b><br/>{esc(cfg['APP_DEVELOPER'])}<br/>Accra, Ghana", body),
                   Paragraph(f"<para align='right'><font size='20' color='#0F2744'><b>{title}</b></font><br/>"
                             f"{esc(inv.number)}</para>", body)]], colWidths=[width * 0.55, width * 0.45])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 2, teal),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    billed = [esc(tenant.name)]
    if inv.billing_email:
        billed.append(esc(inv.billing_email))
    place = ", ".join(x for x in (tenant.address, tenant.city, tenant.region) if x)
    if place:
        billed.append(esc(place))
    facts = [["Date", f"{inv.created_at:%d %b %Y}"], ["Status", inv.status_label]]
    if paid:
        facts += [["Paid on", f"{inv.paid_at:%d %b %Y}"], ["Paid by", inv.channel_detail or inv.channel_label or "—"]]
    facts.append(["Reference", inv.reference])
    info = Table([[Paragraph("<b>Billed to</b><br/>" + "<br/>".join(billed), body),
                   Table(facts, colWidths=[24 * mm, 52 * mm], style=TableStyle([
                       ("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("TEXTCOLOR", (0, 0), (0, -1), muted),
                       ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))]],
                 colWidths=[width - 80 * mm, 80 * mm])
    info.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    period = (f"{inv.period_start:%d %b %Y} to {inv.period_end:%d %b %Y}" if inv.period_start and inv.period_end
              else ("One year" if inv.billing_cycle == "annual" else "One month") + ", from payment")
    lines = [["Description", "Amount"],
             [Paragraph(f"{esc(inv.description)}<br/><font size='8' color='#51627A'>{esc(period)}</font>", body),
              money(inv.price_minor, inv.currency)]]
    if inv.credit_minor:
        lines.append([Paragraph("Credit for unused time on the previous plan", body),
                      "− " + money(inv.credit_minor, inv.currency)])
    lines.append([Paragraph("<b>Total paid</b>" if paid else "<b>Total due</b>", body),
                  money(inv.amount_minor, inv.currency)])
    t = Table(lines, colWidths=[width - 40 * mm, 40 * mm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("TEXTCOLOR", (0, 0), (-1, 0), muted),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, navy), ("LINEBELOW", (0, 1), (-1, -2), 0.4, rule),
        ("LINEABOVE", (0, -1), (-1, -1), 1, navy), ("FONT", (1, 1), (1, -1), "Helvetica", 9.5),
        ("FONT", (1, -1), (1, -1), "Helvetica-Bold", 11), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))

    story = [head, Spacer(1, 14), info, Spacer(1, 18), t, Spacer(1, 16)]
    if paid:
        stamp = Table([["PAID"]], colWidths=[34 * mm], rowHeights=[12 * mm])
        stamp.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica-Bold", 16),
                                   ("TEXTCOLOR", (0, 0), (-1, -1), teal), ("BOX", (0, 0), (-1, -1), 2, teal),
                                   ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        stamp.hAlign = "RIGHT"
        story += [stamp, Spacer(1, 12)]
        story.append(Paragraph("Thank you for your payment. Payments are processed securely by Paystack." if
                               inv.provider == "paystack" else "Payment recorded by PORTIA SkillBridge.", small))
    else:
        story.append(Paragraph("Pay online with Mobile Money or a card from Subscription in PORTIA SkillBridge. "
                               "Prices are in Ghana cedis.", small))
    if inv.failure_reason and not paid:
        story.append(Paragraph(f"Last attempt: {esc(inv.failure_reason)}", small))
    doc.build(story)
    return buf.getvalue()
