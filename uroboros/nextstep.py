"""The typed next-step — Detective's `DO THIS` brain, re-voiced as JSON for the model.

Detective renders one guided next action per state from `cli._derive_inputs` and its
boundary-derivation tree. That derivation is the truth; the CLI prose is one *mouth* on it.
This module is a SECOND mouth: it re-derives the same `(kind, items)` from Detective's
`converge --json` survivor_report — the stable data contract — and hands it to the small
model as structured input, instead of scraping the typeset (and re-typeset-able) prose.

FAITHFUL PORT of Detective/cli.py's `_derive_inputs` + `_boundary_hint` tree (and
equivalence.py's `is_expressible`). Kept deliberately close to the source so the two can be
diffed. One documented seam: `is_expressible` runs on LIVE witness values Detective holds;
`converge --json` has already stringified them (`default=str`), so the witness→test split
here uses the same literal round-trip `--input` itself enforces (`synth._tuple_literal`) and
a misclass degrades to a wasted `--input` the no-progress guard absorbs — never a false kill.

DRIFT NOTE: if Detective changes its derivation logic, this copy must be updated in lockstep.
It keys off the JSON data contract (diff_summary / missing_lines / param_names), which is
robust to cosmetic prose changes but NOT to a change in what the oracle derives.

The kinds, in Detective's priority order — which is also the DIFFICULTY LADDER the model
bake-off was built around (who can supply the input this step needs):
  witness   — [tier 1: pure script] engine already RAN the input; paste it as --input, no model
  test      — [tier 4: purposivistic] the witness is a captured OBJECT, not a typeable literal → human
  lines     — [tier 2: any dolt]  a dark line outranks an edge (coverage precedes kills); the model's staple
  boundary  — [tier 3: complex-but-obvious] engine PROVED an edge (`where qty == 0`) → the model drives it,
              the tier the bake-off qualified qwen3:4b for
  internal  — [tier 4: purposivistic] sits behind a DERIVED LOCAL → certified abstention (never spin)
  author    — [tier 4: purposivistic] nothing derived; you supply the value only you know
"""
from __future__ import annotations

import ast
import difflib
import textwrap

from .synth import _tuple_literal


