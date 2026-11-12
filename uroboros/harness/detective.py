"""uroboros.harness.detective — Detective's body: invoke its CLI in a workspace and read back its own record.

Run one Detective command as an argv with no shell. Resolve Detective and Wesker in the
selected interpreter or use the explicitly configured local checkout roots. It then reads what Detective wrote:
its stdout (with ``--json``, one JSON object holding Detective's own decision record: ``payload``), the newest record of
``.detective/ledger.jsonl`` (the named outcome code) and ``tests/detective/certificates.json`` (the standing). Which
recorded code is the run's own is Detective's decision, ``ledger.outcome_disposition``, loaded from the same engine
and given the partition its docstring asks the caller for; the harness keeps no copy of the rule. It also reads the text
of Wesker's ``engine.py`` from the same path, for its categories (``wesker_categories``). It decides nothing else;
``decisions`` and ``daemon`` do.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

from . import decisions


_ENGINE_ROOTS = """
import importlib.util
import os
from pathlib import Path

roots = []
for name in ("Detective", "Wesker"):
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.origin:
        raise SystemExit(1)
    root = str(Path(spec.origin).resolve().parent.parent)
    if root not in roots:
        roots.append(root)
print(os.pathsep.join(roots))
"""


def engine() -> dict:
    """Resolve the installed engines or the explicitly configured local checkouts."""
    python = os.environ.get("UROBOROS_DETECTIVE_PYTHON") or sys.executable
    path = os.environ.get("UROBOROS_DETECTIVE_PATH", "")
    if not path:
        try:
            probe = subprocess.run(
                [python, "-c", _ENGINE_ROOTS],
                cwd=Path(__file__).resolve().parent,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if probe.returncode == 0:
                path = probe.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"pythonpath": path, "python": python, "state": "ok" if path else "detective_unset"}


def _last_json_line(path: Path) -> dict:
    try:
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else {}
    except (OSError, ValueError):
        return {}


def _json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _detective_module(eng: dict, name: str) -> ModuleType | None:
    """Detective's own module ``name`` (``ledger``, ``atomic_store``: each imports only the standard library), loaded
    from the package ``eng`` resolved, or ``None`` when there is none to load."""
    for entry in str(eng.get("pythonpath") or "").split(os.pathsep):
        path = Path(entry) / "Detective" / f"{name}.py"
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(f"_uroboros_detective_{name}", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001 — a checkout that will not load leaves the decision unmade, named below
            return None
        return module
    return None


def wesker_categories(eng: dict) -> dict | str:
    """Wesker's mutation categories, each with the syntax nodes its mutator visits, read from the package ``eng``
    resolved (when called, never at import): the text of the first ``<entry>/Wesker/engine.py`` on its path, given to
    ``decisions.wesker_categories``, which executes nothing of Wesker's. The categories, or one code: that decision's
    own (``syntax_error`` · ``no_categories`` · ``no_mutator:<MEMBER>``) · ``unreadable`` (the file could not be read) ·
    ``wesker_unset`` (no entry of the path holds the file)."""
    for entry in str(eng.get("pythonpath") or "").split(os.pathsep):
        path = Path(entry) / "Wesker" / "engine.py"
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            return "unreadable"
        return decisions.wesker_categories(source)
    return "wesker_unset"


def outcome(entry: dict, verb: str, eng: dict) -> dict:
    """Which code, if any, the run's ledger entry holds as the invoked verb's own: Detective's
    ``outcome_disposition`` over the partition its docstring names (the codes this verb recorded, and the count any
    other verb recorded), and the code when it is authoritative. ``{"disposition": "unrecorded"}`` when Detective
    wrote no entry, ``"undecided"`` when its decision could not be loaded."""
    if not entry:
        return {"disposition": "unrecorded", "code": ""}
    observations = [o for o in entry.get("outcome") or [] if isinstance(o, list) and len(o) >= 2]
    mine = [o for o in observations if o[1] == verb]
    codes = {str(o[2]) for o in mine if len(o) >= 3}
    ledger = _detective_module(eng, "ledger")
    if ledger is None:
        return {"disposition": "undecided", "code": ""}
    disposition = ledger.outcome_disposition(len(mine), len(observations) - len(mine), len(codes) <= 1)
    return {"disposition": disposition, "code": next(iter(codes)) if disposition == "authoritative" else ""}


def samples(workspace: Path, eng: dict) -> dict:
    """Detective's store of the inputs supplied (``.detective/inputs.json``, written by ``samples.remember``), read
    without touching it. Detective's own reader sets a corrupt file aside, so the harness gathers the four facts itself
    and Detective's ``atomic_store.store_read_disposition`` names what they add up to: ``absent`` · ``unreadable`` ·
    ``corrupt`` · ``readable``, or ``undecided`` when that decision cannot be loaded. ``store`` is the file's object
    when it is readable, else ``{}``."""
    path = workspace / ".detective" / "inputs.json"
    exists, read_ok, parsed, store = path.exists(), False, False, None
    if exists:
        try:
            text = path.read_text(encoding="utf-8")
            read_ok = True
        except OSError:
            text = ""
        try:
            store = json.loads(text) if read_ok else None
            parsed = read_ok
        except ValueError:
            store = None
    atomic_store = _detective_module(eng, "atomic_store")
    if atomic_store is None:
        return {"read": "undecided", "store": {}}
    read = atomic_store.store_read_disposition(exists, read_ok, parsed, isinstance(store, dict))
    return {"read": read, "store": store if read == "readable" and isinstance(store, dict) else {}}


def _payload(stdout: str) -> dict | None:
    """``--json`` stdout as the one object Detective prints, or ``None`` when it is not exactly one JSON object."""
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def run(argv: list, workspace: Path, eng: dict, timeout_s: int = 900) -> dict:
    """One Detective invocation and what Detective recorded about it. ``exit`` 124 marks the wall. Every ``regime``,
    ``audit``, ``converge``, ``doctor`` and ``flag`` is run with ``--json`` (``decisions.json_argv``); ``argv`` is what
    ran, and ``payload`` its stdout read as the one JSON object it prints, ``None`` when it is not one. Detective writes
    its progress to stderr in both modes, so ``--json`` stdout is the payload alone."""
    sent = decisions.json_argv(argv)
    if isinstance(sent, str):
        raise ValueError(f"not a Detective command: {argv!r}")
    env = dict(os.environ)
    env["PYTHONPATH"] = eng["pythonpath"]
    before = _last_json_line(workspace / ".detective" / "ledger.jsonl")
    started = time.time()
    try:
        done = subprocess.run([eng["python"], "-m", "Detective", *sent], cwd=workspace, env=env, capture_output=True, text=True, timeout=timeout_s)
        stdout, stderr, code = done.stdout, done.stderr, done.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, code = "", f"wall: no answer in {timeout_s}s", 124
    after = _last_json_line(workspace / ".detective" / "ledger.jsonl")
    entry = after if after != before else {}
    return {
        "argv": list(sent),
        "exit": code,
        "seconds": round(time.time() - started, 2),
        "stdout": stdout,
        "payload": _payload(stdout),
        "stderr": stderr,
        "ledger": entry,
        "outcome": outcome(entry, sent[0], eng),
        "certificates": _json_file(workspace / "tests" / "detective" / "certificates.json"),
        "samples": samples(workspace, eng),
    }


def archived_converge(unit: Path) -> dict:
    """Recover the typed converge payloads from plays recorded before pin paths were stored in play.json."""
    records = {}
    for file in sorted(unit.glob("step_*_converge.txt"), key=lambda path: int(path.name.split("_")[1]) if path.name.split("_")[1].isdigit() else -1):
        try:
            payload, _end = json.JSONDecoder().raw_decode(file.read_text().lstrip())
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("function"), str) and "written_path" in payload:
            records[payload["function"]] = payload
    return records


def place_certificates(target: Path, module: str, labels: dict, eng: dict) -> str:
    """Write the unit's certificates using the same Detective package and interpreter that produced them."""
    # The certificate store imports Detective's package, so load it under the same environment as the CLI.
    program = r"""
import json
import sys
from Detective import certificates
from Detective.atomic_store import write_json_store

target, module = sys.argv[1:]
labels = json.load(sys.stdin)
for key, label in labels.items():
    path = certificates.record_certificate(
        target, key, label["function_digest"], label["standing"], label.get("refusal", ""),
        cut_reasons=tuple(label.get("cut_reasons", ())),
    )
    if not path:
        print(f"label_unwritten:{key}")
        sys.exit(1)
path = certificates._path(target, certificates.DEFAULT_WRITE_DIR)
entries = certificates._load_all(target, certificates.DEFAULT_WRITE_DIR)
stale = {key for key in entries if key.startswith(module + "::") and key not in labels}
if stale:
    current = {key: entry for key, entry in entries.items() if key not in stale}
    code = write_json_store(path, current, dumps=lambda value: json.dumps(value, indent=2, sort_keys=True) + "\n")
    if code != "write":
        print("label_unwritten:ledger")
        sys.exit(1)
"""
    env = {**os.environ, "PYTHONPATH": eng["pythonpath"], "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([eng["python"], "-c", program, str(target), module], input=json.dumps(labels), env=env, text=True, capture_output=True, check=False)
    if result.returncode == 0:
        return "placed"
    code = result.stdout.strip()
    return code if code.startswith("label_unwritten:") else "label_unwritten:ledger"
