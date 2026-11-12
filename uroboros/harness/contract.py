"""uroboros.harness.contract — the translation format: a module written as a JSON contract of its syntax tree.

The founder's design, 2026-10-06. A module is one JSON contract, complete and machine-readable:
- the imports at the top, and the global objects declared beside them; anything below refers back to those;
- then each class and each function, serially. A class is a grouping: its docstring guides a reader and builds
  nothing. An object sits at the level of a function, and records which functions use it.

The contract's writer fills the intent and nothing else: the docstrings (the prose, and the category of each
operation: Wesker's value, boundary, swap, …), each function's intent tests with the operations they cover, by
category and range (a minimum, not a complete list), each object's value as intent, and the global objects, whose
definitions are an iron contract (the founder, 2026-10-06): a call may add a new global it needs, and never
redefines one. A function's intent tests carry both signs (the founder, 2026-10-06): the tests of what it must do,
and the negative specification tests of what it must never do, each of those named in the coverage line under
Wesker's OUTPUT category by the output operator it rules out (``OUTPUT_OPERATORS``). The tests are the contract's
meaning; a docstring is the fallback for what is not provably, definitionally knowable. Every other part is an empty cell that a model fills with Python's own abstract syntax, held to the
full grammar while it generates (``call_schema``). A cell is filled once and is final; every place that refers to it
reads the same cell, so separate calls cannot drift. A function's signature is such a cell, the model's call (the
founder, 2026-10-06): the intent test, written ahead, calls the function, and Detective's run is where the two meet.

A model call is one function. It sees that function's subtree, everything below it and already written, and its
lineage, everything directly above it up to the file's imports and globals; never its siblings (``call_context``).
Calls run bottom-up, the SICP order: the atomic functions first, then the functions that combine them
(``call_order``). A call may add an import or a new global object to the file, write out the full definition of an
object below its function, and define helper functions inside the body it writes.

The file builds itself from the filled contract (``render_module``: the tree, then ``ast.unparse``). Writing is raw
generation: the intent tests run in Detective's process (``render_tests`` lays them out), never here. The moment the
file is written, Detective runs on it in the contract's typed order and the harness follows Detective's own record
(``daemon.drive``), always over both signs, since every contract carries both. Where Detective asks for a test, it
renders the test itself and the harness writes it (``write_asked_test``); where it stops for values, the judgment seat
answers (``daemon-judge``).

Three parts, in this one module:
- the translation format: everything up to the measures (pure: plain data in and out; every verdict a named code);
- the model and what is known about it (``MODELS``: data);
- the player (``play``), the only part that touches a model or a file: it takes inference and turns it into typing,
  then hands the written module to Detective.
  ``python -m uroboros.harness.contract check CONTRACT`` · ``request CONTRACT FUNCTION`` · ``play CONTRACT --out DIR``.

The contract, field by field (``FORMAT``)::

    {"format": "uroboros.contract/1", "path": "<the module's path>", "doc": "<the module docstring>",
     "imports": ["import math", ...],              # written by the contract's writer
     "added_imports": [<Import or ImportFrom>, ...], # cells: written by the model
     "objects": [<object>, ...],                    # the global objects
     "body": [<class or function>, ...],
     "test_imports": ["from x import f", ...],      # the intent tests' own imports
     "detective": ["regime", "audit"]}              # the typed order Detective runs in (the standard form): regime
                                                    # once for the folder, then audit on each function, and on from
                                                    # there by Detective's own flow

    class:    {"class": "<name>", "doc": "...", "bases": ["<expression>", ...], "members": [<function or object>, ...]}
    object:   {"object": "<name>", "doc": "...", "intent": "<the value as a Python expression>",
               "used_by": ["<function qualname>", ...], "value": null | <expr>,         # value: a cell, never a global's
               "added_by": "<function qualname>"}                                        # a global a call added
    function: {"function": "<name>",
               "doc": {"prose": "...", "operations": [{"category": "BOUNDARY", "what": "..."}, ...]},
               "intent_test": {"source": "<pytest source: its intent tests, both signs>",
                               "covers": [{"category": "...", "subject": "...", "operator": "...", "range": "..."},
                                          {"category": "OUTPUT", "subject": "...", "operator": "<an OUTPUT_OPERATORS key>",
                                           "range": "..."}]},                            # OUTPUT: what it must never do
               "objects": [<object>, ...], "functions": [<function>, ...],             # below it
               "signature": null | {"args": <arguments>, "returns": <expr> | null, "decorator_list": [<expr>, ...]},
               "code": null | [<stmt>, ...]}                                             # signature, code: cells

A syntax-tree node is JSON: ``{"_type": "If", "test": {...}, "body": [...], "orelse": [...]}``; a product (``arguments``,
``arg``, ``keyword``, ``alias``, ``withitem``, ``comprehension``, ``match_case``) has no ``_type``; an operator or a
context is its name (``"Add"``, ``"Load"``); a constant is a JSON value, or ``{"ellipsis": true}``, ``{"bytes": "…"}``,
``{"complex": [re, im]}``.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import copy
import hashlib
import json
import keyword
import re
import symtable
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from . import daemon, decisions, detective
from . import model as wire
from .cassette import find_cassette
from .run import invoke

FORMAT = "uroboros.contract/1"
# The typed order Detective runs in, written in this one place (the founder, 2026-10-06: «a standard form that gets
# written, with a typed order to run»): ``regime`` once for the folder, then ``audit`` on each function, whose clean
# record names ``converge`` in Detective's own record (FOUNDING_PROMPTS [21], [22]). ``seat_contract`` sets a contract's
# ``detective`` to it, and ``contract_verdict`` refuses any other value.
DETECTIVE_ORDER = ("regime", "audit")

# ── Python's abstract grammar (3.12, Parser/Python.asdl): every node, each field's kind and quantifier ─────────────────
#
# A field is (name, kind, quantifier). The quantifier is "" (one), "?" (optional), "*" (a list), "+" (a list of one or
# more: a suite Python's own syntax requires to hold a statement, which the abstract grammar writes as ``stmt*``) or
# "*?" (a list whose items may be None: a dict key for ``**`` unpacking, a keyword-only default that is absent). A kind
# is a sum, a product, an enumeration, or a builtin: "identifier", "dotted" (a module path, or "*"), "int", "string",
# "constant".

_FUNCTION = (
    ("name", "identifier", ""),
    ("args", "arguments", ""),
    ("body", "stmt", "+"),
    ("decorator_list", "expr", "*"),
    ("returns", "expr", "?"),
    ("type_comment", "string", "?"),
    ("type_params", "type_param", "*"),
)
_FOR = (("target", "expr", ""), ("iter", "expr", ""), ("body", "stmt", "+"), ("orelse", "stmt", "*"), ("type_comment", "string", "?"))
_WITH = (("items", "withitem", "*"), ("body", "stmt", "+"), ("type_comment", "string", "?"))
_TRY = (("body", "stmt", "+"), ("handlers", "excepthandler", "*"), ("orelse", "stmt", "*"), ("finalbody", "stmt", "*"))
_COMP = (("elt", "expr", ""), ("generators", "comprehension", "*"))

SUMS: dict = {
    "stmt": {
        "FunctionDef": _FUNCTION,
        "AsyncFunctionDef": _FUNCTION,
        "ClassDef": (
            ("name", "identifier", ""),
            ("bases", "expr", "*"),
            ("keywords", "keyword", "*"),
            ("body", "stmt", "+"),
            ("decorator_list", "expr", "*"),
            ("type_params", "type_param", "*"),
        ),
        "Return": (("value", "expr", "?"),),
        "Delete": (("targets", "expr", "*"),),
        "Assign": (("targets", "expr", "*"), ("value", "expr", ""), ("type_comment", "string", "?")),
        "TypeAlias": (("name", "expr", ""), ("type_params", "type_param", "*"), ("value", "expr", "")),
        "AugAssign": (("target", "expr", ""), ("op", "operator", ""), ("value", "expr", "")),
        "AnnAssign": (("target", "expr", ""), ("annotation", "expr", ""), ("value", "expr", "?"), ("simple", "int", "")),
        "For": _FOR,
        "AsyncFor": _FOR,
        "While": (("test", "expr", ""), ("body", "stmt", "+"), ("orelse", "stmt", "*")),
        "If": (("test", "expr", ""), ("body", "stmt", "+"), ("orelse", "stmt", "*")),
        "With": _WITH,
        "AsyncWith": _WITH,
        "Match": (("subject", "expr", ""), ("cases", "match_case", "*")),
        "Raise": (("exc", "expr", "?"), ("cause", "expr", "?")),
        "Try": _TRY,
        "TryStar": _TRY,
        "Assert": (("test", "expr", ""), ("msg", "expr", "?")),
        "Import": (("names", "alias", "*"),),
        "ImportFrom": (("module", "dotted", "?"), ("names", "alias", "*"), ("level", "int", "?")),
        "Global": (("names", "identifier", "*"),),
        "Nonlocal": (("names", "identifier", "*"),),
        "Expr": (("value", "expr", ""),),
        "Pass": (),
        "Break": (),
        "Continue": (),
    },
    "expr": {
        "BoolOp": (("op", "boolop", ""), ("values", "expr", "*")),
        "NamedExpr": (("target", "expr", ""), ("value", "expr", "")),
        "BinOp": (("left", "expr", ""), ("op", "operator", ""), ("right", "expr", "")),
        "UnaryOp": (("op", "unaryop", ""), ("operand", "expr", "")),
        "Lambda": (("args", "arguments", ""), ("body", "expr", "")),
        "IfExp": (("test", "expr", ""), ("body", "expr", ""), ("orelse", "expr", "")),
        "Dict": (("keys", "expr", "*?"), ("values", "expr", "*")),
        "Set": (("elts", "expr", "*"),),
        "ListComp": _COMP,
        "SetComp": _COMP,
        "DictComp": (("key", "expr", ""), ("value", "expr", ""), ("generators", "comprehension", "*")),
        "GeneratorExp": _COMP,
        "Await": (("value", "expr", ""),),
        "Yield": (("value", "expr", "?"),),
        "YieldFrom": (("value", "expr", ""),),
        "Compare": (("left", "expr", ""), ("ops", "cmpop", "*"), ("comparators", "expr", "*")),
        "Call": (("func", "expr", ""), ("args", "expr", "*"), ("keywords", "keyword", "*")),
        "FormattedValue": (("value", "expr", ""), ("conversion", "int", ""), ("format_spec", "expr", "?")),
        "JoinedStr": (("values", "expr", "*"),),
        "Constant": (("value", "constant", ""), ("kind", "string", "?")),
        "Attribute": (("value", "expr", ""), ("attr", "identifier", ""), ("ctx", "expr_context", "")),
        "Subscript": (("value", "expr", ""), ("slice", "expr", ""), ("ctx", "expr_context", "")),
        "Starred": (("value", "expr", ""), ("ctx", "expr_context", "")),
        "Name": (("id", "identifier", ""), ("ctx", "expr_context", "")),
        "List": (("elts", "expr", "*"), ("ctx", "expr_context", "")),
        "Tuple": (("elts", "expr", "*"), ("ctx", "expr_context", "")),
        "Slice": (("lower", "expr", "?"), ("upper", "expr", "?"), ("step", "expr", "?")),
    },
    "pattern": {
        "MatchValue": (("value", "expr", ""),),
        "MatchSingleton": (("value", "constant", ""),),
        "MatchSequence": (("patterns", "pattern", "*"),),
        "MatchMapping": (("keys", "expr", "*"), ("patterns", "pattern", "*"), ("rest", "identifier", "?")),
        "MatchClass": (("cls", "expr", ""), ("patterns", "pattern", "*"), ("kwd_attrs", "identifier", "*"), ("kwd_patterns", "pattern", "*")),
        "MatchStar": (("name", "identifier", "?"),),
        "MatchAs": (("pattern", "pattern", "?"), ("name", "identifier", "?")),
        "MatchOr": (("patterns", "pattern", "*"),),
    },
    "type_param": {
        "TypeVar": (("name", "identifier", ""), ("bound", "expr", "?")),
        "ParamSpec": (("name", "identifier", ""),),
        "TypeVarTuple": (("name", "identifier", ""),),
    },
    "excepthandler": {
        "ExceptHandler": (("type", "expr", "?"), ("name", "identifier", "?"), ("body", "stmt", "+")),
    },
}

PRODUCTS: dict = {
    "arguments": (
        ("posonlyargs", "arg", "*"),
        ("args", "arg", "*"),
        ("vararg", "arg", "?"),
        ("kwonlyargs", "arg", "*"),
        ("kw_defaults", "expr", "*?"),
        ("kwarg", "arg", "?"),
        ("defaults", "expr", "*"),
    ),
    "arg": (("arg", "identifier", ""), ("annotation", "expr", "?"), ("type_comment", "string", "?")),
    "keyword": (("arg", "identifier", "?"), ("value", "expr", "")),
    "alias": (("name", "dotted", ""), ("asname", "identifier", "?")),
    "withitem": (("context_expr", "expr", ""), ("optional_vars", "expr", "?")),
    "comprehension": (("target", "expr", ""), ("iter", "expr", ""), ("ifs", "expr", "*"), ("is_async", "int", "")),
    "match_case": (("pattern", "pattern", ""), ("guard", "expr", "?"), ("body", "stmt", "+")),
}

ENUMS: dict = {
    "expr_context": ("Load", "Store", "Del"),
    "boolop": ("And", "Or"),
    "operator": ("Add", "Sub", "Mult", "MatMult", "Div", "Mod", "Pow", "LShift", "RShift", "BitOr", "BitXor", "BitAnd", "FloorDiv"),
    "unaryop": ("Invert", "Not", "UAdd", "USub"),
    "cmpop": ("Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"),
}

# Wesker's mutation categories, and the nodes each category's mutator visits (Wesker/engine.py, MutationCategory and
# the _*Mutator classes, at 9e5023c). The docstring's operation categories and the coverage lines use these names; a
# category from elsewhere is allowed, and is not counted by ``operations``.
WESKER: dict = {
    "VALUE": ("Constant",),
    "BOUNDARY": ("Compare",),
    "SWAP": ("Call",),
    "STATE": ("Assign", "AnnAssign", "AugAssign", "Return", "Break", "Continue"),
    "TYPE": ("Call",),
    "EXCEPTION": ("Raise", "ExceptHandler"),
    "OUTPUT": ("FunctionDef", "Return"),
    "ARITHMETIC": ("BinOp", "UnaryOp", "AugAssign"),
    "LOGICAL": ("BoolOp", "UnaryOp"),
    "STMT": ("Expr", "Assign", "AugAssign", "AnnAssign"),
    "DATAFLOW": ("Name",),
}

# The negative specification's vocabulary: Wesker's OUTPUT category, the output operators μ⁻ of Detective's
# negative-specification paper (docs/theory/NEGATIVE_SPECIFICATION.md, Def. 11.8), each with the MUST-NOT it rules out,
# in the paper's words. A coverage line's OUTPUT entry names one: the negative half of the intent tests, «a positive
# and negative intent contract for what it should and should not do» (the founder, 2026-10-06). The paper's family is
# the design; Wesker builds, and Detective's two-sign run measures, nine of these keys as twelve sub-modes (traced
# 2026-10-06): none, const and id always (``return_none``, ``return_const``, ``return_identity``); neg, abs, NaN, empty
# and perm (``return_reorder``) where every observed return type admits them; yield as four sub-modes on a generator.
# A negative test naming any other key is checked by its own run alone. All 38 are allowed (the founder, 2026-10-06),
# and the 29 Detective does not yet measure are registered as improvements to it, by family (Detective #116–#123).
OUTPUT_OPERATORS: dict = {
    "none": "the output must exist",
    "const": "the output must depend on the input",
    "id": "the output must be a non-trivial transform of its input, not a passthrough",
    "zero-of": "the output is not trivially the type's zero",
    "type": "the output type is load-bearing",
    "stale": "the output is a pure function of this call, carrying no leaked state or accidental memoization",
    "not": "the boolean is load-bearing",
    "true": "the predicate must not be constantly true",
    "false": "the predicate must not be constantly false",
    "neg": "the sign is load-bearing",
    "abs": "the output may legitimately be negative",
    "±1": "the exact integer or boundary value",
    "±ε": "the exact real value",
    "scale": "the magnitude and units are load-bearing",
    "round": "the precision is load-bearing",
    "NaN": "the output must be a finite number",
    "inf": "the output must be a finite number",
    "0": "the output must be non-zero where that is an invariant",
    "empty": "the output is non-empty on non-empty input",
    "trunc": "the full content is load-bearing",
    "case": "the case is load-bearing",
    "ws": "the whitespace is load-bearing",
    "rev": "the character order is load-bearing",
    "perm": "the order is load-bearing",
    "drop": "the cardinality and every element are load-bearing",
    "dup": "the multiplicity is load-bearing",
    "singleton": "the cardinality is load-bearing",
    "ctype": "the container's mutability, order and uniqueness contract is load-bearing",
    "dict": "the key/value binding and each field's presence are load-bearing",
    "field": "each field is load-bearing",
    "class": "the exact class is load-bearing",
    "adj": "the exact position in the order is load-bearing",
    "clamp": "a range invariant",
    "sortbreak": "sortedness is load-bearing",
    "yield": "the yielded sequence is the output",
    "raise": "the raise-versus-return contract",
    "state-noop": "the side effect is the output",
    "state-extra": "no unintended side effects",
}

# The statements a body may not hold: an import belongs to the file's imports, written once at the top.
BODY_EXCLUDES = ("Import", "ImportFrom")

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_DOTTED = rf"\*|{_IDENT}(?:\.{_IDENT})*"


def grammar_verdict() -> str:
    """Does the grammar above match the running interpreter's syntax tree (pure)? ``ok`` or ``mismatch:<node>``."""
    tables = [ctors for ctors in SUMS.values()] + [PRODUCTS]
    for table in tables:
        for name, fields in table.items():
            if tuple(getattr(getattr(ast, name, None), "_fields", ("?",))) != tuple(f for f, _k, _q in fields):
                return f"mismatch:{name}"
    for name in (n for names in ENUMS.values() for n in names):
        if not isinstance(getattr(ast, name, None), type):
            return f"mismatch:{name}"
    return "ok"


