"""Step 0 probe agent for Gemini Enterprise (deploy with AGENT_MODE=probe).

Deterministic, no model in the path: the before_model_callback answers every turn itself
(returning an LlmResponse skips the model call), so the probe tests only what GE renders.
Typed text picks a card from probe.BUILDERS; any click that reaches the agent is echoed raw.
"""
import os

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from . import a2ui, probe, validate


def _user_text(content) -> str:
    if not content or not getattr(content, "parts", None):
        return ""
    return " ".join(p.text for p in content.parts if getattr(p, "text", None))


def respond(user_content) -> tuple[str, list[dict]]:
    """(one-line reply, A2UI messages) for this turn."""
    payloads = a2ui.incoming_data(user_content)
    if payloads:
        return "Probe: **received**", probe.received_card(payloads)
    name, messages = probe.cards_for(probe.normalize(_user_text(user_content)))
    return f"Probe: **{name}**", messages


def _answer(callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse:
    text, messages = respond(callback_context.user_content)
    validate.check(messages)
    parts = [genai_types.Part(text=text)] + [a2ui.to_genai_part(m) for m in messages]
    return LlmResponse(content=genai_types.Content(role="model", parts=parts))


probe_agent = LlmAgent(
    name="eli5_iframe_probe",
    # Never called: the callback answers first.
    model=os.getenv("MODEL", "gemini-4-argon"),
    instruction="Reply with one short line. Never output JSON.",
    before_model_callback=_answer,
)
