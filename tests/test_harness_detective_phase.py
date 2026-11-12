"""The Detective phase, by stage, on Detective's own record (docs/builds/2026-10-08/WIRING.md, W3).

``daemon.drive`` follows the decision record each ``--json`` run carries: the folder's ``regime`` once, then on each
function ``audit`` (setup, never an endpoint) whose clean record names ``converge`` (the run, the only place a function
is done). Driven here over real recorded runs (``tests/fixtures/detective_json/``) through the runner seam, so each test
is also the check that the harness ran exactly the commands Detective's record named; the model's seat is a stub chat.
Where a state could not be recorded (a regime repair inside the run, which Detective did not produce on any module), the
record is built from a recorded one and says so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from uroboros.harness import contract, daemon, decisions, schema
from uroboros.harness.cassette import find_cassette, list_cassettes
from uroboros.harness.model import TransportError
from uroboros.harness.run import plan
from tests.detective_replay import MODEL, FakeChat, Replay, answers, crafted, doctor_step, drive, fixture, laid_out, moves, put_back, result, shown, steps, with_command


def test_the_folder_then_each_function_follows_detectives_own_record(tmp_path):
    """The folder's migration runs and its done begins the functions; each function bottom-up is audited, its clean audit
    names the converge that runs, and converge's settled is the function's done. No model is called, no doctor is read
    for a function that is done, and no second regime is added after Detective's own repair."""
    case = fixture("pairs")
    root = laid_out(case, tmp_path)
    replay, chat = Replay.of(case, root), FakeChat()
    record = drive(tmp_path, case, replay, chat)
    assert chat.calls == 0 and replay.left == 0
    setup = record["setup"]
    assert (setup["ending"], setup["route"], setup["class"]) == ("regime:migrated", "done", "clean")
    assert moves(setup) == [("regime", "folder", "migration_available", "run"), ("regime", "folder", "migrated", "setup_clean")]
    assert list(record["functions"]) == ["read_settings.read_pair", "read_settings"]
    for qualname, function in record["functions"].items():
        assert (function["ending"], function["route"], function["class"], function["doctor"]) == ("converge:settled", "done", "clean", None), qualname
        assert moves(function) == [("audit", "setup", "close_the_gap", "run"), ("converge", "run", "settled", "done")], qualname
    assert replay.ran[:4] == [
        ["regime", "--project-root", root],
        ["regime", "--migrate", "--project-root", root],
        ["audit", "pairs.py::read_pair", "--project-root", root, "--two-sign"],
        ["converge", "pairs.py::read_pair", "--project-root", root, "--two-sign"],
    ]
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "converge", "audit", "converge"]
    # the folder keeps each output whole, in order, and a line of the log for each command
    assert sorted(p.name for p in tmp_path.glob("step_*.txt")) == [f"step_{i:02d}_{verb}.txt" for i, verb in enumerate(argv[0] for argv in replay.ran)]
    assert len((tmp_path / "possession.jsonl").read_text().splitlines()) == 6


def test_a_clean_two_sign_audit_begins_the_run_with_exactly_the_converge_it_names(tmp_path):
    """A two-sign audit that finds the suite redundant names ``converge`` (it would name ``--remove`` without the flag),
    and the harness runs that and never a removal."""
    case = fixture("grades")
    root = laid_out(case, tmp_path)
    replay = Replay.of(case, root)
    record = drive(tmp_path, case, replay)
    audit = record["functions"]["pass_rate.passes"]["runs"][0]
    assert audit["decision"]["code"] == "remove_redundant" and audit["decision"]["step"]["argv"] == ["converge", "grades.py::passes"]
    assert audit["move"] == {"move": "run", "argv": ["converge", "grades.py::passes"], "stage": "run"}
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "converge", "audit", "converge"]
    assert not any("--remove" in argv for argv in replay.ran)
    assert [f["route"] for f in record["functions"].values()] == ["done", "done"]


def test_an_audit_that_names_a_removal_instead_is_handed_back_and_nothing_is_removed(tmp_path):
    """A setup run that is not the converge (here a recorded audit whose step is edited to name ``--remove``, as an audit
    without ``--two-sign`` does) is not run: a test the contract owns is never deleted ([15])."""
    case = fixture("clamp")
    root = laid_out(case, tmp_path)
    audit = result(case["steps"][2], root)
    audit["payload"]["decision"]["step"]["argv"] = ["audit", "clamp.py::clamp", "--remove"]
    replay = Replay(steps(case, root, 0, 1, audit, doctor_step(root, "clamp.py::clamp")))
    clamp = drive(tmp_path, case, replay)["functions"]["clamp"]
    assert (clamp["ending"], clamp["route"], clamp["class"]) == ("invalid:setup_run_not_converge", "handback", "handed_back")
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "doctor"] and not any("--remove" in argv for argv in replay.ran)
    assert clamp["runs"][0]["move"] == {"move": "invalid", "code": "setup_run_not_converge"}