# ── The grammar as a JSON schema: what the engine holds the model to while it generates ──────────────────────────────

_CONSTANT_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
        {"type": "object", "properties": {"ellipsis": {"const": True}}, "required": ["ellipsis"], "additionalProperties": False},
        {"type": "object", "properties": {"bytes": {"type": "string"}}, "required": ["bytes"], "additionalProperties": False},
        {"type": "object", "properties": {"complex": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}}, "required": ["complex"], "additionalProperties": False},
    ]
}


def _kind_schema(kind: str) -> dict:
    if kind in SUMS or kind in PRODUCTS or kind == "constant":
        return {"$ref": f"#/$defs/{kind}"}
    if kind in ENUMS:
        return {"enum": list(ENUMS[kind])}
    if kind == "identifier":
        return {"type": "string", "pattern": f"^{_IDENT}$"}
    if kind == "dotted":
        return {"type": "string", "pattern": f"^(?:{_DOTTED})$"}
    if kind == "int":
        return {"type": "integer"}
    return {"type": "string"}


def _field_schema(kind: str, quantifier: str) -> dict:
    one = _kind_schema(kind)
    if quantifier == "*":
        return {"type": "array", "items": one}
    if quantifier == "+":
        return {"type": "array", "items": one, "minItems": 1}
    if quantifier == "*?":
        return {"type": "array", "items": {"anyOf": [one, {"type": "null"}]}}
    if quantifier == "?":
        return {"anyOf": [one, {"type": "null"}]}
    return one


def _node_schema(name: str, fields: tuple, tagged: bool) -> dict:
    properties: dict = {"_type": {"const": name}} if tagged else {}
    properties.update({f: _field_schema(k, q) for f, k, q in fields})
    required = (["_type"] if tagged else []) + [f for f, _k, q in fields if q in ("", "+")]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def grammar_defs(exclude: tuple = ()) -> dict:
    """The full grammar as JSON-schema ``$defs`` (pure). Every constructor has a definition; a sum offers every
    constructor except those in ``exclude`` (``BODY_EXCLUDES`` keeps imports out of bodies)."""
    defs: dict = {"constant": _CONSTANT_SCHEMA}
    for sum_name, ctors in SUMS.items():
        defs[sum_name] = {"anyOf": [{"$ref": f"#/$defs/{n}"} for n in ctors if n not in exclude]}
        for name, fields in ctors.items():
            defs[name] = _node_schema(name, fields, tagged=True)
    for name, fields in PRODUCTS.items():
        defs[name] = _node_schema(name, fields, tagged=False)
    return defs


# ── JSON to syntax tree ──────────────────────────────────────────────────────────────────────────────────────────────


class _Bad(Exception):
    """A JSON value that is not the grammar's, at a path."""


def _constant(value: object, path: str) -> object:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, dict):
        if value == {"ellipsis": True}:
            return ...
        if set(value) == {"bytes"} and isinstance(value["bytes"], str):
            try:
                return value["bytes"].encode("latin-1")
            except UnicodeEncodeError:
                raise _Bad(path) from None
        pair = value.get("complex") if set(value) == {"complex"} else None
        if isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, int | float) and not isinstance(x, bool) for x in pair):
            return complex(pair[0], pair[1])
    raise _Bad(path)


def _make(name: str, fields: tuple, value: dict, path: str, exclude: tuple) -> ast.AST:
    unknown = sorted(set(value) - {f for f, _k, _q in fields} - {"_type"})
    if unknown:
        raise _Bad(f"{path}.{unknown[0]}")
    kwargs: dict = {}
    for field, kind, quantifier in fields:
        if field in value:
            kwargs[field] = _build(value[field], kind, quantifier, f"{path}.{field}", exclude)
        elif quantifier in ("", "+"):
            raise _Bad(f"{path}.{field}")
        else:
            kwargs[field] = [] if quantifier in ("*", "*?") else None
    return getattr(ast, name)(**kwargs)


def _build(value: object, kind: str, quantifier: str, path: str, exclude: tuple) -> object:
    if quantifier in ("*", "*?", "+"):
        if not isinstance(value, list) or (quantifier == "+" and not value):
            raise _Bad(path)
        return [None if (quantifier == "*?" and v is None) else _build(v, kind, "", f"{path}[{i}]", exclude) for i, v in enumerate(value)]
    if quantifier == "?" and value is None:
        return None
    if kind in SUMS:
        if not isinstance(value, dict):
            raise _Bad(path)
        name = value.get("_type")
        if not isinstance(name, str) or name not in SUMS[kind] or (kind == "stmt" and name in exclude):
            raise _Bad(path)
        return _make(name, SUMS[kind][name], value, path, exclude)
    if kind in PRODUCTS:
        if not isinstance(value, dict) or "_type" in value:
            raise _Bad(path)
        return _make(kind, PRODUCTS[kind], value, path, exclude)
    if kind in ENUMS:
        if value not in ENUMS[kind]:
            raise _Bad(path)
        return getattr(ast, str(value))()
    if kind in ("identifier", "dotted"):
        if not isinstance(value, str) or not re.fullmatch(_IDENT if kind == "identifier" else _DOTTED, value):
            raise _Bad(path)
        if any(keyword.iskeyword(part) for part in value.split(".")):
            raise _Bad(path)
        return value
    if kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise _Bad(path)
        return value
    if kind == "string":
        if not isinstance(value, str):
            raise _Bad(path)
        return value
    if kind == "constant":
        return _constant(value, path)
    raise _Bad(path)


def ast_from_json(value: object, kind: str, quantifier: str = "", exclude: tuple = ()) -> tuple:
    """A JSON value as a syntax-tree node (or a list of them) of ``kind`` (pure). Returns ``("ok", node)`` or
    ``("bad:<path>", None)``, the path naming the first field that is not the grammar's. ``exclude`` names statements
    that may not appear (``BODY_EXCLUDES``)."""
    try:
        return "ok", _build(value, kind, quantifier, "$", exclude)
    except _Bad as exc:
        return f"bad:{exc}", None


def _constant_json(value: object, path: str) -> object:
    """A constant as the format writes it, the inverse of ``_constant``."""
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if value is ...:
        return {"ellipsis": True}
    if isinstance(value, bytes):
        return {"bytes": value.decode("latin-1")}
    if isinstance(value, complex):
        return {"complex": [value.real, value.imag]}
    raise _Bad(path)


def _json(node: object, path: str) -> object:
    if isinstance(node, list):
        return [_json(n, f"{path}[{i}]") for i, n in enumerate(node)]
    if isinstance(node, tuple(getattr(ast, name) for name in ENUMS)):
        return type(node).__name__
    if not isinstance(node, ast.AST):
        return node
    name = type(node).__name__
    fields = PRODUCTS.get(name) or next((nodes[name] for nodes in SUMS.values() if name in nodes), None)
    if fields is None:
        raise _Bad(path)
    out: dict = {} if name in PRODUCTS else {"_type": name}
    for field, kind, quantifier in fields:
        value = getattr(node, field, None)
        if kind == "constant":
            out[field] = _constant_json(value, f"{path}.{field}")
        elif not ((quantifier == "?" and value is None) or (quantifier in ("*", "*?") and value == [])):
            out[field] = _json(value, f"{path}.{field}")
    return out


def json_from_ast(node: object) -> tuple:
    """A syntax-tree node (or a list of them) as the contract's JSON (pure): the inverse of ``ast_from_json``, so a
    node the grammar holds comes back as it went. Each field is the grammar's own, left out where its value is the one
    an absent field takes; an operator or a context is its name; a constant is a JSON value, or ``{"ellipsis": true}``,
    ``{"bytes": "…"}``, ``{"complex": [re, im]}``. Returns ``("ok", value)`` or ``("bad:<path>", None)``, the path
    naming the first node or constant the grammar does not hold."""
    try:
        return "ok", _json(node, "$")
    except _Bad as exc:
        return f"bad:{exc}", None


# ── The contract as a tree of elements ───────────────────────────────────────────────────────────────────────────────


def _kind(node: object) -> str:
    if isinstance(node, dict):
        for kind in ("class", "function", "object"):
            if isinstance(node.get(kind), str):
                return kind
    return ""


def _members(node: dict, kind: str) -> list:
    """An element's own elements, in order: a module's objects then body, a class's members, a function's objects
    then functions."""
    if kind == "module":
        return list(node.get("objects") or []) + list(node.get("body") or [])
    if kind == "class":
        return list(node.get("members") or [])
    if kind == "function":
        return list(node.get("objects") or []) + list(node.get("functions") or [])
    return []


def elements(contract: dict) -> list:
    """Every element of the contract as ``(qualname, kind, node, lineage)``, parents before children, in the
    contract's serial order (pure). ``lineage`` is the tuple of the ancestors' qualnames, outermost first."""
    out: list = []

    def visit(node: dict, kind: str, prefix: str, lineage: tuple) -> None:
        for child in _members(node, kind):
            child_kind = _kind(child)
            if not child_kind:
                continue
            qualname = prefix + child[child_kind]
            out.append((qualname, child_kind, child, lineage))
            visit(child, child_kind, qualname + ".", lineage + (qualname,))

    if isinstance(contract, dict):
        visit(contract, "module", "", ())
    return out


def _find(contract: dict, qualname: str) -> tuple:
    for found, kind, node, lineage in elements(contract):
        if found == qualname:
            return node, kind, lineage
    return None, "", ()


def call_order(contract: dict) -> list:
    """The qualnames of the model calls, bottom-up (pure): every function after the functions below it, and in the
    contract's serial order otherwise."""
    order: list = []

    def visit(node: dict, kind: str, prefix: str) -> None:
        for child in _members(node, kind):
            child_kind = _kind(child)
            if child_kind == "class":
                visit(child, "class", prefix + child["class"] + ".")
            elif child_kind == "function":
                qualname = prefix + child["function"]
                visit(child, "function", qualname + ".")
                order.append(qualname)

    if isinstance(contract, dict):
        visit(contract, "module", "")
    return order


def callees(contract: dict) -> dict:
    """Each function that combines others, by qualname, with the names of the functions below it, in the contract's
    serial order (pure): what the contract's nesting states."""
    found: dict = {}
    for qualname, kind, node, _lineage in elements(contract):
        below = [child["function"] for child in _members(node, kind) if _kind(child) == "function"] if kind == "function" else []
        if below:
            found[qualname] = below
    return found


def detective_targets(contract: dict) -> list:
    """Each function in the call order, bottom-up, with the name Detective knows it by in the written file (pure):
    ``[[qualname, "<path>::<name>"], ...]``. The file is written flat (``_flattened``): a function below another sits
    beside it, and one below a class's member is a method of that class."""
    found = {q: (kind, node, lineage) for q, kind, node, lineage in elements(contract)}
    out: list = []
    for qualname in call_order(contract):
        _kind, node, lineage = found[qualname]
        owner = next((found[q][1]["class"] + "." for q in lineage if found[q][0] == "class"), "")
        out.append([qualname, f"{contract['path']}::{owner}{node['function']}"])
    return out


