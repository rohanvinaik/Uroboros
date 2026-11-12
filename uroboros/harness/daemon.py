"""uroboros.harness.daemon — the Detective phase: Detective's own record, followed by stage.

Detective over Wesker owns the measurement and the next action. Once the checked AST renders into a module and
its intent tests, the harness runs each command through ``detective.run`` with ``--json``. It follows the returned
decision record for the current stage through ``decisions.detective_move``. See ``docs/ARCHITECTURE.md``.

- ``folder``: the contract's typed order's first command, ``regime``, once. Its record names Detective's own repair
  (``regime --migrate``), whose record is read next, until the folder is clean.
- ``setup``: on each function, bottom-up, the order's second command, ``audit``. It makes sure the suite is set up right
  and the Detective run is ready to run correctly, and is never an endpoint. A clean two-sign audit names ``converge`` in
  its own record (his choice, 2026-10-08, FOUNDING_PROMPTS [22]).
- ``run``: ``converge``, the only place a function is done, and Detective's own commands from there (a regime repair it
  names, a rendered test to write, a value to supply), each the one its record names, admitted and run.

The moves, and what the harness does with each: ``run`` the command the record names; ``resume`` the converge after a
regime repair completed (a regime record names none); ``write_tests`` append Detective's own rendered tests to the
asked-test file (``write_test``), then run the command it names; ``judge`` ask the judgment seat, only where Detective
stops for values (the ``daemon-judge`` cassette, ``decisions.judgment_dispatch``): the values it gives go on the next
command as ``--input``s, and a mutant it frees is flagged for exactly that item (``decisions.flag_argv``, built by the
harness from Detective's record); ``done`` a function is done only when converge's run reaches it; ``hand_back`` and
``end`` stop the function under Detective's own code; ``setup_clean`` begins the functions. A record the stage model
cannot read (``invalid``) ends the function, and a payload that is a typed refusal, is not JSON, or holds no decision
ends it under the code ``decisions.payload_decision`` names.

doctor is read once by the harness itself, after a function ends in anything but ``done`` (not after the folder's setup,
not between Detective's own steps): its payload is recorded in the function's result as evidence for whoever receives the
hand-back, and never ends, gates or changes anything. A function's step bound (``steps``) counts the commands it runs,
across its setup and its run, and ends it ``budget`` when spent; doctor's read and the flags are not counted.

Every command is admitted by ``decisions.daemon_argv`` and run over both signs (``decisions.two_sign_argv``): every
contract's intent tests carry both (the founder, 2026-10-06); ``detective.run`` adds ``--json``. Placement consumes the terminal evidence through ``admission.admit``.

Each function's result holds every run (its argv, its exit code, its seconds, the stage it was read in, Detective's
decision record and the move the harness made of it), its ending, its route (``done`` or ``handback``), its class
(``decisions.run_class``) and doctor's payload. Everything is also recorded in the folder: each Detective output whole
(``step_NN_<verb>.txt``) and one line per run and per model call (``possession.jsonl``).
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
from collections.abc import Callable
from pathlib import Path

from . import decisions, detective, schema
from .cassette import Cassette, find_cassette
from .run import invoke


def _held(cassette: Cassette, model: dict, system: str = "") -> Cassette:
    """The cassette under the contract's model and options, in place of its own: one model record. The coding seats'
    system message is empty (the record the user message holds is the whole of what the model reads), or ``system`` when
    given."""
    return dataclasses.replace(
        cassette,
        model=str(model["name"]),
        api=str(model["api"]),
        endpoint=str(model.get("endpoint") or ""),
        think=model.get("think"),
        keep_alive=str(model.get("keep_alive") or ""),
        timeout_s=float(model["timeout_s"]),
        options=dict(model.get("options") or {}),
        context_limit=model.get("context_limit"),
        system=system,
    )


def _seat(cassette: Cassette, model: dict) -> Cassette:
    """A seat's cassette under the model the play names (``_held``: its name, wire, context window and residency) and the
    cassette's own call settings, which win where it declares them: its sampling and output cap (``options``), its wall
    (``timeout_s``) and its ``think`` (the runbook, §9: the judgment seat's call settings are its cassette's ``[model]``
    table). A key only the model's record carries stays: it is that model's own workaround (``num_batch``)."""
    held = _held(cassette, model)
    think = held.think if cassette.think is None else cassette.think
    return dataclasses.replace(held, options={**held.options, **cassette.options}, timeout_s=cassette.timeout_s, think=think)


def drive(
    folder: Path,
    module: str,
    order: list,
    functions: list,
    model: dict,
    chat: Callable | None = None,
    runner: Callable | None = None,
    write_test: Callable | None = None,
    steps: int = 16,
    style: int = 2,
    coverage: dict | None = None,
    contracts: dict | None = None,
) -> dict:
    """The Detective phase of the module at ``module`` in ``folder``. The order's first command runs once, for the
    folder; when that setup ends clean, the order's second command runs on each function of ``functions`` (``[[qualname,
    target], ...]``, in order), and each function follows Detective's record to its ending. ``model`` is a
    ``contract.MODELS`` record with its timeout resolved: the judgment seat is that model under the ``daemon-judge``
    cassette's own call settings (``_seat``). ``write_test(qualname, tests)`` appends Detective's own
    rendered tests (a list of sources) to the asked-test file and returns ``""`` or the code that ended the write;
    without it, a request for a test ends the function. ``coverage`` is the contract's coverage reading of each function
    (``{<qualname>: [lines]}``, ``contract.coverage_gaps``) and ``contracts`` what it states of each (``{<qualname>:
    {"doc", "intent_tests", "must_never"}}``, ``contract.judge_contract``), both shown to the judgment seat beside
    Detective's record, as information and never a verdict. ``chat`` and ``runner`` stand in for the model's wire and
    Detective's CLI in tests; ``steps`` bounds the commands one function runs. ``style`` is kept for the callers that
    pass it: the harness declines the style pass (his [15]).

    Returns ``{"setup": {"runs", "ending", "route", "class"}, "functions": {<qualname>: {"runs", "ending", "route",
    "class", "doctor"}}}``: ``route`` is ``done`` only on Detective's own done (a function's converge settled; the
    folder's regime clean), else ``handback``; ``doctor`` is doctor's payload, or ``None`` when it was not read.
    """
    root = os.path.realpath(folder)
    eng = detective.engine()
    run_detective = runner or (lambda argv: detective.run(argv, Path(root), eng))
    seat = _seat(find_cassette("daemon-judge"), model)
    record: dict = {"setup": {"runs": [], "ending": "", "route": "", "class": ""}, "functions": {}}
    counter = [0]

    def note(entry: dict) -> None:
        with (Path(root) / "possession.jsonl").open("a") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")

    def admit(tokens: list, target: str) -> list | str:
        """A command Detective named (or the order's), admitted for this function and run over both signs; or the breach."""
        argv = decisions.daemon_argv(shlex.join(["detective", *tokens]), target, module, root)
        if isinstance(argv, str):
            return f"contract_breach:{argv}"
        policy = decisions.two_sign_argv(argv)
        return f"contract_breach:{policy}" if isinstance(policy, str) else policy

    def signature(target: str) -> list | str:
        """What the harness reads of a target for an ask: its parameters, never its body (the model sees only these)."""
        if not target:
            return "no_function"
        try:
            text = (Path(root) / module).read_text()
        except OSError:
            return "unreadable"
        return decisions.target_signature(text, target.partition("::")[2].rpartition(".")[2])

    def execute(argv: list, stage: str, runs: list, target: str) -> tuple:
        """One command run and kept: its output whole in the folder, a line in the log, and its entry among ``runs``."""
        res = run_detective(argv)
        (Path(root) / f"step_{counter[0]:02d}_{argv[0]}.txt").write_text(str(res.get("stdout") or "") + "\n--- stderr ---\n" + str(res.get("stderr") or ""))
        counter[0] += 1
        entry: dict = {"argv": argv, "exit": res["exit"], "seconds": res.get("seconds"), "stage": stage, "decision": None, "move": None}
        payload = res.get("payload")
        if argv[0] == "converge" and isinstance(payload, dict):
            entry["written_path"] = payload.get("written_path")
            entry["function_digest"] = payload.get("function_digest")
        if argv[0] == "audit" and isinstance(payload, dict):
            for key in ("failing_tests", "errored_tests", "load_failed"):
                if payload.get(key):
                    entry[key] = payload[key]
        runs.append(entry)
        note({"act": "detective", "target": target, "argv": argv, "exit": res["exit"], "seconds": res.get("seconds"), "stage": stage})
        return res, entry

    def judged(move: dict, stop: dict, res: dict, target: str, qualname: str, params: list | str, stage: str, abstained: list, written: list, runs: list) -> tuple:
        """The judgment seat, where Detective stops for values: one call reads the stop (Detective's typed items), the
        function, the values mined from the code the stop prints, what its contract states, the tests Detective wrote
        for it and the inputs already supplied, and answers each item with values, a freedom or cannot-tell. Returns
        ``(code, tokens)``: a code ends the function; else ``tokens`` is the next command, Detective's own with the
        values as ``--input``s (a freedom's mutants are flagged first, by the harness). A reply is admitted whole or not
        at all: a value the harness cannot write as a call (``judge:unrenderable:<item>``) ends the function before any
        flag or command runs."""
        if isinstance(params, str):
            return f"input_unavailable:{params}", []
        kind, items = move["kind"], move["items"]
        names = [p for p, _ in params]
        pools = decisions.ask_pools(decisions.pool_ask(kind, items, names))
        mined = pools if isinstance(pools, dict) else {}
        types = {p: schema.param_schema({"type": decisions.param_type(a) if a else schema.ANY_JSON, "pool": mined.get(p, [])}) for p, a in params}
        form = decisions.judgment_form(kind, items, names, types)
        if isinstance(form, str):
            return f"judge:{form}_form", []
        path, _, qual = target.partition("::")
        key = f"{os.path.relpath(os.path.join(root, path), root)}::{qual}" if qual else ""
        characterization = []
        for held in dict.fromkeys(written):
            if os.path.realpath(held).startswith(root + os.sep):
                try:
                    characterization.append(Path(held).read_text())
                except OSError:
                    continue
        shown = {
            "stop": {"verb": stop["verb"], "code": stop["code"], "kind": kind, "items": items, "then": move["then"]},
            "function": {"name": qual, "params": [{"name": p, "annotation": a} for p, a in params]},
            "pools": mined,
            "contract": (contracts or {}).get(qualname),
            "characterization": characterization,
            "inputs": decisions.supplied_inputs(res.get("samples"), key, res.get("ledger")),
            "coverage": (coverage or {}).get(qualname),
        }
        out = invoke(seat, {"record": json.dumps(shown, ensure_ascii=False, indent=1, default=str)}, {"answers": form["properties"]["answers"]}, None, chat=chat)
        budget = out.records[-1].telemetry.get("context") if out.records else None
        note({"act": "judge", "target": target, "kind": kind, "pools": pools, "status": out.status, "reason": out.reason, **({"context": budget} if budget else {})})
        if out.status != "admitted":
            return f"call_{out.status}:{out.reason}", []
        dispatched = decisions.judgment_dispatch({"answers": out.values.get("answers")}, kind, items, names)
        if isinstance(dispatched, str):
            return f"judge:{dispatched}", []
        literals = []
        for given in dispatched["inputs"]:
            literal = decisions.render_call_args(names, given["values"])
            if not literal:
                return f"judge:unrenderable:{given['item']}", []  # a reply is admitted whole or not at all: nothing is flagged or run
            literals.append(literal)
        keys = decisions.abstained_keys(kind, items, dispatched["abstained"])
        abstained.extend(k for k in (keys if isinstance(keys, list) else []) if k not in abstained)
        for flag in dispatched["flags"]:
            for mutant_id in flag["ids"]:
                argv = decisions.flag_argv(target, mutant_id, flag["note"], root)
                if isinstance(argv, str):
                    return f"judge:flag_{argv}:{mutant_id}", []
                freed, entry = execute(argv, stage, runs, target)
                entry["decision"], entry["move"] = freed.get("payload"), {"move": "flag", "id": mutant_id}
                if freed["exit"] != 0:
                    return f"judge:flag_refused:{mutant_id}", []
        if not literals and not dispatched["flags"]:
            return "judge:cannot_tell", []
        return "", [*move["then"], *(token for lit in literals for token in ("--input", lit))]

    def follow(stage: str, tokens: list, target: str, qualname: str, params: list | str) -> dict:
        """One run: the command it starts with, then Detective's record to the run's ending, route and class."""
        runs: list = []
        abstained: list = []
        written: list = []
        has_params = params != []
        count = 0

        def end(code: str, route: str = "handback") -> dict:
            last = next((r["exit"] for r in reversed(runs) if r["argv"][0] != "flag"), None)
            return {"runs": runs, "ending": code, "route": route, "class": decisions.run_class(route, code, last)}

        while True:
            if count >= steps:
                return end("budget")
            argv = admit(tokens, target)
            if isinstance(argv, str):
                return end(argv)
            count += 1
            res, entry = execute(argv, stage, runs, target)
            payload = res.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("written_path"), str):
                written.append(payload["written_path"])
            read = decisions.payload_decision(argv[0], payload, (res.get("ledger") or {}).get("refusal"))
            if isinstance(read, str):
                entry["decision"] = payload if isinstance(payload, dict) else None
                return end(read)
            entry["decision"] = read
            move = decisions.detective_move(read, stage, has_params, abstained)
            entry["move"] = move
            kind = move["move"]
            if kind in ("done", "setup_clean"):
                return end(move["code"], "done")
            if kind in ("hand_back", "end"):
                return end(move["code"])
            if kind == "run":
                tokens, stage = move["argv"], move["stage"]
            elif kind == "resume":
                tokens, stage = ["converge", target], "run"
            elif kind == "write_tests":
                if write_test is None:
                    return end("write_unavailable")
                code = write_test(qualname, move["tests"])
                note({"act": "write_tests", "target": target, "tests": len(move["tests"]), "outcome": code})
                if code:
                    return end(code)
                tokens, stage = move["then"], move["stage"]
            elif kind == "judge":
                code, tokens = judged(move, read, res, target, qualname, params, stage, abstained, written, runs)
                if code:
                    return end(code)
                stage = move["stage"]
            else:
                return end(f"invalid:{move.get('code', kind)}")

    def doctor(target: str) -> dict | None:
        """doctor's read of a function that did not reach done, as evidence: its payload, or ``None`` when it was not
        one. It ends, gates and changes nothing."""
        argv = decisions.daemon_argv(shlex.join(["detective", "doctor", target, "--json", "--project-root", root]), target, module, root)
        if isinstance(argv, str):
            return None
        runs: list = []
        res, _entry = execute(argv, "doctor", runs, target)
        payload = res.get("payload")
        return payload if isinstance(payload, dict) else None

    if runner is None and eng["state"] != "ok":
        record["setup"] = {"runs": [], "ending": eng["state"], "route": "handback", "class": decisions.run_class("handback", eng["state"], None)}
        return record
    record["setup"] = follow("folder", [order[0]], "", "", [])
    if record["setup"]["route"] != "done":
        return record
    for qualname, target in functions:
        result = follow("setup", [order[1], target], target, qualname, signature(target))
        result["doctor"] = None if result["route"] == "done" else doctor(target)
        record["functions"][qualname] = result
    return record
