"""Excel and PDF versions of the monitoring and evaluation report."""
from __future__ import annotations

import io

from app.models.learner import CATEGORIES, EMPLOYMENT_STATUSES
from app.services.reports import GROUPS, INDICATORS, RATES

APP_NAME = "PORTIA SkillBridge"


def money(minor: int | None) -> str:
    if minor is None:
        return "—"
    amount = minor / 100
    return f"GHS {amount:,.0f}" if amount == int(amount) else f"GHS {amount:,.2f}"


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value:g}%"


def safe(value):
    """Stop spreadsheet programs treating text as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def basis_text(r: dict, key: str) -> str:
    part, whole, unit = r["rate_basis"][key]
    if not whole:
        return f"No {unit} in this period"
    return f"{part} of {whole} {unit[:-1] if whole == 1 else unit}"


def filename(tenant, r: dict, ext: str) -> str:
    f = r["filters"]
    code = "".join(ch for ch in (tenant.code or "workspace") if ch.isalnum() or ch == "-")
    return f"me-report-{code}-{f.start:%Y%m%d}-{f.end:%Y%m%d}.{ext}"


# ---- Excel ---------------------------------------------------------------------------------------

def to_xlsx(r: dict, tenant) -> bytes:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="0F2744")
    title_font = Font(bold=True, size=14)
    wrap = Alignment(wrap_text=True, vertical="top")

    def header(ws, row, values):
        for i, v in enumerate(values, 1):
            c = ws.cell(row=row, column=i, value=v)
            c.font = head_font
            c.fill = head_fill
            c.alignment = Alignment(vertical="center", wrap_text=True)

    def widths(ws, values):
        for i, w in enumerate(values, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Monitoring and evaluation report"
    ws["A1"].font = title_font
    ws["A2"] = safe(tenant.name)
    ws["A3"] = f"Generated {r['generated_at']:%d %b %Y %H:%M} by {APP_NAME}"
    row = 5
    for label, value in r["filters"].describe():
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws.cell(row=row, column=2, value=safe(value))
        row += 1
    ws.cell(row=row, column=1, value="Learners counted").font = Font(bold=True)
    ws.cell(row=row, column=2, value=r["learners_counted"])
    row += 2
    header(ws, row, ["Result", "Learners", "What it counts"])
    chain_first = row + 1
    for key, label, definition in INDICATORS:
        row += 1
        ws.cell(row=row, column=1, value=label).alignment = wrap
        ws.cell(row=row, column=2, value=r["counts"][key]).alignment = wrap
        ws.cell(row=row, column=3, value=definition).alignment = wrap
    chain_last = row
    row += 2
    header(ws, row, ["Rate", "Value", "Based on", "How it is worked out"])
    for key, label, definition in RATES:
        row += 1
        ws.cell(row=row, column=1, value=label)
        v = r["rates"][key]
        c = ws.cell(row=row, column=2, value=(v / 100) if v is not None else None)
        c.number_format = "0.0%"
        ws.cell(row=row, column=3, value=basis_text(r, key))
        ws.cell(row=row, column=4, value=definition).alignment = wrap
    widths(ws, [28, 12, 62, 70])
    chart = BarChart()
    chart.type = "bar"
    chart.title = "Results chain"
    chart.legend = None
    chart.y_axis.title = "Learners"
    chart.add_data(Reference(ws, min_col=2, min_row=chain_first, max_row=chain_last), titles_from_data=False)
    chart.set_categories(Reference(ws, min_col=1, min_row=chain_first, max_row=chain_last))
    chart.y_axis.majorGridlines = None
    chart.x_axis.scaling.orientation = "maxMin"  # Registered at the top, like the page
    chart.height, chart.width = 8, 16
    ws.add_chart(chart, "F2")

    # By group
    ws = wb.create_sheet("By group")
    header(ws, 1, ["Result"] + [label for _, label in GROUPS])
    for i, d in enumerate(r["disaggregation"], 2):
        ws.cell(row=i, column=1, value=d["label"])
        for j, (g, _) in enumerate(GROUPS, 2):
            ws.cell(row=i, column=j, value=d["values"][g])
    row = len(r["disaggregation"]) + 3
    header(ws, row, ["Age band (at end of period)", "Registered", "Completed", "Placed"])
    for b in r["by_band"]:
        row += 1
        for j, v in enumerate((b["label"], b["registered"], b["completed"], b["placed"]), 1):
            ws.cell(row=row, column=j, value=v)
    row += 2
    header(ws, row, ["Region", "Registered", "Completed", "Placed"])
    for b in r["by_region"]:
        row += 1
        for j, v in enumerate((safe(b["label"]), b["registered"], b["completed"], b["placed"]), 1):
            ws.cell(row=row, column=j, value=v)
    widths(ws, [30, 14, 14, 14, 16, 18])
    ws.freeze_panes = "B2"

    # By programme
    ws = wb.create_sheet("By programme")
    header(ws, 1, ["Code", "Programme", "Enrolled", "Completed", "Completion rate", "Attendance rate",
                   "Assessed competent", "Placed in work"])
    for i, p in enumerate(r["programmes"], 2):
        vals = [safe(p["programme"].code), safe(p["programme"].name), p["enrolled"], p["completed"],
                None if p["completion_rate"] is None else p["completion_rate"] / 100,
                None if p["attendance_rate"] is None else p["attendance_rate"] / 100, p["competent"], p["placed"]]
        for j, v in enumerate(vals, 1):
            c = ws.cell(row=i, column=j, value=v)
            if j in (5, 6):
                c.number_format = "0.0%"
    widths(ws, [14, 44, 11, 11, 15, 15, 18, 14])
    ws.freeze_panes = "C2"

    # Monthly
    ws = wb.create_sheet("Monthly")
    series = r["trend"]["series"]
    names = [("enrolled", "Enrolled"), ("completed", "Completed"), ("certified", "Certificates"), ("placed", "Placed")]
    header(ws, 1, ["Month"] + [n for _, n in names])
    for i, m in enumerate(r["trend"]["months"], 2):
        ws.cell(row=i, column=1, value=m.strftime("%b %Y"))
        for j, (k, _) in enumerate(names, 2):
            ws.cell(row=i, column=j, value=series[k][i - 2])
    last = len(r["trend"]["months"]) + 1
    if last > 1:
        lc = LineChart()
        lc.title = "Month by month"
        lc.add_data(Reference(ws, min_col=2, max_col=5, min_row=1, max_row=last), titles_from_data=True)
        lc.set_categories(Reference(ws, min_col=1, min_row=2, max_row=last))
        lc.height, lc.width = 8, 18
        ws.add_chart(lc, "G2")
    widths(ws, [12, 11, 11, 13, 10])

    # Employment
    ws = wb.create_sheet("Employment")
    header(ws, 1, ["Placements started in the period", "Number"])
    row = 1
    for label, n in r["placements_by_type"] or [("None", 0)]:
        row += 1
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=2, value=n)
    row += 2
    header(ws, row, ["Follow-up check", "Checks recorded", "Still working", "Share still working"])
    for x in r["retention"]:
        row += 1
        ws.cell(row=row, column=1, value=f"{x['months']} months")
        ws.cell(row=row, column=2, value=x["done"])
        ws.cell(row=row, column=3, value=x["working"])
        c = ws.cell(row=row, column=4, value=None if x["rate"] is None else x["rate"] / 100)
        c.number_format = "0.0%"
    row += 2
    ws.cell(row=row, column=1, value="Average monthly pay at start").font = Font(bold=True)
    ws.cell(row=row, column=2, value=money(r["average_pay_minor"]))
    ws.cell(row=row + 1, column=1, value="Average monthly pay at follow-up").font = Font(bold=True)
    ws.cell(row=row + 1, column=2, value=money(r["average_followup_pay_minor"]))
    widths(ws, [34, 16, 14, 18])

    # Learners
    if "learners" in r:
        ws = wb.create_sheet("Learners")
        flags = [(k, label) for k, label, _ in INDICATORS]
        header(ws, 1, ["Learner number", "First name", "Surname", "Gender", "Age", "Age band", "Region",
                       "Disability", "Groups", "Employment status now"] + [label for _, label in flags])
        for i, row_ in enumerate(r["learners"], 2):
            l = row_["learner"]
            vals = [l.learner_no, l.first_name, l.last_name, l.gender_label, row_["age"], row_["band"],
                    l.region or "", "Yes" if l.has_disability else "No",
                    "; ".join(CATEGORIES.get(c, c) for c in (l.categories or [])),
                    EMPLOYMENT_STATUSES.get(l.employment_status or "", "")]
            vals += ["Yes" if row_[k] else "" for k, _ in flags]
            for j, v in enumerate(vals, 1):
                ws.cell(row=i, column=j, value=safe(v))
        widths(ws, [15, 14, 14, 10, 6, 10, 16, 10, 30, 20] + [12] * len(flags))
        ws.freeze_panes = "D2"
        ws.auto_filter.ref = ws.dimensions

    for sheet in wb.worksheets:  # print each sheet one page wide, landscape
        sheet.page_setup.orientation = "landscape"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
    wb.properties.creator = APP_NAME
    wb.properties.title = "Monitoring and evaluation report"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---- PDF -----------------------------------------------------------------------------------------

def to_pdf(r: dict, tenant) -> bytes:
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.shapes import Drawing, Line, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    navy = colors.HexColor("#0F2744")
    teal = colors.HexColor("#0D9488")
    ink = colors.HexColor("#0F172A")
    muted = colors.HexColor("#51627A")
    rule = colors.HexColor("#D9E0EA")
    series = [colors.HexColor(h) for h in ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")]

    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9, leading=12, textColor=ink)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=10, textColor=muted)
    h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=navy)
    h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=navy,
                        spaceBefore=10, spaceAfter=4)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=11)

    def esc(text) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def table(rows, widths, *, numeric_from=1, numeric_to=-1, head=True):
        data = [[Paragraph(esc(c), cell) if isinstance(c, str) and len(c) > 28 else c for c in row] for row in rows]
        t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
        style = [("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("TEXTCOLOR", (0, 0), (-1, -1), ink),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.4, rule), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                 ("ALIGN", (numeric_from, 0), (numeric_to, -1), "RIGHT")]
        if head:
            style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("TEXTCOLOR", (0, 0), (-1, 0), muted),
                      ("LINEBELOW", (0, 0), (-1, 0), 0.8, navy)]
        t.setStyle(TableStyle(style))
        return t

    def chain_chart(width):
        keys = [k for k, *_ in INDICATORS if k != "assessed"]
        labels = {k: label for k, label, _ in INDICATORS}
        values = [r["counts"][k] for k in keys][::-1]
        d = Drawing(width, 150)
        c = HorizontalBarChart()
        c.x, c.y, c.width, c.height = 110, 8, width - 150, 136
        c.data = [values]
        c.categoryAxis.categoryNames = [labels[k] for k in keys][::-1]
        c.categoryAxis.labels.fontName = "Helvetica"
        c.categoryAxis.labels.fontSize = 8
        c.categoryAxis.labels.fillColor = ink
        c.categoryAxis.strokeColor = rule
        c.valueAxis.valueMin = 0
        c.valueAxis.valueMax = max(values + [1]) * 1.15
        c.valueAxis.visible = False
        c.bars[0].fillColor = series[0]
        c.bars[0].strokeColor = None
        c.barWidth = 10
        c.barLabelFormat = "%d"
        c.barLabels.fontName = "Helvetica-Bold"
        c.barLabels.fontSize = 8
        c.barLabels.fillColor = ink
        c.barLabels.nudge = 8
        d.add(c)
        return d

    def trend_chart(width):
        months = r["trend"]["months"]
        names = [("enrolled", "Enrolled"), ("completed", "Completed"), ("certified", "Certificates"), ("placed", "Placed")]
        data = [r["trend"]["series"][k] for k, _ in names]
        d = Drawing(width, 170)
        c = HorizontalLineChart()
        c.x, c.y, c.width, c.height = 30, 30, width - 40, 110
        c.data = data
        step = max(1, len(months) // 8)
        c.categoryAxis.categoryNames = [m.strftime("%b %y") if i % step == 0 else "" for i, m in enumerate(months)]
        c.categoryAxis.labels.fontName = "Helvetica"
        c.categoryAxis.labels.fontSize = 7
        c.categoryAxis.labels.fillColor = muted
        c.categoryAxis.strokeColor = rule
        top = max([max(s) if s else 0 for s in data] + [1])
        c.valueAxis.valueMin = 0
        c.valueAxis.valueMax = top + max(1, top // 5)
        c.valueAxis.valueStep = max(1, (top + 4) // 4)
        c.valueAxis.labels.fontName = "Helvetica"
        c.valueAxis.labels.fontSize = 7
        c.valueAxis.labels.fillColor = muted
        c.valueAxis.strokeColor = None
        c.valueAxis.visibleGrid = True
        c.valueAxis.gridStrokeColor = rule
        c.valueAxis.gridStrokeWidth = 0.4
        for i in range(len(data)):
            c.lines[i].strokeColor = series[i]
            c.lines[i].strokeWidth = 1.6
        d.add(c)
        x = 30
        for i, (_, n) in enumerate(names):  # legend
            d.add(Line(x, 160, x + 14, 160, strokeColor=series[i], strokeWidth=2))
            d.add(String(x + 18, 157, n, fontName="Helvetica", fontSize=8, fillColor=ink))
            x += 26 + len(n) * 4.6
        return d

    buf = io.BytesIO()
    generated = r["generated_at"]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 10 * mm, f"{tenant.name} · Monitoring and evaluation report · "
                                             f"generated {generated:%d %b %Y %H:%M} by {APP_NAME}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, A4[1] - 12 * mm, A4[0] - 18 * mm, A4[1] - 12 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title="Monitoring and evaluation report", author=tenant.name,
                            creator=APP_NAME)
    width = A4[0] - 36 * mm
    f = r["filters"]
    story = [Paragraph("Monitoring and evaluation report", h1), Paragraph(esc(tenant.name), body), Spacer(1, 6)]
    story.append(table([[k, v] for k, v in f.describe()] + [["Learners counted", str(r["learners_counted"])]],
                       [40 * mm, width - 40 * mm], numeric_from=2, head=False))

    story.append(Paragraph("Results", h2))
    story.append(chain_chart(width))
    story.append(table([["Result", "Learners", "What it counts"]] +
                       [[label, str(r["counts"][k]), d] for k, label, d in INDICATORS],
                       [42 * mm, 20 * mm, width - 62 * mm], numeric_from=1, numeric_to=1))
    rates = table([["Rate", "Value", "Based on"]] +
                  [[label, pct(r["rates"][k]), basis_text(r, k)] for k, label, _ in RATES],
                  [55 * mm, 22 * mm, width - 77 * mm], numeric_from=1, numeric_to=1)
    story.append(KeepTogether([Paragraph("Rates", h2), rates]))

    groups = table([["Result"] + [label for _, label in GROUPS]] +
                   [[d["label"]] + [str(d["values"][g]) for g, _ in GROUPS] for d in r["disaggregation"]],
                   [44 * mm] + [(width - 44 * mm) / len(GROUPS)] * len(GROUPS))
    story.append(KeepTogether([Paragraph("By gender, age and disability", h2), groups,
                               Paragraph("Youth means aged 15 to 35 at the end of the period. Learners can be in "
                                         "more than one group.", small)]))
    bands = table([["Age band", "Registered", "Completed", "Placed"]] +
                  [[b["label"], str(b["registered"]), str(b["completed"]), str(b["placed"])] for b in r["by_band"]],
                  [width * 0.4] + [width * 0.2] * 3)
    story.append(KeepTogether([Paragraph("By age", h2), bands]))
    if r["by_region"]:
        regions = table([["Region", "Registered", "Completed", "Placed"]] +
                        [[b["label"], str(b["registered"]), str(b["completed"]), str(b["placed"])]
                         for b in r["by_region"]], [width * 0.4] + [width * 0.2] * 3)
        story.append(KeepTogether([Paragraph("By region", h2), regions]))

    story.append(KeepTogether([Paragraph("Month by month", h2), trend_chart(width)]))

    if r["programmes"]:
        prog = table([["Programme", "Enrolled", "Completed", "Completion", "Attendance", "Competent", "Placed"]] +
                     [[f"{p['programme'].code} · {p['programme'].name}", str(p["enrolled"]), str(p["completed"]),
                       pct(p["completion_rate"]), pct(p["attendance_rate"]), str(p["competent"]), str(p["placed"])]
                      for p in r["programmes"]],
                     [width - 6 * 19 * mm] + [19 * mm] * 6)
        story.append(KeepTogether([Paragraph("By programme", h2), prog]))

    emp_rows = [["Placements started", "Number"]] + [[g, str(n)] for g, n in r["placements_by_type"]]
    if len(emp_rows) == 1:
        emp_rows.append(["None in this period", "0"])
    emp_rows += [["Average monthly pay at start", money(r["average_pay_minor"])],
                 ["Average monthly pay at follow-up", money(r["average_followup_pay_minor"])]]
    retention = table([["Follow-up check", "Recorded", "Still working", "Share"]] +
                      [[f"{x['months']} months", str(x["done"]), str(x["working"]), pct(x["rate"])]
                       for x in r["retention"]], [width * 0.4] + [width * 0.2] * 3)
    story.append(KeepTogether([Paragraph("Employment", h2), table(emp_rows, [width * 0.6, width * 0.4]),
                               Spacer(1, 8), retention]))

    story.append(Paragraph("How the rates are worked out", h2))
    for _, label, definition in RATES:
        story.append(Paragraph(f"<b>{esc(label)}.</b> {esc(definition)}.", small))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Figures come from the records in this workspace when the report was generated. "
                           "Archived learner records are left out.", small))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


