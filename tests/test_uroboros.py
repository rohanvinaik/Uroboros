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
from uroboros import drive
from uroboros.drive import (
    _changed_line_ranges,
    _functions_in_ranges,
    changed_targets,
    enumerate_targets,
)
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
    def test_module_functions_only_skips_methods_tests_dunders(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def alpha():\n    pass\n\n"
            "def __hidden():\n    pass\n\n"
            "class Box:\n    def open(self):\n        pass\n    def __init__(self):\n        pass\n\n"
            "def beta():\n    def nested():\n        pass\n    return nested\n"
        )
        (tmp_path / "test_mod.py").write_text("def test_alpha():\n    pass\n")
        (tmp_path / "conftest.py").write_text("def fixture_thing():\n    pass\n")
        targets = list(enumerate_targets(tmp_path, tmp_path))
        # methods (Box.open) are skipped until Detective can pin a receiver (#25);
        # dunders, nested locals, and test files are skipped too — module functions only
        assert targets == ["mod.py::alpha", "mod.py::beta"]

    def test_skips_venv_vcs_cache_and_build_trees(self, tmp_path):
        (tmp_path / "src.py").write_text("def real():\n    pass\n")
        for buried in (".venv/lib/dep.py", "venv/dep.py", "node_modules/x.py",
                       "build/gen.py", "__pycache__/c.py", ".git/hooks/h.py", "pkg.egg-info/e.py"):
            p = tmp_path / buried
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("def buried():\n    pass\n")
        # only the project's own source is crawled — never a virtualenv or a cache tree
        assert list(enumerate_targets(tmp_path, tmp_path)) == ["src.py::real"]

    def test_scopes_to_the_library_package_not_tests_data_or_venv(self, tmp_path):
        (tmp_path / "mypkg").mkdir()
        (tmp_path / "mypkg" / "__init__.py").write_text("")
        (tmp_path / "mypkg" / "core.py").write_text("def real():\n    pass\n")
        for aux in ("tests", "data", "benchmarks"):          # packages, but never the library
            (tmp_path / aux).mkdir()
            (tmp_path / aux / "__init__.py").write_text("")
            (tmp_path / aux / "x.py").write_text("def aux():\n    pass\n")
        (tmp_path / ".venv" / "lib").mkdir(parents=True)
        (tmp_path / ".venv" / "lib" / "dep.py").write_text("def dep():\n    pass\n")
        # a flat-layout repo resolves to its actual package — not tests/, data/, or .venv/
        assert list(enumerate_targets(tmp_path, tmp_path)) == ["mypkg/core.py::real"]


# ── diff-mode pure core — parse a diff, map ranges to functions ───────────────
class TestChangedLineRanges:
    def test_keeps_new_side_span_strips_b_prefix(self):
        diff = "+++ b/pkg/mod.py\n@@ -10,0 +11,3 @@ def ctx\n+x\n+y\n+z\n"
        assert _changed_line_ranges(diff) == {"pkg/mod.py": [(11, 13)]}

    def test_omitted_count_means_one_line(self):
        diff = "+++ b/m.py\n@@ -5 +7 @@\n+one\n"
        assert _changed_line_ranges(diff) == {"m.py": [(7, 7)]}

    def test_drops_pure_deletion_and_dev_null(self):
        # a deletion-only hunk (new-count 0) maps to no current line; /dev/null is a
        # removed file — neither should appear in the crawl set
        diff = ("+++ b/keep.py\n@@ -20 +24,0 @@\n-gone\n"
                "+++ /dev/null\n@@ -1,2 +0,0 @@\n-def dead(): pass\n")
        assert _changed_line_ranges(diff) == {}


class TestFunctionsInRanges:
    SRC = "def a():\n    pass\n\ndef b():\n    x = 1\n    return x\n\ndef __hidden():\n    pass\n"

    def test_only_functions_overlapping_a_range(self):
        assert _functions_in_ranges(self.SRC, [(5, 5)]) == ["b"]   # line 5 is inside b
        assert _functions_in_ranges(self.SRC, [(1, 1)]) == ["a"]

    def test_skips_dunder_even_when_in_range(self):
        assert _functions_in_ranges(self.SRC, [(8, 8)]) == []      # __hidden excluded

    def test_no_overlap_yields_nothing(self):
        assert _functions_in_ranges(self.SRC, [(3, 3)]) == []      # blank gap between a and b

    def test_unparseable_source_is_empty_not_a_crash(self):
        assert _functions_in_ranges("def oops(:\n", [(1, 1)]) == []


