#!/usr/bin/env python3
"""The ONE model call — typed input synthesis.

Uroboros' cycle owns control flow; the model is a PURE FUNCTION invoked at exactly
one branch: choosing test inputs to reach an un-exercised branch. It fills a typed
skeleton (a JSON array of argument-lists), never emits Python, and cannot produce a
malformed call because it never writes call syntax — this code does, deterministically,
from its JSON. That is what keeps a big model from getting clever: there is no shell,
no loop, nothing to drive. It selects values into a schema; the serpent does the rest.
"""
from __future__ import annotations

import ast
import json
import urllib.request

# ── §6a constants — native Ollama, thinking off, deterministic, capped ──
OLLAMA_URL = "http://localhost:11434/api/chat"
SYNTH_OPTS = {"temperature": 0.1, "num_predict": 400, "num_ctx": 8192}

MAX_CALLS = 6            # per synthesis request
POOL_CAP = 24            # max enum candidates per scalar slot

# ── §3a typed-skeleton, adapted from free-form to input synthesis ──
# The project's production pattern (phase_e_worker._bank_output_schema) is
# `"items": {"enum": closed_vocabulary}`: the model SELECTS, never generates.
# The old schema here (items:{type:array}) let a model pick arity AND values
# freely, so a 1.9GB model echoed the 16 missing-LINE-numbers back as a 16-arg
# call. The fix pins BOTH axes structurally:
#   • Arity — each call is an OBJECT keyed by parameter name, additionalProperties
#     false, every param required. The model cannot add, drop, or reorder args.
#   • Value — each SCALAR slot is `anyOf:[{enum: mined_pool}, {type: annotated}]`.
#     The enum is the closed §3a vocabulary (source literals + boundary edges);
#     the typed wildcard is the user-chosen bounded escape for a killer the pool
#     can't name. Either way the value is TYPE-correct — no line-number tuple can
#     land in a `str` slot. Deterministic code assembles the positional tuple
#     from the object in signature order; the model never writes call syntax.

# annotation string → (json_type, python_caster) for the wildcard leg + edges
_TYPE_MAP = {
    "str": ("string", str), "int": ("integer", int), "float": ("number", float),
    "bool": ("boolean", bool), "list": ("array", list), "dict": ("object", dict),
    "bytes": ("string", str),
}


def _base_type(ann: str) -> str:
    """Reduce an annotation like 'list[str]' | 'dict' | 'str | None' to a base."""
    ann = (ann or "").strip()
    for base in ("str", "int", "float", "bool", "list", "dict", "bytes"):
        if ann == base or ann.startswith(base + "[") or ann.startswith(base + " "):
            return base
    if "list" in ann:
        return "list"
    if "dict" in ann:
        return "dict"
    return ""          # unknown → wildcard-any


