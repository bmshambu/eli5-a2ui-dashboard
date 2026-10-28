"""A2UI v0.9 cards: welcome, tab picker, chart-style picker, the dashboard (Canvas +
IFrameSrcdoc) and errors.

Every button carries in its context everything the handler needs (sheet, style, measure,
dimension), so a click works from any card without guessing from state, and a `prompt` that GE
shows as the user's turn.
"""
import json

from . import a2ui, page, review
from .analysis import STYLES, Analysis, available
from .schema import Narrative
from .sheets import Profile

FRAME_HEIGHT = 1150  # the page scrolls inside the frame when it is longer
TAB_ACTION = "tab"  # picked a tab -> style picker
TABS_ACTION = "tabs"  # show the tab picker again
CHART_ACTION = page.CHART_ACTION  # draw sheet/style/measure/dim
ASK_ACTION = page.ASK_ACTION  # a question about the data
PDF_ACTION = "pdf"  # the agent builds the PDF and sends it back as a file
REVIEW_ACTION = "review"  # check the uploaded file and say what to fix
STYLES_ACTION = "styles"  # the chart-style picker for a tab


def _text(cid: str, text: str, hint: str = "body") -> dict:
    return {"id": cid, "component": "MaterialText", "text": text, "usageHint": hint}


def _flat(context: dict) -> dict:
    """A2UI context values must be plain values: nested ones (the explorer's filters) travel as JSON and
    empty ones (no category column, so dim is None) are left out."""
    return {k: json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list)) else v for k, v in context.items() if v is not None}


def _button(cid: str, label: str, action: str, context: dict, icon: str | None = None, variant: str = "stroked") -> dict:
    b = {"id": cid, "component": "MaterialButton", "label": label, "variant": variant,
         "action": {"event": {"name": action, "context": _flat(context)}}}
    if icon:
        b["leadingIcon"] = icon
    return b


def _card(prefix: str, children: list[dict]) -> list[dict]:
    comps = [
        {"id": "root", "component": "MaterialCard", "children": ["col"]},
        {"id": "col", "component": "MaterialColumn", "align": "stretch", "children": [c["id"] for c in children]},
        *children,
    ]
    return a2ui.surface(prefix, comps)


def _step(i: int, icon: str, title: str, what: str) -> list[dict]:
    return [{"id": f"step{i}", "component": "MaterialRow", "align": "start", "children": [f"step{i}i", f"step{i}c"]},
            {"id": f"step{i}i", "component": "MaterialIcon", "icon": icon, "color": "primary"},
            {"id": f"step{i}c", "component": "MaterialColumn", "children": [f"step{i}t", f"step{i}d"]},
            _text(f"step{i}t", f"**{title}**"), _text(f"step{i}d", what, "caption")]


def welcome_card() -> list[dict]:
    steps = [(1, "upload_file", "Attach an Excel or CSV file", "Use the attach button. Any layout: title rows, totals and one column per month are fine."),
             (2, "fact_check", "Check it first (optional)", "Tap Review my file to see what's missing or wrong, cell by cell, before you chart."),
             (3, "insights", "Your dashboard opens", "Filters, bubbles, treemap and heatmap with the insights beside them. Lookup tabs "
                                                   "(Products, Customers) are joined on automatically; other chart styles are one tap away.")]
    extra = [c for n, icon, t, d in steps for c in _step(n, icon, t, d)]
    children = [_text("title", "Turn a spreadsheet into charts with insights", "h4"), *[c for c in extra if c["id"] in ("step1", "step2", "step3")],
                {"id": "div", "component": "MaterialDivider"},
                _text("eg", "Or just ask, for example *\"show revenue by region as a heatmap\"*.", "caption")]
    comps = [
        {"id": "root", "component": "MaterialCard", "children": ["col"]},
        {"id": "col", "component": "MaterialColumn", "align": "stretch", "children": [c["id"] for c in children]},
        *children, *[c for c in extra if c["id"] not in ("step1", "step2", "step3")],
    ]
    return a2ui.surface("dash-welcome", comps)


def _review_button(hint: str | None = None) -> list[dict]:
    out = [_button("review", "Review my file", REVIEW_ACTION, {"prompt": "Review my file"}, "fact_check")]
    if hint:
        out.append(_text("reviewd", hint, "caption"))
    return out


