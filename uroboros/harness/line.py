"""uroboros.harness.line — the coding line's driver: one unit played until every function is done or escalated.

The founder, step 6 of ``docs/theory/THE_CODING_LINE.md``: when the contract is bad, «a local model re-run loop? Or it just
gives up and flags it as requiring active intelligence to fix? … both should be built and functional options». ``run_line``
is that loop, in both modes. Every dependency is passed in as a callable (the play, the receiver, the next step, the two
repairs and the replay), so the loop decides only what it is given, and it trusts no callable's result unchecked. A function
once escalated is never played or judged again: an abstention is never retried into an answer (THE_DYAD, Law 5). The
receiver's four decisions are ``decisions.DECISIONS``, and ``decisions.receiver_next`` is the usual ``next_step``.
"""

from __future__ import annotations

from pathlib import Path

from .decisions import DECISIONS


def run_line(contract, mode, ceiling, play, receive, next_step, revise, reissue, replay):
    """Drive one unit through the line until every function is done or escalated.

    contract: the contract's path (a str). mode: "local" or "escalate". ceiling: an int >= 1 (not a bool).
    The callables, all supplied by the caller:
      play(contract, excluded) and replay(played, excluded) each return a RESULT:
          {"played": str (the play's folder), "contract": str (the filled contract it played), "done": bool (the play's
           own record says every function it played is done), "handbacks": [{"function": str, "ending": str,
           "evidence": str}]}
        excluded is a tuple of the functions escalated so far, in the order they were escalated (() before any).
      receive(handback) -> one of DECISIONS
      next_step(decision, current, history, unit_replays, ceiling) -> "revise" | "reissue" | "replay" |
          "escalate:<why>" | anything else. current is {"ending", "evidence"} of the hand-back; history is that
          function's own earlier rounds, oldest first, each {"decision", "ending", "evidence"}; unit_replays is how
          many replays this run has made so far.
      revise(filled_contract, handback) and reissue(filled_contract, function) each return a new contract path, or a
          str starting "refused:". filled_contract is ALWAYS the latest RESULT's "contract".

    The loop:
      1. result = play(contract, <the escalated functions so far>).
      2. Check the RESULT. When it is not a dict of exactly that shape, escalate the unit (function "") with why
         "invalid_play", and stop. When "done" is True but hand-backs remain, or "done" is False and none remain,
         escalate the unit with why "inconsistent_play", and stop.
      3. The open hand-backs are those whose function is not escalated, in order.
         - None open: stop.
         - mode "escalate": escalate the function of every open hand-back, in order, why "mode"; stop.
      4. mode "local": h is the first open hand-back.
         - When h's function already has `ceiling` recorded rounds, escalate it, why "ceiling"; go to 3.
         - decision = receive(h). A decision not in DECISIONS escalates the function, why "invalid_decision"; go to 3.
         - step = next_step(decision, {"ending": h["ending"], "evidence": h["evidence"]}, that function's earlier
           rounds, unit_replays, ceiling). Record the round {"function", "ending", "evidence", "decision", "step"}.
           Then:
           - "revise": new = revise(result["contract"], h). "reissue": new = reissue(result["contract"],
             h["function"]). A str not starting "refused:" becomes the contract, and the loop goes to 1. A
             "refused:..." str escalates the function, why that str. Anything that is not a str escalates it, why
             "invalid_repair". After either, go to 3.
           - "replay": unit_replays += 1; result = replay(result["played"], <the escalated functions so far>); go to 2.
           - "escalate:<why>": escalate the function, why <why>; go to 3.
           - anything else: escalate the function, why "invalid_step"; go to 3.
    A function is escalated at most once. Once it is escalated, its hand-backs are no longer open, and every later play
    and replay receives it in excluded.

    Returns {"outcome": "done" when nothing was escalated, else "escalated",
             "contract": the path last passed to play,
             "rounds": [every recorded round, in order],
             "escalated": [{"function", "why"}, in the order escalated]}.
    Returns "invalid", before calling anything, when contract is not a str, mode is neither "local" nor "escalate",
    or ceiling is not an int >= 1.
    """
    if not isinstance(contract, str):
        return "invalid"
    if not isinstance(mode, str) or mode not in ("local", "escalate"):
        return "invalid"
    if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
        return "invalid"

    rounds = []
    escalated = []
    unit_replays = 0
    played_contract = contract

    def excluded():
        return tuple(entry["function"] for entry in escalated)

    def escalate(function, why):
        if function not in excluded():  # a function is escalated at most once
            escalated.append({"function": function, "why": why})

    def judge(result):
        """Step 2 as a named code: "ok", "invalid_play" or "inconsistent_play"."""
        if not isinstance(result, dict) or set(result) != {"played", "contract", "done", "handbacks"}:
            return "invalid_play"
        if not isinstance(result["played"], str) or not isinstance(result["contract"], str):
            return "invalid_play"
        if not isinstance(result["done"], bool) or not isinstance(result["handbacks"], list):
            return "invalid_play"
        for handback in result["handbacks"]:
            if not isinstance(handback, dict) or set(handback) != {"function", "ending", "evidence"}:
                return "invalid_play"
            if not all(isinstance(value, str) for value in handback.values()):
                return "invalid_play"
        if result["done"] and result["handbacks"]:
            return "inconsistent_play"
        if not result["done"] and not result["handbacks"]:
            return "inconsistent_play"
        return "ok"

    result = play(played_contract, excluded())  # 1
    while True:
        verdict = judge(result)  # 2
        if verdict != "ok":
            escalate("", verdict)
            break

        gone = excluded()  # 3
        open_handbacks = [handback for handback in result["handbacks"] if handback["function"] not in gone]
        if not open_handbacks:
            break
        if mode == "escalate":
            for handback in open_handbacks:
                escalate(handback["function"], "mode")
            break

        h = open_handbacks[0]  # 4, mode "local"
        function = h["function"]
        earlier = [r for r in rounds if r["function"] == function]
        if len(earlier) >= ceiling:
            escalate(function, "ceiling")
            continue
        decision = receive(h)
        if decision not in DECISIONS:
            escalate(function, "invalid_decision")
            continue
        history = [{"decision": r["decision"], "ending": r["ending"], "evidence": r["evidence"]} for r in earlier]
        step = next_step(decision, {"ending": h["ending"], "evidence": h["evidence"]}, history, unit_replays, ceiling)
        rounds.append({"function": function, "ending": h["ending"], "evidence": h["evidence"], "decision": decision, "step": step})

        if step == "revise" or step == "reissue":
            new = revise(result["contract"], h) if step == "revise" else reissue(result["contract"], function)
            if not isinstance(new, str):
                escalate(function, "invalid_repair")
            elif new.startswith("refused:"):
                escalate(function, new)
            else:
                played_contract = new
                result = play(played_contract, excluded())  # the loop goes to 1
        elif step == "replay":
            unit_replays += 1
            result = replay(result["played"], excluded())  # go to 2
        elif isinstance(step, str) and step.startswith("escalate:"):
            escalate(function, step[len("escalate:") :])
        else:
            escalate(function, "invalid_step")

    return {
        "outcome": "escalated" if escalated else "done",
        "contract": played_contract,
        "rounds": rounds,
        "escalated": escalated,
    }