def test_audits_failing_test_is_handed_back_and_no_converge_runs(tmp_path):
    """Audit names an intent test the code fails and exits 0: the code, not the exit, ends the function. Setup never ends
    a function as done, and nothing is run after it but doctor's read, kept as evidence."""
    case = fixture("clamp_failing")
    root = laid_out(case, tmp_path)
    replay = Replay.of(case, root)
    record = drive(tmp_path, case, replay)
    clamp = record["functions"]["clamp"]
    assert (clamp["ending"], clamp["route"], clamp["class"]) == ("audit:fix_failing_test", "handback", "handed_back")
    assert moves(clamp) == [("audit", "setup", "fix_failing_test", "hand_back")] and clamp["runs"][0]["exit"] == 0
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "doctor"]
    assert replay.ran[-1] == ["doctor", "clamp.py::clamp", "--json", "--project-root", root]
    assert clamp["doctor"] == put_back(case["steps"][3]["payload"], root) and clamp["doctor"]["green"]["code"] == "clean"
    assert decisions.handback_receivers(clamp["ending"]) == ["lead", "sol"]
    assert decisions.possession_exit([record["setup"]["class"], clamp["class"]]) == 1


def test_a_module_that_does_not_load_at_setup_is_handed_back_with_doctors_read(tmp_path):
    """Audit's own ``fix_load`` stop (here an audit record BUILT from a recorded one: on every unloadable module
    recorded, Detective refused with ``collection_errors`` instead, and never printed this code) ends the function at
    setup under it, handed back to admission: no converge runs, and doctor's read is the receiver's evidence."""
    case = fixture("clamp")
    root = laid_out(case, tmp_path)
    stop = result(case["steps"][2], root)
    stop["payload"]["decision"] = {
        "verb": "audit",
        "code": "fix_load",
        "route": "",
        "step": {"kind": "stop", "argv": None, "remedy": "fix_load", "then": ["audit", "clamp.py::clamp"]},
        "plan": None,
        "residual": None,
        "optional": [],
        "state": {"headline": "nothing_measured"},
        "final": None,
    }
    replay = Replay(steps(case, root, 0, 1, stop, doctor_step(root, "clamp.py::clamp")))
    clamp = drive(tmp_path, case, replay)["functions"]["clamp"]
    assert (clamp["ending"], clamp["route"], clamp["class"]) == ("audit:fix_load", "handback", "handed_back")
    assert moves(clamp) == [("audit", "setup", "fix_load", "end")]
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "doctor"] and clamp["doctor"] is not None
    assert decisions.handback_receivers(clamp["ending"]) == ["admission"]


def test_a_module_detective_cannot_load_is_refused_and_handed_back_with_doctors_read(tmp_path):
    """The suite cannot be collected, so Detective refuses the audit with exit 2: the function ends under that refusal,
    and doctor's read, which names the module it could not import, is its evidence. (``ending_class`` reads exit 2 as the
    world being wrong; the record, not the class, is what the receiver is handed.)"""
    case = fixture("clamp_unloadable")
    root = laid_out(case, tmp_path)
    replay = Replay.of(case, root)
    clamp = drive(tmp_path, case, replay)["functions"]["clamp"]
    assert (clamp["ending"], clamp["route"], clamp["class"]) == ("audit:refused:collection_errors", "handback", "world_wrong")
    assert (clamp["runs"][-1]["exit"], clamp["runs"][-1]["decision"]["reason"], clamp["runs"][-1]["move"]) == (2, "collection_errors", None)
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "doctor"]
    assert clamp["doctor"]["green"]["code"] == "deps_missing" and clamp["doctor"]["green"]["facts"]["missing"] == ["nonexistent_module_qq"]


def test_a_stop_ends_under_detectives_code_and_a_sample_is_judged_then_converge_runs_once_with_the_values(tmp_path):
    """line_total's converge stops (the suite does not verify under real pytest, exit 3): it ends under Detective's own
    code and the next function goes on. order_total's audit asks for a real sample: the seat is asked once, and the
    converge that follows runs once, with the values the harness renders. The function is broken, so the values do not
    help (the same ask, 0 of 11 mutants killed: a true repeat), and Detective itself ends the loop
    (``supplied_did_not_reach``, its rule reaching a sample stop): the seat is not asked again. doctor is read after each
    of the two."""
    case = fixture("orders_failing")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, 2, 3, doctor_step(root, "orders.py::line_total"), 4, 5, doctor_step(root, "orders.py::order_total")))
    chat = FakeChat(answers({"supply": {"order": [[2, 1.5], [1, 4.0]]}}))
    record = drive(tmp_path, case, replay, chat)
    line, order = record["functions"]["order_total.line_total"], record["functions"]["order_total"]
    assert (line["ending"], line["route"], line["class"]) == ("converge:fix_verification", "handback", "untrusted")
    assert moves(line) == [("audit", "setup", "close_the_gap", "run"), ("converge", "run", "fix_verification", "end")] and line["runs"][-1]["exit"] == 3
    assert (order["ending"], order["route"], order["class"]) == ("converge:provide_sample:supplied_did_not_reach", "handback", "handed_back")
    assert moves(order) == [("audit", "setup", "provide_sample", "judge"), ("converge", "run", "provide_sample", "hand_back")]
    assert replay.ran[6] == ["converge", "orders.py::order_total", "--input", "([[2, 1.5], [1, 4.0]],)", "--project-root", root, "--two-sign"]
    assert "0/11 killed" in order["runs"][-1]["decision"]["final"] and order["runs"][-1]["decision"]["plan"]["supplied"] == ["([[2, 1.5], [1, 4.0]],)"]
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "converge", "doctor", "audit", "converge", "doctor"]
    assert chat.calls == 1 and not replay.left
    assert (line["doctor"] is not None, order["doctor"] is not None) == (True, True)
    first = shown(chat, 0)
    assert first["stop"]["kind"] == "sample" and first["stop"]["items"] == [] and first["function"] == {"name": "order_total", "params": [{"name": "order", "annotation": ""}]}
    assert decisions.handback_receivers(order["ending"]) == ["admission"]
    assert decisions.possession_exit([record["setup"]["class"], line["class"], order["class"]]) == 3


