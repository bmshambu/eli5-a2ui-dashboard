"""Spreadsheet insights agent for Gemini Enterprise.

The flow: the user uploads an .xlsx or .csv, picks a tab (tab picker card, skipped when there is
one tab), picks a chart style (style picker card), and the side panel opens a dashboard: the
chart plus the story behind it (headline, KPI tiles, insights with evidence, actions, follow-up
questions). Typed requests work at any point: "show revenue by region as a heatmap", "use the
Customers tab", "why did West fall?" (a question re-tells the current chart's story around it).

All numbers come from analysis.py (pandas, no model). The one model call per chart writes the
narrative from those facts, and llm.grounded() replaces any claim whose numbers don't match.
If the model fails the dashboard still ships with a facts-only narrative.
Later turns reuse the latest upload in the session events, so clicks need no re-upload.
"Download PDF" rebuilds the analysis from the sheet and the narrative kept in session state.
AGENT_MODE=probe serves the step 0 probe (see PROBE.md).
"""
import hashlib
import logging
import os
import re
from typing import AsyncGenerator, Callable, NamedTuple

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types as genai_types

from . import a2ui, analysis, llm, pdf, render, sheets, validate
from .analysis import STYLES
from .schema import Narrative

log = logging.getLogger(__name__)

STATE_KEY = "dash_last"  # {"file", "sheet", "style", "measure", "dim", "narrative"}: the dashboard last shown
MAX_TEXT = 500

_GREETING = re.compile(r"^(hi|hello|hey|help|start|menu|what can you do\??|who are you\??)[!. ]*$", re.I)
_TABS = re.compile(r"\b(?:other|another|different|switch|change|show|list|which|all)\b.{0,20}\b(?:tabs?|sheets?)\b|^\W*(?:tabs?|sheets?)\W*$", re.I)
# Checked in order: "small multiples" before "multiples", heatmap's "grid" before anything else.
_STYLE_WORDS = (
    ("multiples", r"small\s+multiples?|multiples|sparklines?|panels?\s+per"),
    ("heatmap", r"heat\s*-?\s*maps?|matrix"),
    ("bridge", r"bridge|waterfall|what\s+changed"),
    ("pareto", r"pareto|80\s*/\s*20|ranked|ranking|rank\b|top\s+\d+|drivers?|bar\s+chart"),
    ("scatter", r"scatter|relationship|correlat\w*|\bversus\b|\bvs\.?\b"),
    ("distribution", r"distribution|histogram|spread"),
    ("trend", r"trend|forecast|over\s+time|line\s+chart|timeline"),
    ("auto", r"\bauto\b|you\s+pick|best\s+chart|surprise\s+me"),
)

# Swapped in tests for a fake client; built lazily so the agent pickles for Agent Engine.
client_factory: Callable = llm.make_client
_client = None
_books: dict[str, list[sheets.Sheet]] = {}  # parsed workbooks by content hash (parsing is the slow part)


def _get_client():
    global _client
    if _client is None:
        _client = client_factory()
    return _client


def workbook(up: sheets.Upload) -> list[sheets.Sheet]:
    key = hashlib.sha1(up.data).hexdigest()
    if key not in _books:
        if len(_books) >= 4:
            _books.pop(next(iter(_books)))
        _books[key] = sheets.read(up)
    return _books[key]


def _user_text(content) -> str:
    if not content or not getattr(content, "parts", None):
        return ""
    return " ".join(p.text for p in content.parts if getattr(p, "text", None)).strip()[:MAX_TEXT]


def style_in(text: str) -> str | None:
    for style, pattern in _STYLE_WORDS:
        if re.search(pattern, text or "", re.I):
            return style
    return None


