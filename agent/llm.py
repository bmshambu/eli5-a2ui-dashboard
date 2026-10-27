"""The one model call per chart: Gemini writes the story around facts computed in analysis.py.

The model never sees raw rows and never does arithmetic. It gets the numbered facts and writes a
headline, insights, actions and follow-up questions, citing fact ids. grounded() then checks
every number in the text against the facts and replaces any claim it can't match, so a figure
on the page is always one Python computed. If the call fails, fallback() builds the same shape
from the facts alone, so the user still gets a dashboard.
The client is passed in so tests can use a fake one; it is built lazily so the agent pickles.
"""
import logging
import os
import re

from google import genai
from google.genai import types as genai_types

from .analysis import STYLES, Analysis
from .schema import Insight, Narrative

log = logging.getLogger(__name__)

PROMPT = """You are a sharp, careful data analyst writing for busy business professionals.
Below are facts computed exactly from the "{sheet}" tab of the spreadsheet {file}. The user sees them as a
"{style}" chart of {measure}{by}. Write the story behind the chart: what matters, why, and what to do next.

Rules:
- Use only the facts. Every number you write must appear in a fact, written the same way (for example
  1.2M, 85.8K, +14.0%, 2.4x). Never compute new numbers, totals, averages, differences or percentages.
- Each insight lists the ids of the facts it rests on in evidence.
- Lead with the most decision-relevant finding, not the biggest number: change, what drove it,
  concentration risk, momentum, anomalies and the forecast beat restating a total.
- Say what each finding means for the business in plain words. Do not state causes as fact; say what is
  worth checking.
- If a quality fact affects a conclusion (for example an incomplete last period), say so in the insight
  and in caveats.
- Plain text only: no markdown, emoji or fact ids in the text.
- Follow-up questions must be answerable from this spreadsheet (other columns, tabs or chart styles).
{focus}
Columns in this tab: {columns}
Chart styles available: {styles}

Facts:
{facts}"""

FOCUS = """- The user asked: "{q}". Answer it first, in the headline and the first insight, from the facts.
  If the facts cannot answer it, say so plainly in the first insight and suggest which chart style or column would."""


def make_client() -> genai.Client:
    """Vertex AI client. MODEL_LOCATION is separate from the Agent Engine region because the
    newest Gemini models are served from the global endpoint only."""
    return genai.Client(
        vertexai=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_CLOUD_PROJECT"),
        location=os.getenv("MODEL_LOCATION", "global"),
    )


def model_name() -> str:
    return os.getenv("MODEL", "gemini-4-argon")


def _thinking() -> genai_types.ThinkingConfig | None:
    """Low thinking keeps answers quick. THINKING_LEVEL='' leaves the model default."""
    level = os.getenv("THINKING_LEVEL", "low").strip()
    return genai_types.ThinkingConfig(thinking_level=level.upper()) if level else None


def prompt(a: Analysis, file: str, focus: str | None = None) -> str:
    cols = ", ".join(f"{c} ({'number' if c in a.prof.measures else 'date' if c == a.prof.time else 'category' if c in a.prof.dimensions else 'other'})"
                     for c in a.prof.measures + a.prof.dimensions + ([a.prof.time] if a.prof.time else []))
    return PROMPT.format(sheet=a.sheet, file=file, style=STYLES[a.style][0], measure=a.measure,
                         by=f" by {a.dim}" if a.dim and a.style not in ("trend", "distribution", "scatter") else "",
                         focus=FOCUS.format(q=focus.replace('"', "'")) if focus else "", columns=cols,
                         styles=", ".join(v[0] for k, v in STYLES.items() if k != "auto"), facts=a.fact_lines())


async def narrate(client, a: Analysis, file: str, focus: str | None = None) -> Narrative:
    response = await client.aio.models.generate_content(
        model=model_name(),
        contents=prompt(a, file, focus),
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=Narrative,
            thinking_config=_thinking(), temperature=0.3,
        ),
    )
    narrative = response.parsed if isinstance(response.parsed, Narrative) else Narrative.model_validate_json(response.text)
    return grounded(narrative, a)


# ── Number check ─────────────────────────────────────────────────────────────

_NUM = re.compile(r"(?<![A-Za-z\d.])[-+]?\d[\d,]*(?:\.\d+)?\s?(?:[KMB](?![a-z])|%|x(?![a-z]))?")
_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9}


def _parsed(text: str) -> list[tuple[float, str, float]]:
    """(absolute value, kind, half of the last written digit) per number: '1.2M' -> (1.2e6, '', 5e4),
    '+14.0%' -> (14.0, '%', 0.05). Kind is '%', 'x' or '' for amounts and counts."""
    out = []
    for m in _NUM.finditer(text or ""):
        s = m.group(0).replace(",", "").replace(" ", "")
        kind = s[-1] if s[-1] in "%x" else ""
        scale = _SCALE.get(s[-1], 1.0)
        s = s.rstrip("KMB%x").lstrip("+-")
        try:
            v = float(s)
        except ValueError:
            continue
        decimals = len(s.split(".")[1]) if "." in s else 0
        out.append((v * scale, kind, 0.5 * 10 ** -decimals * scale))
    return out