def tabs_card(file: str, tabs: list[tuple[str, Profile]], hint: str | None = None) -> list[dict]:
    """One button per tab, with what was found in it."""
    children = [_text("title", f"Which tab of {file}?", "h4"), *_review_button(hint)]
    for i, (name, prof) in enumerate(tabs):
        children.append(_button(f"tab{i}", f"{name} · {prof.rows:,} rows", TAB_ACTION,
                                {"sheet": name, "prompt": f"Use the {name} tab"}, "table_chart"))
        children.append(_text(f"tabd{i}", prof.summary(), "caption"))
    return _card("dash-tabs", children)


def styles_card(file: str, sheet: str, prof: Profile, notes: list[str] = (), hint: str | None = None) -> list[dict]:
    """The chart styles this tab can support, best first."""
    ok = available(prof)
    children = [_text("title", f"How should I chart {sheet}?", "h4"),
                _text("found", f"I found {prof.summary()}.")]
    if notes:
        children.append(_text("notes", "Tidied: " + " ".join(notes), "caption"))
    for s, (label, icon, what) in STYLES.items():
        if not ok.get(s):
            continue
        children.append(_button(f"st-{s}", label, CHART_ACTION, {"sheet": sheet, "style": s,
                                "prompt": f"Show {sheet} as {label.lower()}"}, icon, "flat" if s == "auto" else "stroked"))
        children.append(_text(f"std-{s}", what, "caption"))
    children.append(_button("other", "Pick another tab", TABS_ACTION, {"prompt": "Show me the other tabs"}, "tab"))
    children += _review_button(hint)
    return _card("dash-styles", children)


def review_card(file: str, tabs: list) -> list[dict]:
    """The file check in the side panel; Pick a tab stays a native button."""
    comps = [
        {"id": "frame", "component": "IFrameSrcdoc", "htmlContent": review.html(file, tabs), "height": FRAME_HEIGHT},
        _button("tabs", "Pick a tab to chart", TABS_ACTION, {"prompt": "Show me the tabs"}, "tab"),
    ]
    children = ["frame", "tabs"] if any(t.chartable for t in tabs) else ["frame"]
    root = {"id": "root", "component": "Canvas", "cardTitle": f"File check · {file}"[:120],
            "cardDescription": review.headline(file, tabs)[:200], "cardIcon": "fact_check", "autoOpen": True, "children": children}
    return a2ui.surface("dash-review", [root, *[c for c in comps if c["id"] in children]])


def dashboard_card(a: Analysis, n: Narrative, file: str, hint: str | None = None, tabs: int = 1) -> list[dict]:
    """Opener card in chat; the dashboard opens in the side panel. Download PDF, Chart styles, Pick
    another tab and (after an upload with something to fix) Review my file are native buttons."""
    ctx = {**a.meta(), "prompt": f"Download {a.sheet} insights as PDF"}
    comps = [
        {"id": "frame", "component": "IFrameSrcdoc", "htmlContent": page.page(a, n, file), "height": FRAME_HEIGHT},
        _button(PDF_ACTION, "Download PDF", PDF_ACTION, ctx, "download"),
        _button("styles", "Chart styles", STYLES_ACTION, {"sheet": a.sheet, "prompt": f"Show chart styles for {a.sheet}"}, "bar_chart"),
    ]
    if tabs > 1:
        comps.append(_button("tabs", "Pick another tab", TABS_ACTION, {"prompt": "Show me the other tabs"}, "tab"))
    if hint and "to fix" in hint:
        comps.append(_button(REVIEW_ACTION, "Review my file", REVIEW_ACTION, {"prompt": "Review my file"}, "fact_check"))
    root = {"id": "root", "component": "Canvas", "cardTitle": f"{a.sheet} · {STYLES[a.style][0]}",
            "cardDescription": n.headline[:200], "cardIcon": "insights", "autoOpen": True, "children": [c["id"] for c in comps]}
    return a2ui.surface("dash", [root, *comps])


def error_card(message: str, retry: dict | None = None, review_file: bool = False) -> list[dict]:
    children = [_text("title", "That didn't work", "h4"), _text("msg", message)]
    if retry:
        children.append(_button("retry", "Try again", CHART_ACTION, retry, "refresh"))
    if review_file:
        children += _review_button("See what's in each tab and what to change so it can be charted.")
    return _card("dash-error", children)