def _find(names: list[str], text: str, prefer_after: str | None = None) -> str | None:
    """The column or tab named in text (longest match wins; with prefer_after, one that follows it)."""
    hits = [n for n in names if re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", text or "", re.I)]
    if prefer_after:
        after = [n for n in hits if re.search(rf"\b{prefer_after}\s+(?:the\s+)?{re.escape(n)}(?![\w])", text, re.I)]
        hits = after or hits
    return max(hits, key=len) if hits else None


def sheet_in(text: str, tabs: list[sheets.Sheet]) -> sheets.Sheet | None:
    name = _find([t.name for t in tabs], text)
    if name:
        return next(t for t in tabs if t.name == name)
    m = re.search(r"\b(?:tab|sheet)\s*(\d{1,2})\b", text or "", re.I)
    if m and 1 <= int(m.group(1)) <= len(tabs):
        return tabs[int(m.group(1)) - 1]
    return None


def _sheet(tabs: list[sheets.Sheet], name: str | None) -> sheets.Sheet | None:
    return next((t for t in tabs if t.name == name), None) or next((t for t in tabs if t.name.lower() == str(name or "").lower()), None)


class Reply(NamedTuple):
    text: str  # the one-line reply
    messages: list[dict]  # A2UI messages
    remember: dict | None = None  # new STATE_KEY value
    files: tuple = ()  # file parts, e.g. the PDF


def tabs_reply(up: sheets.Upload, tabs: list[sheets.Sheet]) -> Reply:
    if len(tabs) == 1:
        return styles_reply(up, tabs[0])
    return Reply(f"**{up.name}** has {len(tabs)} tabs with numbers. Which one should I chart?",
                 render.tabs_card(up.name, [(t.name, sheets.profile(t)) for t in tabs]))


def styles_reply(up: sheets.Upload, sheet: sheets.Sheet) -> Reply:
    return Reply(f"Pick a chart style for the **{sheet.name}** tab, or tell me what you want to see.",
                 render.styles_card(up.name, sheet.name, sheets.profile(sheet), sheet.notes))


async def chart(up: sheets.Upload, sheet: sheets.Sheet, style: str = "auto", measure: str | None = None,
                dim: str | None = None, focus: str | None = None) -> Reply:
    """The dashboard: computed analysis, then the narrative (model, or facts-only on failure)."""
    retry = {"sheet": sheet.name, "style": style, "measure": measure, "dim": dim, "prompt": f"Show {sheet.name} again"}
    try:
        a = analysis.analyze(sheet, style if style in STYLES else "auto", measure, dim)
    except Exception as exc:  # noqa: BLE001  (the user gets a message, not a broken turn)
        log.exception("analysis failed for %s/%s", sheet.name, style)
        reason = str(exc) if isinstance(exc, ValueError) else "something in this tab tripped me up"
        return Reply(f"Sorry, I couldn't chart that: {reason}.", render.error_card(f"I couldn't chart **{sheet.name}**: {reason}."))
    try:
        n = await llm.narrate(_get_client(), a, up.name, focus)
    except Exception:  # noqa: BLE001
        log.exception("narrative failed for %s/%s; using the facts-only narrative", sheet.name, a.style)
        n = llm.fallback(a)
    try:
        messages = render.dashboard_card(a, n, up.name)
        validate.check(messages)
    except Exception:  # noqa: BLE001
        log.exception("dashboard card failed for %s/%s", sheet.name, a.style)
        return Reply("Sorry, I couldn't draw that one.", render.error_card("The chart didn't render. Try again or pick another style.", retry))
    remember = {"file": up.name, **a.meta(), "narrative": n.model_dump(mode="json")}
    return Reply(f"**{n.headline}**\n\n{STYLES[a.style][0]} of {a.measure} from the {a.sheet} tab is open in the side panel.",
                 messages, remember)


def pdf_reply(up: sheets.Upload, tabs: list[sheets.Sheet], ctx: dict, last: dict | None) -> Reply:
    sheet = _sheet(tabs, ctx.get("sheet"))
    if sheet is None:
        return Reply("Sorry, I couldn't find that tab any more. Upload the file again.", [])
    try:
        a = analysis.analyze(sheet, ctx.get("style") or "auto", ctx.get("measure"), ctx.get("dim"))
        same = last and last.get("file") == up.name and all(last.get(k) == v for k, v in a.meta().items())
        n = Narrative.model_validate(last["narrative"]) if same else llm.fallback(a)
        data = pdf.build(a, n, up.name)
    except Exception:  # noqa: BLE001
        log.exception("pdf failed for %s", ctx)
        return Reply("Sorry, I couldn't make the PDF. Try the button again.", [])
    name = pdf.filename(sheet.name)
    part = genai_types.Part(inline_data=genai_types.Blob(mime_type="application/pdf", data=data, display_name=name))
    return Reply(f"Here is the **{sheet.name}** report as a PDF: {name}", [], files=(part,))


async def respond(content, up: sheets.Upload | None, fresh: bool = False, last: dict | None = None) -> Reply:
    """The reply to this turn. `up` is this turn's upload or the latest in the session; `fresh`
    says it arrived this turn; `last` is the dashboard shown last (STATE_KEY)."""
    action = a2ui.extract_action(content)
    name = (action or {}).get("name")
    ctx = (action or {}).get("context") or {}
    text = _user_text(content) if action is None else str(ctx.get("prompt") or "") if name == render.ASK_ACTION else ""
    if up is None:
        return Reply("Upload an Excel or CSV file and I'll chart it with insights.", render.welcome_card())
    try:
        tabs = workbook(up)
    except ValueError as exc:
        return Reply(f"Sorry, {exc}.", render.error_card(f"Sorry, {exc}."))
    except Exception:  # noqa: BLE001
        log.exception("could not read %s", up.name)
        return Reply("Sorry, I couldn't read that file.", render.error_card(f"I couldn't read **{up.name}**. Is it a normal .xlsx or .csv file?"))

    if name == render.PDF_ACTION:
        return pdf_reply(up, tabs, ctx, last)
    if name == render.TABS_ACTION:
        return tabs_reply(up, tabs)
    if name == render.TAB_ACTION:
        sheet = _sheet(tabs, ctx.get("sheet"))
        return styles_reply(up, sheet) if sheet else tabs_reply(up, tabs)
    if name == render.CHART_ACTION:
        sheet = _sheet(tabs, ctx.get("sheet"))
        if sheet is None:
            return tabs_reply(up, tabs)
        return await chart(up, sheet, ctx.get("style") or "auto", ctx.get("measure"), ctx.get("dim"))

    # Typed text, or the prompt of an "Ask next" chip (which also carries the chart it came from).
    base = ctx if name == render.ASK_ACTION else (None if fresh else last)
    if base and base.get("file", up.name) != up.name:
        base = None
    if not text or _GREETING.match(text):
        return tabs_reply(up, tabs)
    if _TABS.search(text):
        return tabs_reply(up, tabs)
    sheet = sheet_in(text, tabs) or (_sheet(tabs, base.get("sheet")) if base else None) or (tabs[0] if len(tabs) == 1 else None)
    if sheet is None:
        return tabs_reply(up, tabs)
    style = style_in(text)
    prof = sheets.profile(sheet)
    measure = _find(prof.measures, text)
    dim = _find(prof.dimensions, text, prefer_after="by")
    same_sheet = base and base.get("sheet") == sheet.name
    if fresh and style is None and measure is None and dim is None and not text.rstrip().endswith("?"):
        return styles_reply(up, sheet)  # "here's my file, use the Sales tab" -> pick a style
    command = style is not None or (sheet_in(text, tabs) is not None and not text.rstrip().endswith("?"))
    return await chart(up, sheet,
                       style or (base.get("style") if same_sheet else None) or "auto",
                       measure or (base.get("measure") if same_sheet else None),
                       dim or (base.get("dim") if same_sheet else None),
                       focus=None if command else text)


class SheetInsightsAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        log.info("turn parts: %s", sheets.describe(ctx.user_content))
        uploaded = sheets.uploads_in(ctx.user_content)
        up = uploaded[-1] if uploaded else sheets.latest(ctx.session.events)
        other_files = [p for p in getattr(ctx.user_content, "parts", None) or []
                       if getattr(p, "inline_data", None) and not (p.inline_data.data or b"").startswith(a2ui.TAG_START)]
        if other_files and not uploaded:
            reply = Reply("I can chart Excel (.xlsx) and CSV files. Please upload one of those.", render.welcome_card())
        else:
            reply = await respond(ctx.user_content, up, bool(uploaded), ctx.session.state.get(STATE_KEY))
        validate.check(reply.messages)
        parts = [genai_types.Part(text=reply.text), *reply.files] + [a2ui.to_genai_part(m) for m in reply.messages]
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=genai_types.Content(role="model", parts=parts),
            actions=EventActions(state_delta={STATE_KEY: reply.remember} if reply.remember else {}),
        )


if os.getenv("AGENT_MODE") == "probe":
    from .probe_agent import probe_agent as root_agent
else:
    root_agent = SheetInsightsAgent(name="sheet_insights", description="Turns an uploaded spreadsheet tab into a chart plus insight "
                                                                       "analysis: trends, forecast, drivers, anomalies and next steps.")
