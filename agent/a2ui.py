"""A2UI v0.9 helpers for Gemini Enterprise (GE).

Transport and message rules proven in GE (ge-chat-spike and the v0.9 concierge):
  - every message carries "version": "v0.9", or GE falls back to v0.8 and renders nothing
  - one A2UI message per A2A DataPart, mime type application/json+a2ui
  - ADK emits a DataPart when a text/plain blob is wrapped in <a2a_datapart_json> tags
  - a fresh surfaceId per card; reusing one updates the old card instead
  - one A2UI message must stay under ~1 MB or GE drops the card silently
"""
import json
import uuid

from google.genai import types as genai_types

A2UI_MIME_TYPE = "application/json+a2ui"
VERSION = "v0.9"
# GE composite catalog: Material + basic + GE custom (Canvas, IFrameSrcdoc, ...).
CATALOG_ID = "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json"
MAX_MESSAGE_BYTES = 800_000  # margin under the ~900 KB that passed in GE

TAG_START = b"<a2a_datapart_json>"
TAG_END = b"</a2a_datapart_json>"


def new_surface_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def create_surface(surface_id: str) -> dict:
    return {"version": VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": CATALOG_ID}}


def update_components(surface_id: str, components: list[dict]) -> dict:
    return {"version": VERSION, "updateComponents": {"surfaceId": surface_id, "components": components}}


def surface(prefix: str, components: list[dict]) -> list[dict]:
    """The two messages that draw one card: createSurface, then its components."""
    sid = new_surface_id(prefix)
    return [create_surface(sid), update_components(sid, components)]


def message_bytes(message: dict) -> int:
    return len(json.dumps(message).encode("utf-8"))


def to_genai_part(a2ui_message: dict) -> genai_types.Part:
    """Wraps one A2UI message so ADK emits it as an A2A DataPart."""
    data_part_json = json.dumps(
        {"kind": "data", "data": a2ui_message, "metadata": {"mimeType": A2UI_MIME_TYPE}}
    )
    return genai_types.Part(
        inline_data=genai_types.Blob(
            mime_type="text/plain",
            data=TAG_START + data_part_json.encode("utf-8") + TAG_END,
        )
    )


def incoming_data(content) -> list:
    """Every structured payload in the user's turn: DataParts (unwrapped) and JSON text parts."""
    found = []
    if not content or not getattr(content, "parts", None):
        return found
    for p in content.parts:
        blob = getattr(p, "inline_data", None)
        if blob and blob.data and blob.data.startswith(TAG_START):
            try:
                found.append(json.loads(blob.data[len(TAG_START):-len(TAG_END)]))
            except (ValueError, UnicodeDecodeError):
                found.append({"unparsed": blob.data[:2000].decode("utf-8", "replace")})
        elif getattr(p, "text", None) and p.text.lstrip().startswith("{"):
            try:
                found.append(json.loads(p.text))
            except ValueError:
                pass
    return found


def extract_action(content) -> dict | None:
    """The v0.9 click payload {"action": {"name", "context", ...}}, if this turn carries one."""
    for data in incoming_data(content):
        inner = data.get("data", data) if isinstance(data, dict) else None
        act = inner.get("action") if isinstance(inner, dict) else None
        if isinstance(act, dict):
            return act
    return None
