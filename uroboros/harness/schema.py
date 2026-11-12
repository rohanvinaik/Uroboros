"""Build and check the schemas for declared cassette operations.

A correctness-shaped call is a JSON schema the model fills by SELECTING. This module
builds that schema from a cassette's declared slots plus their sourced vocabularies,
renders the explicit answer block the prompt carries, and tallies a reply against the
slots for deterministic admission (``decisions.admission``).

Slots arrive as plain dicts so every function here is pinnable:

    {"name": str, "kind": str, "role": "value"|"proposal", "vocabulary": list,
     "allow_abstain": bool, "max_items": int, "params": list, "description": str}

``params`` is used only by ``call_objects``: ``[{"name", "type", "pool"}]``, the
argument skeleton of one target, supplied by the caller at run time. A ``code`` slot
also carries ``shape`` (the unit's shape contract, supplied at run time), ``pattern``
(``decisions.code_pattern``) and ``max_chars``; its ``vocabulary`` is the global set of
allowed keywords and operators. A ``contract`` slot carries ``form``, the JSON schema of
a module's contract (``contract.contract_schema``), supplied at run time.
"""

from __future__ import annotations

import json
import re
import string

from . import decisions

# A call argument's declared base type → its JSON-schema type (the type lock).
_JSON_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "list": "array", "dict": "object"}

# Scalar kinds and their JSON-schema types.
_SCALARS = {"bool": "boolean", "int": "integer", "float": "number"}

# A call argument's type when its parameter is untyped: any JSON value (the founder, 2026-10-06).
ANY_JSON = "any"


def _nullable(schema: dict, allow_abstain: bool) -> dict:
    """Admit ``null`` — the typed abstention — beside a schema, when the slot allows it.
    Abstention lives IN the schema because forcing a pick is how mutually exclusive
    labels get co-asserted (a model with no legal empty answer must invent one)."""
    return {"anyOf": [schema, {"type": "null"}]} if allow_abstain else schema


