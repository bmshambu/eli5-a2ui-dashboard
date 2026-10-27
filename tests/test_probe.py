import asyncio
import json

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from agent import a2ui, probe, validate
from agent.probe_agent import probe_agent as root_agent, respond


@pytest.mark.parametrize("trigger", list(probe.BUILDERS))
def test_every_card_validates(trigger):
    name, messages = probe.cards_for(trigger)
    assert name == trigger
    validate.check(messages)
    assert all(m["version"] == "v0.9" for m in messages)
    assert messages[0]["createSurface"]["catalogId"] == a2ui.CATALOG_ID


def test_fresh_surface_id_each_time():
    a = probe.iframe_js()[0]["createSurface"]["surfaceId"]
    b = probe.iframe_js()[0]["createSurface"]["surfaceId"]
    assert a != b


def test_200kb_page_is_realistic_but_under_budget():
    size = a2ui.message_bytes(probe.iframe_200kb()[1])
    assert 200_000 < size < a2ui.MAX_MESSAGE_BYTES


def test_canvas_is_root():
    comps = probe.iframe_canvas()[1]["updateComponents"]["components"]
    root = next(c for c in comps if c["id"] == "root")
    assert root["component"] == "Canvas" and root["children"] == ["frame"]


def test_unknown_trigger_falls_back_to_help():
    assert probe.cards_for("hello there")[0] == "help"
    assert probe.normalize("  `IFrame-JS` ") == "iframe-js"


def test_validator_rejects_unknown_property():
    bad = a2ui.update_components("s", [{"id": "root", "component": "MaterialChips", "label": "x", "options": []}])
    assert any("unknown property 'label'" in e for e in validate.errors(bad))


def test_validator_rejects_wrong_card_shape():
    bad = a2ui.update_components("s", [{"id": "root", "component": "MaterialCard", "child": "x"}])
    assert validate.errors(bad)


def _click_content(action: dict) -> genai_types.Content:
    wrapped = json.dumps({"kind": "data", "data": {"version": "v0.9", "action": action}})
    blob = genai_types.Blob(mime_type="text/plain", data=b"<a2a_datapart_json>" + wrapped.encode() + b"</a2a_datapart_json>")
    return genai_types.Content(role="user", parts=[genai_types.Part(inline_data=blob)])


def test_click_is_echoed():
    action = {"name": "probe_native_click", "context": {"via": "native"}}
    content = _click_content(action)
    assert a2ui.extract_action(content) == action
    text, messages = respond(content)
    assert text == "Probe: **received**"
    validate.check(messages)
    summary = next(c for c in messages[1]["updateComponents"]["components"] if c["id"] == "summary")
    assert "probe_native_click" in summary["text"]


def test_agent_end_to_end_without_model():
    """The callback answers before any model call, so this runs offline."""
    async def run():
        runner = InMemoryRunner(agent=root_agent, app_name="probe")
        session = await runner.session_service.create_session(app_name="probe", user_id="u")
        msg = genai_types.Content(role="user", parts=[genai_types.Part(text="iframe-js")])
        return [e async for e in runner.run_async(user_id="u", session_id=session.id, new_message=msg)]

    events = asyncio.run(run())
    parts = [p for e in events if e.content for p in e.content.parts]
    assert parts[0].text == "Probe: **iframe-js**"
    assert len([p for p in parts if p.inline_data]) == 2