def test_the_values_detective_asks_for_reach_the_line_and_it_settles(tmp_path):
    """A line no test runs is asked for with the guard that reaches it. The seat reads the stop as Detective's typed items
    with the values the guard states pooled beside the parameter's type, what the contract says, the tests Detective wrote
    so far and the inputs held; its values go on the command Detective named next, as ``--input``, and Detective settles
    the function."""
    case = fixture("label")
    root = laid_out(case, tmp_path)
    written = Path(put_back(case["steps"][3]["payload"]["written_path"], root))
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text("# Detective's own test\n")
    replay = Replay.of(case, root)
    chat = FakeChat(answers({"supply": {"code": "VIP-1"}}))
    record = drive(tmp_path, case, replay, chat, coverage={"label": []}, contracts={"label": contract.judge_contract(case["contract"], "label")})
    label = record["functions"]["label"]
    assert (label["ending"], label["route"], label["class"], label["doctor"]) == ("converge:settled", "done", "clean", None)
    assert replay.ran[-1] == ["converge", "labels.py::label", "--input", "('VIP-1',)", "--project-root", root, "--two-sign"]
    assert moves(label) == [("audit", "setup", "close_the_gap", "run"), ("converge", "run", "close_the_gap", "judge"), ("converge", "run", "settled", "done")]
    seen = shown(chat)
    assert seen["stop"] == {"verb": "converge", "code": "close_the_gap", "kind": "lines", "items": [{"guard": "code.startswith('VIP-')", "line": 11}], "then": ["converge", "labels.py::label"]}
    assert seen["function"] == {"name": "label", "params": [{"name": "code", "annotation": ""}]} and seen["pools"] == {"code": ["VIP-"]}
    assert set(seen["contract"]) == {"doc", "intent_tests", "must_never"} and "def test_ordinary_code_is_standard" in seen["contract"]["intent_tests"]
    assert [c["category"] for c in seen["contract"]["must_never"]] == ["OUTPUT"]
    assert seen["characterization"] == ["# Detective's own test\n"] and seen["coverage"] == [] and set(seen["inputs"]) == {"store", "remembered", "last_run"}
    # the hole offers the value mined from the guard, beside the parameter's own type (an untyped parameter takes any JSON value)
    item = chat.payloads[0]["format"]["properties"]["answers"]["properties"]["0"]["anyOf"][0]
    assert item["properties"]["supply"]["properties"]["code"] == schema.param_schema({"type": schema.ANY_JSON, "pool": ["VIP-"]})
    assert [m["role"] for m in chat.payloads[0]["messages"]] == ["user"]  # the coding seats' system message is empty


def test_the_seat_is_the_models_name_under_the_cassettes_own_call_settings(tmp_path):
    """The judgment seat plays as the model the play names, under the call settings its cassette declares (the runbook,
    §9): the output cap, the sampling, the wall and ``think`` are the seat's; the model's name, residency and context
    window are the model's; a key only the model's record carries (laguna's ``num_batch``, its own workaround) stays."""
    case = fixture("label")
    sent = {}
    for key in ("qwen3-coder-next", "laguna-xs-2.1"):
        folder = tmp_path / key
        chat = FakeChat(answers({"supply": {"code": "VIP-1"}}))
        drive(folder, case, Replay.of(case, laid_out(case, folder)), chat, model={**contract.MODELS[key], "timeout_s": 900})
        sent[key] = (chat.payloads[0], chat.timeouts[0])
    coder, laguna = sent["qwen3-coder-next"], sent["laguna-xs-2.1"]
    assert (coder[0]["model"], coder[0]["think"], coder[0]["keep_alive"], coder[1]) == ("qwen3-coder-next:latest", False, "30m", 180.0)
    assert coder[0]["options"] == {"temperature": 0.1, "num_ctx": 32768, "num_predict": 1024}
    assert (laguna[0]["model"], laguna[0]["think"], laguna[1]) == ("laguna-xs-2.1:q3_K_M", False, 180.0)
    assert laguna[0]["options"] == {"temperature": 0.1, "top_k": 20, "top_p": 1.0, "num_ctx": 32768, "num_predict": 1024, "num_batch": 31}


def test_a_value_the_harness_cannot_write_as_a_call_ends_the_function_before_anything_runs(tmp_path):
    """A reply can carry a number no Python literal writes (``NaN``, which ``json`` reads). The harness writes call syntax
    itself, so the value is refused by name, with its item: a reply is admitted whole or not at all, and the answer to the
    first item is not run without the second."""
    case = fixture("label")
    root = laid_out(case, tmp_path)
    ask = result(case["steps"][3], root)
    ask["payload"]["decision"]["plan"]["items"].append({"guard": "code == 'X-9'", "line": 13})
    replay = Replay(steps(case, root, 0, 1, 2, ask, doctor_step(root, "labels.py::label")))
    chat = FakeChat('{"answers": {"0": {"supply": {"code": "VIP-1"}}, "1": {"supply": {"code": NaN}}}}')
    label = drive(tmp_path, case, replay, chat)["functions"]["label"]
    assert (label["ending"], label["route"], label["class"]) == ("judge:unrenderable:1", "handback", "handed_back")
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "converge", "doctor"] and chat.calls == 1