def execute_line(source, out, model_key, mode, ceiling, *, contract_writer=None, target=None, chat=None, runner=None):
    """Run the production adapters and write the rounds, escalations and optional placement result."""
    import ast
    import hashlib
    import json

    from . import admission, contract, daemon, decisions
    from .cassette import find_cassette
    from .run import invoke

    try:
        out.mkdir(parents=True)
    except FileExistsError:
        return {"outcome": "out_exists"}
    model = contract.MODELS[model_key]
    seat = daemon._seat(find_cassette("receiver"), model)
    folder = None
    count = 0
    evidence = {}
    repairs = {}
    fills = {}

    def play(path, excluded, replay_of=None):
        nonlocal folder, count
        folder = out / f"round_{count:02d}"
        count += 1
        result = contract.play(
            Path(path),
            folder,
            model_key,
            chat=chat,
            runner=runner,
            contract_writer=contract_writer,
            admission="local-harness",
            excluded=excluded,
            repairs=repairs,
        )
        if replay_of:
            result["replay_of"] = replay_of
            (folder / "play.json").write_text(json.dumps(result, indent=1))
        calls = folder / "calls.jsonl"
        if calls.exists():
            for row in calls.read_text().splitlines():
                entry = json.loads(row)
                if entry.get("fill") == "filled":
                    fills[entry["qualname"]] = json.loads(entry["reply"])
        snapshot = folder / "contract.json"
        unit = json.loads(snapshot.read_text()) if snapshot.exists() else {}
        backs = {name: evidence[name] for name in excluded if name in evidence}
        for row in (folder / "handbacks.jsonl").read_text().splitlines():
            handback = json.loads(row)
            name = handback["function"]
            if name in excluded:
                continue
            node = contract._find(unit, name)[0] if unit else None
            driven = result.get("detective", {})
            runs = driven.get("functions", {}).get(name, {}).get("runs", [])
            terminal = runs[-1] if runs else {}
            observations = {key: terminal[key] for key in ("failing_tests", "errored_tests", "load_failed") if key in terminal}
            backs[name] = {"handback": handback, "contract_entry": node, "round": str(folder), "observations": observations}
        evidence.update(backs)
        records = []
        for name, entry in backs.items():
            handback = entry["handback"]
            # Keep the decision and observations, omitting run-specific doctor timestamps and durations.
            doctor = handback.get("doctor") or {}
            stable = {
                "decision": handback["decision"],
                "doctor": {color: doctor[color].get("code") for color in ("green", "red", "yellow") if isinstance(doctor.get(color), dict)},
                "observations": entry["observations"],
                "contract_entry": entry["contract_entry"],
            }
            call = handback.get("evidence") or {}
            stable["call"] = {key: call[key] for key in ("reply", "fill", "transport") if key in call}
            basis = json.dumps(stable, ensure_ascii=False, sort_keys=True).replace(str(folder.resolve()), "<round>").replace(str(folder), "<round>")
            records.append({"function": name, "ending": handback["ending"], "evidence": hashlib.sha256(basis.encode()).hexdigest()})
        # Detective runs the full suite. Prioritize the owner of a shared failing intent test.
        # This orders repairs and preserves every Detective hand-back and its target.
        test_file = f"tests/test_{Path(unit.get('path', '')).stem}.py"
        reported = {
            name: set(entry["observations"].get("failing_tests", ()))
            for name, entry in backs.items()
        }
        owned = {
            name: reported[name] & {
                f"{test_file}::{test}"
                for test in admission.intent_names((entry["contract_entry"] or {}).get("intent_test", {}).get("source"))
            }
            for name, entry in backs.items()
        }
        recognized = set().union(*owned.values())
        records.sort(key=lambda row: bool(reported[row["function"]] & recognized) and not owned[row["function"]])
        return {"played": str(folder), "contract": str(snapshot if snapshot.exists() else path), "done": result["done"] and not records, "handbacks": records}

    def receive(handback):
        if handback["ending"] == "transport":
            return "machine_wrong"
        if handback["ending"] in ("judge:cannot_tell", "no_remaining_choices", "context_over_capacity", "call_refused:context_over_capacity"):
            return "cannot_tell"
        entry = evidence[handback["function"]]
        node = entry["contract_entry"] or {}
        authored = node.get("doc") or {}
        tests = node.get("intent_test") or {}
        code = None
        if node and node.get("code") is not None:
            status, definition = contract._function_def(node, handback["function"])
            if status == "ok":
                code = ast.unparse(ast.fix_missing_locations(definition))
        shown = {
            "question": "Classify the cause of this hand-back using the stated intent, failing tests, and current code.",
            "ending": handback["ending"],
            "observations": entry["observations"],
            "intent": authored.get("prose"),
            "intent_tests": tests.get("source"),
            "current_code": code,
            "detective_decision": entry["handback"]["decision"],
            "doctor": entry["handback"]["doctor"],
            "decision_meanings": {
                "code_wrong": "The current code violates the stated intent or its matching tests, and a different implementation can satisfy them.",
                "contract_wrong": "The tests or contract contradict stated intent, or the contract provides no way to express it. A failing test alone does not imply this.",
                "machine_wrong": "The evidence identifies a runtime or tool fault independent of the code and contract.",
                "cannot_tell": "The evidence does not distinguish the causes.",
            },
        }
        result = invoke(seat, {"record": json.dumps(shown, ensure_ascii=False)}, {"decision": DECISIONS}, chat=chat)
        with (out / "receiver.jsonl").open("a") as log:
            log.write(json.dumps({"function": handback["function"], "result": result.to_dict()}, ensure_ascii=False) + "\n")
        return result.values.get("decision", "cannot_tell") if result.status == "admitted" else "cannot_tell"

    def next_step(decision, current, history, replays, limit):
        if current["ending"] in ("context_over_capacity", "call_refused:context_over_capacity"):
            return "escalate:context_over_capacity"
        step = decisions.receiver_next(decision, current, history, replays, limit)
        return "escalate:contract_wrong" if step == "revise" else step

    def reissue(path, name):
        if not name:
            return "refused:unit_scope"
        current = json.loads(Path(path).read_text())
        code, updated, _detail = contract.reissue(current, name)
        if code not in ("reissued", "unwritten"):
            return f"refused:{code}"
        repair = repairs.setdefault(name, {"rejected": []})
        repair["evidence"] = evidence[name]["handback"]
        node = evidence[name]["contract_entry"]
        repair["prior_cells"] = {key: node.get(key) for key in ("signature", "code")}
        template = node.get("ast_template", {})
        prior_fill = fills.pop(name, {})
        prior = prior_fill.get("choices") if not template.get("holes") else None
        options = template.get("choices", {})
        if prior is None and len(options) == 1 and not template.get("holes"):
            slot, labels = next(iter(options.items()))
            for label in labels:
                candidate = {slot: label}
                status, cells = decisions.expand_ast_template(node["ast_template"], candidate)
                if status == "expanded" and all(cells[key] == value for key, value in repair["prior_cells"].items()):
                    prior = candidate
                    break
        if isinstance(prior, dict) and prior not in repair["rejected"]:
            repair["rejected"].append(prior)
        revised = out / f"reissue_{count:02d}.json"
        revised.write_text(json.dumps(updated, indent=1, ensure_ascii=False))
        return str(revised)

    def replay(played, excluded):
        snapshot = Path(played) / "contract.json"
        return play(str(snapshot if snapshot.exists() else source), excluded, replay_of=played)

    result = run_line(str(source), mode, ceiling, play, receive, next_step, None, reissue, replay)
    result["played"] = str(folder)
    for escalation in result["escalated"]:
        entry = evidence.get(escalation["function"])
        if entry:
            escalation.update(entry)
        if escalation["why"] in ("contract_wrong", "context_over_capacity"):
            escalation["receivers"] = [contract_writer or "contract_author"]
        elif escalation["why"] == "second_machine_fault":
            escalation["receivers"] = ["founder"]
        else:
            escalation["receivers"] = (entry or {}).get("handback", {}).get("receivers", ["founder"])
    if result["outcome"] == "done":
        checked = admission.admit(folder, target)
        result["admission"] = checked
        if checked["outcome"] != ("checked" if target is None else "admitted"):
            result["outcome"] = "admission_failed"
    (out / "line.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result
