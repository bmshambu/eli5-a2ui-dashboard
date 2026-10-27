"""Offline validation of A2UI v0.9 messages against the pinned GE composite catalog.

GE rejects a whole surface when one component has an unknown or missing property, so every
payload is checked here before it ships. Schemas in schemas/v0_9 are copies of:
  - gemini_enterprise_composite_catalog.json  (www.gstatic.com/vertexaisearch/a2ui/v0_9/, via google/A2UI samples)
  - common_types.json, server_to_client.json  (google/A2UI specification/v0_9/json)
"""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .a2ui import MAX_MESSAGE_BYTES, message_bytes

_SCHEMAS = Path(__file__).parent / "schemas" / "v0_9"
_BASE = "https://a2ui.org/specification/v0_9/"


def _load(name: str) -> dict:
    return json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    catalog = _load("gemini_enterprise_composite_catalog.json")
    common = _load("common_types.json")
    envelope = _load("server_to_client.json")
    registry = Registry().with_resources([
        (common["$id"], Resource.from_contents(common)),
        (envelope["$id"], Resource.from_contents(envelope)),
        (catalog["$id"], Resource.from_contents(catalog)),
        # server_to_client.json points at "catalog.json"; bind it to the composite catalog
        (_BASE + "catalog.json", Resource.from_contents(catalog)),
    ])
    return Draft202012Validator(envelope, registry=registry)


def _resolve(ref: str, catalog: dict, common: dict) -> dict:
    doc, _, pointer = ref.partition("#")
    node = common if doc.endswith("common_types.json") else catalog
    for key in pointer.strip("/").split("/"):
        node = node[key]
    return node


def _collect(schema: dict, catalog: dict, common: dict, out: set) -> None:
    if "$ref" in schema:
        _collect(_resolve(schema["$ref"], catalog, common), catalog, common, out)
    out.update(schema.get("properties", {}))
    for sub in schema.get("allOf", []):
        _collect(sub, catalog, common, out)


@lru_cache(maxsize=1)
def _allowed_props() -> dict[str, set[str]]:
    """Property names per component. The catalog leaves Material and GE components open
    (no unevaluatedProperties), but GE's renderer rejects unknown keys, so we close them here."""
    catalog = _load("gemini_enterprise_composite_catalog.json")
    common = _load("common_types.json")
    allowed = {}
    for name, schema in catalog["components"].items():
        props: set[str] = set()
        _collect(schema, catalog, common, props)
        allowed[name] = props
    return allowed


def _unknown_props(message: dict) -> list[str]:
    found = []
    allowed = _allowed_props()
    for i, comp in enumerate(message.get("updateComponents", {}).get("components", [])):
        props = allowed.get(comp.get("component"))
        if props is None:
            continue  # unknown component names are reported by the schema check
        for key in sorted(set(comp) - props):
            found.append(f"updateComponents/components/{i} ({comp.get('id')}): unknown property '{key}'")
    return found


def errors(message: dict) -> list[str]:
    """Problems with one A2UI message; empty when GE should accept it."""
    found = [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:300]}"
        for e in _validator().iter_errors(message)
    ]
    found += _unknown_props(message)
    size = message_bytes(message)
    if size > MAX_MESSAGE_BYTES:
        found.append(f"message is {size} bytes, over the {MAX_MESSAGE_BYTES} byte budget")
    return found


def check(messages: list[dict]) -> None:
    """Raise ValueError listing every problem across a reply's messages."""
    problems = [f"message {i}: {p}" for i, m in enumerate(messages) for p in errors(m)]
    if problems:
        raise ValueError("A2UI validation failed:\n" + "\n".join(problems))