def test_a_function_that_spends_its_step_bound_ends_budget_and_is_handed_back(tmp_path):
    """The bound counts the commands a function runs, across its setup and its run: the judged converge is among them,
    doctor's read is not. The command that would exceed it is never run."""
    case = fixture("label")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, 2, 3, doctor_step(root, "labels.py::label")))
    chat = FakeChat(answers({"supply": {"code": "VIP-1"}}))
    label = drive(tmp_path, case, replay, chat, steps=2)["functions"]["label"]
    assert (label["ending"], label["route"], label["class"]) == ("budget", "handback", "handed_back")
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "converge", "doctor"] and chat.calls == 1
    assert label["doctor"] is not None and decisions.handback_receivers("budget") == ["admission"]
    # a bound of exactly the commands it takes is enough
    exact = tmp_path / "exact"
    exact.mkdir()
    replay = Replay.of(case, laid_out(case, exact))
    label = drive(exact, case, replay, FakeChat(answers({"supply": {"code": "VIP-1"}})), steps=3)["functions"]["label"]
    assert (label["ending"], label["route"]) == ("converge:settled", "done") and not replay.left


def test_a_converge_witness_is_run_with_no_model_call_and_the_test_detective_renders_is_written(tmp_path):
    """A witness Detective ran is a command it names: run as it is, no model called. Its next record asks for a test and
    carries the rendering: the harness writes Detective's own sources and runs the command it names. The record after
    that asks for a line the environment guards, on a method: nothing a model could give."""
    case = fixture("counter")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, 2, 3, 4, 5, doctor_step(root, "counters.py::Counter.bump")))
    chat, written = FakeChat(), []

    def write_test(qualname, tests):
        written.append((qualname, tests))
        return ""

    bump = drive(tmp_path, case, replay, chat, write_test=write_test)["functions"]["Counter.bump"]
    assert chat.calls == 0
    assert replay.ran[4] == ["converge", "counters.py::Counter.bump", "--input", "(2,)", "--project-root", root, "--two-sign"]
    assert moves(bump) == [
        ("audit", "setup", "close_the_gap", "run"),
        ("converge", "run", "close_the_gap", "run"),
        ("converge", "run", "close_the_gap", "write_tests"),
        ("converge", "run", "close_the_gap", "judge"),
    ]
    sources = [item["test"] for item in case["steps"][4]["payload"]["decision"]["plan"]["items"]]
    assert written == [("Counter.bump", sources)] and all("recv.bump(2)" in s and "assert recv.n == 2" in s for s in sources)
    assert replay.ran[5] == ["converge", "counters.py::Counter.bump", "--project-root", root, "--two-sign"]
    assert (bump["ending"], bump["class"], bump["route"]) == ("input_unavailable:no_function", "handed_back", "handback")


def test_without_a_writer_a_request_for_a_test_ends_the_function(tmp_path):
    case = fixture("counter")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, 2, 3, 4, doctor_step(root, "counters.py::Counter.bump")))
    bump = drive(tmp_path, case, replay)["functions"]["Counter.bump"]
    assert (bump["ending"], bump["class"]) == ("write_unavailable", "handed_back")


def test_a_write_that_is_refused_ends_the_function_under_its_code(tmp_path):
    case = fixture("counter")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, 2, 3, 4, doctor_step(root, "counters.py::Counter.bump")))
    bump = drive(tmp_path, case, replay, write_test=lambda qualname, tests: "write_refused:syntax")["functions"]["Counter.bump"]
    assert (bump["ending"], bump["class"]) == ("write_refused:syntax", "untrusted")


def test_a_function_that_takes_nothing_has_no_sample_to_give_and_is_handed_back(tmp_path):
    """For a function that takes no parameter the seat has nothing to supply: Detective's ask for a sample ends the
    function under its own code, with no model call. The harness reads the signature, never the body."""
    case = fixture("orders_failing")
    root = laid_out(case, tmp_path)
    (tmp_path / "orders.py").write_text(case["files"]["orders.py"].replace("def order_total(order):", "def order_total():").replace("for q, p in order", "for q, p in []"))
    replay = Replay(steps(case, root, 0, 1, 2, 3, doctor_step(root, "orders.py::line_total"), 4, doctor_step(root, "orders.py::order_total")))
    chat = FakeChat()
    order = drive(tmp_path, case, replay, chat)["functions"]["order_total"]
    assert (order["ending"], order["class"], chat.calls) == ("audit:provide_sample:no_params", "handed_back", 0)
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "converge", "doctor", "audit", "doctor"]
    assert moves(order) == [("audit", "setup", "provide_sample", "hand_back")]