# ── comparison parsing (Detective cli._comparisons) ───────────────────────────
def _comparisons(src: str) -> list[tuple[str, type, str]]:
    """(left_src, op_class, right_src) for each single-operator comparison in a line,
    normalizing `if …:` / `elif …:` headers so the fragment parses on its own."""
    s = src.strip()
    if s.startswith("elif "):
        s = "if " + s[len("elif "):]
    if s.endswith(":"):
        s += "\n    pass"
    try:
        tree = ast.parse(s)
    except SyntaxError:
        return []
    out: list[tuple[str, type, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            out.append((ast.unparse(node.left), type(node.ops[0]), ast.unparse(node.comparators[0])))
    return out


_ORDERING_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_HOLDS_AT_EQ = (ast.LtE, ast.GtE)


def _differs_at_eq(op: type, m_op: type) -> bool:
    """Do two ordering comparisons disagree when their operands are EQUAL? True iff exactly
    one holds at the edge — a strict↔non-strict shift. `<`→`<=` yes; `<=`→`>=` no (both True
    at ==); `<`→`>` no (both False)."""
    return op in _ORDERING_OPS and m_op in _ORDERING_OPS and (op in _HOLDS_AT_EQ) != (m_op in _HOLDS_AT_EQ)


def _difference_region(op: type, m_op: type, left: str, right: str) -> str | None:
    """The relation an input must satisfy for original and mutated comparison to DISAGREE —
    the region a distinguishing witness lands in, else None."""
    if _differs_at_eq(op, m_op):
        return f"{left} == {right}"
    pair = {op, m_op}
    if pair == {ast.GtE, ast.Eq}:
        return f"{left} > {right}"
    if pair == {ast.LtE, ast.Eq}:
        return f"{left} < {right}"
    if pair in ({ast.Gt, ast.Eq}, {ast.Lt, ast.Eq}):
        return f"{left} == {right}"
    return None


# ── control-dependence: is the comparison provably always evaluated? ──────────
def _may_divert(node: ast.AST) -> bool:
    """Can executing `node` route control away from the statement after it? Return/raise
    anywhere within (a conditional early exit counts), an unbounded `while`, or `try`/`match`.
    Nested function/class/lambda scopes are not descended."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return False
    if isinstance(node, (ast.Return, ast.Raise, ast.Break, ast.Continue, ast.While, ast.Try, ast.Match)):
        return True
    if hasattr(ast, "TryStar") and isinstance(node, ast.TryStar):
        return True
    return any(_may_divert(child) for child in ast.iter_child_nodes(node))


def _predecessors_fall_through(parent: ast.AST, child: ast.stmt) -> bool:
    """True when every statement BEFORE `child` in its suite provably falls through to it."""
    for field in ("body", "orelse", "finalbody"):
        suite = getattr(parent, field, None)
        if isinstance(suite, list) and child in suite:
            return all(not _may_divert(prior) for prior in suite[: suite.index(child)])
    return True  # child is a test/iter expr, not a suite member


def _always_evaluated(orig_src: str, left: str, op: type, right: str) -> bool:
    """True only when the matched comparison PROVABLY evaluates on every call — no enclosing
    branch, loop body, short-circuit position, ternary arm, try, match, or nested scope
    decides whether it runs, and no sequential predecessor can divert control before it.
    Anything unparseable or unlocatable is False (a claim this cannot verify abstains)."""
    try:
        tree = ast.parse(textwrap.dedent(orig_src))
    except SyntaxError:
        return False
    if not tree.body or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    fn = tree.body[0]
    target: ast.Compare | None = None
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) is op
                and ast.unparse(node.left) == left and ast.unparse(node.comparators[0]) == right):
            target = node
            break
    if target is None:
        return False

    def path_to(node: ast.AST) -> list[ast.AST] | None:
        if node is target:
            return [node]
        for child in ast.iter_child_nodes(node):
            sub = path_to(child)
            if sub is not None:
                return [node, *sub]
        return None

    path = path_to(fn)
    if path is None:
        return False
    for parent, child in zip(path, path[1:], strict=False):
        if isinstance(child, ast.stmt) and not _predecessors_fall_through(parent, child):
            return False
        if parent is fn:
            continue
        if isinstance(parent, (ast.If, ast.While)):
            if child is not parent.test:
                return False
        elif isinstance(parent, (ast.For, ast.AsyncFor)):
            if child is not parent.iter:
                return False
        elif isinstance(parent, ast.IfExp):
            if child is not parent.test:
                return False
        elif isinstance(parent, ast.BoolOp):
            if child is not parent.values[0]:  # only the first operand always evaluates
                return False
        elif isinstance(parent, (ast.Try, ast.ExceptHandler, ast.Match, ast.ListComp, ast.SetComp,
                                 ast.DictComp, ast.GeneratorExp, ast.Lambda, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            return False
    return True


def _params_only(left: str, right: str, param_names: tuple[str, ...]) -> bool:
    """True when every name the comparison reads is a function parameter — the one case where
    `supply an input where {region}` is literally satisfiable. A derived local makes it internal."""
    for src in (left, right):
        try:
            tree = ast.parse(src, mode="eval")
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in param_names:
                return False
    return True


def _boundary_hint(diff_summary: str, param_names: tuple[str, ...] | None = None) -> str | None:
    """For a BOUNDARY mutant — an operator shift on a comparison — name the region a
    distinguishing input must land in, or classify it as an internal condition (a derived
    local, or one behind enclosing control flow). None if no rule names the region."""
    marker = "\n+ "
    if not (diff_summary.startswith("- ") and marker in diff_summary):
        return None
    idx = diff_summary.index(marker)
    orig_lines = diff_summary[2:idx].splitlines()
    mut_lines = diff_summary[idx + len(marker):].splitlines()
    o_changed: list[str] = []
    m_changed: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=orig_lines, b=mut_lines).get_opcodes():
        if tag == "equal":
            continue
        o_changed += orig_lines[i1:i2]
        m_changed += mut_lines[j1:j2]
    m_cmps = [c for ln in m_changed for c in _comparisons(ln)]
    for ln in o_changed:
        for left, op, right in _comparisons(ln):
            for m_left, m_op, m_right in m_cmps:
                if (m_left == left and m_right == right
                        and (region := _difference_region(op, m_op, left, right))):
                    if param_names is not None and not _params_only(left, right, param_names):
                        return (f"internal condition `{region}` decides this — not a direct input "
                                "constraint; Detective could not derive a verified call from the "
                                "parameters")
                    if param_names is not None and not _always_evaluated("\n".join(orig_lines), left, op, right):
                        return (f"internal condition `{region}` sits behind enclosing control flow "
                                "— the relation alone is not path-complete; supply a call that "
                                "reaches this comparison and lands on the edge")
                    return f"distinguish at the boundary — supply an input where {region}"
    return None


def _is_internal_hint(hint: str) -> bool:
    """Classify a rendered `_boundary_hint` line without re-deriving it."""
    return hint.startswith("internal condition ")


def _hint_relation(hint: str) -> str:
    """The bare relation out of a boundary hint — 'where amt == 0'."""
    tail = hint.split("—", 1)[-1].strip()
    return tail[len("supply an input "):] if tail.startswith("supply an input ") else tail


# ── line gaps and witnesses, off the JSON state ───────────────────────────────
def _line_gap_items(state: dict) -> list[str]:
    """Uncovered lines as REACH requirements — 'line 30 — reached only when: <guard>'.
    Guards are per-line and may be absent (an unconditional line sits behind no branch)."""
    guards = dict(state.get("missing_line_guards") or ())
    return [
        f"line {ln} — reached only when: {guards[ln]}" if ln in guards else f"line {ln} — reach it"
        for ln in (state.get("missing_lines") or ())
    ]


def _witness_tuple(witness: dict) -> str | None:
    """A witness's args as a paste-able positional tuple literal, or None if any arg is not a
    literal `--input` can carry. This is the JSON stand-in for `is_expressible`: the same
    round-trip `--input` enforces, run on the (already-serialized) witness args."""
    args = witness.get("args")
    return _tuple_literal(list(args)) if isinstance(args, (list, tuple)) else None


def _witness_desc(witness: dict) -> str:
    """A human description of a non-typeable witness's args — for the `test` kind."""
    args = witness.get("args") or []
    return ", ".join(f"{type(a).__name__}: {repr(a)[:70]}" for a in args) or "(no args)"


def derive_next_step(state: dict) -> dict:
    """Detective's `_derive_inputs`, re-derived from a `converge --json` state.

    Returns `{"kind", "items", "total"}` — the single typed next action for this function,
    in Detective's priority order (witness > test > lines > boundary > internal > author).
    `items` is uncapped here (Uroboros is one function at a time, not a batch surface).
    """
    verdicts = (state.get("survivor_report") or {}).get("verdicts", []) or []
    param_names = tuple(state.get("param_names") or ()) or None

    killable = [v for v in verdicts if v.get("killable")]
    equivalent = [v for v in verdicts if not v.get("killable") and not v.get("crash_only")]

    witnesses = [v for v in killable if v.get("witness")]
    typeable = [v for v in witnesses if _witness_tuple(v["witness"]) is not None]
    if typeable:
        return {"kind": "witness",
                "items": [_witness_tuple(v["witness"]) for v in typeable],
                "total": len(typeable)}
    if witnesses:
        return {"kind": "test", "items": [_witness_desc(v["witness"]) for v in witnesses],
                "total": len(witnesses)}

    hints: list[str] = []
    internal: list[str] = []
    for v in equivalent:
        h = _boundary_hint(v.get("diff_summary", ""), param_names)
        if not h:
            continue
        if _is_internal_hint(h):
            if h not in internal:
                internal.append(h)
        elif (rel := _hint_relation(h)) not in hints:
            hints.append(rel)

    # A DARK LINE OUTRANKS AN EQUIVALENT'S EDGE — coverage is a precondition for the kill axis.
    if gaps := _line_gap_items(state):
        return {"kind": "lines", "items": gaps, "total": len(gaps)}
    if hints:
        return {"kind": "boundary", "items": hints, "total": len(hints)}
    if internal:
        return {"kind": "internal", "items": internal, "total": len(internal)}
    return {"kind": "author", "items": [], "total": 0}