def _open_objects(contract: dict, qualname: str) -> list:
    """The object cells a call on ``qualname`` may still fill: the objects in the function's subtree, each while its
    value is empty. A global object is never open: the contract's writer defines it, an iron contract (the founder,
    2026-10-06); a call may add a new global it needs (``new_objects``), and never redefines one."""
    return [q for q, kind, node, _lineage in elements(contract) if kind == "object" and node.get("value") is None and q.startswith(qualname + ".")]


# ── Verdicts on the contract ─────────────────────────────────────────────────────────────────────────────────────────


def _parse_import(line: object) -> ast.stmt | None:
    if not isinstance(line, str):
        return None
    try:
        body = ast.parse(line).body
    except SyntaxError:
        return None
    return body[0] if len(body) == 1 and isinstance(body[0], ast.Import | ast.ImportFrom) else None


def _parses_as_expression(text: object) -> bool:
    if not isinstance(text, str):
        return False
    try:
        ast.parse(text, mode="eval")
    except SyntaxError:
        return False
    return True


def _signature_verdict(signature: object) -> str:
    """A filled signature cell: ``ok``, ``bad:<field>``, or ``bad:args:repeated`` when two parameters share a name,
    which Python refuses («duplicate argument»)."""
    if not isinstance(signature, dict) or set(signature) - {"args", "returns", "decorator_list"} or "args" not in signature:
        return "bad:signature"
    args = ast_from_json(signature["args"], "arguments")[1]
    if not isinstance(args, ast.arguments):
        return "bad:args"
    for field, kind, quantifier in (("returns", "expr", "?"), ("decorator_list", "expr", "*")):
        if field in signature and ast_from_json(signature[field], kind, quantifier)[0] != "ok":
            return f"bad:{field}"
    names = [a.arg for a in [*args.posonlyargs, *args.args, args.vararg, *args.kwonlyargs, args.kwarg] if a is not None]
    return "bad:args:repeated" if len(names) != len(set(names)) else "ok"


_CONTAINERS = {"module": ("object", "class", "function"), "class": ("function", "object"), "function": ("object", "function")}


def _containment_verdict(node: dict, kind: str, prefix: str) -> str:
    """Every element sits where the format allows it: ``ok`` or ``bad_element:<where>``."""
    containers = ("objects", "body") if kind == "module" else ("members",) if kind == "class" else ("objects", "functions")
    for container in containers:
        items = node.get(container, [])
        if not isinstance(items, list):
            return f"bad_element:{prefix}{container}"
        allowed = ("object",) if container == "objects" else ("function",) if container == "functions" else _CONTAINERS[kind]
        for i, item in enumerate(items):
            item_kind = _kind(item)
            if item_kind not in allowed or (kind == "module" and container == "body" and item_kind == "object"):
                return f"bad_element:{prefix}{container}[{i}]"
            status = _containment_verdict(item, item_kind, f"{prefix}{item[item_kind]}.") if item_kind != "object" else "ok"
            if status != "ok":
                return status
    return "ok"


def _object_verdict(node: dict, qualname: str) -> str:
    used_by = node.get("used_by", [])
    if not isinstance(node.get("doc", ""), str) or not isinstance(used_by, list) or not all(isinstance(u, str) for u in used_by):
        return f"bad_element:{qualname}"
    if not _parses_as_expression(node.get("intent")):
        return f"bad_intent:{qualname}"
    if node.get("value") is not None and ast_from_json(node["value"], "expr")[0] != "ok":
        return f"bad_cell:{qualname}"
    return "ok"


def _class_verdict(node: dict, qualname: str) -> str:
    bases = node.get("bases", [])
    if not isinstance(node.get("doc", ""), str) or not isinstance(bases, list) or not all(_parses_as_expression(b) for b in bases):
        return f"bad_element:{qualname}"
    return "ok"


def _function_verdict(node: dict, qualname: str) -> str:
    doc = node.get("doc")
    operations = doc.get("operations") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or not isinstance(doc.get("prose"), str) or not doc["prose"].strip():
        return f"missing_doc:{qualname}"
    if (
        not isinstance(operations, list)
        or not operations
        or not all(isinstance(o, dict) and isinstance(o.get("category"), str) and o["category"] and isinstance(o.get("what", ""), str) for o in operations)
    ):
        return f"missing_doc:{qualname}"
    test = node.get("intent_test")
    if not isinstance(test, dict) or not isinstance(test.get("source"), str) or not test["source"].strip():
        return f"missing_intent_test:{qualname}"
    try:
        ast.parse(test["source"])
    except SyntaxError:
        return f"bad_test:{qualname}"
    covers = test.get("covers")
    if not isinstance(covers, list) or not all(isinstance(c, dict) and isinstance(c.get("category"), str) and c["category"] for c in covers):
        return f"missing_coverage:{qualname}"
    if not any(c["category"] != "OUTPUT" for c in covers):
        return f"missing_coverage:{qualname}"
    negative = [c for c in covers if c["category"] == "OUTPUT"]
    if not negative:
        return f"missing_negative:{qualname}"
    if not all(c.get("operator") in OUTPUT_OPERATORS for c in negative):
        return f"unknown_output_operator:{qualname}"
    if node.get("signature") is not None and _signature_verdict(node["signature"]) != "ok":
        return f"bad_cell:{qualname}"
    if node.get("code") is not None and ast_from_json(node["code"], "stmt", "*", BODY_EXCLUDES)[0] != "ok":
        return f"bad_cell:{qualname}"
    if "ast_template" in node:
        status, cells = decisions.expand_ast_template(node["ast_template"], None)
        if status != "expanded" or _signature_verdict(cells["signature"]) != "ok":
            return f"bad_template:{qualname}"
        status, stmts = ast_from_json(cells["code"], "stmt", "*", BODY_EXCLUDES)
        if status != "ok" or not stmts or (node.get("signature") is None) != (node.get("code") is None):
            return f"bad_template:{qualname}"
    return "ok"


def _emitted_names(members: list) -> list:
    """The names a class's members, or a module's body, put in one scope: objects, classes, and every function with
    the functions below it (they are written flat, beside the function that combines them)."""
    names: list = []
    for member in members:
        kind = _kind(member)
        if kind == "function":
            names += [member["function"]] + _emitted_names(member.get("functions") or [])
        elif kind:
            names.append(member[kind])
    return names


def contract_digest(source: dict) -> str:
    """The identity recorded when admitting this exact filled contract."""
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def contract_verdict(contract: object) -> str:
    """Is this a contract the harness can play (pure)? ``ok``, or the first defect:
    ``invalid:<field>`` · ``bad_import:<index>`` · ``bad_element:<where>`` · ``bad_name:<qualname>`` ·
    ``duplicate:<qualname>`` · ``name_clash:<name>`` · ``missing_doc:<qualname>`` · ``missing_intent_test:<qualname>`` ·
    ``bad_test:<qualname>`` · ``missing_coverage:<qualname>`` (no coverage of what it must do) ·
    ``missing_negative:<qualname>`` (no OUTPUT coverage: nothing of what it must never do) ·
    ``unknown_output_operator:<qualname>`` · ``bad_intent:<qualname>`` · ``bad_cell:<qualname>`` ·
    ``unknown_user:<qualname>``."""
    if not isinstance(contract, dict) or contract.get("format") not in (FORMAT, "squishos.contract/1"):
        return "invalid:format"
    for key, kind in (("path", str), ("doc", str), ("imports", list), ("objects", list), ("body", list)):
        if not isinstance(contract.get(key), kind):
            return f"invalid:{key}"
    path = PurePosixPath(contract["path"])
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        return "invalid:path"
    if "revises" in contract and (not isinstance(contract["revises"], str) or not re.fullmatch(r"[a-f0-9]{64}", contract["revises"])):
        return "invalid:revises"
    if contract.get("detective") != list(DETECTIVE_ORDER):
        return "invalid:detective"
    for key in ("test_imports", "added_imports"):
        if not isinstance(contract.get(key, []), list):
            return f"invalid:{key}"
    for i, line in enumerate(contract["imports"]):
        if _parse_import(line) is None:
            return f"bad_import:{i}"
    for i, node in enumerate(contract.get("added_imports", [])):
        status, imp = ast_from_json(node, "stmt")
        if status != "ok" or not isinstance(imp, ast.Import | ast.ImportFrom):
            return f"invalid:added_imports[{i}]"
    status = _containment_verdict(contract, "module", "")
    if status != "ok":
        return status
    found = elements(contract)
    seen: set = set()
    functions = {q for q, kind, _n, _l in found if kind == "function"}
    for qualname, kind, node, _lineage in found:
        if not re.fullmatch(_IDENT, node[kind]):
            return f"bad_name:{qualname}"
        if qualname in seen:
            return f"duplicate:{qualname}"
        seen.add(qualname)
        verdict = {"object": _object_verdict, "class": _class_verdict, "function": _function_verdict}[kind](node, qualname)
        if verdict != "ok":
            return verdict
        if kind == "object" and any(u not in functions for u in node.get("used_by", [])):
            return f"unknown_user:{qualname}"
    scopes = [list(contract["objects"]) + list(contract["body"])] + [n.get("members") or [] for _q, kind, n, _l in found if kind == "class"]
    for scope in scopes:
        names = _emitted_names(scope)
        clash = next((n for n in names if names.count(n) > 1), None)
        if clash is not None:
            return f"name_clash:{clash}"
    return "ok"


def contract_schema() -> dict:
    """The JSON schema of the contract writer's reply, the seat's form (pure; SEPARATION_OF_AUTHORITY.md R2): the
    obligations it writes (the module's docstring; each function's docstring at two levels, its prose and each
    operation's category; each function's intent tests of both signs; each object's value as intent; the globals) and
    the empty structure, every signature and body ``null`` for the code writer. Two parts are asked as the writer knows
    them and built by the harness (the founder, 2026-10-07, ``FOUNDING_PROMPTS.md`` separation [7]): each function sits
    at its scope's top and names in ``combines`` the functions it combines, and its coverage is two lists, ``covers``
    for what it must do and ``never`` for what it must never do (OUTPUT, by output operator), each at least one line.
    ``seat_contract`` builds the stored contract from a reply; ``contract_verdict`` reads what neither can: an intent
    test that parses, a name defined twice, a user that is no function."""
    ident = {"type": "string", "pattern": f"^{_IDENT}$"}
    text = {"type": "string", "minLength": 1}
    lines = {"type": "array", "items": {"type": "string", "pattern": "^(import|from) "}}
    operation = {"type": "object", "properties": {"category": {"enum": sorted(WESKER)}, "what": text}, "required": ["category", "what"], "additionalProperties": False}
    must = {
        "type": "object",
        "properties": {"category": {"enum": sorted(c for c in WESKER if c != "OUTPUT")}, "subject": text, "operator": {"type": "string"}, "range": text},
        "required": ["category", "subject", "range"],
        "additionalProperties": False,
    }
    never = {
        "type": "object",
        "properties": {"category": {"const": "OUTPUT"}, "subject": text, "operator": {"enum": sorted(OUTPUT_OPERATORS)}, "range": text},
        "required": ["category", "subject", "operator", "range"],
        "additionalProperties": False,
    }
    function = {
        "type": "object",
        "properties": {
            "function": ident,
            "doc": {
                "type": "object",
                "properties": {"prose": text, "operations": {"type": "array", "items": operation, "minItems": 1}},
                "required": ["prose", "operations"],
                "additionalProperties": False,
            },
            "intent_test": {
                "type": "object",
                "properties": {"source": text, "covers": {"type": "array", "items": must, "minItems": 1}, "never": {"type": "array", "items": never, "minItems": 1}},
                "required": ["source", "covers", "never"],
                "additionalProperties": False,
            },
            "objects": {"type": "array", "items": {"$ref": "#/$defs/object"}},
            "combines": {"type": "array", "items": ident},
            "signature": {"type": "null"},
            "code": {"type": "null"},
            "ast_template": {
                "type": "object",
                "properties": {"signature": {"type": "object"}, "code": {"type": "array", "items": {"type": "object"}}, "choices": {"type": "object"}},
                "required": ["signature", "code", "choices"],
                "additionalProperties": False,
            },
        },
        "required": ["function", "doc", "intent_test", "objects", "combines", "signature", "code"],
        "additionalProperties": False,
    }
    obj = {
        "type": "object",
        "properties": {"object": ident, "doc": text, "intent": text, "used_by": {"type": "array", "items": {"type": "string", "pattern": f"^{_IDENT}(\\.{_IDENT})?$"}}, "value": {"type": "null"}},
        "required": ["object", "doc", "intent", "used_by", "value"],
        "additionalProperties": False,
    }
    klass = {
        "type": "object",
        "properties": {
            "class": ident,
            "doc": text,
            "bases": {"type": "array", "items": text},
            "members": {"type": "array", "items": {"anyOf": [{"$ref": "#/$defs/function"}, {"$ref": "#/$defs/object"}]}},
        },
        "required": ["class", "doc", "bases", "members"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "format": {"const": FORMAT},
            "path": {"type": "string", "pattern": f"^{_IDENT}(/{_IDENT})*\\.py$"},
            "doc": text,
            "imports": lines,
            "objects": {"type": "array", "items": {"$ref": "#/$defs/object"}},
            "body": {"type": "array", "items": {"anyOf": [{"$ref": "#/$defs/function"}, {"$ref": "#/$defs/class"}]}, "minItems": 1},
            "test_imports": {**lines, "minItems": 1},
        },
        "required": ["format", "path", "doc", "imports", "objects", "body", "test_imports"],
        "additionalProperties": False,
        "$defs": {"function": function, "object": obj, "class": klass},
    }