@pytest.mark.parametrize(
    ("answer", "ending"),
    [
        (lambda root: crafted(["audit", "clamp.py::clamp", "--project-root", root, "--two-sign", "--json"], None, 1, {"refusal": "target_not_found"}), "audit:refused:target_not_found"),
        (lambda root: crafted(["audit", "clamp.py::clamp", "--project-root", root, "--two-sign", "--json"], None, 1, {}), "detective:unparsed"),
        (lambda root: crafted(["audit", "clamp.py::clamp", "--project-root", root, "--two-sign", "--json"], {"exit_code": 0}, 0, {}), "audit:no_decision"),
        (
            lambda root: crafted(["audit", "clamp.py::clamp", "--project-root", root, "--two-sign", "--json"], {"verdict": "REFUSED", "reason": "regime_conflict", "exit_code": 2}, 2, {}),
            "audit:refused:regime_conflict",
        ),
    ],
)
def test_a_run_whose_payload_holds_no_record_ends_under_the_code_that_says_why(tmp_path, answer, ending):
    """A typed refusal ends ``<verb>:refused:<reason>``; a text-only exit whose ledger names a refusal ends under it
    (Detective prints no JSON for a target not found); a payload that is no object, or has no decision, ends under its own
    code. doctor is read after each."""
    case = fixture("clamp")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, answer(root), doctor_step(root, "clamp.py::clamp")))
    clamp = drive(tmp_path, case, replay)["functions"]["clamp"]
    assert (clamp["ending"], clamp["route"]) == (ending, "handback")
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "doctor"] and clamp["doctor"] is not None


def test_a_supplied_value_that_changes_nothing_ends_the_run_under_detectives_own_hand_back(tmp_path):
    """A loop is Detective's to end ([20]), and by his ruling (a) it hands back ``supplied_did_not_reach`` only when a
    supplied value changes nothing: the same ask and no new kill. Here the line behind a hashed key is asked for, the value
    misses it, and the ask, the candidates and the kills (11 of 33) are as they were; the harness ends the function there,
    routed as Detective's other hand-backs. (A value that kills more is progress: Detective offers the ask again, and the
    harness follows.)"""
    case = fixture("route")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, 2, 3, 4, doctor_step(root, "routes.py::route")))
    chat = FakeChat(answers({"supply": {"token": "a", "n": 1}}))
    route = drive(tmp_path, case, replay, chat)["functions"]["route"]
    assert (route["ending"], route["route"], route["class"]) == ("converge:close_the_gap:supplied_did_not_reach", "handback", "handed_back")
    assert replay.ran[4] == ["converge", "routes.py::route", "--input", "('a', 1)", "--project-root", root, "--two-sign"]
    assert moves(route)[-2:] == [("converge", "run", "close_the_gap", "judge"), ("converge", "run", "close_the_gap", "hand_back")]
    asked, answered = (route["runs"][i]["decision"] for i in (-2, -1))
    assert asked["plan"]["items"] == answered["plan"]["items"] == [{"guard": "key == 4242", "line": 14}] and answered["plan"]["supplied"] == ["('a', 1)"]
    assert asked["state"] == answered["state"] and "11/33 killed" in asked["final"] and "11/33 killed" in answered["final"]
    assert shown(chat)["stop"]["kind"] == "lines" and decisions.handback_receivers(route["ending"]) == ["admission"] and chat.calls == 1


def test_a_regime_repair_inside_the_run_resumes_converge_and_its_done_is_not_the_functions(tmp_path):
    """Converge names a regime repair (here a converge record BUILT from a recorded settled one: Detective produced none
    on any module this wave recorded), whose own record ends in ``done`` — that done only resumes converge on the
    function again; the function is done when converge's run reaches it."""
    case = fixture("clamp")
    root = laid_out(case, tmp_path)
    repair = result(case["steps"][3], root)
    repair["payload"]["decision"] = {
        "verb": "converge",
        "code": "repair_measurement",
        "route": "regime",
        "step": {"kind": "run", "argv": ["regime", "clamp.py::clamp"], "remedy": "", "then": None},
        "plan": None,
        "residual": None,
        "optional": [],
        "state": {"standing": "ungateable"},
        "final": None,
    }
    mended = with_command(result(case["steps"][1], root), ["regime", "clamp.py::clamp", "--project-root", root, "--json"])
    replay = Replay(steps(case, root, 0, 1, 2, repair, mended, 3))
    clamp = drive(tmp_path, case, replay)["functions"]["clamp"]
    assert moves(clamp) == [
        ("audit", "setup", "close_the_gap", "run"),
        ("converge", "run", "repair_measurement", "run"),
        ("regime", "run", "migrated", "resume"),
        ("converge", "run", "settled", "done"),
    ]
    assert [argv[0] for argv in replay.ran][2:] == ["audit", "converge", "regime", "converge"]
    assert (clamp["ending"], clamp["route"], clamp["class"]) == ("converge:settled", "done", "clean")
    assert replay.ran[-1] == ["converge", "clamp.py::clamp", "--project-root", root, "--two-sign"]


def test_a_conflict_in_the_folder_ends_the_phase_and_doctor_is_not_read_for_it(tmp_path):
    """The folder's setup ends under Detective's own code (here a regime record BUILT from a recorded one, as a conflicting
    suite layout would print it); no function begins and doctor is not read."""
    case = fixture("pairs")
    root = laid_out(case, tmp_path)
    conflict = result(case["steps"][0], root)
    conflict["exit"] = 2
    conflict["payload"]["decision"].update(code="conftest-collision", step={"kind": "stop", "argv": None, "remedy": "regime_conflict", "then": None})
    replay = Replay(steps(case, root, conflict))
    record = drive(tmp_path, case, replay)
    assert (record["setup"]["ending"], record["setup"]["route"], record["setup"]["class"]) == ("regime:conftest-collision", "handback", "world_wrong")
    assert record["functions"] == {} and [argv[0] for argv in replay.ran] == ["regime"]