def _json_type(value: object) -> str:
    """The JSON-schema type a pooled value has (``""`` for one JSON cannot carry)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return {str: "string", list: "array", dict: "object"}.get(type(value), "")


def param_schema(param: dict) -> dict:
    """One call argument → its slot: the mined pool as an enum, beside a TYPE-LOCKED
    wildcard for a value the pool cannot name. Openness is admissible here and only
    here because the warrant is downstream — the input is judged by the mutation kill
    it produces, never trusted for its content (THE_DYAD II.4). The lock holds for the
    pool too: a pooled value of another type is left out of the enum, so the pool
    offers values and never widens the hole. An unknown type still locks to a JSON
    scalar, so arity stays fixed by the enclosing object; a ``|None`` type
    (``decisions.param_type``) also admits ``null``. An untyped argument (``ANY_JSON``)
    takes any JSON value (the founder, 2026-10-06): a sample of a parameter the
    signature leaves untyped may be a list or a mapping."""
    raw = str(param.get("type", "")).strip()
    base, nullable = (raw[: -len("|None")], True) if raw.endswith("|None") else (raw, False)
    if base == ANY_JSON:
        types = ["string", "integer", "number", "boolean", "array", "object", "null"]
    elif base in _JSON_TYPES:
        types = [_JSON_TYPES[base], *(["null"] if nullable else [])]
    else:
        types = ["string", "integer", "number", "boolean", *(["null"] if nullable else [])]
    admits = set(types) | ({"integer"} if "number" in types else set())
    pool = [v for v in param.get("pool") or [] if _json_type(v) in admits]
    legs: list = [{"enum": pool}] if pool else []
    legs += [{"type": t} for t in types]
    return {"anyOf": legs} if len(legs) > 1 else legs[0]


def slot_schema(kind: str, vocabulary: list, allow_abstain: bool, max_items: int, params: list, pattern: str = "", max_chars: int = 0, form: dict | None = None) -> dict:
    """One declared slot → its JSON-schema fragment (``{}`` for an unknown kind, which
    the gate has already refused).

    - ``enum``          one value from the closed set (or ``null``)
    - ``enum_list``     up to ``max_items`` values from the closed set; ``[]`` is the abstention
    - ``bool/int/float``  type-locked scalars (or ``null``)
    - ``text``          free text — legal only as a proposal (the gate enforces that)
    - ``call_objects``  up to ``max_items`` argument OBJECTS keyed by parameter name,
                        every parameter required, no extras: arity is structural
    - ``code``          one function body as Python statements (or ``null``), held while it is
                        typed to ``pattern``, the unit's shape contract under the allowed syntax
                        (``decisions.code_pattern``)
    - ``contract``      a module's contract (or ``null``), held to ``form``, the schema of the
                        contract's form; its ``$defs`` belong at the whole schema's root
                        (``cassette_schema``)
    - ``form``          an object (or ``null``) held to ``form``, the schema its caller supplies at
                        run time, as a contract slot is
    """
    k = kind.strip().lower()
    if k == "enum":
        return _nullable({"enum": list(vocabulary)}, allow_abstain)
    if k == "enum_list":
        schema: dict = {"type": "array", "items": {"enum": list(vocabulary)}, "maxItems": max(1, max_items)}
        if not allow_abstain:
            schema["minItems"] = 1
        return schema
    if k in _SCALARS:
        return _nullable({"type": _SCALARS[k]}, allow_abstain)
    if k == "text":
        return {"type": "string"}
    if k == "call_objects":
        names = [str(p.get("name", "")) for p in params]
        call = {
            "type": "object",
            "properties": {str(p.get("name", "")): param_schema(p) for p in params},
            "required": names,
            "additionalProperties": False,
        }
        array: dict = {"type": "array", "items": call, "maxItems": max(1, max_items)}
        if not allow_abstain:
            array["minItems"] = 1
        return array
    if k == "code":
        body: dict = {"type": "string", "minLength": 1, "pattern": pattern or "^$"}
        if max_chars > 0:
            body["maxLength"] = max_chars
        return _nullable(body, allow_abstain)
    if k in decisions.FORM_KINDS:
        held = {key: value for key, value in (form or {}).items() if key != "$defs"}
        return _nullable(held or {"type": "object"}, allow_abstain)
    return {}


def cassette_schema(slots: list) -> dict:
    """The whole typed hole: one object, every slot required, nothing extra. The model
    cannot add, drop or rename a field — it can only choose what goes in each. A
    ``contract`` slot's form keeps its definitions at the root, where its ``$ref``s
    point (``#/$defs/...``)."""
    whole: dict = {
        "type": "object",
        "properties": {
            s["name"]: slot_schema(
                s.get("kind", ""),
                s.get("vocabulary") or [],
                bool(s.get("allow_abstain")),
                int(s.get("max_items") or 1),
                s.get("params") or [],
                str(s.get("pattern") or ""),
                int(s.get("max_chars") or 0),
                s.get("form") if isinstance(s.get("form"), dict) else None,
            )
            for s in slots
        },
        "required": [s["name"] for s in slots],
        "additionalProperties": False,
    }
    defs = {k: v for s in slots if isinstance(s.get("form"), dict) for k, v in (s["form"].get("$defs") or {}).items()}
    if defs:
        whole["$defs"] = defs
    return whole


def answer_block(slots: list) -> str:
    """The explicit answer instructions appended to the prompt: each slot, its allowed
    values, and whether empty is legal. Redundant with the schema on purpose — the
    schema makes invention impossible, the list lets the model choose thoughtfully
    (``setupatlas-architecture`` §4.3). Deterministic: same slots, same text."""
    lines = ["Answer with ONE JSON object. Select only from what is offered; never invent a label."]
    for s in slots:
        name, kind = s["name"], s.get("kind", "")
        vocab = list(s.get("vocabulary") or [])
        desc = f" — {s['description']}" if s.get("description") else ""
        if kind == "enum":
            empty = " (or null if none applies)" if s.get("allow_abstain") else ""
            lines.append(f'- "{name}": exactly one of {vocab}{empty}{desc}')
        elif kind == "enum_list":
            empty = " ([] if none applies)" if s.get("allow_abstain") else ""
            lines.append(f'- "{name}": up to {int(s.get("max_items") or 1)} of {vocab}{empty}{desc}')
        elif kind in _SCALARS:
            empty = " (or null if unknown)" if s.get("allow_abstain") else ""
            lines.append(f'- "{name}": a {_SCALARS[kind]}{empty}{desc}')
        elif kind == "text":
            lines.append(f'- "{name}": your own words — a proposal, not a value{desc}')
        elif kind == "call_objects":
            names = [str(p.get("name", "")) for p in s.get("params") or []]
            empty = " ([] if no input can help)" if s.get("allow_abstain") else ""
            lines.append(f'- "{name}": up to {int(s.get("max_items") or 1)} objects, each setting every one of {names}{empty}{desc}')
        elif kind == "code":
            empty = " (or null if the contract does not determine it)" if s.get("allow_abstain") else ""
            shape = s.get("shape") if isinstance(s.get("shape"), dict) else {}
            narrower = shape.get("syntax")
            tokens = narrower if isinstance(narrower, list) else vocab
            lines.append(f'- "{name}": the function\'s body as Python statements, with no def line and no docstring{empty}{desc}')
            allowed = [
                f"the names {list(shape.get('names') or [])}",
                f"after a dot, the attributes {list(shape.get('attributes') or [])}",
                f"the strings {list(shape.get('strings') or [])}, each in single quotes",
                f"the numbers {list(shape.get('numbers') or [])}",
                f"and the syntax {list(tokens)}",
            ]
            lines.append("  It may contain only: " + "; ".join(allowed) + ". Indent with spaces.")
        elif kind == "contract":
            empty = " (or null if the instructions do not determine a module)" if s.get("allow_abstain") else ""
            lines.append(f'- "{name}": the module\'s contract, in the form the schema holds, every signature and body null{empty}{desc}')
        elif kind == "form":
            empty = " (or null if none can be given)" if s.get("allow_abstain") else ""
            lines.append(f'- "{name}": one object, in the form the schema holds{empty}{desc}')
    return "\n".join(lines)


def template_fields(template: str) -> list:
    """The ``{placeholder}`` names a prompt template uses, in first-use order. The caller
    must supply every one; a missing field is a refusal (``evidence_missing``), never a
    silently blank prompt. Literal braces are written ``{{ }}``."""
    out: list = []
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        return out
    for _text, field, _spec, _conv in parsed:
        if field is not None and field != "" and field not in out:
            out.append(field)
    return out


def _check_value(kind: str, value: object, vocabulary: list, param_names: list, pattern: str = "") -> str:
    """Classify one slot's value: ``filled``, ``abstained``, ``off_vocabulary`` or
    ``violation`` (wrong type / shape). A ``code`` value is re-escaped exactly as the
    engine sees it typed (JSON, non-ASCII kept) and must match its slot's ``pattern``;
    one that does not is off its vocabulary, a contract breach under a decode-time
    pattern, never a retry."""
    if kind == "enum":
        if value is None:
            return "abstained"
        return "filled" if value in vocabulary else "off_vocabulary"
    if kind == "enum_list":
        if not isinstance(value, list):
            return "violation"
        if not value:
            return "abstained"
        return "filled" if all(v in vocabulary for v in value) else "off_vocabulary"
    if kind in _SCALARS:
        if value is None:
            return "abstained"
        if isinstance(value, bool):
            return "filled" if kind == "bool" else "violation"
        if kind == "int":
            return "filled" if isinstance(value, int) else "violation"
        if kind == "float":
            return "filled" if isinstance(value, int | float) else "violation"
        return "violation"
    if kind == "call_objects":
        if not isinstance(value, list):
            return "violation"
        if not value:
            return "abstained"
        ok = all(isinstance(c, dict) and all(n in c for n in param_names) for c in value)
        return "filled" if ok else "violation"
    if kind == "code":
        if value is None:
            return "abstained"
        if not isinstance(value, str) or not value.strip():
            return "violation"
        typed = json.dumps(value, ensure_ascii=False)[1:-1]
        return "filled" if re.fullmatch(pattern, typed) else "off_vocabulary"
    if kind in decisions.FORM_KINDS:
        # Its form is held at decode time; what a schema cannot read is the caller's gate (contract.contract_verdict).
        if value is None:
            return "abstained"
        return "filled" if isinstance(value, dict) else "violation"
    return "violation"


def tally(slots: list, reply: dict) -> dict:
    """Count a reply's VALUE slots for admission: ``missing`` (absent or wrong type),
    ``off_vocabulary``, ``abstained``, ``filled``. Proposal (text) slots are never
    counted — they are not values, whatever they say (Law 8)."""
    counts = {"missing": 0, "off_vocabulary": 0, "abstained": 0, "filled": 0}
    for s in slots:
        kind = s.get("kind", "")
        if kind == "text":
            continue
        name = s["name"]
        if name not in reply:
            counts["missing"] += 1
            continue
        param_names = [str(p.get("name", "")) for p in s.get("params") or []]
        verdict = _check_value(kind, reply[name], list(s.get("vocabulary") or []), param_names, s.get("pattern", ""))
        counts["missing" if verdict == "violation" else verdict] += 1
    return counts