def _seat_scope(members: list, scope: str) -> tuple:
    """One scope of a reply in the seat's form (a module's body, a class's members) as the contract nests it:
    ``("ok", members)`` or the first defect with ``[]``."""
    named: dict = {}
    for member in members:
        if _kind(member) == "function":
            if member["function"] in named:
                return f"name_clash:{member['function']}", []
            named[member["function"]] = member
    combined_by: dict = {}
    for name, node in named.items():
        combines = node.get("combines", [])
        if not isinstance(combines, list) or not all(isinstance(child, str) for child in combines):
            return f"bad_element:{scope}{name}.combines", []
        test = node.get("intent_test")
        if isinstance(test, dict) and any(not isinstance(test.get(key, []), list) for key in ("covers", "never")):
            return f"missing_coverage:{scope}{name}", []
        for child in combines:
            if child not in named:
                return f"combines_unknown:{scope}{name}:{child}", []
            if child in combined_by:
                return f"combined_twice:{scope}{child}", []
            combined_by[child] = name
    for name in named:
        seen, current = set(), name
        while current in combined_by:
            if current in seen:
                return f"loop:{scope}{name}", []
            seen.add(current)
            current = combined_by[current]

    def built(name: str) -> dict:
        node = named[name]
        given = node.get("intent_test")
        test: dict = given if isinstance(given, dict) else {}
        covers = list(test.get("covers") or []) + list(test.get("never") or [])
        out = {key: value for key, value in node.items() if key != "combines"}
        return {**out, "intent_test": {"source": test.get("source"), "covers": covers}, "functions": [built(child) for child in node.get("combines") or []]}

    out: list = []
    for member in members:
        kind = _kind(member)
        if kind == "function" and member["function"] not in combined_by:
            out.append(built(member["function"]))
        elif kind == "class":
            children = member.get("members", [])
            if not isinstance(children, list):
                return f"bad_element:{scope}{member['class']}.members", []
            status, nested = _seat_scope(children, f"{scope}{member['class']}.")
            if status != "ok":
                return status, []
            out.append({**member, "members": nested})
        elif kind != "function":
            out.append(member)
    return "ok", out


def seat_contract(reply: object) -> tuple:
    """The stored contract built from a reply in the seat's form (pure; the founder, 2026-10-07, separation [7]): each
    function placed below the one that names it in ``combines``, its two coverage lists, what it must do and what
    it must never do, merged into one, and the contract's ``detective`` set to the typed order (``DETECTIVE_ORDER``),
    which the seat's form does not carry. ``("ok", contract)``, or the first defect with ``None``:
    ``combines_unknown:<qualname>:<name>`` (a name its own scope does not define) · ``combined_twice:<qualname>`` (a
    function two others combine) · ``loop:<qualname>`` (a function that combines itself, directly or through others) ·
    ``name_clash:<name>`` (two functions of one name in one scope). A function named in an object's ``used_by`` as its
    scope knows it (``passes``, ``Class.method``) is named there by the qualname its place gives it
    (``pass_rate.passes``). What it does not read it leaves to ``contract_verdict``."""
    if not isinstance(reply, dict) or not isinstance(reply.get("body"), list):
        return "ok", reply
    status, body = _seat_scope(copy.deepcopy(reply["body"]), "")
    if status != "ok":
        return status, None
    built = {**copy.deepcopy(reply), "body": body, "detective": list(DETECTIVE_ORDER)}
    status = _containment_verdict(built, "module", "")
    if status != "ok":
        return status, None
    where = {seat: qualname for qualname, seat in _seat_names(built).items()}
    for qualname, kind, node, _lineage in elements(built):
        if kind == "object" and "used_by" in node:
            users = node["used_by"]
            if not isinstance(users, list) or not all(isinstance(user, str) for user in users):
                return f"bad_element:{qualname}", None
            node["used_by"] = [where.get(user, user) for user in users]
    return "ok", built


def _seat_names(contract: dict) -> dict:
    """Each function of a stored contract, by qualname, with the name the seat's form gives it: its own name within
    its scope, a class's members or the module, whatever functions it sits below."""
    kinds = {qualname: kind for qualname, kind, _node, _lineage in elements(contract)}
    names: dict = {}
    for qualname, kind, node, lineage in elements(contract):
        if kind == "function":
            classes = [q for q in lineage if kinds.get(q) == "class"]
            names[qualname] = f"{classes[-1]}.{node['function']}" if classes else node["function"]
    return names


def _seat_object(node: dict, names: dict) -> dict:
    used_by = node.get("used_by")
    users = [names.get(user, user) for user in used_by] if isinstance(used_by, list) else []
    return {"object": node["object"], "doc": node.get("doc", ""), "intent": node.get("intent"), "used_by": users, "value": None}


def _flat_scope(members: list, names: dict) -> list:
    """One scope of a stored contract in the seat's form: every function at the scope's top, bottom-up."""
    out: list = []

    def visit(node: dict) -> None:
        below = [child for child in node.get("functions") or [] if _kind(child) == "function"]
        for child in below:
            visit(child)
        given = node.get("intent_test")
        test: dict = given if isinstance(given, dict) else {}
        covers = [c for c in test.get("covers") or [] if isinstance(c, dict)]
        intent = {"source": test.get("source"), "covers": [c for c in covers if c.get("category") != "OUTPUT"], "never": [c for c in covers if c.get("category") == "OUTPUT"]}
        objects = [_seat_object(o, names) for o in node.get("objects") or [] if _kind(o) == "object"]
        flat = {"function": node["function"], "doc": node.get("doc"), "intent_test": intent, "objects": objects, "combines": [child["function"] for child in below], "signature": None, "code": None}
        if "ast_template" in node:
            flat["ast_template"] = copy.deepcopy(node["ast_template"])
        out.append(flat)

    for member in members:
        kind = _kind(member)
        if kind == "function":
            visit(member)
        elif kind == "class":
            out.append({"class": member["class"], "doc": member.get("doc", ""), "bases": list(member.get("bases") or []), "members": _flat_scope(list(member.get("members") or []), names)})
        elif kind == "object":
            out.append(_seat_object(member, names))
    return out


def seat_form(contract: dict) -> dict:
    """A stored contract as the contract writer writes it (pure), the inverse of ``seat_contract``: every function at
    its scope's top, bottom-up, naming the functions below it in ``combines``; its coverage split into ``covers`` and
    ``never``; each object's users named as their scopes know them; every cell empty; and only the contract writer's
    own parts, without the imports and globals a code writer's call added (``added_imports``, ``added_by``) and without
    the typed order (``detective``), which ``seat_contract`` sets."""
    names = _seat_names(contract)
    globals_ = [_seat_object(o, names) for o in contract.get("objects") or [] if _kind(o) == "object" and not o.get("added_by")]
    keys = ("format", "path", "doc", "imports", "test_imports")
    return {**{key: contract.get(key) for key in keys}, "objects": globals_, "body": _flat_scope(list(contract.get("body") or []), names)}


def _entries(contract: dict) -> dict:
    """Each function of a stored contract by the name the seat's form gives it, with its qualname and its entry as the
    contract writer writes it, every cell empty (pure): what a revision compares, function by function."""
    where = {seat: qualname for qualname, seat in _seat_names(contract).items()}
    out: dict = {}

    def scope(members: list, prefix: str) -> None:
        for member in members:
            kind = _kind(member)
            if kind == "function":
                out[prefix + member["function"]] = (where.get(prefix + member["function"]), member)
            elif kind == "class":
                scope(member["members"], f"{prefix}{member['class']}.")

    scope(seat_form(contract)["body"], "")
    return out


# ── Rendering: the filled contract as Python ─────────────────────────────────────────────────────────────────────────


def _docstring(text: str) -> ast.Expr:
    return ast.Expr(value=ast.Constant(value=text, kind=None))


def _indented(text: str, indent: str) -> str:
    """A docstring's text with its continuation lines at the indentation of the body it opens."""
    return "\n".join(indent + line if i and line.strip() else line for i, line in enumerate(text.strip().splitlines()))


def _function_doc(doc: dict, indent: str) -> str:
    """A function's docstring at its indentation: the prose, then each operation's category and what it is."""
    lines = [f"{indent}    {o['category']}: {o.get('what', '')}".rstrip() for o in doc.get("operations") or []]
    prose = _indented(doc.get("prose", ""), indent)
    return prose + (f"\n\n{indent}Operations:\n" + "\n".join(lines) + f"\n{indent}" if lines else "")


def _object_doc(node: dict) -> str:
    users = ", ".join(node.get("used_by") or [])
    return (node.get("doc") or "").strip() + (f"\n\nUsed by: {users}" if users else "")


def _object_stmts(node: dict, qualname: str) -> tuple:
    """An object as its assignment and its docstring: ``("ok", [stmt, …])`` or ``(code, [])``. The value is the
    model's full definition when its cell is filled, else the contract's intent."""
    if node.get("value") is not None:
        status, value = ast_from_json(node["value"], "expr")
        if status != "ok":
            return f"bad_cell:{qualname}", []
    else:
        try:
            value = ast.parse(node.get("intent", ""), mode="eval").body
        except SyntaxError:
            return f"bad_intent:{qualname}", []
    stmts: list = [ast.Assign(targets=[ast.Name(id=node["object"], ctx=ast.Store())], value=value, type_comment=None)]
    doc = _object_doc(node)
    if doc:
        stmts.append(_docstring(doc))
    return "ok", stmts


def _function_def(node: dict, qualname: str, indent: str = "    ") -> tuple:
    """One filled function as a ``FunctionDef``: its docstring, then its own objects, then the model's code.
    ``("ok", node)`` or ``("unfilled:<qualname>" | "bad_cell:<qualname>" | …, None)``. ``indent`` is its body's."""
    signature, code = node.get("signature"), node.get("code")
    if signature is None or code is None:
        return f"unfilled:{qualname}", None
    if _signature_verdict(signature) != "ok":
        return f"bad_cell:{qualname}", None
    args = ast_from_json(signature["args"], "arguments")[1]
    returns = ast_from_json(signature.get("returns"), "expr", "?")[1]
    decorators = ast_from_json(signature.get("decorator_list", []), "expr", "*")[1]
    status, body = ast_from_json(code, "stmt", "*", BODY_EXCLUDES)
    if status != "ok":
        return f"bad_cell:{qualname}", None
    statements: list[ast.stmt] = [_docstring(_function_doc(node["doc"], indent))]
    for obj in node.get("objects") or []:
        status, stmts = _object_stmts(obj, f"{qualname}.{obj['object']}")
        if status != "ok":
            return status, None
        statements += stmts
    statements += list(body or [])
    fn = ast.FunctionDef(
        name=node["function"],
        args=args,
        body=statements,
        decorator_list=decorators,
        returns=returns,
        type_comment=None,
        type_params=[],
    )
    return "ok", fn


def _flattened(node: dict, qualname: str, indent: str = "    ") -> tuple:
    """A function and every function below it, written flat, atomics first: ``("ok", [FunctionDef, …])``."""
    out: list = []
    for child in node.get("functions") or []:
        status, defs = _flattened(child, f"{qualname}.{child['function']}", indent)
        if status != "ok":
            return status, []
        out += defs
    status, fn = _function_def(node, qualname, indent)
    if status != "ok":
        return status, []
    return "ok", out + [fn]


def _class_def(node: dict) -> tuple:
    body: list = [_docstring(_indented(node["doc"], "    "))] if node.get("doc") else []
    for member in node.get("members") or []:
        kind = _kind(member)
        qualname = f"{node['class']}.{member[kind]}"
        status, stmts = _object_stmts(member, qualname) if kind == "object" else _flattened(member, qualname, "        ")
        if status != "ok":
            return status, None
        body += stmts
    bases = [ast.parse(b, mode="eval").body for b in node.get("bases") or []]
    return "ok", ast.ClassDef(name=node["class"], bases=bases, keywords=[], body=body or [ast.Pass()], decorator_list=[], type_params=[])


def _imports(contract: dict) -> list:
    """The file's imports, the contract's then the model's, each once, in order."""
    nodes = [n for n in (_parse_import(line) for line in contract.get("imports") or []) if n is not None]
    nodes += [n for n in (ast_from_json(j, "stmt")[1] for j in contract.get("added_imports") or []) if isinstance(n, ast.Import | ast.ImportFrom)]
    seen: set = set()
    out: list = []
    for node in nodes:
        text = ast.unparse(node)
        if text not in seen:
            seen.add(text)
            out.append(node)
    return out


def _source(node: ast.AST) -> str:
    return ast.unparse(ast.fix_missing_locations(node))


