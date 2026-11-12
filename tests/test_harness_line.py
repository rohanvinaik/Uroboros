"""The piece B4's own tests, placed unchanged: ``line.run_line`` (B4): one unit driven until every function is done or escalated."""

import pytest

from uroboros.harness.line import run_line


def hb(fn, ending="audit:fix_failing_test", evidence="e1"):
    return {"function": fn, "ending": ending, "evidence": evidence}


def res(folder, handbacks=(), contract=None):
    handbacks = list(handbacks)
    return {"played": folder, "contract": contract or f"{folder}/contract.json", "done": not handbacks, "handbacks": handbacks}


class World:
    """A stub world: play and replay return the queued results in order, and every call is recorded."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def play(self, contract, excluded):
        self.calls.append(("play", contract, excluded))
        return self.results.pop(0)

    def replay(self, played, excluded):
        self.calls.append(("replay", played, excluded))
        return self.results.pop(0)

    def revise(self, contract, handback):
        self.calls.append(("revise", contract, handback["function"]))
        return contract + "+r"

    def reissue(self, contract, function):
        self.calls.append(("reissue", contract, function))
        return contract + "+i"


def never(*args):
    raise AssertionError("must not be called")


def step_is(value):
    return lambda decision, current, history, replays, ceiling: value


def test_a_clean_first_play_is_done():
    w = World([res("p0")])
    assert run_line("c.json", "local", 3, w.play, never, never, never, never, never) == {
        "outcome": "done",
        "contract": "c.json",
        "rounds": [],
        "escalated": [],
    }
    assert w.calls == [("play", "c.json", ())]


def test_escalate_mode_sends_every_hand_back_up():
    w = World([res("p0", [hb("f"), hb("g")])])
    got = run_line("c.json", "escalate", 3, w.play, never, never, never, never, never)
    assert got["outcome"] == "escalated" and got["rounds"] == []
    assert got["escalated"] == [{"function": "f", "why": "mode"}, {"function": "g", "why": "mode"}]


def test_wrong_code_is_reissued_from_the_filled_contract_and_played_again():
    w = World([res("p0", [hb("f")]), res("p1")])
    got = run_line("c.json", "local", 3, w.play, lambda h: "code_wrong", step_is("reissue"), w.revise, w.reissue, w.replay)
    assert got["outcome"] == "done" and got["contract"] == "p0/contract.json+i"
    assert got["rounds"] == [{"function": "f", "ending": "audit:fix_failing_test", "evidence": "e1", "decision": "code_wrong", "step": "reissue"}]
    assert w.calls == [("play", "c.json", ()), ("reissue", "p0/contract.json", "f"), ("play", "p0/contract.json+i", ())]


def test_a_wrong_contract_is_revised_from_the_filled_contract():
    w = World([res("p0", [hb("f")]), res("p1")])
    got = run_line("c.json", "local", 3, w.play, lambda h: "contract_wrong", step_is("revise"), w.revise, w.reissue, w.replay)
    assert got["outcome"] == "done" and ("revise", "p0/contract.json", "f") in w.calls


def test_a_machine_fault_replays_the_last_played_folder_and_counts_it():
    seen = []

    def step(decision, current, history, replays, ceiling):
        seen.append(replays)
        return "replay"

    w = World([res("p0", [hb("f", ending="regime:conftest-collision")]), res("p0b")])
    got = run_line("c.json", "local", 3, w.play, lambda h: "machine_wrong", step, w.revise, w.reissue, w.replay)
    assert got["outcome"] == "done" and ("replay", "p0", ()) in w.calls and seen == [0]


def test_an_escalated_function_goes_up_and_the_rest_go_on():
    w = World([res("p0", [hb("f"), hb("g")]), res("p1", [hb("f")])])
    got = run_line(
        "c.json",
        "local",
        3,
        w.play,
        lambda h: "cannot_tell" if h["function"] == "f" else "code_wrong",
        lambda d, c, h, r, k: "escalate:cannot_tell" if d == "cannot_tell" else "reissue",
        w.revise,
        w.reissue,
        w.replay,
    )
    assert got["outcome"] == "escalated"
    assert got["escalated"] == [{"function": "f", "why": "cannot_tell"}]
    assert [r["function"] for r in got["rounds"]] == ["f", "g"]


def test_an_escalated_function_is_left_out_of_every_later_play():
    w = World([res("p0", [hb("f"), hb("g")]), res("p1")])
    run_line(
        "c.json",
        "local",
        3,
        w.play,
        lambda h: "cannot_tell" if h["function"] == "f" else "code_wrong",
        lambda d, c, h, r, k: "escalate:cannot_tell" if d == "cannot_tell" else "reissue",
        w.revise,
        w.reissue,
        w.replay,
    )
    assert w.calls == [("play", "c.json", ()), ("reissue", "p0/contract.json", "g"), ("play", "p0/contract.json+i", ("f",))]


def test_an_escalated_function_is_left_out_of_a_replay():
    def receive(h):
        return "cannot_tell" if h["function"] == "f" else "machine_wrong"

    def step(decision, current, history, replays, ceiling):
        return "escalate:cannot_tell" if decision == "cannot_tell" else "replay"

    w = World([res("p0", [hb("f"), hb("g", ending="regime:conftest-collision")]), res("p0b")])
    got = run_line("c.json", "local", 3, w.play, receive, step, w.revise, w.reissue, w.replay)
    assert ("replay", "p0", ("f",)) in w.calls and got["escalated"] == [{"function": "f", "why": "cannot_tell"}]


def test_a_refused_repair_sends_the_function_up():
    w = World([res("p0", [hb("f")])])
    got = run_line("c.json", "local", 3, w.play, lambda h: "code_wrong", step_is("reissue"), w.revise, lambda c, f: "refused:final", w.replay)
    assert got["escalated"] == [{"function": "f", "why": "refused:final"}]


def test_a_repair_that_returns_no_path_sends_the_function_up():
    w = World([res("p0", [hb("f")])])
    got = run_line("c.json", "local", 3, w.play, lambda h: "code_wrong", step_is("reissue"), w.revise, lambda c, f: None, w.replay)
    assert got["escalated"] == [{"function": "f", "why": "invalid_repair"}]


def test_an_unknown_decision_sends_the_function_up_without_a_step():
    w = World([res("p0", [hb("f")])])
    got = run_line("c.json", "local", 3, w.play, lambda h: "maybe", never, w.revise, w.reissue, w.replay)
    assert got["escalated"] == [{"function": "f", "why": "invalid_decision"}] and got["rounds"] == []


def test_the_ceiling_holds_even_when_the_steps_never_stop():
    w = World([res(f"p{i}", [hb("f", evidence=f"e{i}")]) for i in range(10)])
    got = run_line("c.json", "local", 2, w.play, lambda h: "code_wrong", step_is("reissue"), w.revise, w.reissue, w.replay)
    assert len(got["rounds"]) == 2 and got["escalated"] == [{"function": "f", "why": "ceiling"}]


def test_an_unknown_step_sends_the_function_up():
    w = World([res("p0", [hb("f")])])
    got = run_line("c.json", "local", 3, w.play, lambda h: "code_wrong", step_is("invalid"), w.revise, w.reissue, w.replay)
    assert got["escalated"] == [{"function": "f", "why": "invalid_step"}]


def test_the_history_passed_on_is_that_functions_own():
    seen = []
    w = World([res("p0", [hb("f", evidence="a")]), res("p1", [hb("f", evidence="b")]), res("p2")])

    def step(decision, current, history, replays, ceiling):
        seen.append([r["evidence"] for r in history])
        return "reissue"

    run_line("c.json", "local", 3, w.play, lambda h: "code_wrong", step, w.revise, w.reissue, w.replay)
    assert seen == [[], ["a"]]


def test_a_malformed_play_result_stops_the_unit():
    w = World([{"played": "p0"}])
    got = run_line("c.json", "local", 3, w.play, never, never, never, never, never)
    assert got["outcome"] == "escalated" and got["escalated"] == [{"function": "", "why": "invalid_play"}]


@pytest.mark.parametrize("bad", [{"played": "p0", "contract": "c", "done": False, "handbacks": []}, {"played": "p0", "contract": "c", "done": True, "handbacks": [hb("f")]}])
def test_a_play_whose_record_contradicts_itself_stops_the_unit(bad):
    w = World([bad])
    got = run_line("c.json", "local", 3, w.play, never, never, never, never, never)
    assert got["escalated"] == [{"function": "", "why": "inconsistent_play"}]


@pytest.mark.parametrize("contract, mode, ceiling", [(3, "local", 3), ("c.json", "auto", 3), ("c.json", "local", 0), ("c.json", "local", True)])
def test_malformed_input_is_invalid(contract, mode, ceiling):
    assert run_line(contract, mode, ceiling, never, never, never, never, never, never) == "invalid"


def test_production_line_preserves_escalated_function_and_replays_transport_once(tmp_path, monkeypatch):
    import json

    from uroboros.harness import contract, line

    source = tmp_path / "input.json"
    source.write_text(json.dumps({"body": [{"function": "worker", "code": None}]}))
    calls = []

    def play(path, folder, model, **_options):
        calls.append((path, model))
        folder.mkdir()
        (folder / "contract.json").write_text(path.read_text())
        handback = {"function": "worker", "ending": "transport", "decision": None, "doctor": None, "receivers": ["admission"]}
        (folder / "handbacks.jsonl").write_text(json.dumps(handback) + "\n")
        return {"done": False}

    monkeypatch.setattr(contract, "play", play)
    out = tmp_path / "line"
    result = line.execute_line(source, out, "qwen3.5-4b", "local", 2)
    assert len(calls) == 2
    assert calls[1][0] == out / "round_00/contract.json"
    assert [record["step"] for record in result["rounds"]] == ["replay", "escalate:second_machine_fault"]
    [escalation] = result["escalated"]
    assert escalation["function"] == "worker"
    assert escalation["contract_entry"]["function"] == "worker"
    assert escalation["receivers"] == ["founder"]
    assert json.loads((out / "line.json").read_text()) == result


def test_reissue_keeps_detective_observations_and_prioritizes_the_failing_test_owner(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from uroboros.harness import admission, contract, line, run

    def function(name, children):
        return {
            "function": name,
            "functions": children,
            "doc": {"prose": "Return a value.", "operations": []},
            "signature": {"args": {}},
            "code": [{"_type": "Return", "value": {"_type": "Constant", "value": 0}}],
            "intent_test": {"source": f"def test_{name}(): pass"},
        }

    source = tmp_path / "input.json"
    source.write_text(json.dumps({"path": "unit.py", "body": [function("total", [function("helper", [])])]}))
    failure = {"failing_tests": ["tests/test_unit.py::test_total"]}
    decision = {"code": "fix_failing_test"}
    targets = ["total.helper", "total"]

    def play(path, folder, model, **options):
        folder.mkdir()
        (folder / "contract.json").write_text(path.read_text())
        if options["repairs"]:
            assert set(options["repairs"]) == {"total"}
            receipt = options["repairs"]["total"]
            assert receipt["judgment"] == "code_wrong"
            assert receipt["decision"] == decision
            assert receipt["observations"] == failure
            assert receipt["prior_code"] == "def total():\n    return 0"
            assert "open AST cells" in receipt["instruction"]
            (folder / "handbacks.jsonl").write_text("")
            return {"done": True}
        handbacks = [
            {"function": target, "ending": "audit:fix_failing_test", "decision": decision, "doctor": None}
            for target in targets
        ]
        (folder / "handbacks.jsonl").write_text("".join(json.dumps(row) + "\n" for row in handbacks))
        return {"done": False, "detective": {"functions": {target: {"runs": [failure]} for target in targets}}}

    monkeypatch.setattr(contract, "play", play)
    monkeypatch.setattr(contract, "reissue", lambda unit, _target: ("unwritten", unit, {}))
    monkeypatch.setattr(admission, "admit", lambda *_args: {"outcome": "checked"})
    monkeypatch.setattr(run, "invoke", lambda *_args, **_kwargs: SimpleNamespace(
        status="admitted", values={"decision": "code_wrong"}, to_dict=lambda: {},
    ))
    result = line.execute_line(source, tmp_path / "line", "qwen3.5-4b", "local", 2)
    assert result["outcome"] == "done"
    assert [row["function"] for row in result["rounds"]] == ["total"]
