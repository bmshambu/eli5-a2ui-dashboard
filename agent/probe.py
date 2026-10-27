"""Step 0 probe: what does IFrameSrcdoc allow in Gemini Enterprise?

Already proven in GE (v0p9 guidelines s8): the composite catalog, Canvas, and IFrameSrcdoc
with inline CSS. This probe checks the rest, one trigger word per card:

  help           list of triggers
  iframe-js      JavaScript tabs + inline SVG inside the frame
  iframe-canvas  the same page inside a Canvas side panel
  iframe-action  in-frame buttons that postMessage an a2ui_action, plus a native button
  iframe-200kb   the iframe-js page padded to about 200 KB
Any click that reaches the agent is echoed back raw in a "Received" card.
"""
import json
import re

from . import a2ui, probe_pages

TRIGGERS = {
    "help": "this list",
    "iframe-js": "JavaScript tabs + inline SVG inside IFrameSrcdoc",
    "iframe-canvas": "the same page inside a Canvas side panel",
    "iframe-action": "in-frame postMessage buttons + a native button",
    "iframe-200kb": "the iframe-js page padded to about 200 KB",
}


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", (text or "").strip().lower())


def _text(cid: str, text: str, hint: str = "body") -> dict:
    return {"id": cid, "component": "MaterialText", "text": text, "usageHint": hint}


def _frame(cid: str, html_doc: str, height: int) -> dict:
    return {"id": cid, "component": "IFrameSrcdoc", "htmlContent": html_doc, "height": height}


def _card(prefix: str, title: str, note: str, body: list[dict]) -> list[dict]:
    """MaterialCard > MaterialColumn > [title, *body, note]."""
    comps = [
        {"id": "root", "component": "MaterialCard", "children": ["col"]},
        {"id": "col", "component": "MaterialColumn", "align": "stretch",
         "children": ["title", *[c["id"] for c in body], "note"]},
        _text("title", title, "h4"),
        *body,
        _text("note", note, "caption"),
    ]
    return a2ui.surface(prefix, comps)


def help_card() -> list[dict]:
    lines = "\n".join(f"- **{k}**: {v}" for k, v in TRIGGERS.items())
    return _card("probe-help", "ELI5 iframe probe", "Type one trigger per message.",
                 [_text("list", lines)])


def iframe_js() -> list[dict]:
    return _card(
        "probe-js", "iframe-js",
        "Pass: the pill says 'JavaScript: running' and the level tabs switch. Fail: red pill, tabs do nothing.",
        [_frame("frame", probe_pages.js_page(), 520)],
    )


def iframe_canvas() -> list[dict]:
    comps = [
        {"id": "root", "component": "Canvas", "cardTitle": "Why is the sky blue?",
         "cardDescription": "Probe: iframe-canvas (JS tabs in the side panel)", "cardIcon": "lightbulb",
         "autoOpen": True, "children": ["frame"]},
        _frame("frame", probe_pages.js_page(), 640),
    ]
    return a2ui.surface("probe-canvas", comps)


def iframe_action() -> list[dict]:
    native = {
        "id": "native", "component": "MaterialButton", "label": "C: native MaterialButton",
        "action": {"event": {"name": "probe_native_click",
                             "context": {"prompt": "Clicked the native button", "via": "native"}}},
    }
    return _card(
        "probe-action", "iframe-action",
        "Click A, B and C one at a time. Each one that reaches the agent comes back as a 'Received' card.",
        [_frame("frame", probe_pages.action_page(), 220), native],
    )


def iframe_200kb() -> list[dict]:
    return _card(
        "probe-200kb", "iframe-200kb",
        "Pass: the card renders like iframe-js. Fail: no card, or an error.",
        [_frame("frame", probe_pages.js_page(padding_bytes=200_000), 520)],
    )


def received_card(payloads: list) -> list[dict]:
    """Echo whatever structured data arrived, so we learn GE's exact click shape."""
    action = next((p.get("data", p).get("action") for p in payloads
                   if isinstance(p, dict) and isinstance(p.get("data", p), dict)
                   and isinstance(p.get("data", p).get("action"), dict)), None)
    summary = (f"**name:** {action.get('name')}  \n**context:** {json.dumps(action.get('context'))}"
               if action else "No `action` key found; see the raw payload below.")
    raw = json.dumps(payloads, indent=2)[:20_000]
    return _card(
        "probe-received", "Received",
        "Copy this card's contents into the results log.",
        [_text("summary", summary), _frame("raw", probe_pages.raw_page("Raw payload", raw), 320)],
    )


BUILDERS = {
    "help": help_card,
    "iframe-js": iframe_js,
    "iframe-canvas": iframe_canvas,
    "iframe-action": iframe_action,
    "iframe-200kb": iframe_200kb,
}


def cards_for(trigger: str) -> tuple[str, list[dict]]:
    """(name, messages) for a typed trigger; unknown text gets the help card."""
    if trigger in BUILDERS:
        return trigger, BUILDERS[trigger]()
    return "help", help_card()