def render_module(contract: dict) -> tuple:
    """The filled contract as one Python module, built from its syntax tree (pure). ``("ok", source)``, or a
    contract verdict, ``unfilled:<qualname>``, ``bad_cell:<qualname>`` or ``syntax:<message>`` with ``""``. The file
    is compiled before it is returned: it builds itself, or it is refused."""
    verdict = contract_verdict(contract)
    if verdict != "ok":
        return verdict, ""
    body: list = ([_docstring(contract["doc"])] if contract["doc"] else []) + _imports(contract)
    for obj in contract["objects"]:
        status, stmts = _object_stmts(obj, obj["object"])
        if status != "ok":
            return status, ""
        body += stmts
    for item in contract["body"]:
        kind = _kind(item)
        status, made = _class_def(item) if kind == "class" else _flattened(item, item["function"])
        if status != "ok":
            return status, ""
        body += [made] if kind == "class" else made
    try:
        source = _source(ast.Module(body=body, type_ignores=[])) + "\n"
        compile(source, contract["path"] or "<contract>", "exec")
    except (SyntaxError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        return f"syntax:{type(exc).__name__}: {exc}", ""
    return "ok", source


def render_tests(contract: dict) -> tuple:
    """Every function's intent tests, both signs, in call order, under the tests' own imports (pure): ``("ok",
    source)`` or a contract verdict, or ``syntax:<message>``, with ``""``. They run in Detective's process, never
    here."""
    verdict = contract_verdict(contract)
    if verdict != "ok":
        return verdict, ""
    tests = [_find(contract, q)[0]["intent_test"]["source"].strip() for q in call_order(contract)]
    head = "\n".join(contract.get("test_imports") or [])
    source = (head + "\n\n\n" if head else "") + "\n\n\n".join(tests) + "\n"
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return f"syntax:{exc}", ""
    return "ok", source


# ── The docking protocol: what one call reads, what it may write, and how its answer fills the contract ──────────────


def _object_view(node: dict, qualname: str) -> dict:
    value = node.get("value")
    status, expr = ast_from_json(value, "expr") if value is not None else ("empty", None)
    return {
        "qualname": qualname,
        "name": node["object"],
        "doc": node.get("doc", ""),
        "intent": node.get("intent", ""),
        "used_by": list(node.get("used_by") or []),
        "definition": _source(expr) if status == "ok" and isinstance(expr, ast.AST) else None,
    }


def _ancestor_view(contract: dict, qualname: str) -> dict:
    node, kind, _lineage = _find(contract, qualname)
    if kind == "class":
        return {"class": node["class"], "qualname": qualname, "doc": node.get("doc", ""), "bases": list(node.get("bases") or [])}
    return {"function": node["function"], "qualname": qualname, "doc": node.get("doc")}


def call_context(contract: dict, qualname: str) -> dict | str:
    """What the call on one function reads (pure): the file (its path, docstring, imports and global objects), the
    function's lineage above it (each ancestor's own declaration, never its other members), the function's own
    contract (docstring, intent tests of both signs and their coverage line, its objects), and everything below it,
    already written, as source. Codes: a contract verdict · ``unknown_target``."""
    verdict = contract_verdict(contract)
    if verdict != "ok":
        return verdict
    node, kind, lineage = _find(contract, qualname)
    if kind != "function":
        return "unknown_target"
    found = elements(contract)
    below = []
    for q in call_order(contract):
        if q.startswith(qualname + "."):
            status, fn = _function_def(_find(contract, q)[0], q)
            below.append({"qualname": q, "source": _source(fn)} if status == "ok" and fn is not None else {"qualname": q, "unwritten": status})
    return {
        "format": FORMAT,
        "target": qualname,
        "file": {
            "path": contract["path"],
            "doc": contract["doc"],
            "imports": [ast.unparse(n) for n in _imports(contract)],
            "objects": [_object_view(n, q) for q, k, n, line in found if k == "object" and not line],
        },
        "above": [_ancestor_view(contract, a) for a in lineage],
        "function": {
            "qualname": qualname,
            "name": node["function"],
            "doc": node["doc"],
            "intent_test": node["intent_test"],
            "objects": [_object_view(n, q) for q, k, n, line in found if k == "object" and line and line[-1] == qualname],
            **({"ast_template": node["ast_template"]} if "ast_template" in node else {}),
        },
        "below": below,
    }


def call_schema(contract: dict, qualname: str) -> dict | str:
    """The JSON schema one call's answer is held to while the model generates (pure): the function's empty cells
    (its signature, its code), imports for the file, new global objects for the file, and the definitions of the
    object cells below the function it may still fill, all over Python's full grammar, with imports kept out of
    bodies. Codes: a contract verdict · ``unknown_target``."""
    verdict = contract_verdict(contract)
    if verdict != "ok":
        return verdict
    node, kind, _lineage = _find(contract, qualname)
    if kind != "function":
        return "unknown_target"
    if "ast_template" in node:
        options = node["ast_template"]["choices"]
        selection = {"type": "object", "properties": {name: {"enum": list(values)} for name, values in options.items()}, "required": list(options), "additionalProperties": False}
        return {"type": "object", "properties": {"choices": selection}, "required": ["choices"], "additionalProperties": False}
    expr_or_null = {"anyOf": [{"$ref": "#/$defs/expr"}, {"type": "null"}]}
    properties: dict = {}
    if node.get("signature") is None:
        properties["signature"] = {
            "type": "object",
            "properties": {"args": {"$ref": "#/$defs/arguments"}, "returns": expr_or_null, "decorator_list": {"type": "array", "items": {"$ref": "#/$defs/expr"}}},
            "required": ["args", "returns", "decorator_list"],
            "additionalProperties": False,
        }
    if node.get("code") is None:
        properties["code"] = {"type": "array", "items": {"$ref": "#/$defs/stmt"}, "minItems": 1}
    properties["imports"] = {"type": "array", "items": {"anyOf": [{"$ref": "#/$defs/Import"}, {"$ref": "#/$defs/ImportFrom"}]}}
    properties["new_objects"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string", "pattern": f"^{_IDENT}$"}, "doc": {"type": "string"}, "value": {"$ref": "#/$defs/expr"}},
            "required": ["name", "doc", "value"],
            "additionalProperties": False,
        },
    }
    open_objects = _open_objects(contract, qualname)
    if open_objects:
        properties["objects"] = {"type": "object", "properties": {q: expr_or_null for q in open_objects}, "required": open_objects, "additionalProperties": False}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False, "$defs": grammar_defs(BODY_EXCLUDES)}


def fill(contract: dict, qualname: str, reply: object) -> tuple:
    """One call's answer written into the contract (pure; the contract given is never changed). Returns
    ``("filled", new_contract)`` or ``(code, contract)`` with nothing applied: a contract verdict ·
    ``unknown_target`` · ``bad_reply:<what>``. A filled cell is final: an answer to a cell that is not empty is refused
    as ``bad_reply:final:<cell>``, apart from an answer that is malformed (``bad_reply:<cell>``), since the two need
    different remedies."""
    verdict = contract_verdict(contract)
    if verdict != "ok":
        return verdict, contract
    node, kind, _lineage = _find(contract, qualname)
    if kind != "function":
        return "unknown_target", contract
    if not isinstance(reply, dict):
        return "bad_reply:not_an_object", contract
    if "ast_template" in node:
        if node.get("code") is not None:
            return "bad_reply:final:code", contract
        if set(reply) != {"choices"} or not isinstance(reply["choices"], dict):
            return "bad_reply:choices", contract
        status, reply = decisions.expand_ast_template(node["ast_template"], reply["choices"])
        if status != "expanded":
            return f"bad_reply:{status}", contract
    open_objects = _open_objects(contract, qualname)
    allowed = {"imports", "new_objects", "objects"} | ({"signature"} if node.get("signature") is None else set()) | ({"code"} if node.get("code") is None else set())
    extra = sorted(set(reply) - allowed)
    if extra:
        return (f"bad_reply:final:{extra[0]}" if extra[0] in ("signature", "code") else f"bad_reply:{extra[0]}"), contract
    new = copy.deepcopy(contract)
    target = _find(new, qualname)[0]
    if node.get("signature") is None:
        if _signature_verdict(reply.get("signature")) != "ok":
            return "bad_reply:signature", contract
        target["signature"] = copy.deepcopy(reply["signature"])
    if node.get("code") is None:
        status, stmts = ast_from_json(reply.get("code"), "stmt", "*", BODY_EXCLUDES)
        if status != "ok" or not stmts:
            return f"bad_reply:code:{status if status != 'ok' else 'empty'}", contract
        target["code"] = copy.deepcopy(reply["code"])
    have = {ast.unparse(n) for n in _imports(new)}
    bound = set(_emitted_names(list(new["objects"]) + list(new["body"])))
    bound |= {a.asname or a.name.split(".")[0] for n in _imports(new) for a in n.names}
    stem = PurePosixPath(new.get("path") or "").stem
    added = list(new.get("added_imports") or [])
    imports = reply.get("imports", [])
    if not isinstance(imports, list):
        return "bad_reply:imports", contract
    for i, value in enumerate(imports):
        status, imp = ast_from_json(value, "stmt")
        if status != "ok" or not isinstance(imp, ast.Import | ast.ImportFrom):
            return f"bad_reply:imports[{i}]", contract
        text = ast.unparse(imp)
        if text in have:
            continue
        verdict = _import_verdict(imp, stem, bound)
        if verdict != "ok":
            return f"bad_reply:imports[{i}]:{verdict}", contract
        have.add(text)
        bound |= {a.asname or a.name.split(".")[0] for a in imp.names}
        added.append(copy.deepcopy(value))
    new["added_imports"] = added
    new_objects = reply.get("new_objects", [])
    if not isinstance(new_objects, list):
        return "bad_reply:new_objects", contract
    taken = set(_emitted_names(list(new["objects"]) + list(new["body"])))
    taken |= {a.asname or a.name.split(".")[0] for n in _imports(new) for a in n.names}
    for i, item in enumerate(new_objects):
        status, value = ast_from_json(item.get("value") if isinstance(item, dict) else None, "expr")
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(item, dict) or set(item) != {"name", "doc", "value"} or not isinstance(item["doc"], str) or status != "ok":
            return f"bad_reply:new_objects[{i}]", contract
        if not isinstance(name, str) or not re.fullmatch(_IDENT, name) or keyword.iskeyword(name) or name in taken:
            return f"bad_reply:new_objects[{i}]:{name}", contract
        taken.add(name)
        new["objects"].append({"object": name, "doc": item["doc"], "intent": ast.unparse(value), "used_by": [qualname], "value": None, "added_by": qualname})
    objects = reply.get("objects", {})
    if not isinstance(objects, dict):
        return "bad_reply:objects", contract
    for q, value in objects.items():
        if value is None:
            continue
        if q not in open_objects or ast_from_json(value, "expr")[0] != "ok":
            return f"bad_reply:objects:{q}", contract
        _find(new, q)[0]["value"] = copy.deepcopy(value)
    return _admitted(contract, new)


# ── Admission of a whole change, before any cell becomes final (SEPARATION_OF_AUTHORITY.md R6) ───────────────────────
#
# A reply is checked as the change it makes to the whole contract, statically: nothing is imported or run (the
# founder, 2026-10-07: Detective's first run is the load check). Construction: the module, with a stub in every cell
# still open, compiles as Python reads it. Reference: no import names the module itself or binds a name the module
# already defines, and every name the reply brings into the module's own scope is defined, imported or a builtin.
# Validation: the new contract passes its own verdict, so no reply can leave it unplayable.

_STUB_SIGNATURE: dict = {"args": {"vararg": {"arg": "args"}, "kwarg": {"arg": "kwargs"}}, "returns": None, "decorator_list": []}
_STUB_CODE: list = [{"_type": "Raise", "exc": {"_type": "Call", "func": {"_type": "Name", "id": "NotImplementedError", "ctx": "Load"}, "args": [], "keywords": []}, "cause": None}]
_MODULE_NAMES = frozenset({"__name__", "__doc__", "__file__", "__spec__", "__loader__", "__package__", "__builtins__", "__annotations__"})


def _import_verdict(imp: ast.Import | ast.ImportFrom, stem: str, bound: set) -> str:
    """An import a call adds, against the module it is written into (pure). ``ok``; ``self`` when it names the module
    itself; ``bound:<name>`` when it binds a name the module already defines or imports, an element defined twice (the
    founder, 2026-10-06: «avoiding drift from multiple model calls/elements defined more than once/import nesting»)."""
    modules = [a.name for a in imp.names] if isinstance(imp, ast.Import) else [imp.module or ""]
    if any(m.split(".")[0] == stem for m in modules):
        return "self"
    for alias in imp.names:
        name = alias.asname or alias.name.split(".")[0]
        if name in bound:
            return f"bound:{name}"
    return "ok"


def _stubbed(contract: dict) -> dict:
    """The contract with a stub in every open function cell, ``(*args, **kwargs)`` raising NotImplementedError, so the
    module renders and compiles as it stands (pure; the contract given is never changed)."""
    stubbed = copy.deepcopy(contract)
    for qualname in call_order(stubbed):
        node = _find(stubbed, qualname)[0]
        if node.get("signature") is None:
            node["signature"] = copy.deepcopy(_STUB_SIGNATURE)
        if node.get("code") is None:
            node["code"] = copy.deepcopy(_STUB_CODE)
    return stubbed


def _unresolved_names(source: str, path: str) -> list:
    """The names the module's own scope reads that nothing defines (pure): read there, and neither assigned nor imported
    there, and neither a builtin nor a module attribute. At load such a name stops the module. Names read inside a
    function body are its own scope's, and are not this check's."""
    try:
        table = symtable.symtable(source, path or "<contract>", "exec")
    except SyntaxError:
        return []
    known = set(dir(builtins)) | _MODULE_NAMES
    return sorted(s.get_name() for s in table.get_symbols() if s.is_referenced() and not (s.is_assigned() or s.is_imported() or s.get_name() in known))


def _admitted(contract: dict, new: dict) -> tuple:
    """The whole change a reply makes, admitted or refused (pure): ``("filled", new)``, or ``(code, contract)`` with
    nothing applied. Codes: ``bad_reply:verdict:<contract verdict>`` · ``bad_reply:module:<render code>`` (a module that
    does not compile, ``syntax:…``) · ``bad_reply:module_names:<name,…>``, the names the reply brings into the module's
    own scope that nothing defines. A name the module read undefined before the reply is the contract's, never the
    reply's (R1): the reply is judged on its own change, and Detective's first run finds the contract's."""
    verdict = contract_verdict(new)
    if verdict != "ok":
        return f"bad_reply:verdict:{verdict}", contract
    status, source = render_module(_stubbed(new))
    if status != "ok":
        return f"bad_reply:module:{status}", contract
    path = new.get("path") or ""
    before = set(_unresolved_names(render_module(_stubbed(contract))[1], path))
    unresolved = [name for name in _unresolved_names(source, path) if name not in before]
    if unresolved:
        return f"bad_reply:module_names:{','.join(unresolved)}", contract
    return "filled", new


# ── Measures over the contract ───────────────────────────────────────────────────────────────────────────────────────


def operations(code: object) -> dict:
    """How many nodes of each of Wesker's categories a written body holds (pure): the nodes each category's mutator
    visits, so a node can count in more than one. ``{}`` when the code is not the grammar's."""
    status, stmts = ast_from_json(code, "stmt", "*", BODY_EXCLUDES)
    if status != "ok":
        return {}
    counts = dict.fromkeys(WESKER, 0)
    for stmt in stmts or []:
        for node in ast.walk(stmt):
            name = type(node).__name__
            for category, names in WESKER.items():
                if name in names:
                    counts[category] += 1
    return counts


def coverage_gaps(contract: dict, qualname: str) -> list | str:
    """The coverage lines of one written function whose Wesker category its body holds no node of (pure): the
    minimum contract read against the code, a record and never a gate. Codes: ``unknown_target`` · ``unwritten``."""
    node, kind, _lineage = _find(contract, qualname)
    if kind != "function":
        return "unknown_target"
    if node.get("code") is None:
        return "unwritten"
    counts = operations(node["code"])
    return [c for c in node["intent_test"]["covers"] if c.get("category") in WESKER and counts.get(c["category"], 0) == 0]


def judge_contract(contract: dict, qualname: str) -> dict | str:
    """What the judgment seat is shown of a function's contract (pure; WIRING W4): its docstring (the prose and the
    operations), its intent tests' source, and its coverage lines of category OUTPUT, what it must never do. Codes:
    ``unknown_target``."""
    node, kind, _lineage = _find(contract, qualname)
    if kind != "function":
        return "unknown_target"
    test = node["intent_test"]
    return {"doc": node["doc"], "intent_tests": test["source"], "must_never": [c for c in test["covers"] if c.get("category") == "OUTPUT"]}


def call_weight(value: object) -> int:
    """The size of a call, counted in the nodes of its JSON (pure): every object, list and value is one. Computed from
    the call's context, before anything is generated."""
    if isinstance(value, dict):
        return 1 + sum(call_weight(v) for v in value.values())
    if isinstance(value, list):
        return 1 + sum(call_weight(v) for v in value)
    return 1


