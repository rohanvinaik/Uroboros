"""Pure-logic guard-tests for Uroboros' control-flow and model-constraint code.

These run WITHOUT Detective or Ollama — they pin the deterministic surfaces the
crawl's correctness rests on: the regime-target derivation (a real bug once
shipped here), the one place call syntax is written, and the schema whose whole
job is to make a malformed model call structurally impossible. The engine loop
itself (det/process_function) is exercised by the integration flex, not here.
"""
import ast

import pytest

from uroboros.cycle import _regime_target
from uroboros.drive import enumerate_targets
from uroboros.synth import (
    _base_type,
    _mine_literals,
    _mine_mapping_keys,
    _scalar_pool,
    _signature_spec,
    _tuple_literal,
    build_synth_schema,
)


# ── _regime_target — guards the bug this suite was born from ──────────────────
class TestRegimeTarget:
    def test_bare_file_becomes_empty_whole_repo_target(self):
        # a bare file.py is REFUSED by `detective regime`; resolve the whole repo
        assert _regime_target("shipping.py") == ""

    def test_directory_becomes_empty_target(self):
        assert _regime_target("src/") == ""

    def test_function_target_passes_through_intact(self):
        # the exact regression: file.py::func must reach regime WHOLE, not split
        assert _regime_target("shipping.py::classify_discount") == "shipping.py::classify_discount"


# ── _tuple_literal — the ONLY place call syntax is written; must be airtight ──
class TestTupleLiteral:
    def test_single_arg_gets_trailing_comma(self):
        assert _tuple_literal([5]) == "(5,)"

    def test_multi_arg_positional_order(self):
        assert _tuple_literal([6, 7]) == "(6, 7)"

    def test_string_arg_is_quoted_literal(self):
        assert _tuple_literal(["VIP"]) == "('VIP',)"

    def test_round_trips_through_literal_eval(self):
        lit = _tuple_literal([6, 7])
        assert ast.literal_eval(lit) == (6, 7)

    def test_rejects_value_that_does_not_round_trip(self):
        # repr(nan) == 'nan', a bare Name literal_eval refuses — must return None,
        # never hand detective a broken --input
        assert _tuple_literal([float("nan")]) is None


# ── build_synth_schema — arity + type locked, structurally ───────────────────
class TestSynthSchema:
    def _schema(self):
        spec = [("total", "float"), ("coupon", "str")]
        lits = _mine_literals("def f(total, coupon):\n    return coupon == 'VIP' or total > 100")
        return build_synth_schema(spec, lits, "")

    def test_call_is_object_with_every_param_required(self):
        call_obj = self._schema()["properties"]["calls"]["items"]
        assert call_obj["required"] == ["total", "coupon"]         # arity locked
        assert call_obj["additionalProperties"] is False           # no extra args
        assert set(call_obj["properties"]) == {"total", "coupon"}

    def test_scalar_slot_is_enum_or_typed_wildcard(self):
        slot = self._schema()["properties"]["calls"]["items"]["properties"]["coupon"]
        legs = slot["anyOf"]
        assert {"type": "string"} in legs                          # type locked
        assert any("enum" in leg for leg in legs)                  # mined menu offered


# ── the mining helpers — the closed vocabulary is derived, not guessed ────────
class TestMining:
    def test_base_type_reduces_generics_and_unions(self):
        assert _base_type("list[str]") == "list"
        assert _base_type("str | None") == "str"
        assert _base_type("dict") == "dict"
        assert _base_type("Optional[int]") == ""                   # unknown → wildcard

    def test_signature_spec_reads_annotations(self):
        spec = _signature_spec("def f(a: int, b: str):\n    pass", "f")
        assert spec == [("a", "int"), ("b", "str")]

    def test_mine_literals_excludes_bools_captures_values(self):
        lits = _mine_literals("def f(x):\n    return x == 90 or x == 'VIP' or True")
        assert 90 in lits["int"]
        assert "VIP" in lits["str"]

    def test_mine_mapping_keys_from_get_and_subscript(self):
        src = "def f(d):\n    return d.get('a') + d['b']"
        assert _mine_mapping_keys(src, "d") == ["a", "b"]

    def test_scalar_pool_seeds_boundary_neighbours(self):
        # off-by-one mutants need a ±1 witness around every mined int
        pool = _scalar_pool("int", {"int": {10}, "str": set(), "float": set()})
        assert 9 in pool and 11 in pool


# ── enumerate_targets — the crawl set: skip tests, keep source order ──────────
class TestEnumerateTargets:
    def test_skips_tests_dunders_and_keeps_source_order(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def alpha():\n    pass\n\n"
            "def __hidden():\n    pass\n\n"
            "def beta():\n    def nested():\n        pass\n    return nested\n"
        )
        (tmp_path / "test_mod.py").write_text("def test_alpha():\n    pass\n")
        (tmp_path / "conftest.py").write_text("def fixture_thing():\n    pass\n")
        targets = list(enumerate_targets(tmp_path, tmp_path))
        assert targets == ["mod.py::alpha", "mod.py::beta"]        # order, no dunder/nested/tests


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
