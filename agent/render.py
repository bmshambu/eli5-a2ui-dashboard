"""A2UI v0.9 cards: welcome, tab picker, chart-style picker, the dashboard (Canvas +
IFrameSrcdoc) and errors.

Every button carries in its context everything the handler needs (sheet, style, measure,
dimension), so a click works from any card without guessing from state, and a `prompt` that GE
shows as the user's turn.
"""
import json

from . import a2ui, page
from .analysis import STYLES, Analysis, available
from .schema import Narrative
from .sheets import Profile

FRAME_HEIGHT = 1150  # the page scrolls inside the frame when it is longer
TAB_ACTION = "tab"  # picked a tab -> style picker
TABS_ACTION = "tabs"  # show the tab picker again
CHART_ACTION = page.CHART_ACTION  # draw sheet/style/measure/dim
ASK_ACTION = page.ASK_ACTION  # a question about the data
PDF_ACTION = "pdf"  # the agent builds the PDF and sends it back as a file


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


def welcome_card() -> list[dict]:
    return _card("dash-welcome", [
        _text("title", "Turn a spreadsheet into a chart with insights", "h4"),
        _text("intro", "Upload an Excel (.xlsx) or CSV file using the attach button. I'll show you its tabs, you pick one and a chart "
                       "style, and I'll draw it in the side panel with the story behind the numbers: what changed, what drove it, "
                       "what stands out, where it is heading and what to check next."),
        _text("styles", "**Chart styles:** " + " · ".join(v[0] for k, v in STYLES.items() if k != "auto")
              + ". Or say what you want, for example *\"show revenue by region as a heatmap\"*.", "caption"),
    ])


def tabs_card(file: str, tabs: list[tuple[str, Profile]]) -> list[dict]:
    """One button per tab, with what was found in it."""
    children = [_text("title", f"Which tab of {file}?", "h4")]
    for i, (name, prof) in enumerate(tabs):
        children.append(_button(f"tab{i}", f"{name} · {prof.rows:,} rows", TAB_ACTION,
                                {"sheet": name, "prompt": f"Use the {name} tab"}, "table_chart"))
        children.append(_text(f"tabd{i}", prof.summary(), "caption"))
    return _card("dash-tabs", children)


def styles_card(file: str, sheet: str, prof: Profile, notes: list[str] = ()) -> list[dict]:
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
    return _card("dash-styles", children)


def dashboard_card(a: Analysis, n: Narrative, file: str) -> list[dict]:
    """Opener card in chat; the insight page opens in the side panel. Download PDF and Pick
    another tab are native buttons (the frame can't download)."""
    ctx = {**a.meta(), "prompt": f"Download {a.sheet} insights as PDF"}
    comps = [
        {"id": "frame", "component": "IFrameSrcdoc", "htmlContent": page.page(a, n, file), "height": FRAME_HEIGHT},
        _button(PDF_ACTION, "Download PDF", PDF_ACTION, ctx, "download"),
        _button("tabs", "Pick another tab", TABS_ACTION, {"prompt": "Show me the other tabs"}, "tab"),
    ]
    root = {"id": "root", "component": "Canvas", "cardTitle": f"{a.sheet} · {STYLES[a.style][0]}",
            "cardDescription": n.headline[:200], "cardIcon": "insights", "autoOpen": True, "children": ["frame", PDF_ACTION, "tabs"]}
    return a2ui.surface("dash", [root, *comps])


def error_card(message: str, retry: dict | None = None) -> list[dict]:
    children = [_text("title", "That didn't work", "h4"), _text("msg", message)]
    if retry:
        children.append(_button("retry", "Try again", CHART_ACTION, retry, "refresh"))
    return _card("dash-error", children)