def _signature_spec(source: str, func: str) -> list[tuple[str, str]]:
    """[(param_name, base_type)] from the def's annotations. AST, no exec."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            out = []
            for a in node.args.args:
                ann = ast.unparse(a.annotation) if a.annotation else ""
                out.append((a.arg, _base_type(ann)))
            return out
    return []


def _mine_literals(source: str) -> dict:
    """Constants that appear in the function body — the boundary values the
    branches actually test against (the §3a closed vocabulary, mined not guessed)."""
    lits: dict[str, set] = {"str": set(), "int": set(), "float": set()}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lits
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                continue
            if isinstance(v, str) and len(v) <= 128:
                lits["str"].add(v)
            elif isinstance(v, int):
                lits["int"].add(v)
            elif isinstance(v, float):
                lits["float"].add(v)
    return lits


def _mine_mapping_keys(source: str, param: str) -> list[str]:
    """String keys read off a dict param via param.get('k') or param['k'] —
    lets a structured slot offer a candidate shape derived from the code."""
    keys: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == param and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == param and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
    return sorted(keys)


def _scalar_pool(base: str, lits: dict) -> list:
    """enum candidates for a scalar slot: mined literals ∪ type-generic edges,
    incl. boundary ±1 around every mined int so off-by-one mutants have a killer."""
    if base == "str":
        pool = set(lits["str"]) | {"", "a", "0", "x" * 200}
    elif base == "int":
        pool = set(lits["int"]) | {0, 1, -1, 2}
        for n in list(lits["int"]):
            pool |= {n - 1, n + 1}
    elif base == "float":
        pool = set(lits["float"]) | {0.0, 1.0, -1.0} | {float(n) for n in lits["int"]}
    elif base == "bool":
        pool = {True, False}
    else:
        pool = set()
    # deterministic order, capped
    return sorted(pool, key=lambda x: (str(type(x)), str(x)))[:POOL_CAP]


def _slot_schema(name: str, base: str, lits: dict, source: str) -> dict:
    """One parameter → its JSON-schema slot. Scalars: enum ∪ typed-wildcard.
    Mappings/sequences: typed with a mined candidate shape as an enum hint."""
    if base in ("str", "int", "float", "bool"):
        pool = _scalar_pool(base, lits)
        jtype = _TYPE_MAP[base][0]
        legs: list[dict] = []
        if pool:
            legs.append({"enum": pool})
        legs.append({"type": jtype})          # bounded wildcard escape (type-locked)
        return {"anyOf": legs} if len(legs) > 1 else legs[0]
    if base == "dict":
        keys = _mine_mapping_keys(source, name)
        # a shape hint the model may copy-and-tweak, plus a free typed object
        hint = {k: None for k in keys}
        legs = [{"type": "object"}]
        if keys:
            legs.insert(0, {"enum": [{}, hint]})
        return {"anyOf": legs}
    if base == "list":
        return {"anyOf": [{"enum": [[], [1]]}, {"type": "array"}]}
    # unknown annotation → any JSON scalar, arity still locked by the object shape
    return {"anyOf": [{"type": t} for t in ("string", "integer", "number", "boolean")]}


def build_synth_schema(spec: list[tuple[str, str]], lits: dict, source: str) -> dict:
    """The per-target skeleton: `calls` is an array of arity-locked call OBJECTS,
    one required slot per parameter, additionalProperties false."""
    props = {name: _slot_schema(name, base, lits, source) for name, base in spec}
    call_obj = {
        "type": "object",
        "properties": props,
        "required": [n for n, _ in spec],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"calls": {"type": "array", "maxItems": MAX_CALLS, "items": call_obj}},
        "required": ["calls"],
    }


SYNTH_PROMPT = """You choose test INPUTS for one Python function by SELECTING \
values — you never write code, and you never invent structure.

Signature:
    {signature}
Parameters (each call must set every one, by name):
    {param_names}

The function's source (read its `if`, comparison, and `raise` guards — the \
values it tests against are your best choices):
{source}

Detective could not reach these source lines with the inputs it tried:
    {missing_lines}
{focus}
Produce {min_calls}-{max_calls} calls. Each call is a JSON OBJECT mapping every \
parameter name to a value. PREFER the menu of offered values for each parameter \
(they are the boundary values the branches test); only invent a value when no \
menu option can reach an unreached branch. Between them, the calls should hit: a \
normal case, an edge/boundary case, and any case that trips a `raise` or a rare \
branch.