class TestChangedTargets:
    def test_end_to_end_crawls_only_the_changed_function(self, tmp_path):
        import subprocess

        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef same(x):\n    return x\n")
        run = lambda *args: subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True)
        run("init"); run("-c", "user.email=x@x", "-c", "user.name=x", "add", "-A")
        run("-c", "user.email=x@x", "-c", "user.name=x", "commit", "-m", "base")
        # change ONLY `add`; `same` is untouched
        (tmp_path / "calc.py").write_text(
            "def add(a, b):\n    if a < 0:\n        return b\n    return a + b\n\n\ndef same(x):\n    return x\n"
        )
        assert list(changed_targets(tmp_path, "HEAD")) == ["calc.py::add"]


class TestEngineResolver:
    """The engine-runtime ladder's IMPURE shell — the FS/env probing Detective structurally
    cannot pin. The pure DECISION (`_engine_prefix`) is pinned by the Detective-synth suite;
    this proves the shell feeds it the right facts across repo shapes, so a globally-installed
    Uroboros runs the engine WHERE the project's deps live instead of blind in its own env.
    """

    def _no_active_env(self, monkeypatch):
        # Neutralise the host's activated venv so a real $VIRTUAL_ENV can't leak into the probe.
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    def test_isolated_venv_with_uv_takes_the_bridge(self, tmp_path, monkeypatch):
        self._no_active_env(monkeypatch)
        monkeypatch.setattr(drive.shutil, "which", lambda _: "/usr/bin/uv")
        (tmp_path / ".venv" / "bin").mkdir(parents=True)   # isolates deps, but engine NOT in it
        prefix, cwd = drive._resolve_engine(tmp_path)
        assert prefix == ["/usr/bin/uv", "run", "--no-sync", "--with", "detective-spec", "detective"]
        assert cwd == str(tmp_path)   # uv discovers the project from cwd

    def test_uv_lock_alone_signals_isolation(self, tmp_path, monkeypatch):
        self._no_active_env(monkeypatch)
        monkeypatch.setattr(drive.shutil, "which", lambda _: "/usr/bin/uv")
        (tmp_path / "uv.lock").write_text("")   # a lock without a materialised .venv still isolates
        assert drive._resolve_engine(tmp_path)[0][0] == "/usr/bin/uv"

    def test_no_isolation_runs_the_global_engine(self, tmp_path, monkeypatch):
        self._no_active_env(monkeypatch)
        monkeypatch.setattr(drive.shutil, "which", lambda _: "/usr/bin/uv")
        assert drive._resolve_engine(tmp_path) == (["detective"], None)   # nothing to miss

    def test_isolated_without_uv_degrades_and_warns(self, tmp_path, monkeypatch, capsys):
        self._no_active_env(monkeypatch)
        monkeypatch.setattr(drive.shutil, "which", lambda _: None)   # no uv bridge available
        (tmp_path / ".venv").mkdir()
        prefix, cwd = drive._resolve_engine(tmp_path)
        assert (prefix, cwd) == (["detective"], None)                # policy A: run global, keep crawling
        assert "isolates its dependencies" in capsys.readouterr().out  # …but say so, once

    def test_engine_in_repo_venv_beats_the_bridge(self, tmp_path, monkeypatch):
        self._no_active_env(monkeypatch)
        monkeypatch.setattr(drive.shutil, "which", lambda _: "/usr/bin/uv")
        binp = tmp_path / ".venv" / "bin"
        binp.mkdir(parents=True)
        (binp / "detective").write_text("#!/bin/sh\n")   # engine installed IN the repo's venv
        prefix, cwd = drive._resolve_engine(tmp_path)
        assert prefix == [str(binp / "detective")] and cwd is None   # use it directly, no uv needed

    def test_resolution_is_cached_per_root(self, tmp_path, monkeypatch):
        self._no_active_env(monkeypatch)
        calls = []
        monkeypatch.setattr(drive.shutil, "which", lambda x: calls.append(x) or "/usr/bin/uv")
        (tmp_path / "uv.lock").write_text("")
        drive._resolve_engine(tmp_path)
        drive._resolve_engine(tmp_path)
        assert len(calls) == 1   # hundreds of per-function det() calls must not re-probe the tree


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