@pytest.mark.parametrize(
    ("named", "code"),
    [
        (["converge", "other.py::g"], "contract_breach:foreign_target"),
        (["audit", "clamp.py::clamp", "--remove"], "contract_breach:forbidden_option"),
        (["converge", "clamp.py::clamp", "--project-root", "/elsewhere"], "contract_breach:foreign_root"),
        (["flag", "clamp.py::clamp", "VALUE_1"], "contract_breach:forbidden_verb"),
    ],
)
def test_a_command_naming_another_function_a_removal_or_another_folder_is_never_run(tmp_path, named, code):
    """Admission: the commands Detective's record names run only when they are Detective's verbs on this function, in this
    folder; otherwise the function ends under the breach, named, and nothing runs."""
    case = fixture("clamp")
    root = laid_out(case, tmp_path)
    breach = result(case["steps"][3], root)
    breach["payload"]["decision"].update(code="close_the_gap", step={"kind": "run", "argv": named, "remedy": "", "then": None})
    replay = Replay(steps(case, root, 0, 1, 2, breach, doctor_step(root, "clamp.py::clamp")))
    clamp = drive(tmp_path, case, replay)["functions"]["clamp"]
    assert (clamp["ending"], clamp["class"]) == (code, "untrusted")
    assert [argv[0] for argv in replay.ran] == ["regime", "regime", "audit", "converge", "doctor"]


def test_a_failed_model_call_ends_its_function_and_the_next_goes_on(tmp_path):
    """The founder, 2026-10-06: a failed call in the Detective phase stops that function, and the next one goes on."""
    case = fixture("orders_failing")
    root = laid_out(case, tmp_path)
    replay = Replay(steps(case, root, 0, 1, 2, 3, doctor_step(root, "orders.py::line_total"), 4, doctor_step(root, "orders.py::order_total")))
    chat = FakeChat(TransportError("down"), TransportError("down"))
    record = drive(tmp_path, case, replay, chat)
    ends = {q: (f["ending"], f["class"]) for q, f in record["functions"].items()}
    assert ends == {"order_total.line_total": ("converge:fix_verification", "untrusted"), "order_total": ("call_failed:stop_no_progress", "untrusted")}
    assert chat.calls == 2


def test_play_follows_the_record_writes_the_hand_backs_and_routes_them(tmp_path):
    """Through the real entry: ``contract.play`` renders the module and its tests from the filled contract, hands them to
    Detective in the contract's order, and writes each function the phase handed back to ``handbacks.jsonl``, routed by
    its ending, with the function's last Detective record and doctor's payload as its evidence (WIRING W8, its first
    bullet). A clean module hands back nothing, and ``play.json`` records ``done`` only for it."""
    clean = fixture("pairs")
    src = tmp_path / "pairs.contract.json"
    src.write_text(json.dumps(clean["contract"]))
    out = tmp_path / "clean"
    out.mkdir()
    replay = Replay.of(clean, os.path.realpath(out))
    record = contract.play(src, out, chat=FakeChat(), runner=replay)
    assert (record["outcome"], record["calls"]) == ("written", 0)
    assert {rel: (out / rel).read_text() for rel in clean["files"]} == clean["files"]
    assert json.loads((out / "contract.json").read_text()) == clean["contract"]
    assert record["detective"]["setup"]["route"] == "done" and all(f["route"] == "done" for f in record["detective"]["functions"].values())
    assert (out / "handbacks.jsonl").read_text() == "" and json.loads((out / "play.json").read_text())["detective"]["setup"]["ending"] == "regime:migrated"
    assert record["done"] is True and json.loads((out / "play.json").read_text())["done"] is True
    failing = fixture("clamp_failing")
    src.write_text(json.dumps(failing["contract"]))
    out = tmp_path / "failing"
    out.mkdir()
    replay = Replay.of(failing, os.path.realpath(out))
    record = contract.play(src, out, chat=FakeChat(), runner=replay, contract_writer="lead", admission="lead")
    assert record["grant"] == {"contract_writer": "lead", "code_writer": contract.DEFAULT_MODEL, "admission": "lead"}
    routed = [json.loads(line) for line in (out / "handbacks.jsonl").read_text().splitlines()]
    assert [(h["function"], h["ending"], h["receivers"], h["decisions"]) for h in routed] == [("clamp", "audit:fix_failing_test", ["lead", "sol"], ["contract_wrong", "code_wrong", "machine_wrong"])]
    root = os.path.realpath(out)
    assert list(routed[0]) == ["function", "ending", "decision", "doctor", "receivers", "decisions"]
    assert routed[0]["decision"] == record["detective"]["functions"]["clamp"]["runs"][-1]["decision"] == put_back(failing["steps"][2]["payload"]["decision"], root)
    assert routed[0]["decision"]["code"] == "fix_failing_test" and routed[0]["doctor"] == put_back(failing["steps"][3]["payload"], root) and routed[0]["doctor"]["green"]["code"] == "clean"
    assert record["outcome"] == "written" and record["done"] is False and json.loads((out / "play.json").read_text())["done"] is False