# ── The model, and what is known about it ────────────────────────────────────────────────────────────────────────────
#
# Ported from the function-body cassette's [model] section and from the night shift's lanes.json and local_model.json,
# which lived in a session's scratchpad; they live here now. A speed or a capacity is a measurement, never an
# estimate: each stays None until it is measured. ``max_weight`` takes the place of the count of prompt tokens: the
# largest call, in ``call_weight``'s units, that the model is given. An empty endpoint is $UROBOROS_LLM_ENDPOINT, else
# the wire's default; a ``think`` of None is not sent.

MODELS: dict = {
    "qwen3.5-4b": {
        "name": "qwen3.5:4b",
        "api": "ollama",
        "endpoint": "",
        "think": False,
        "keep_alive": "5m",
        "timeout_s": 180,
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 2048},
        "context_limit": 262144,
        "max_weight": None,
        "why": "local small-model profile for declared AST choices",
    },
    "qwen3-coder-next": {
        "name": "qwen3-coder-next:latest",
        "api": "ollama",
        "endpoint": "",
        "think": None,
        "keep_alive": "30m",
        "timeout_s": 900,
        "options": {"temperature": 0.1, "num_ctx": 32768, "num_predict": 16384},
        "max_weight": None,
        "prompt_tok_s": None,
        "gen_tok_s": None,
        "load_s": None,
        "why": (
            "the newest MoE coder (the founder: «the newest MoE one would be best»): qwen3next, 79.7B, Q4_K_M; its capabilities are "
            "completion and tools, so think is not sent. num_ctx as in Set 1's run (2026-10-05: 31 calls, prompts to 2,399 tokens, "
            "outputs to 1,001, every one 'stop'). num_predict raised from that run's 3,072: its 27 bodies, written as JSON syntax "
            "trees, measured 6.2-7.0 times their Python text, so its largest output is about 6,200-7,000 tokens"
        ),
    },
    "qwen3.8-27b": {
        "name": "qwen3.8:27b-q4_K_M",
        "api": "ollama",
        "endpoint": "http://127.0.0.1:11434",
        "think": True,
        "keep_alive": "12h",
        "timeout_s": None,
        "options": {"num_ctx": 262144},
        "max_weight": None,
        "prompt_tok_s": 103,
        "gen_tok_s": 5,
        "load_s": 10,
        "why": "this laptop's 27B (the night shift's gpu lane); num_ctx and keep_alive from local_model.json, measured 2026-10-05: 262,144 -> 25.01 GiB, 9.7 s load",
    },
    "gemma4-12b": {
        "name": "gemma4:12b-it-qat",
        "api": "ollama",
        "endpoint": "http://localhost:11435",
        "think": False,
        "keep_alive": "12h",
        "timeout_s": None,
        "options": {"num_ctx": 65536},
        "max_weight": None,
        "prompt_tok_s": 111,
        "gen_tok_s": 18.6,
        "load_s": 5,
        "why": (
            "the family MacBook Pro through this laptop's tunnel (the founder, 2026-10-05: «Yes, Gemma 4 12B (Recommended)»); "
            "measured at num_ctx 65,536: 7.18 GiB, 5.3 s load; 18% memory free, so the window stays at 65,536"
        ),
    },
    "qwen3.5-35b": {
        "name": "qwen3.5:35b",
        "api": "ollama",
        "endpoint": "http://127.0.0.1:11434",
        "think": True,
        "keep_alive": "15m",
        "timeout_s": None,
        "options": {"num_ctx": 65536},
        "max_weight": None,
        "prompt_tok_s": 850,
        "gen_tok_s": 34.3,
        "load_s": 10,
        "why": (
            "a calibration trial of the 36B MoE on this laptop's GPU (the founder, 2026-10-05: «a MoE model should be better»); "
            "measured at num_ctx 65,536 beside the resident 27B: 22.32 GiB, 9.7 s load"
        ),
    },
    "laguna-xs-2.1": {
        "name": "laguna-xs-2.1:q3_K_M",
        "api": "ollama",
        "endpoint": "http://127.0.0.1:11434",
        "think": True,
        "keep_alive": "30m",
        "timeout_s": 900,
        "options": {"temperature": 1.0, "top_k": 20, "top_p": 1.0, "num_ctx": 32768, "num_predict": 16384, "num_batch": 31},
        "max_weight": None,
        "prompt_tok_s": 125,
        "gen_tok_s": 42.1,
        "load_s": 2,
        "why": (
            "a sanity check of a small new MoE coder (the founder, 2026-10-07: «a good new MoE model that would activate ~8-16GB, good "
            "for coding»): Poolside's Laguna XS 2.1, 33.4B with 3B active (256 experts, 8 used), as bartowski's imatrix Q3_K_M (15.58 GB) "
            "with its YaRN attention factor set to Poolside's own GGUF's 1.0, under Ollama's poolside-v1 renderer and parser; sampling as "
            "its card. Measured 2026-10-07 at num_ctx 32,768: 14.69 GiB, 1.9 s load (warm), 42.1 tok/s thinking. It thinks: over five draws "
            "of that day's orders contract it wrote both functions correctly in three thinking, and order_total in none without; on the "
            "durations contract, in none either way (~/.cache/squishos-e2e/2026-10-07-laguna/tally.txt). num_batch 31: from 32 tokens a "
            "batch, ggml's Metal backend runs the batched expert kernel (ggml-metal-common.cpp, ne21 >= 32, at llama.cpp b10809 and "
            "Ollama's b11232), and on this file that turns the reply to special tokens until the repeat limit; below 32, and on the CPU, "
            "it answers correctly. The cap costs prompt speed: 125 tok/s at 31, 85 at 16 (a 4,091-token prompt)"
        ),
    },
}
DEFAULT_MODEL = "qwen3-coder-next"
LINE_MODEL = "qwen3.5-4b"
_DEFAULT_TIMEOUT_S = 900

# ── The player: a contract through the model, one function at a time ────────────────────────────────────────────────


