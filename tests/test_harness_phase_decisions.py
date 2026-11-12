"""The pure decisions the Detective phase's driver dispatches on (docs/builds/2026-10-08/WIRING.md, W3 and W4).

Each is total over plain data and returns a named code: what a ``--json`` run's payload leaves the harness to read, the
class of a finished run, the ask the values are mined from, the keys of what the judgment seat could not tell, the flag
command, and who receives a hand-back the seat could not answer. They are also Detective-pinned (``tests/detective/``);
these say what they are for.
"""

from __future__ import annotations

from uroboros.harness import decisions


def test_a_payload_gives_its_decision_or_the_ending_that_stands_in_its_place():
    """The record is the payload's ``decision``. Without one the run ends under a code that says why: a typed refusal is
    ``<verb>:refused:<reason>``; no payload is ``<verb>:refused:<refusal>`` when Detective's ledger names one (its
    text-only exits print no JSON) and ``detective:unparsed`` when it does not; a payload with no decision is
    ``<verb>:no_decision``."""
    record = {"verb": "audit", "code": "close_the_gap", "step": {"kind": "run"}}
    assert decisions.payload_decision("audit", {"decision": record, "exit_code": 0}, None) is record
    assert decisions.payload_decision("audit", {"verdict": "REFUSED", "reason": "collection_errors", "exit_code": 2}, None) == "audit:refused:collection_errors"
    assert decisions.payload_decision("converge", {"verdict": "REFUSED"}, None) == "converge:refused:unnamed"
    assert decisions.payload_decision("converge", {"verdict": "REFUSED", "reason": ""}, None) == "converge:refused:unnamed"
    assert decisions.payload_decision("converge", {"verdict": "REFUSED", "reason": 3}, None) == "converge:refused:unnamed"
    assert decisions.payload_decision("converge", None, "target_not_found") == "converge:refused:target_not_found"
    assert decisions.payload_decision("converge", None, None) == decisions.payload_decision("converge", None, "") == "detective:unparsed"
    assert decisions.payload_decision("converge", None, 7) == "detective:unparsed"
    assert decisions.payload_decision("audit", {"exit_code": 0}, None) == "audit:no_decision"
    assert decisions.payload_decision("audit", {"decision": "a page"}, None) == "audit:no_decision"
    assert decisions.payload_decision("audit", [1, 2], "target_not_found") == "audit:refused:target_not_found"  # not an object: no payload
    assert decisions.payload_decision("", {"decision": record}, None) == decisions.payload_decision(None, None, None) == "invalid"


def test_a_run_is_clean_only_when_detective_s_own_done_ended_it():
    """``clean`` is for the route ``done``. A run that did not reach Detective's done is never clean, whatever its last
    code said (``converge:settled`` is clean only as a function's done), and a clean code on a hand-back reads as one."""
    assert decisions.run_class("done", "converge:settled", 0) == "clean"
    assert decisions.run_class("done", "regime:clean", None) == "clean"
    assert decisions.run_class("handback", "converge:settled", 0) == "handed_back"
    assert decisions.run_class("handback", "regime:migrated", 0) == "handed_back"
    assert decisions.run_class("handback", "audit:fix_failing_test", 0) == "handed_back"
    assert decisions.run_class("handback", "converge:fix_verification", 3) == "untrusted"
    assert decisions.run_class("handback", "regime:conftest-collision", 2) == "world_wrong"
    assert decisions.run_class("handback", "audit:refused:collection_errors", 2) == "world_wrong"
    assert decisions.run_class("handback", "budget", None) == "handed_back"
    assert decisions.run_class("handback", "call_failed:stop_no_progress", None) == "untrusted"
    assert decisions.run_class("handback", "", 0) == decisions.run_class("stuck", "budget", None) == decisions.run_class(None, "budget", None) == "invalid"