def test_a_play_whose_folder_setup_does_not_end_clean_hands_back_the_setup_alone(tmp_path):
    """WIRING W8, its first bullet, through ``contract.play``: when the folder's setup does not end clean (here a regime
    record BUILT from a recorded one, as a conflicting suite layout would print it), the setup's own record is written,
    its function ``""``, its evidence regime's last record and no doctor's read; no function's is, since none began; and
    ``play.json`` does not record ``done``."""
    case = fixture("pairs")
    src = tmp_path / "pairs.contract.json"
    src.write_text(json.dumps(case["contract"]))
    out = tmp_path / "out"
    out.mkdir()
    root = os.path.realpath(out)
    conflict = result(case["steps"][0], root)
    conflict["exit"] = 2
    conflict["payload"]["decision"].update(code="conftest-collision", step={"kind": "stop", "argv": None, "remedy": "regime_conflict", "then": None})
    replay = Replay(steps(case, root, conflict))
    record = contract.play(src, out, chat=FakeChat(), runner=replay)
    assert (record["outcome"], record["detective"]["setup"]["route"], record["detective"]["functions"]) == ("written", "handback", {})
    routed = [json.loads(line) for line in (out / "handbacks.jsonl").read_text().splitlines()]
    assert routed == [
        {
            "function": "",
            "ending": "regime:conftest-collision",
            "decision": conflict["payload"]["decision"],
            "doctor": None,
            "receivers": decisions.handback_receivers("regime:conftest-collision"),
            "decisions": list(decisions.RECEIVER_DECISIONS),
        }
    ]
    assert routed[0]["decision"]["code"] == "conftest-collision" and [argv[0] for argv in replay.ran] == ["regime"]
    assert record["done"] is False and json.loads((out / "play.json").read_text())["done"] is False


def test_a_play_that_writes_no_module_never_records_done(tmp_path):
    """WIRING W8, its first bullet: ``play.json`` always carries ``done``, and it is false for a play that ends
    ``incomplete`` (a function skipped) and for one refused before any call."""
    case = fixture("clamp")
    src = tmp_path / "clamp.contract.json"
    src.write_text(json.dumps(case["contract"]))
    again = tmp_path / "again"
    assert contract.main(["reissue", str(src), "clamp", "--out", str(again)]) == 0
    out = tmp_path / "incomplete"
    record = contract.play(again / "contract.json", out, chat=FakeChat(TransportError("down")))
    assert (record["outcome"], record["skipped"], record["done"]) == ("incomplete", ["transport@clamp"], False)
    assert json.loads((out / "play.json").read_text())["done"] is False
    evidence = json.loads((out / "calls.jsonl").read_text())
    handback = json.loads((out / "handbacks.jsonl").read_text())
    assert (handback["function"], handback["ending"], handback["evidence"]) == ("clamp", "transport", evidence)
    assert handback["decision"] is None and handback["doctor"] is None
    assert json.loads((out / "contract.json").read_text()) == json.loads((again / "contract.json").read_text())

    def truncated(*_args):
        return '{"code":', {"done_reason": "length"}

    out = tmp_path / "truncated"
    record = contract.play(again / "contract.json", out, chat=truncated)
    assert record["skipped"] == ["truncated@clamp"]
    handback = json.loads((out / "handbacks.jsonl").read_text())
    assert (handback["ending"], handback["evidence"]["telemetry"]) == ("truncated", {"done_reason": "length"})
    bad = tmp_path / "bad.contract.json"
    bad.write_text(json.dumps({**case["contract"], "detective": ["regime", "converge"]}))
    refusals = ((tmp_path / "absent.json", contract.DEFAULT_MODEL, "unreadable"), (bad, contract.DEFAULT_MODEL, "invalid:detective"), (src, "nowhere", "unknown_model"))
    for i, (path, model_key, outcome) in enumerate(refusals):
        out = tmp_path / f"refused_{i}"
        chat = FakeChat()
        record = contract.play(path, out, model_key, chat=chat)
        assert (record["outcome"], record["done"], chat.calls) == (outcome, False, 0)
        assert json.loads((out / "play.json").read_text())["done"] is False
        handback = json.loads((out / "handbacks.jsonl").read_text())
        assert (handback["function"], handback["ending"], handback["evidence"]) == ("", outcome, None)


def test_play_hands_the_seat_what_the_contract_states_and_its_coverage_reading(tmp_path):
    """Through ``contract.play``: where Detective stops for values, the judgment seat is shown what the contract states of
    the function (its docstring, its intent tests, the lines it must never do) and the contract's coverage reading of the
    code that was written, beside Detective's stop."""
    case = fixture("label")
    src = tmp_path / "labels.contract.json"
    src.write_text(json.dumps(case["contract"]))
    out = tmp_path / "out"
    out.mkdir()
    chat = FakeChat(answers({"supply": {"code": "VIP-1"}}))
    record = contract.play(src, out, chat=chat, runner=Replay.of(case, os.path.realpath(out)))
    seen = shown(chat)
    assert record["detective"]["functions"]["label"]["route"] == "done" and chat.calls == 1
    assert set(seen["contract"]) == {"doc", "intent_tests", "must_never"} and seen["contract"] == contract.judge_contract(case["contract"], "label")
    assert seen["contract"]["must_never"] and {c["category"] for c in seen["contract"]["must_never"]} == {"OUTPUT"}
    assert seen["coverage"] == record["coverage"]["label"] == contract.coverage_gaps(case["contract"], "label")


