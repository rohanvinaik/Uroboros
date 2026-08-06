"""Guard-tests for the nextstep port — faithfulness to Detective's own derivation.

`uroboros/nextstep.py` re-implements Detective/cli's `_derive_inputs` + `_boundary_hint` tree.
Its correctness IS equality with the source, so the load-bearing test is DIFFERENTIAL: feed
the same inputs to both and assert identical output. It importorskips if Detective's private
helpers move (a rename is a structural change, not a logic bug — the value tests below still
pin behavior); a genuine MISMATCH is the drift alarm the DRIFT NOTE in nextstep.py warns of.

These functions take AST objects / valid diff strings / converge-state dicts — inputs the small
model cannot synthesize — so Detective leaves them 'unclosed' on a self-crawl. This unit suite
is the documented exemption (Detective conventions: engine-core that can't self-profile → unit).
"""
import pytest

from uroboros import nextstep as ns


def _ds(orig_body: str, mut_body: str) -> str:
    """A Detective-shaped diff_summary: '- <whole original>\\n+ <whole mutant>'."""
    head = "def f(x: int, y: int) -> str:\n"
    return "- " + head + orig_body + "\n+ " + head + mut_body


DIFFS = {
    "strict_shift":         _ds("    if x >= 60:\n        return 'a'\n    return 'b'",
                                "    if x > 60:\n        return 'a'\n    return 'b'"),
    "behind_control_flow":  _ds("    if y < 0:\n        return 'z'\n    if x >= 60:\n        return 'a'\n    return 'b'",
                                "    if y < 0:\n        return 'z'\n    if x > 60:\n        return 'a'\n    return 'b'"),
    "ge_to_eq":             _ds("    if x >= 5:\n        return 'a'\n    return 'b'",
                                "    if x == 5:\n        return 'a'\n    return 'b'"),
    "le_to_ge_flip":        _ds("    if x <= 5:\n        return 'a'\n    return 'b'",
                                "    if x >= 5:\n        return 'a'\n    return 'b'"),
    "operand_change":       _ds("    if x >= 0:\n        return 'a'\n    return 'b'",
                                "    if x >= -1:\n        return 'a'\n    return 'b'"),
    "derived_local":        _ds("    r = x * 2\n    if r >= 10:\n        return 'a'\n    return 'b'",
                                "    r = x * 2\n    if r > 10:\n        return 'a'\n    return 'b'"),
}
PN = ("x", "y")


# ── the drift alarm: my derivation must equal Detective's own ─────────────────
class TestFaithfulToDetective:
    @pytest.mark.parametrize("name", list(DIFFS))
    def test_boundary_hint_matches_source(self, name):
        det = pytest.importorskip("Detective.cli")
        if not hasattr(det, "_boundary_hint"):
            pytest.skip("Detective._boundary_hint moved — differential guard n/a; value tests still pin")
        assert ns._boundary_hint(DIFFS[name], PN) == det._boundary_hint(DIFFS[name], PN)


# ── behavior pinned independently of Detective's importability ────────────────
class TestBoundaryHintValues:
    def test_strict_shift_is_boundary_edge(self):
        assert ns._boundary_hint(DIFFS["strict_shift"], PN) == \
            "distinguish at the boundary — supply an input where x == 60"

    def test_ge_to_eq_collapses_to_strict_side(self):
        assert ns._boundary_hint(DIFFS["ge_to_eq"], PN) == \
            "distinguish at the boundary — supply an input where x > 5"

    def test_behind_control_flow_is_internal(self):
        assert ns._is_internal_hint(ns._boundary_hint(DIFFS["behind_control_flow"], PN))

    def test_derived_local_operand_is_internal(self):
        assert ns._is_internal_hint(ns._boundary_hint(DIFFS["derived_local"], PN))

    def test_direction_flip_names_no_region(self):
        assert ns._boundary_hint(DIFFS["le_to_ge_flip"], PN) is None   # both True at ==

    def test_operand_change_is_not_an_op_shift(self):
        assert ns._boundary_hint(DIFFS["operand_change"], PN) is None


# ── derive_next_step: the priority order, off crafted converge states ─────────
class TestDeriveNextStep:
    def _state(self, **kw) -> dict:
        base = {"survivor_report": {"verdicts": []}, "missing_lines": [],
                "missing_line_guards": [], "param_names": ["x", "y"]}
        base.update(kw)
        return base

    def _equiv(self, diff, category="BOUNDARY"):
        return {"killable": False, "crash_only": False, "category": category, "diff_summary": diff}

    def test_witness_outranks_everything_and_pastes(self):
        st = self._state(missing_lines=[9],
                         survivor_report={"verdicts": [{"killable": True, "witness": {"args": [1, "gold"]}}]})
        out = ns.derive_next_step(st)
        assert out["kind"] == "witness" and out["items"] == ["(1, 'gold')"]

    def test_lines_outrank_boundary(self):
        # coverage is a precondition: a dark line beats an equivalent's edge
        st = self._state(missing_lines=[12], survivor_report={"verdicts": [self._equiv(DIFFS["strict_shift"])]})
        out = ns.derive_next_step(st)
        assert out["kind"] == "lines" and out["items"] == ["line 12 — reach it"]

    def test_line_guard_is_rendered(self):
        st = self._state(missing_lines=[7], missing_line_guards=[[7, "x > 3"]])
        assert ns.derive_next_step(st)["items"] == ["line 7 — reached only when: x > 3"]

    def test_boundary_when_no_line_gap(self):
        st = self._state(survivor_report={"verdicts": [self._equiv(DIFFS["strict_shift"])]})
        assert ns.derive_next_step(st)["kind"] == "boundary"

    def test_internal_when_derived_local(self):
        st = self._state(survivor_report={"verdicts": [self._equiv(DIFFS["derived_local"])]})
        assert ns.derive_next_step(st)["kind"] == "internal"

    def test_author_when_nothing_derived(self):
        assert ns.derive_next_step(self._state())["kind"] == "author"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