def call_payload(contract: dict, qualname: str, record: dict, api: str, repair: dict | None = None) -> dict | str:
    """The exact request one call sends and its weight, with a context budget for adaptive profiles (pure). The payload is
    built by the harness's wire (``model.request_payload``) with the call's schema as the format. Codes: a contract
    verdict · ``unknown_target`` · ``written`` (its cells are filled; a filled cell is never asked again) ·
    ``over_capacity`` (heavier than the model's ``max_weight``) · ``context_over_capacity`` (the window cannot fit the request)
    · ``context_options_invalid`` (the model lacks a finite positive window or reply budget)."""
    context = call_context(contract, qualname)
    if isinstance(context, str):
        return context
    node = _find(contract, qualname)[0]
    if node.get("signature") is not None and node.get("code") is not None:
        return "written"
    schema = call_schema(contract, qualname)
    if isinstance(schema, str):
        return schema
    if repair:
        context["repair"] = repair
        options = schema.get("properties", {}).get("choices", {}).get("properties", {})
        if len(options) == 1:
            name, slot = next(iter(options.items()))
            rejected = {selection.get(name) for selection in repair["rejected"]}
            slot["enum"] = [label for label in slot["enum"] if label not in rejected]
            if not slot["enum"]:
                return "no_remaining_choices"
    weight = call_weight(context)
    cap = record.get("max_weight")
    if isinstance(cap, int) and not isinstance(cap, bool) and weight > cap:
        return "over_capacity"
    messages = [{"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=1)}]
    options, budget = wire.context_budget(api, messages, dict(record.get("options") or {}), record.get("context_limit"))
    if budget and isinstance(budget["num_ctx"], str):
        return budget["num_ctx"]
    payload, ignored = wire.request_payload(api, record["name"], messages, schema, options, record.get("think"), record.get("keep_alive") or "")
    return {"weight": weight, "payload": payload, "ignored": ignored, **({"context": budget} if budget else {})}


def _log(out_dir: Path, entry: dict) -> None:
    with (out_dir / "calls.jsonl").open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_asked_test(contract: dict, tests: list, out_dir: Path) -> str:
    """Detective's own asked tests, written (the founder, 2026-10-08, FOUNDING_PROMPTS [17]: the harness renders it, and no
    model is called): each source in ``tests`` is the test Detective rendered for an ask of its own (its record's
    ``test``, built from its observations), appended in order to ``tests/test_<stem>_asked.py`` under the play folder,
    the file created with the test file's imports when new. The whole file must compile; nothing is written otherwise.

    Returns ``""``, or ``write_refused:syntax`` (the file would not compile)."""
    path = out_dir / "tests" / f"test_{PurePosixPath(contract['path']).stem}_asked.py"
    existing = path.read_text() if path.exists() else "\n".join(contract.get("test_imports") or [])
    whole = (existing.rstrip("\n") + "\n\n\n" if existing.strip() else "") + "\n\n\n".join(source.strip("\n") for source in tests) + "\n"
    try:
        compile(whole, str(path), "exec")
    except (SyntaxError, ValueError):
        return "write_refused:syntax"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(whole)
    return ""


# ── Hand-backs and the receiver's decision (SEPARATION_OF_AUTHORITY.md R7) ─────────────────────────────────────────────
#
# Each ending the Detective phase hands back is written as a typed record routed to its receivers, with its evidence
# and the values the receiver's decision takes; the harness routes and decides nothing. A receiver that finds the code
# wrong re-issues that one function: the contract's next version, its cells cleared, so a fresh call writes it alone.


def handbacks(driven: object) -> list:
    """The Detective phase's hand-backs, each a typed record routed to its receivers (pure): ``{"function", "ending",
    "decision", "doctor", "receivers", "decisions"}``, the folder's setup first (``function`` ``""``), then each
    function, in order, for every one whose route is not ``done`` (``daemon.drive``: a function routes ``done`` only on
    converge's done, the folder only on its clean setup). ``decision`` and ``doctor`` are the evidence the receiver
    reads (the founder, 2026-10-07: «Detective's refusal and doctor's read as its evidence»): ``decision`` is its last
    Detective record, the ``decision`` of the last entry of its runs (Detective's decision, or what its run printed
    where none could be read), ``None`` when it ran nothing or the entry holds none; ``doctor`` is doctor's payload,
    read once after the function ended (FOUNDING_PROMPTS [21]), ``None`` for the setup and wherever it was not read.
    ``decisions`` are the values the receiver's decision takes (``decisions.RECEIVER_DECISIONS``)."""
    out: list = []
    if not isinstance(driven, dict):
        return out
    setup, functions = driven.get("setup"), driven.get("functions")
    items = ([("", setup)] if isinstance(setup, dict) else []) + (list(functions.items()) if isinstance(functions, dict) else [])
    for qualname, outcome in items:
        if not isinstance(outcome, dict) or outcome.get("route") == "done":
            continue
        runs = outcome.get("runs")
        last = runs[-1] if isinstance(runs, list) and runs else None
        ending = outcome.get("ending")
        out.append(
            {
                "function": qualname,
                "ending": ending,
                "decision": last.get("decision") if isinstance(last, dict) else None,
                "doctor": outcome.get("doctor"),
                "receivers": decisions.handback_receivers(ending),
                "decisions": list(decisions.RECEIVER_DECISIONS),
            }
        )
    return out


def _names_read(contract: dict, qualname: str, skip: set) -> set:
    """The names the contract's written code reads (pure), outside one function's own cells and objects and outside the
    globals in ``skip``: every other function's signature and code, and every other object's value and intent."""
    names: set = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            if value.get("_type") == "Name" and isinstance(value.get("id"), str):
                names.add(value["id"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    own, _kind, _lineage = _find(contract, qualname)
    mine = {f"{qualname}.{o.get('object')}" for o in (own or {}).get("objects") or [] if isinstance(o, dict)}
    for q, kind, node, _lineage in elements(contract):
        if kind == "function" and q != qualname:
            walk(node.get("signature"))
            walk(node.get("code"))
        elif kind == "object" and q not in mine and q not in skip:
            walk(node.get("value"))
            try:
                names |= {n.id for n in ast.walk(ast.parse(node.get("intent") or "", mode="eval")) if isinstance(n, ast.Name)}
            except SyntaxError:
                continue
    return names


def reissue(contract: dict, qualname: str) -> tuple:
    """The receiver's ``code_wrong`` (R7): the contract's next version, with one function's cells cleared so that a
    fresh call writes that function alone (pure; the contract given is never changed). Cleared: its signature, its code
    and the values of its own objects. Removed: the globals its earlier call added (``added_by``), except those other
    written code reads, which are kept. Its imports stay, since the contract does not record who added an import (the
    founder, 2026-10-07: «Remove its globals»). Cells stay final within a version: the clearing is the next version.

    Returns ``("reissued", new, done)``, ``done`` naming what was ``cleared``, ``removed`` and ``kept``; or ``(code,
    contract, {})``: a contract verdict · ``unknown_target`` · ``unwritten`` (no cell of it is filled: a play asks for
    it as the contract stands)."""
    verdict = contract_verdict(contract)
    if verdict != "ok":
        return verdict, contract, {}
    if _find(contract, qualname)[1] != "function":
        return "unknown_target", contract, {}
    new = copy.deepcopy(contract)
    target = _find(new, qualname)[0]
    cleared = [f"{qualname}.{cell}" for cell in ("signature", "code") if target.get(cell) is not None]
    target["signature"] = target["code"] = None
    for obj in target.get("objects") or []:
        if obj.get("value") is not None:
            obj["value"] = None
            cleared.append(f"{qualname}.{obj['object']}")
    if not cleared:
        return "unwritten", contract, {}
    added = [o["object"] for o in new["objects"] if o.get("added_by") == qualname]
    removed = set(added)
    while True:
        read = removed & _names_read(new, qualname, removed)
        if not read:
            break
        removed -= read
    new["objects"] = [o for o in new["objects"] if o["object"] not in removed]
    return "reissued", new, {"cleared": cleared, "removed": [n for n in added if n in removed], "kept": [n for n in added if n not in removed]}


def revision(current: dict, written: dict) -> tuple:
    """The receiver's ``contract_wrong`` (R7): the contract's next version, assembled from the contract as it stands and
    the one the contract writer wrote in revising it (pure; neither is changed; the founder, 2026-10-07, separation
    [7]). Each function whose contract the revision changes (its entry as the contract writer writes it, ``seat_form``:
    its docstring, its intent tests and their coverage, its objects, the functions it combines) starts with every cell
    empty, as a reissue clears one; every other function keeps its cells, the values of its own objects among them,
    wherever the revision places it. A global a call added stays with a function that keeps its cells and goes with
    one that does not, unless written code that stays reads it, as in a reissue; the imports the calls added stay,
    since the contract does not record who added an import.

    Returns ``("revised", new, done)``, ``done`` naming the functions ``kept`` and ``changed`` (by the new version's
    qualnames) and ``dropped`` (by the current one's), and the globals the calls added that were ``removed`` or
    ``carried``; ``("unchanged", new, done)`` when the contract writer changed nothing it writes; or ``(code, written,
    {})``: ``current:<verdict>`` (the contract as it stands is not one the harness can play) · the written contract's
    own verdict · ``moved`` (another module's path) · ``version:<verdict>``, the next version as assembled, its cells
    and the calls' globals carried in, refused by its verdict (a global the revision declares under the name of one a
    call added: ``version:duplicate:<name>``)."""
    verdict = contract_verdict(current)
    if verdict != "ok":
        return f"current:{verdict}", written, {}
    verdict = contract_verdict(written)
    if verdict != "ok":
        return verdict, written, {}
    if written["path"] != current["path"]:
        return "moved", written, {}
    before, after = _entries(current), _entries(written)
    renamed = {before[seat][0]: after[seat][0] for seat in before if seat in after}
    new = copy.deepcopy(written)
    kept: list = []
    changed: list = []
    kept_from: set = set()
    for seat, (qualname, entry) in after.items():
        if seat not in before or before[seat][1] != entry:
            changed.append(qualname)
            continue
        source, target = _find(current, before[seat][0])[0], _find(new, qualname)[0]
        target["signature"], target["code"] = copy.deepcopy(source.get("signature")), copy.deepcopy(source.get("code"))
        values = {o["object"]: o.get("value") for o in source.get("objects") or []}
        for obj in target.get("objects") or []:
            obj["value"] = copy.deepcopy(values.get(obj["object"]))
        kept.append(qualname)
        kept_from.add(before[seat][0])
    added = [o for o in current["objects"] if o.get("added_by")]
    carried = [{**copy.deepcopy(o), "added_by": renamed.get(o["added_by"], o["added_by"]), "used_by": [renamed[u] for u in o.get("used_by") or [] if u in renamed]} for o in added]
    own = list(new["objects"])
    new["objects"] = own + carried
    removed = {o["object"] for o in added if o["added_by"] not in kept_from}
    while True:
        read = removed & _names_read(new, "", removed)
        if not read:
            break
        removed -= read
    new["objects"] = own + [o for o in carried if o["object"] not in removed]
    if "added_imports" in current:
        new["added_imports"] = copy.deepcopy(current["added_imports"])
    if seat_form(current) != seat_form(written):
        new["revises"] = contract_digest(current)
    else:
        new = current
    verdict = contract_verdict(new)
    if verdict != "ok":
        return f"version:{verdict}", written, {}
    done = {
        "kept": kept,
        "changed": changed,
        "dropped": [qualname for seat, (qualname, _entry) in before.items() if seat not in after],
        "removed": [o["object"] for o in added if o["object"] in removed],
        "carried": [o["object"] for o in added if o["object"] not in removed],
    }
    return ("unchanged" if new is current else "revised"), new, done


# ── The contract writer's seat (SEPARATION_OF_AUTHORITY.md R2) ─────────────────────────────────────────────────────────
#
# One call reads the founder's instructions for a module, with its imports and globals as they stand, and writes the
# module's contract, held at decode time to its form (``contract_schema``, the ``contract-write`` cassette's one slot).
# One call per module, apart from every other authority's call (the founder, 2026-10-07: «Separate calls»). The seat is
# held by whoever is granted it (R1); when the lead holds it in session, the lead writes the JSON and ``check`` gates it.


def _given_verdict(imports: list, objects: list) -> str:
    """Are the imports and globals the seat is given to keep the module's own (pure)? ``ok``, or the first that is not:
    ``bad_given:imports[<i>]`` (not one import line) · ``bad_given:objects[<i>]`` (not a global object in the
    contract's form). Either is its caller's to fix, never the contract writer's."""
    for i, line in enumerate(imports):
        if _parse_import(line) is None:
            return f"bad_given:imports[{i}]"
    for i, given in enumerate(objects):
        if _kind(given) != "object" or not re.fullmatch(_IDENT, given["object"]) or _object_verdict(given, given["object"]) != "ok":
            return f"bad_given:objects[{i}]"
    return "ok"


def seat_verdict(written: object, path: str, imports: list, objects: list) -> str:
    """Is a contract the seat wrote one this module can take (pure)? ``ok``, or the first defect: an import or global it
    was given to keep that is not the module's own (``_given_verdict``: its caller's to fix) · the contract's own
    verdict (``contract_verdict``) · ``moved`` (another module's path) · ``dropped_import:<i>`` (an import the module
    holds is gone) · ``dropped_object:<name>`` (a global the module holds is gone, or its intent changed) ·
    ``filled:<qualname>`` (a cell the code writer fills is written: the contract writer never writes code)."""
    given = _given_verdict(imports, objects)
    if given != "ok":
        return given
    verdict = contract_verdict(written)
    if verdict != "ok" or not isinstance(written, dict):
        return verdict
    if written["path"] != path:
        return "moved"
    for i, line in enumerate(imports):
        if line not in written["imports"]:
            return f"dropped_import:{i}"
    held = {o["object"]: o.get("intent") for o in written["objects"]}
    for kept in objects:
        if held.get(kept["object"]) != kept["intent"]:
            return f"dropped_object:{kept['object']}"
    for qualname, kind, node, _lineage in elements(written):
        if (kind == "function" and (node.get("signature") is not None or node.get("code") is not None)) or (kind == "object" and node.get("value") is not None):
            return f"filled:{qualname}"
    return "ok"


def _seat_evidence(instructions: str, path: str, imports: list, objects: list, categories: dict, current: dict | None = None, handback: dict | None = None) -> dict:
    """What the contract writer reads (pure): one field, ``record``, the JSON text of one object, every value as data
    and no sentence of the harness's (FOUNDING_PROMPTS [13], [16]): his ``instructions``, the module's ``path`` and
    ``stem``, its ``imports`` and ``globals`` as they stand, and the two vocabularies its form is written in:
    ``categories`` (Wesker's operation categories, each with the syntax nodes it visits, as its caller read them from
    Wesker) and ``output_operators`` (each with what it rules out). A revision reads besides ``current``, the module's
    contract as it stands in the seat's form, and ``handback``, the hand-back record that found it wrong (the founder,
    2026-10-07, separation [7] and [10]); a first write reads ``null`` for each."""
    record = {
        "instructions": instructions,
        "path": path,
        "stem": PurePosixPath(path).stem,
        "imports": list(imports),
        "globals": list(objects),
        "current": seat_form(current) if current is not None else None,
        "handback": handback,
        "categories": categories,
        "output_operators": OUTPUT_OPERATORS,
    }
    return {"record": json.dumps(record, ensure_ascii=False, indent=1, default=str)}


def write_contract(
    instructions: str,
    path: str,
    model_key: str = DEFAULT_MODEL,
    imports: list | None = None,
    objects: list | None = None,
    chat: Callable | None = None,
    current: dict | None = None,
    handback: dict | None = None,
    categories: dict | None = None,
) -> tuple:
    """The contract writer's seat: one call, held to the seat's form (``contract_schema``) at decode time, the stored
    contract built from it (``seat_contract``) and judged by ``seat_verdict``. A revision gives it besides the
    module's contract as it stands and the hand-back record that found it wrong (``revise_contract``). What it reads is
    one record (``_seat_evidence``); ``categories`` is Wesker's categories for it, read from Wesker's own source when
    not given (``detective.wesker_categories``, resolved at call time). Returns ``(code, contract, outcome)``:
    ``written`` with the contract; ``unknown_model``, ``bad_given:…`` (what it is given to keep is not the module's
    own), or ``categories_unavailable:<code>`` (Wesker's categories could not be read), each before any call; the
    call's own ``call_<status>:<reason>`` (an
    abstention among them: the instructions do not determine a module); the build's own refusal
    (``combines_unknown:…``, ``combined_twice:…``, ``loop:…``, ``name_clash:…``) with the reply it refused; or the
    seat's verdict, with the contract it refused. ``chat`` stands in for the wire in tests."""
    model = MODELS.get(model_key)
    if model is None:
        return "unknown_model", None, None
    given = _given_verdict(imports or [], objects or [])
    if given != "ok":
        return given, None, None
    if categories is None:
        read = detective.wesker_categories(detective.engine())
        if isinstance(read, str):
            return f"categories_unavailable:{read}", None, None
        categories = read
    held = {**model, "timeout_s": model.get("timeout_s") or _DEFAULT_TIMEOUT_S}
    cassette = daemon._held(find_cassette("contract-write"), held, system="")
    evidence = _seat_evidence(instructions, path, imports or [], objects or [], categories, current, handback)
    out = invoke(cassette, evidence, {"contract": contract_schema()}, None, chat=chat)
    if out.status != "admitted" or "contract" not in out.values:
        return f"call_{out.status}:{out.reason}", None, out
    reply = out.values["contract"]
    status, written = seat_contract(reply)
    if status != "ok":
        return status, reply, out
    verdict = seat_verdict(written, path, imports or [], objects or [])
    return ("written" if verdict == "ok" else verdict), written, out


def revise_contract(
    instructions: str,
    current: dict,
    handback: dict,
    model_key: str = DEFAULT_MODEL,
    imports: list | None = None,
    objects: list | None = None,
    chat: Callable | None = None,
    categories: dict | None = None,
) -> tuple:
    """The receiver's ``contract_wrong`` through the contract writer's seat (R7; the founder, 2026-10-07, separation [7]
    and [10]): one call reads his instructions, the module's contract as it stands and the hand-back record that found
    it wrong, and writes the contract's next version, which ``revision`` assembles. Returns ``(code, contract, done,
    outcome)``: ``revised`` or ``unchanged`` with the next version and what each function became;
    ``bad_given:current:<verdict>`` (the contract as it stands is not one the harness can play) or
    ``bad_given:handback`` (no hand-back record naming its function), each before any call; the seat's own codes
    (``write_contract``), with what it refused; or ``revision``'s refusal, with the contract the seat wrote."""
    verdict = contract_verdict(current)
    if verdict != "ok":
        return f"bad_given:current:{verdict}", None, {}, None
    if not isinstance(handback, dict) or not isinstance(handback.get("function"), str):
        return "bad_given:handback", None, {}, None
    code, written, outcome = write_contract(instructions, current["path"], model_key, imports, objects, chat, current=current, handback=handback, categories=categories)
    if code != "written":
        return code, written, {}, outcome
    status, new, done = revision(current, written)
    return status, new, done, outcome


def play(
    contract_path: Path,
    out_dir: Path,
    model_key: str = DEFAULT_MODEL,
    chat: Callable | None = None,
    runner: Callable | None = None,
    contract_writer: str | None = None,
    admission: str | None = None,
    *,
    excluded: tuple = (),
    repairs: dict | None = None,
) -> dict:
    """Play a contract through the model: one call per unwritten function, bottom-up, each answer filled into the
    contract, and the contract saved after every call (``out_dir/contract.json``: a run started from it asks only for
    what is still unwritten; a written cell is never asked again). A call that fails skips that function alone and the
    run goes on (the founder, 2026-10-06: «Skip just the failed function»). When every function is written, the module
    and its intent tests are written under ``out_dir``, and Detective runs on them there in the contract's typed order,
    the harness following Detective's own record (``daemon.drive``; its records and endings are the record's
    ``detective``), and every test Detective asks for written as Detective rendered it (``write_asked_test``). Every
    write call
    is logged whole in ``out_dir/calls.jsonl``: its weight, the raw reply, the telemetry, the fill's code. No retry and
    no test: writing is raw generation. Each ending the phase hands back is written to ``out_dir/handbacks.jsonl``,
    routed to its receivers (``handbacks``). A stop before Detective carries the skipped function and its call
    evidence, or the folder's refusal when no function was reached. A checked contract is saved even if it was
    already filled, so a receiver can replay from the run folder.

    Returns the run's record, written to ``out_dir/play.json``. Its ``grant`` names who holds each authority for the
    unit (R1): the code writer is the model; the contract writer and admission are recorded as the caller gives them,
    ``None`` when not given. Its ``outcome`` is ``written``; ``incomplete``, with ``skipped`` naming each skipped
    function as ``<code>@<qualname>`` (``transport``, ``unparseable``, ``over_capacity``, ``bad_reply:…``); or the code
    that refused the run before any call. Its ``done`` is ``True`` only when the module was written, the folder's setup
    routes ``done`` and every function of the contract routes ``done`` (``decisions.admission_bar`` gives ``pass`` over
    the play's own routes); any other ending records ``False``. ``chat`` stands in for the wire in tests, and
    ``runner`` for Detective's CLI.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    grant = {"contract_writer": contract_writer, "code_writer": model_key, "admission": admission}
    record: dict = {"contract": str(contract_path), "model": model_key, "grant": grant, "out": str(out_dir), "calls": 0, "skipped": [], "outcome": "", "done": False}
    routed: list = []

    def hand_back(qualname: str, ending: str, evidence: dict | None = None) -> None:
        routed.append(
            {
                "function": qualname,
                "ending": ending,
                "decision": None,
                "doctor": None,
                "receivers": decisions.handback_receivers(ending),
                "decisions": list(decisions.RECEIVER_DECISIONS),
                "evidence": evidence,
            }
        )

    def skip(qualname: str, ending: str, evidence: dict | None = None) -> None:
        record["skipped"].append(f"{ending}@{qualname}")
        hand_back(qualname, ending, evidence)

    def finish(outcome: str) -> dict:
        if outcome != "written" and not routed:
            hand_back("", outcome)
        (out_dir / "handbacks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in routed))
        record["outcome"] = outcome
        if outcome == "written":
            driven = record["detective"]
            classes = [driven["setup"]["class"], *(item["class"] for item in driven["functions"].values())]
            exit_code = decisions.possession_exit(classes)
            record["exit_code"] = 3 if exit_code == 0 and not record["done"] else exit_code
        else:
            record["exit_code"] = 1 if outcome == "incomplete" else 2
        (out_dir / "play.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
        return record

    try:
        contract = json.loads(contract_path.read_text())
    except (OSError, ValueError):
        return finish("unreadable")
    verdict = contract_verdict(contract)
    if verdict != "ok":
        return finish(verdict)
    model = MODELS.get(model_key)
    if model is None:
        return finish("unknown_model")
    snapshot = out_dir / "contract.json"
    snapshot.write_text(json.dumps(contract, indent=1, ensure_ascii=False))
    api = wire.resolve_api(model["api"])
    endpoint = wire.resolve_endpoint(api, model.get("endpoint") or "")
    send = chat or wire.chat
    for qualname in call_order(contract):
        node = _find(contract, qualname)[0]
        if qualname in excluded:
            if node["code"] is None or node["signature"] is None:
                skip(qualname, "excluded_unfilled")
            continue
        repair = (repairs or {}).get(qualname)
        request = call_payload(contract, qualname, model, api, repair)
        if request == "written":
            continue
        if isinstance(request, str):
            skip(qualname, request)
            continue
        entry = {"qualname": qualname, "weight": request["weight"], "ignored": request["ignored"]}
        if "context" in request:
            entry["context"] = request["context"]
        try:
            content, telemetry = send(endpoint, request["payload"], model.get("timeout_s") or _DEFAULT_TIMEOUT_S, api)
        except wire.TransportError as exc:
            failure = {**entry, "transport": str(exc)}
            _log(out_dir, failure)
            skip(qualname, "transport", failure)
            continue
        record["calls"] += 1
        try:
            reply = json.loads(content)
        except ValueError:
            code = decisions.reply_failure(telemetry.get("done_reason"))
            failure = {**entry, "telemetry": telemetry, "fill": code, "reply": content}
            _log(out_dir, failure)
            skip(qualname, code, failure)
            continue
        if repair and isinstance(reply, dict) and reply.get("choices") in repair["rejected"]:
            code = "bad_reply:repeated_choices"
        else:
            code, contract = fill(contract, qualname, reply)
        result = {**entry, "telemetry": telemetry, "fill": code, "reply": content}
        _log(out_dir, result)
        if code != "filled":
            skip(qualname, code, result)
            continue
        snapshot.write_text(json.dumps(contract, indent=1, ensure_ascii=False))
    if record["skipped"]:
        return finish("incomplete")
    status, source = render_module(contract)
    if status != "ok":
        return finish(status)
    module_path = out_dir / contract["path"]
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(source)
    status, tests = render_tests(contract)
    if status != "ok":
        return finish(status)
    test_path = out_dir / "tests" / f"test_{module_path.stem}.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(tests)
    # The contract's minimum read against the code it got: each coverage line whose Wesker category the written body
    # holds no node of, per function — information for the audit, never a gate.
    record["coverage"] = {q: coverage_gaps(contract, q) for q in call_order(contract)}
    held = {**model, "timeout_s": model.get("timeout_s") or _DEFAULT_TIMEOUT_S}

    def write_test(_qualname: str, tests: list) -> str:
        return write_asked_test(contract, tests, out_dir)

    judged = {q: judge_contract(contract, q) for q in call_order(contract)}
    record["detective"] = daemon.drive(
        out_dir,
        contract["path"],
        contract["detective"],
        [target for target in detective_targets(contract) if target[0] not in excluded],
        held,
        chat=chat,
        runner=runner,
        write_test=write_test,
        coverage=record["coverage"],
        contracts=judged,
    )
    routed.extend(handbacks(record["detective"]))
    setup = {key: record["detective"]["setup"].get(key) for key in ("route", "ending")}
    routes = {q: {key: f.get(key) for key in ("route", "ending")} for q, f in record["detective"]["functions"].items()}
    record["done"] = decisions.admission_bar("written", setup, routes, call_order(contract)) == "pass"
    return finish("written")


def _seat_mode(path: object, current: object, handback: object, function: object) -> str:
    """Which operation a ``contract-write`` command line asks for (pure): ``write``, a first write (``--path`` alone) ·
    ``revise``, a revision (``--current`` with ``--handback`` and ``--function``) · ``mixed``, anything else."""
    if path is None and current is not None and handback is not None and function is not None:
        return "revise"
    if path is not None and current is None and handback is None and function is None:
        return "write"
    return "mixed"


def _revise_command(instructions: str, current_path: Path, handbacks_path: Path, function: str, out: Path, model_key: str) -> int:
    """``contract-write INSTRUCTIONS --current CONTRACT --handback HANDBACKS --function QUALNAME --out NEXT``: the
    receiver's ``contract_wrong`` through the seat (``revise_contract``). The next version lands at ``NEXT``, recorded in
    ``NEXT.revision.json`` with the decision, the contract it came from, the hand-back and what each function became; a
    refusal lands beside it, named refused. Exit codes: 0 revised · 1 unchanged (the receiver's again), or a refusal
    with what it refused · 2 unreadable, no hand-back for the function, refused before any call, or a failed call."""
    try:
        current = json.loads(current_path.read_text())
        records = [json.loads(line) for line in handbacks_path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        print("unreadable")
        return 2
    record = next((r for r in records if isinstance(r, dict) and r.get("function") == function), None)
    if record is None:
        print("unknown_handback")
        return 2
    code, new, done, _outcome = revise_contract(instructions, current, record, model_key)
    print(code)
    if code in ("revised", "unchanged"):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(new, indent=1, ensure_ascii=False))
        sidecar = {"decision": "contract_wrong", "function": function, "from": str(current_path.resolve()), "handback": record, **done}
        Path(f"{out}.revision.json").write_text(json.dumps(sidecar, indent=1, ensure_ascii=False, default=str))
        for key in ("kept", "changed", "dropped", "removed", "carried"):
            for name in done[key]:
                print(f"  {key} {name}")
        return 0 if code == "revised" else 1
    if new is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{out}.refused.json").write_text(json.dumps(new, indent=1, ensure_ascii=False))
        return 1
    return 2


def _seat_command(form_path: Path, out: Path, revises: Path | None = None) -> int:
    """``seat FORM --out CONTRACT``: a contract its driver writes (the founder, 2026-10-08: an outline to clean, pinned
    code). ``FORM`` is the contract writer's form (``contract_schema``, the shape the ``contract-write`` seat holds a
    model to), written by whoever holds the contract authority: a person, or the model driving the line. No model,
    Detective or Wesker is touched: ``seat_contract`` builds the stored contract and ``seat_verdict`` judges it. On
    ``ok`` the contract lands at ``CONTRACT`` and ``seated`` is printed, then each function in call order with its
    weight, as ``check`` prints them. A refusal prints its code and keeps what it refused in ``CONTRACT.refused.json``
    (the form, when the build refused it; the built contract, when the verdict did), and ``CONTRACT`` is not written.
    Exit codes: 0 seated · 1 refused · 2 ``unreadable`` (``FORM`` cannot be read as JSON; nothing is written)."""
    try:
        form = json.loads(form_path.read_text())
    except (OSError, ValueError):
        print("unreadable")
        return 2
    code, written = seat_contract(form)
    refused = form
    if code == "ok":
        path = written["path"] if isinstance(written, dict) and isinstance(written.get("path"), str) else ""
        code, refused = seat_verdict(written, path, [], []), written
    if code == "ok" and revises is not None:
        try:
            current = json.loads(revises.read_text())
        except (OSError, ValueError):
            print("unreadable_revision_base")
            return 2
        code, written, _details = revision(current, written)
        if code == "unchanged":
            print(code)
            return 1
        refused = written
        if code == "revised":
            code = "ok"
    out.parent.mkdir(parents=True, exist_ok=True)
    if code != "ok":
        print(code)
        Path(f"{out}.refused.json").write_text(json.dumps(refused, indent=1, ensure_ascii=False))
        return 1
    out.write_text(json.dumps(written, indent=1, ensure_ascii=False))
    print("seated")
    for qualname in call_order(written):
        print(f"{qualname}\tweight={call_weight(call_context(written, qualname))}")
    return 0


def main(argv: list | None = None) -> int:
    """``check CONTRACT`` (the verdict, then each call in order with its weight as the contract stands) ·
    ``seat FORM --out CONTRACT`` (a contract its driver writes, built from the contract writer's form and judged with
    no model call: the contract seated, or its refusal with what it refused in ``CONTRACT.refused.json``;
    ``_seat_command``) ·
    ``request CONTRACT FUNCTION`` (the exact request one call would send; nothing is sent) ·
    ``play CONTRACT --out DIR [--contract-writer WHO] [--admission WHO]`` · ``reissue CONTRACT FUNCTION --out DIR``
    (the receiver's ``code_wrong``: the next version in ``DIR/contract.json``, recorded in ``DIR/reissue.json``) ·
    ``contract-write INSTRUCTIONS --path MODULE.py --out CONTRACT`` (the contract writer's seat, one call: the
    contract written, or the seat's refusal with what it refused in ``CONTRACT.refused.json``) ·
    ``contract-write INSTRUCTIONS --current CONTRACT --handback HANDBACKS --function QUALNAME --out NEXT`` (the
    receiver's ``contract_wrong``: the next version, recorded in ``NEXT.revision.json``; ``_revise_command``). Exit
    codes: 0 written, and every function's ending is clean, or reissued, or a contract written, revised or seated · 1
    incomplete (each skipped function is printed), a function handed back, a contract the seat refused (a form
    ``seat`` refused among them), or a revision that changed nothing · 2 refused before any call (an unreadable file
    among them), a failed call, or the world is wrong for Detective · 3 an ending
    that could not be trusted (``decisions.possession_exit``). Each
    function's ending is printed with its class: Detective's own code (``audit:fix_failing_test``), or the harness code
    that ended its run."""
    parser = argparse.ArgumentParser(prog="uroboros", description="Write a module from its contract, one function per model call.")
    parser.add_argument("--model", choices=sorted(MODELS))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the JSON schema for the contract author")
    commands.add_parser("check").add_argument("contract")
    request = commands.add_parser("request")
    request.add_argument("contract")
    request.add_argument("function")
    run = commands.add_parser("play")
    run.add_argument("contract")
    run.add_argument("--out", required=True)
    run.add_argument("--contract-writer", default=None, help="who holds the contract authority for this unit (recorded only)")
    run.add_argument("--admission", default=None, help="who holds admission for this unit (recorded only)")
    again = commands.add_parser("reissue")
    again.add_argument("contract")
    again.add_argument("function")
    again.add_argument("--out", required=True)
    seat = commands.add_parser("contract-write")
    seat.add_argument("instructions", help="a text file: the instructions for the module")
    target = seat.add_mutually_exclusive_group(required=True)
    target.add_argument("--path", help="a first write: the module's path, as the contract names it")
    target.add_argument("--current", help="a revision: the module's contract as it stands")
    seat.add_argument("--handback", help="a revision: the hand-back records its play wrote (handbacks.jsonl)")
    seat.add_argument("--function", help="a revision: the function whose hand-back was decided contract_wrong")
    seat.add_argument("--out", required=True, help="where the contract is written")
    line = commands.add_parser("line")
    line.add_argument("contract")
    line.add_argument("--mode", choices=("local", "escalate"), default="local")
    line.add_argument("--ceiling", type=int, default=2)
    line.add_argument("--out", required=True)
    line.add_argument("--contract-writer", default=None)
    line.add_argument("--target", help="admit the completed unit into this repository")
    driver = commands.add_parser("seat")
    driver.add_argument("form", help="a JSON file: the module in the contract writer's form")
    driver.add_argument("--out", required=True, help="where the seated contract is written")
    driver.add_argument("--revises", type=Path, help="the exact contract snapshot of the version being revised")
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(contract_schema(), ensure_ascii=False, indent=2))
        return 0
    args.model = args.model or (LINE_MODEL if args.command == "line" else DEFAULT_MODEL)
    if args.command == "line":
        from .line import execute_line

        if args.ceiling < 1:
            line.error("--ceiling must be positive")
        result = execute_line(
            Path(args.contract),
            Path(args.out),
            args.model,
            args.mode,
            args.ceiling,
            contract_writer=args.contract_writer,
            target=Path(args.target) if args.target else None,
        )
        print(result["outcome"])
        return 0 if result["outcome"] == "done" else 2 if result["outcome"] == "out_exists" else 1
    if args.command == "seat":
        return _seat_command(Path(args.form), Path(args.out), args.revises)
    if args.command == "contract-write":
        mode = _seat_mode(args.path, args.current, args.handback, args.function)
        if mode == "mixed":
            seat.error("a revision takes --current with --handback and --function; a first write takes --path alone")
        try:
            instructions = Path(args.instructions).read_text()
        except OSError:
            print("unreadable")
            return 2
        if mode == "revise":
            return _revise_command(instructions, Path(args.current), Path(args.handback), args.function, Path(args.out), args.model)
        code, written, _outcome = write_contract(instructions, args.path, args.model)
        print(code)
        if written is not None:
            target = Path(args.out) if code == "written" else Path(f"{args.out}.refused.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(written, indent=1, ensure_ascii=False))
        return 0 if code == "written" else 1 if written is not None else 2
    if args.command == "play":
        result = play(Path(args.contract), Path(args.out), args.model, contract_writer=args.contract_writer, admission=args.admission)
        print(result["outcome"])
        for skipped in result["skipped"]:
            print(f"  skipped {skipped}")
        if result["outcome"] != "written":
            return result.get("exit_code", 2)
        driven = result["detective"]
        print(f"  detective {driven['setup']['ending']} ({driven['setup']['class']}): the folder")
        for qualname, outcome in driven["functions"].items():
            print(f"  detective {outcome['ending']} ({outcome['class']}): {qualname}")
        return result["exit_code"]
    try:
        contract = json.loads(Path(args.contract).read_text())
    except (OSError, ValueError):
        print("unreadable")
        return 2
    verdict = contract_verdict(contract)
    if verdict != "ok":
        print(verdict)
        return 2
    if args.command == "check":
        print("ok")
        for qualname in call_order(contract):
            print(f"{qualname}\tweight={call_weight(call_context(contract, qualname))}")
        return 0
    if args.command == "reissue":
        code, new, done = reissue(contract, args.function)
        print(code)
        if code != "reissued":
            return 2
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "contract.json").write_text(json.dumps(new, indent=1, ensure_ascii=False))
        sidecar = {"decision": "code_wrong", "function": args.function, "from": str(Path(args.contract).resolve()), **done}
        (out / "reissue.json").write_text(json.dumps(sidecar, indent=1, ensure_ascii=False))
        for key in ("cleared", "removed", "kept"):
            for name in done[key]:
                print(f"  {key} {name}")
        return 0
    record = MODELS[args.model]
    result = call_payload(contract, args.function, record, wire.resolve_api(record["api"]))
    if isinstance(result, str):
        print(result)
        return 2
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
