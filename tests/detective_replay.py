"""Recorded Detective runs, replayed for the harness's tests (``tests/fixtures/detective_json/``).

The fixtures are recorded by ``scripts/record_detective_json.py``: real Detective, run as the harness runs it (the
interpreter's own ``python -m Detective``, ``--json``, both signs), on the files ``contract.play`` renders from a filled
contract. A fixture is one case: ``contract`` (what it was recorded from), ``files`` (the module and its intent tests as
rendered), ``steps`` (each exactly the record ``detective.run`` returned: ``argv``, ``exit``, ``payload``, ``ledger``,
``outcome``, ``certificates``, ``samples``, and ``stdout`` only when there was no payload), ``asked`` (the asked-test file
the line wrote), ``interpreter`` and ``two_sign`` (a case run under Detective's own interpreter, or without ``--two-sign``,
says so), ``versions`` (the Detective and Wesker commits). The folder it ran in is shown ``{root}`` and this machine's home
``{home}``.

``Replay`` is the runner seam: it answers each command the harness runs with the next recorded step, and fails the test
when the command is not the one recorded, so a replay is also the check that the harness ran exactly what Detective's
record named. ``drive`` runs the Detective phase over a recorded case through that seam, with a stub chat for the
judgment seat.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from uroboros.harness import contract, daemon, decisions

FIXTURES = Path(__file__).parent / "fixtures" / "detective_json"
HOME = str(Path.home())
MODEL = {**contract.MODELS["qwen3-coder-next"], "timeout_s": 900}


class FakeChat:
    """Replays scripted replies; a reply that is an Exception is raised. Records every payload it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0
        self.payloads: list = []
        self.timeouts: list = []

    def __call__(self, endpoint, payload, timeout_s, api="openai"):
        self.calls += 1
        self.api = api
        self.payloads.append(payload)
        self.timeouts.append(timeout_s)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, {"output_tokens": 5}


def shown(chat: FakeChat, index: int = 0) -> dict:
    """What the judgment seat's ``index``-th call was shown: the typed record, which is the whole of the user message
    (no answer text follows it: WIRING W7)."""
    return json.loads(chat.payloads[index]["messages"][-1]["content"])


def answers(*items) -> str:
    """The judgment seat's reply: one answer for each item of the stop, by index (``{"supply": {...}}``, ``{"freedom":
    <note>}`` or ``{"cannot_tell": None}``)."""
    return json.dumps({"answers": {str(i): item for i, item in enumerate(items)}})


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def put_back(value, root: str):
    """A recorded value with its folder and this machine's home put back."""
    if isinstance(value, str):
        return value.replace("{root}", root).replace("{home}", HOME)
    if isinstance(value, list):
        return [put_back(v, root) for v in value]
    if isinstance(value, dict):
        return {k: put_back(v, root) for k, v in value.items()}
    return value


def laid_out(case: dict, folder: Path) -> str:
    """The case's files written into ``folder`` as Detective found them; the folder's real path."""
    for rel, text in case["files"].items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    return os.path.realpath(folder)


def result(recorded: dict, root: str) -> dict:
    """One recorded step as the runner returned it, its folder ``root``: the payload also as the ``stdout`` it was read
    from."""
    step = put_back(copy.deepcopy(recorded), root)
    payload = step.get("payload")
    return {"seconds": 0.0, "stdout": json.dumps(payload) if payload is not None else "", "stderr": "", **step}


def crafted(argv: list, payload: object, exit_code: int = 0, ledger: dict | None = None, **more) -> dict:
    """A step no fixture holds, as the runner would return it: the harness is held to its argv like a recorded one."""
    return {
        "argv": argv,
        "exit": exit_code,
        "seconds": 0.0,
        "stdout": json.dumps(payload) if payload is not None else "",
        "stderr": "",
        "payload": payload,
        "ledger": ledger or {},
        "samples": {},
        **more,
    }


def with_command(step: dict, argv: list) -> dict:
    """A recorded step answering another command (a record borrowed for a function the harness is driving)."""
    return {**step, "argv": argv}


def steps(case: dict, root: str, *picks) -> list:
    """Recorded steps by index, and any step built by the test as it is."""
    return [result(case["steps"][p], root) if isinstance(p, int) else p for p in picks]


def doctor_step(root: str, target: str) -> dict:
    """doctor's read of a function that did not reach done, borrowed from a recording (the one of ``clamp_failing``) and
    answering this command: the payload is what a recorded doctor printed, the command is the one the harness runs."""
    return with_command(result(fixture("clamp_failing")["steps"][3], root), ["doctor", target, "--json", "--project-root", root])


def drive(folder: Path, case: dict, runner, chat=None, model: dict | None = None, **more) -> dict:
    """``daemon.drive`` over a recorded case: its contract's path, typed order and targets, under ``model`` (the default
    coding model's record unless given), with ``runner`` for Detective's CLI and a stub chat for the judgment seat."""
    return daemon.drive(folder, case["contract"]["path"], case["contract"]["detective"], contract.detective_targets(case["contract"]), model or MODEL, chat=chat or FakeChat(), runner=runner, **more)


def moves(run: dict) -> list:
    """What a run did, one tuple per command: ``(verb, stage, Detective's code, the harness's move)``."""
    return [(r["argv"][0], r["stage"], (r["decision"] or {}).get("code"), (r["move"] or {}).get("move")) for r in run["runs"]]


class Replay:
    """Detective's CLI as recorded: each command the harness runs is answered with the next step, in order, and a command
    that is not the one the step records fails the test, with both (the recorded ``--json``, which ``detective.run`` adds,
    set aside). ``ran`` lists the commands run, ``left`` the steps not asked for. ``loose`` skips the comparison, for a
    step imported from elsewhere."""

    def __init__(self, steps: list, loose: bool = False):
        self.steps = list(steps)
        self.ran: list = []
        self.loose = loose

    @classmethod
    def of(cls, case: dict, root: str, indices: list | None = None, **more) -> Replay:
        picked = range(len(case["steps"])) if indices is None else indices
        return cls([result(case["steps"][i], root) for i in picked], **more)

    def __call__(self, argv: list) -> dict:
        assert self.steps, f"the replay holds no step for {argv}"
        step = self.steps.pop(0)
        self.ran.append(list(argv))
        if not self.loose:
            assert decisions.json_argv(list(argv)) == step["argv"], f"the harness ran {argv}, the record is of {step['argv']}"
        return step

    @property
    def left(self) -> int:
        return len(self.steps)