def test_the_asked_tests_play_writes_are_the_ones_detective_rendered(tmp_path):
    """Through ``contract.play``: a request for a test makes no model call; Detective's own sources are written to
    ``tests/test_<stem>_asked.py`` under the play folder, the file created with the test file's imports, and it compiles.
    (The fixture holds the file the recording wrote through the same function.)"""
    case = fixture("counter")
    src = tmp_path / "counters.contract.json"
    src.write_text(json.dumps(case["contract"]))
    out = tmp_path / "out"
    out.mkdir()
    root = os.path.realpath(out)
    replay = Replay(steps(case, root, 0, 1, 2, 3, 4, 5, doctor_step(root, "counters.py::Counter.bump")))
    chat = FakeChat()
    contract.play(src, out, chat=chat, runner=replay)
    asked = out / "tests" / "test_counters_asked.py"
    assert chat.calls == 0 and asked.read_text() == case["asked"]["tests/test_counters_asked.py"]
    compile(asked.read_text(), str(asked), "exec")
    assert asked.read_text().startswith("from counters import Counter\n\n\nfrom counters import Counter\nimport pytest")
    assert asked.read_text().count("def test_Counter_bump_") == 2


def test_wrong_code_is_reissued_for_its_function_alone_and_play_writes_only_it(tmp_path):
    """The receiver's code_wrong (R7; the plan's step 2): reissue clears one function's cells into the contract's next
    version, recorded with the decision that cleared them, and a play of that version makes exactly one write call and
    follows Detective's record on the module it writes."""
    case = fixture("clamp")
    src = tmp_path / "clamp.contract.json"
    src.write_text(json.dumps(case["contract"]))
    again = tmp_path / "again"
    assert contract.main(["reissue", str(src), "clamp", "--out", str(again)]) == 0
    new = json.loads((again / "contract.json").read_text())
    assert json.loads((again / "reissue.json").read_text()) == {
        "decision": "code_wrong",
        "function": "clamp",
        "from": str(src.resolve()),
        "cleared": ["clamp.signature", "clamp.code"],
        "removed": [],
        "kept": [],
    }
    assert (new["body"][0]["signature"], new["body"][0]["code"]) == (None, None) and json.loads(src.read_text()) == case["contract"]
    assert contract.reissue(new, "clamp") == ("unwritten", new, {})
    assert contract.reissue(case["contract"], "nowhere") == ("unknown_target", case["contract"], {})
    assert contract.main(["reissue", str(again / "contract.json"), "clamp", "--out", str(tmp_path / "twice")]) == 2
    out = tmp_path / "out"
    out.mkdir()
    node = case["contract"]["body"][0]
    reply = json.dumps({"signature": node["signature"], "code": node["code"], "imports": [], "new_objects": []})
    chat = FakeChat(reply)
    record = contract.play(again / "contract.json", out, chat=chat, runner=Replay.of(case, os.path.realpath(out)))
    assert (record["outcome"], record["calls"], chat.calls) == ("written", 1, 1)
    assert {rel: (out / rel).read_text() for rel in case["files"]} == case["files"]
    assert record["detective"]["functions"]["clamp"]["route"] == "done"


def test_the_judgment_seats_request_holds_its_record_and_its_schema_alone(tmp_path):
    """WIRING W7 (FOUNDING_PROMPTS [13], [16]: «The prompt is NOT English»): the judgment seat's whole request, built and
    not sent, holds no text outside its record and its schema. There is no system message; the one user message is the
    record's text exactly, with no answer text after it; the format is the schema that holds the reply."""
    seat = daemon._seat(find_cassette("daemon-judge"), MODEL)
    assert (seat.answer, seat.system) == ("none", "")
    form = decisions.judgment_form("lines", [{"guard": "code.startswith('VIP-')", "line": 11}], ["code"], {"code": schema.param_schema({"type": schema.ANY_JSON, "pool": ["VIP-"]})})
    text = json.dumps({"stop": {"verb": "converge", "kind": "lines"}, "function": {"name": "label — é"}}, ensure_ascii=False, indent=1)
    built = plan(seat, {"record": text}, {"answers": form["properties"]["answers"]})
    assert (built["verdict"], built["invocation"]) == ("ok", "ok")
    assert built["payload"]["messages"] == [{"role": "user", "content": text}]
    assert built["payload"]["format"] == schema.cassette_schema(built["slots"]) and list(built["payload"]["format"]["properties"]) == ["answers"]
    # and as the phase itself sends it: the recorded stop of ``label``, the seat's one call
    case = fixture("label")
    root = laid_out(case, tmp_path)
    chat = FakeChat(answers({"supply": {"code": "VIP-1"}}))
    drive(tmp_path, case, Replay.of(case, root), chat, coverage={"label": []}, contracts={"label": contract.judge_contract(case["contract"], "label")})
    sent = chat.payloads[0]
    assert chat.calls == 1 and [m["role"] for m in sent["messages"]] == ["user"]
    content = sent["messages"][0]["content"]
    assert content == json.dumps(json.loads(content), ensure_ascii=False, indent=1) and set(json.loads(content)) == {"stop", "function", "pools", "contract", "characterization", "inputs", "coverage"}
    assert "Answer with" not in content and list(sent["format"]["properties"]) == ["answers"]


def test_no_choose_call_is_ever_made_and_the_old_cassettes_are_gone():
    """The model is called only where Detective stops for values, by the judgment seat (``daemon-judge``): the cassette
    that chose Detective's next action is not in the library."""
    ids = {cid for cid, _ in list_cassettes()}
    assert "daemon-judge" in ids and "daemon-choose" not in ids
    seat = find_cassette("daemon-judge")
    assert (seat.verdict(), seat.seat, seat.system, [(s.name, s.kind, s.source) for s in seat.slots]) == ("ok", "typed_hole", "", [("answers", "form", "runtime")])
    assert seat.template.strip() == "{record}"