def test_the_ask_the_values_are_mined_from_is_built_from_detective_s_typed_items():
    """A line's guard, a boundary's relation and an internal condition whose form is an expression are code; a prose
    condition is not, and a sample or author ask states none. The parameters are the function's own."""
    assert decisions.pool_ask("lines", [{"line": 11, "guard": "code.startswith('VIP-')"}, {"line": 14, "guard": ""}], ["code"]) == {
        "params": [{"name": "code"}],
        "lines": [{"when": "code.startswith('VIP-')"}, {"when": ""}],
        "relations": [],
        "conditions": [],
    }
    assert decisions.pool_ask("boundary", [{"relation": "x == 0", "mutants": []}], ["x"])["relations"] == ["x == 0"]
    internal = [{"condition": "h == 500", "form": "expression", "mutants": []}, {"condition": "the hash is large", "form": "prose", "mutants": []}]
    assert decisions.pool_ask("internal", internal, ["text"])["conditions"] == ["h == 500"]
    for kind in ("sample", "author"):
        assert decisions.pool_ask(kind, [], ["order"]) == {"params": [{"name": "order"}], "lines": [], "relations": [], "conditions": []}
    assert decisions.ask_pools(decisions.pool_ask("boundary", [{"relation": "qty == 0"}, {"relation": "code in ('A', 'B')"}], ["qty", "code"])) == {"qty": [0], "code": ["A", "B"]}
    assert decisions.ask_pools(decisions.pool_ask("lines", [{"line": 3, "guard": "x == ("}], ["x"])) == "unparseable"
    for bad in (("witness", [], ["x"]), ("lines", "not a list", ["x"]), ("lines", ["a"], ["x"]), ("lines", [], "x"), ("lines", [], [1])):
        assert decisions.pool_ask(*bad) == "invalid", bad


def test_what_the_seat_could_not_tell_is_remembered_by_the_key_of_each_item():
    """So Detective's next ask for the same item is handed back and never put to the model twice: an item's key
    (``item_key``), and for a sample or author ask, which has no items, the kind."""
    lines = [{"line": 11, "guard": "g"}, {"line": 14, "guard": "h"}]
    assert decisions.abstained_keys("lines", lines, [1]) == ["line:14"]
    assert decisions.abstained_keys("lines", lines, [0, 1]) == ["line:11", "line:14"]
    assert decisions.abstained_keys("lines", lines, []) == []
    assert decisions.abstained_keys("boundary", [{"relation": "x == 0"}], [0]) == ["relation:x == 0"]
    assert decisions.abstained_keys("internal", [{"condition": "h == 500"}], [0]) == ["condition:h == 500"]
    assert decisions.abstained_keys("sample", [], [0]) == ["sample"] and decisions.abstained_keys("author", [], [0]) == ["author"]
    for bad in (
        ("witness", [], [0]),
        ("lines", lines, [2]),
        ("lines", lines, [-1]),
        ("lines", lines, [True]),
        ("lines", lines, ["0"]),
        ("lines", {}, [0]),
        ("lines", lines, 0),
        ("lines", [{"guard": "g"}], [0]),
    ):
        assert decisions.abstained_keys(*bad) == "invalid", bad


def test_a_freedom_flags_a_mutant_by_a_command_the_harness_builds_whole():
    """From Detective's record and the model's note, never from the model's text; ``daemon_argv`` is not widened to
    ``flag``, which it refuses."""
    assert decisions.flag_argv("m.py::f", "BOUNDARY_3a405b77", "the edge is open", "/ws") == ["flag", "m.py::f", "BOUNDARY_3a405b77", "--note", "the edge is open", "--json", "--project-root", "/ws"]
    assert decisions.daemon_argv("detective flag 'm.py::f' BOUNDARY_3a405b77", "m.py::f", "m.py", "/ws") == "forbidden_verb"
    for bad in (("", "id", "note", "/ws"), ("m.py::f", "", "note", "/ws"), ("m.py::f", "id", "", "/ws"), ("m.py::f", "id", "note", ""), ("m.py::f", None, "note", "/ws"), (1, "id", "note", "/ws")):
        assert decisions.flag_argv(*bad) == "invalid", bad


def test_a_hand_back_the_seat_could_not_tell_goes_to_the_founder():
    """A stop Detective makes for values, where the model had none to give and none to free (``judge:cannot_tell``), is
    the founder's, as an admission's cannot-tell is; every other hand-back keeps its receivers."""
    assert decisions.handback_receivers("judge:cannot_tell") == decisions.handback_receivers("admission:cannot_tell") == ["founder"]
    assert decisions.handback_receivers("audit:fix_failing_test") == ["lead", "sol"]
    assert decisions.handback_receivers("converge:close_the_gap:supplied_did_not_reach") == decisions.handback_receivers("budget") == ["admission"]
    assert decisions.handback_receivers("judge:flag_refused:BOUNDARY_1") == decisions.handback_receivers(None) == ["admission"]