Respond with JSON: {{"calls": [{{"{first_param}": ...}}, ...]}}"""


def _focus_block(requirement: dict | None) -> str:
    """A residual-targeted directive: name the ONE condition to drive. For an
    internal-condition residual the condition names a DERIVED local, so the model
    must reason BACKWARD from it to parameter values — the small reasoning task."""
    if not requirement:
        return ""
    line, cond = requirement.get("line"), requirement.get("condition")
    return (f"\nFOCUS — reach line {line} by choosing parameters whose execution makes "
            f"this condition TRUE:\n    {cond}\nWork backward from it to the inputs that "
            f"produce it. At least one call MUST satisfy it.\n")


def _tuple_literal(args: list) -> str | None:
    """Turn one JSON argument-list into a Python-literal positional tuple.

    The model emitted JSON; this is where — and the ONLY where — call syntax
    gets written, deterministically. `repr` of a JSON-derived value is a valid
    Python literal. A one-arg tuple needs the trailing comma. Reject anything
    that does not round-trip through ast.literal_eval, so a malformed synthesis
    can never reach detective as a broken --input.
    """
    lit = "(" + ", ".join(repr(a) for a in args) + ("," if len(args) == 1 else "") + ")"
    try:
        ast.literal_eval(lit)
    except (ValueError, SyntaxError):
        return None
    return lit


def synthesize_inputs(state: dict, source: str, func: str, model: str,
                      requirement: dict | None = None) -> tuple[list[str], dict]:
    """THE ONE MODEL CALL. Returns (validated --input strings, telemetry).

    The schema is built PER TARGET from the signature: arity-locked call objects
    keyed by parameter, scalar slots constrained to a mined enum ∪ typed wildcard.
    The model selects into that skeleton; this function assembles the positional
    tuple deterministically from the returned object, in signature order. Because
    arity and type are structural, `calls_valid == calls_emitted` by construction
    unless the model returns no JSON at all.

    Telemetry comes straight off the Ollama response — Prism watches Claude Code
    (API models), not this local fleet, so token accounting for the fleet lives here.
    """
    spec = _signature_spec(source, func)
    lits = _mine_literals(source)
    schema = build_synth_schema(spec, lits, source)
    names = [n for n, _ in spec]
    pool_sizes = {n: len(_scalar_pool(b, lits)) for n, b in spec if b in ("str", "int", "float", "bool")}

    param_str = ", ".join(f"{n}: {b or 'any'}" for n, b in spec) or "(none)"
    sig_str = state.get("signature") or f"{func}({param_str})"
    prompt = SYNTH_PROMPT.format(
        signature=sig_str,
        param_names=param_str,
        source=source,
        missing_lines=state.get("missing_lines") or ([requirement["line"]] if requirement else []),
        focus=_focus_block(requirement),
        min_calls=min(3, MAX_CALLS), max_calls=MAX_CALLS,
        first_param=names[0] if names else "arg",
    )
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": schema,         # §3a: full typed skeleton, not just "json"
        "think": False,           # §6a: thinking off or the budget vanishes
        "keep_alive": "10m",      # stay resident across interleaved converge calls —
                                  # else Ollama reloads the model every residual
        "options": SYNTH_OPTS,
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    raw = json.loads(urllib.request.urlopen(req, timeout=180).read())
    content = raw.get("message", {}).get("content", "")

    n_raw, out, arity_ok = 0, [], 0
    parse_ok = True
    try:
        calls = json.loads(content).get("calls", [])
        n_raw = len(calls)
        for c in calls:
            if not isinstance(c, dict):
                continue
            # assemble positional args in SIGNATURE order — the model chose the
            # values by name; call syntax is written here, deterministically, once.
            if all(n in c for n in names):
                arity_ok += 1
                lit = _tuple_literal([c[n] for n in names])
                if lit and lit not in out:
                    out.append(lit)
    except (json.JSONDecodeError, AttributeError):
        parse_ok = False

    tele = {
        "prompt_tokens": raw.get("prompt_eval_count", 0),
        "output_tokens": raw.get("eval_count", 0),
        "eval_ms": round(raw.get("eval_duration", 0) / 1e6, 1),
        "total_ms": round(raw.get("total_duration", 0) / 1e6, 1),
        "load_ms": round(raw.get("load_duration", 0) / 1e6, 1),
        "calls_emitted": n_raw,
        "calls_arity_ok": arity_ok,     # objects with every required param present
        "calls_valid": len(out),        # survived literal-assembly + dedup
        "json_parsed": parse_ok,        # did the model honour the schema at all
        "pool_sizes": pool_sizes,       # enum menu size per scalar param (context)
    }
    return out, tele


def _function_source(file: str, func: str, root: str) -> str:
    """The target function's source text, for the synthesis prompt only."""
    path = f"{root}/{file}"
    try:
        tree = ast.parse(open(path).read())
    except (OSError, SyntaxError):
        return "(source unavailable)"
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return ast.get_source_segment(open(path).read(), node) or "(not found)"
    return "(function not found)"