def numbers(text: str) -> list[float]:
    return [v for v, _, _ in _parsed(text)]


def _allowed(a: Analysis) -> list[tuple[float, str, float]]:
    found = [x for f in a.facts for x in _parsed(f.text)]
    return found + [x for k in a.kpis for x in _parsed(f"{k['value']} {k.get('note', '')}")]


def supported(text: str, allowed: list[tuple[float, str, float]]) -> bool:
    """True when every number in text is a computed number, as written or rounded (14.0% may be
    written 14%, 1.23M may be 1.2M). Percentages only match percentages. Small counts are fine."""
    for v, kind, half in _parsed(text):
        if not kind and v <= 12 and float(v).is_integer():
            continue
        if not any(k == kind and abs(v - x) <= half + 1e-9 * max(abs(x), 1) for x, k, _ in allowed):
            return False
    return True


def grounded(n: Narrative, a: Analysis) -> Narrative:
    """The narrative with every claim whose numbers aren't in the facts replaced by the facts."""
    allowed = _allowed(a)
    by_id = {f.id: f for f in a.facts}
    base = fallback(a)
    dropped = 0
    insights = []
    for ins in n.insights[:5]:
        ev = [e for e in ins.evidence if e in by_id]
        if supported(ins.title, allowed) and supported(ins.text, allowed):
            insights.append(ins.model_copy(update={"evidence": ev}))
        elif ev:
            dropped += 1
            insights.append(ins.model_copy(update={"text": " ".join(by_id[e].text for e in ev[:2]), "evidence": ev,
                                                   "title": ins.title if supported(ins.title, allowed) else base.insights[0].title}))
        else:
            dropped += 1
    if len(insights) < 2:
        insights += [i for i in base.insights if i.title not in {x.title for x in insights}][: 3 - len(insights)]
    headline = n.headline if supported(n.headline, allowed) else base.headline
    summary = n.summary if supported(n.summary, allowed) else base.summary
    actions = [x for x in n.actions if supported(x, allowed)][:3] or base.actions
    questions = [q.strip() for q in n.questions if q.strip()][:3] or base.questions
    caveats = [x for x in n.caveats if supported(x, allowed)][:4]
    if dropped:
        log.warning("replaced %d insight(s) whose numbers were not in the facts", dropped)
    return Narrative(headline=headline, summary=summary, insights=insights, actions=actions, questions=questions, caveats=caveats)


# ── No-model fallback ────────────────────────────────────────────────────────

_TITLES = {"trend": ("trend", "The direction of travel"), "forecast": ("trend", "Where it is heading"),
           "driver": ("driver", "What is driving it"), "concentration": ("risk", "How concentrated it is"),
           "anomaly": ("anomaly", "Values that stand out"), "relationship": ("pattern", "A relationship worth knowing"),
           "distribution": ("pattern", "How values are spread")}


def fallback(a: Analysis) -> Narrative:
    """The narrative from the facts alone (model unavailable, or a claim failed the number check)."""
    ranked = sorted((f for f in a.facts if f.kind not in ("summary", "quality")), key=lambda f: -f.weight)
    head = ranked[0].text if ranked else a.facts[0].text
    summary = " ".join(f.text for f in ranked[1:3]) or a.facts[0].text
    seen, insights = set(), []
    for f in ranked:
        kind, title = _TITLES.get(f.kind, ("pattern", "Worth knowing"))
        if title in seen:
            continue
        seen.add(title)
        insights.append(Insight(kind=kind, title=title, text=f.text, evidence=[f.id]))
        if len(insights) == 4:
            break
    kinds = {f.kind for f in a.facts if f.weight >= 1}
    actions = []
    if "anomaly" in kinds:
        actions.append("Check the flagged values for data errors or one-off events before relying on the totals.")
    if "concentration" in kinds:
        actions.append(f"Review how much depends on the top {a.dim or 'items'} and whether that is a risk.")
    if "forecast" in kinds:
        actions.append("Compare the forecast range with your plan or budget for the next periods.")
    actions = actions[:3] or ["Look at the breakdown by another category to see what sits behind the total."]
    other_dim = next((d for d in a.prof.dimensions if d != a.dim), None)
    questions = [q for q in (
        f"Show {a.measure} as a heatmap" if a.style != "heatmap" and a.prof.dimensions else None,
        f"Which {a.dim} changed the most?" if a.dim and a.prof.time else None,
        f"Break {a.measure} down by {other_dim}" if other_dim else None,
        f"Show the trend of {a.measure}" if a.style != "trend" and a.prof.time else None,
        f"How is {a.measure} spread across rows?",
    ) if q][:3]
    caveats = [f.text for f in a.facts if f.kind == "quality" and f.weight >= 1][:3]
    return Narrative(headline=head, summary=summary, insights=insights, actions=actions, questions=questions, caveats=caveats)
