"""The dashboard as an A4 PDF report, built on the server.

GE's frame can't print or download, so the native "Download PDF" button asks the agent, which
rebuilds the analysis from the sheet (no model call: the narrative is kept in session state)
and sends the file back in chat. Pure Python (reportlab), so it runs on Agent Engine.
"""
import io
import re
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .analysis import STYLES, Analysis, num
from .page import chart_title
from .schema import Narrative

W, H = A4
MARGIN = 18 * mm
BODY_W = W - 2 * MARGIN
INK, INK2, MUTED, LINE = colors.HexColor("#0b0b0b"), colors.HexColor("#52514e"), colors.HexColor("#898781"), colors.HexColor("#e1e0d9")
BLUE, ORANGE, SOFT = colors.HexColor("#2a78d6"), colors.HexColor("#eb6834"), colors.HexColor("#eef3fb")

_S = {
    "eyebrow": ParagraphStyle("eyebrow", fontName="Helvetica", fontSize=8.5, leading=11, textColor=MUTED),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=18, leading=23, textColor=INK, spaceBefore=4, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=INK, spaceBefore=10, spaceAfter=4),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK, spaceAfter=4),
    "muted": ParagraphStyle("muted", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK2, spaceAfter=2),
    "kpi_k": ParagraphStyle("kpi_k", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK2),
    "kpi_v": ParagraphStyle("kpi_v", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK),
}


def _p(text: str, style: str = "body") -> Paragraph:
    # Helvetica covers Latin-1; swap the few symbols it lacks.
    text = str(text).replace("→", "to").replace("▲", "+").replace("▼", "-").replace("…", "...")
    text = text.encode("latin-1", "replace").decode("latin-1")
    return Paragraph(escape(text), _S[style])


def filename(sheet: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", sheet).strip("-")[:50] or "sheet"
    return f"{stem}-insights.pdf"


def _drawing(a: Analysis) -> Drawing | None:
    c = a.chart
    d = Drawing(BODY_W, 190)
    if c["type"] == "trend":
        vals = c["values"]
        ch = HorizontalLineChart()
        ch.data = [vals] + ([[v if v is not None else None for v in c["rolling"]]] if c.get("rolling") else [])
        ch.categoryAxis.categoryNames = c["labels"]
        step = max(1, len(vals) // 8)
        ch.categoryAxis.categoryNames = [lab if i % step == 0 else "" for i, lab in enumerate(c["labels"])]
        ch.lines[0].strokeColor, ch.lines[0].strokeWidth = BLUE, 2
        if len(ch.data) > 1:
            ch.lines[1].strokeColor, ch.lines[1].strokeWidth = ORANGE, 1.2
    elif c["type"] in ("pareto", "bridge", "multiples", "heatmap"):
        if c["type"] == "pareto":
            items = [(it["label"], it["value"]) for it in c["items"][:12]]
        elif c["type"] == "bridge":
            items = [(c["start"]["label"], c["start"]["value"])] + [(s["label"], s["value"]) for s in c["steps"]] + [(c["end"]["label"], c["end"]["value"])]
        elif c["type"] == "multiples":
            items = [(pn["label"], pn["last"] or 0) for pn in c["panels"]]
        else:
            items = [(r, sum(v for v in row if v is not None)) for r, row in zip(c["rows"], c["values"])]
        items = list(reversed(items))
        ch = HorizontalBarChart()
        ch.data = [[v for _, v in items]]
        ch.categoryAxis.categoryNames = [lab[:22] for lab, _ in items]
        ch.bars[0].fillColor = BLUE
        ch.bars.strokeColor = None
        d = Drawing(BODY_W, max(120, 16 * len(items) + 40))
    elif c["type"] == "distribution":
        ch = VerticalBarChart()
        ch.data = [c["counts"]]
        ch.categoryAxis.categoryNames = [num(e) if i % 3 == 0 else "" for i, e in enumerate(c["edges"][:-1])]
        ch.bars[0].fillColor = BLUE
        ch.bars.strokeColor = None
    else:
        return None
    ch.x, ch.y, ch.width, ch.height = 70, 24, BODY_W - 90, d.height - 40
    ch.valueAxis.labelTextFormat = lambda v: num(v)
    ch.valueAxis.labels.fontName = ch.categoryAxis.labels.fontName = "Helvetica"
    ch.valueAxis.labels.fontSize = ch.categoryAxis.labels.fontSize = 7
    ch.valueAxis.gridStrokeColor, ch.valueAxis.visibleGrid = LINE, 1
    d.add(ch)
    if c["type"] == "trend" and c.get("forecast"):
        f = c["forecast"][-1]
        d.add(String(BODY_W - 4, d.height - 10, f"Forecast {f['label']}: {num(f['value'])} ({num(f['lo'])} to {num(f['hi'])})",
                     fontName="Helvetica", fontSize=7.5, fillColor=INK2, textAnchor="end"))
    return d


def build(a: Analysis, n: Narrative, file: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
                            title=f"{a.sheet} insights", author="Spreadsheet insights agent")
    story = [_p(f"{file} · {a.sheet} tab · {a.prof.rows:,} rows · {STYLES[a.style][0]}", "eyebrow"), _p(n.headline, "h1"), _p(n.summary)]
    cells = [[[_p(k["label"], "kpi_k"), _p(k["value"], "kpi_v"), _p(k.get("note", ""), "kpi_k")] for k in a.kpis]]
    kpi = Table(cells, colWidths=[BODY_W / len(a.kpis)] * len(a.kpis))
    kpi.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [Spacer(1, 6), kpi]
    title, sub = chart_title(a)
    drawing = _drawing(a)
    if drawing is not None:
        story.append(KeepTogether([_p(title, "h2"), _p(sub, "muted"), drawing]))
    story.append(_p("Insights", "h2"))
    facts = {f.id: f.text for f in a.facts}
    for ins in n.insights:
        block = [_p(f"{ins.kind.upper()} · {ins.title}", "h2"), _p(ins.text)]
        ev = "; ".join(facts[e] for e in ins.evidence if e in facts)
        if ev:
            block.append(_p(f"Evidence: {ev}", "muted"))
        story.append(KeepTogether(block))
    story.append(_p("What to do next", "h2"))
    story += [_p(f"{i}. {x}") for i, x in enumerate(n.actions, 1)]
    if n.caveats:
        story.append(_p("Caveats", "h2"))
        story += [_p(f"- {x}") for x in n.caveats]
    story.append(_p("How these numbers were made", "h2"))
    story.append(_p("Every figure was computed from the sheet in Python; the AI wrote the words, and any sentence whose numbers "
                    "did not match the computed facts was replaced by the facts.", "muted"))
    story += [_p(f"{f.id}  {f.text}", "muted") for f in a.facts]
    doc.build(story)
    return buf.getvalue()
