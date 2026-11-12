"""Admit a unit from its terminal Detective records and carry its evidence into a target repository.

The target gate runs on the placed module and tests. A successful placement copies Detective's
certificate and commits the paths. A failed placement restores them.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from . import contract, daemon, decisions, detective
from .cassette import find_cassette
from .run import invoke

# Detective's own classes of what a settled converge leaves, as possession's typed state names them
# (possession._ROWS["converge"]): still killable, unclassified, unresolved, unproven-equivalent, crash-only.
SURVIVOR_CLASSES = ("killable", "unclassified", "unresolved", "unproven_equivalent", "crash_only")

# The verdicts every record may take; a contradiction names its intent test or coverage line besides.
VERDICTS = ("agrees", "cannot_tell")

# ── The clean check and the survivors (pure) ─────────────────────────────────────────────────────────────────────


def clean(record: object) -> str:
    """Did the unit's Detective phase end clean (pure)? ``clean``; or ``not_written:<outcome>`` when the play wrote no
    module; or the first ending that is not clean, as ``<class>:<qualname>`` (the folder's setup as ``<class>:``); or
    ``unread`` for a record that is not a play's."""
    if not isinstance(record, dict) or not isinstance(record.get("outcome"), str):
        return "unread"
    if record["outcome"] != "written":
        return f"not_written:{record['outcome']}"
    detective = record.get("detective")
    setup = detective.get("setup") if isinstance(detective, dict) else None
    functions = detective.get("functions") if isinstance(detective, dict) else None
    if not isinstance(setup, dict) or not isinstance(functions, dict):
        return "unread"
    for qualname, outcome in [("", setup), *functions.items()]:
        given = outcome.get("class") if isinstance(outcome, dict) else None
        if given != "clean":
            return f"{given if isinstance(given, str) and given else 'unread'}:{qualname}"
    return "clean"


def _last_state(outcome: object) -> object:
    runs = outcome.get("runs") if isinstance(outcome, dict) else None
    correctness = [r for r in runs or [] if isinstance(r, dict) and not r.get("taste")] if isinstance(runs, list) else []
    if not correctness:
        return None
    last = correctness[-1]
    decision = last.get("decision")
    return decision.get("state") if isinstance(decision, dict) else last.get("state")


def survivors(record: object) -> dict:
    """Each function's survivors, by Detective's own class, as its last correctness run left them (pure):
    ``{<qualname>: {"standing", "killed", "residual", <class>: <count>, ...}}``, its last typed state's own fields
    (``SURVIVOR_CLASSES``); a function with no typed state is ``{"standing": "unread"}``. Nothing is flagged and
    nothing probed beyond Detective's own blocks (the founder, 2026-10-07, separation [8] and [11]): the module is
    admitted modulo these, as Detective's COMPLETE reads them."""
    detective = record.get("detective") if isinstance(record, dict) else None
    functions = detective.get("functions") if isinstance(detective, dict) else None
    out: dict = {}
    for qualname, outcome in (functions or {}).items() if isinstance(functions, dict) else []:
        state = _last_state(outcome)
        if not isinstance(state, dict):
            out[qualname] = {"standing": "unread"}
            continue
        given_standing, given_facts = state.get("standing"), state.get("facts")
        if isinstance(given_standing, str):
            terminal = outcome["runs"][-1]["decision"]
            out[qualname] = {"standing": given_standing, "residual": terminal.get("residual"), **{k: v for k, v in state.items() if k != "standing"}}
            continue
        standing: dict = given_standing if isinstance(given_standing, dict) else {}
        facts: dict = given_facts if isinstance(given_facts, dict) else {}
        classes = {name: facts[name] for name in SURVIVOR_CLASSES if name in facts}
        out[qualname] = {"standing": standing.get("code", "unread"), "killed": standing.get("killed"), "residual": facts.get("residual"), **classes}
    return out


def pinned_file(record: object, qualname: str) -> str:
    """The test file Detective wrote its pins for one function into, as its last correctness run names it (pure), or
    ``""`` when it wrote none."""
    detective = record.get("detective") if isinstance(record, dict) else None
    functions = detective.get("functions") if isinstance(detective, dict) else None
    state = _last_state(functions.get(qualname)) if isinstance(functions, dict) else None
    facts = state.get("facts") if isinstance(state, dict) else None
    wrote = facts.get("wrote") if isinstance(facts, dict) else None
    path = wrote.get("path") if isinstance(wrote, dict) else None
    return path if isinstance(path, str) else ""


# ── The records Detective pinned (pure) ──────────────────────────────────────────────────────────────────────────


def _is_name(node: object, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _equals(node: object) -> tuple:
    """``assert <left> == <right>``: ``(left, right)``, or ``(None, None)``."""
    if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare) and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq):
        return node.test.left, node.test.comparators[0]
    return None, None


def _raised(node: ast.Try) -> dict | None:
    """Detective's raise pin (``_raises_witness_property``): the call in a ``try``, its error caught by type, its
    message asserted or passed, any other error and a return each failing. ``{"call", "raises", "message"}`` or
    ``None``."""
    if len(node.body) != 1 or len(node.handlers) != 2 or not node.orelse or node.finalbody:
        return None
    assign, caught, other = node.body[0], node.handlers[0], node.handlers[1]
    if not (isinstance(assign, ast.Assign) and len(assign.targets) == 1 and _is_name(assign.targets[0], "_result") and isinstance(assign.value, ast.Call)):
        return None
    if not (isinstance(caught.type, ast.Name) and caught.name == "_exc" and _is_name(other.type, "BaseException")):
        return None
    if len(caught.body) == 1 and isinstance(caught.body[0], ast.Pass):
        return {"call": ast.unparse(assign.value), "raises": caught.type.id, "message": None}
    left, right = _equals(caught.body[0]) if len(caught.body) == 1 else (None, None)
    said = isinstance(left, ast.Call) and _is_name(left.func, "str") and len(left.args) == 1 and _is_name(left.args[0], "_exc")
    if said and isinstance(right, ast.Constant) and isinstance(right.value, str):
        return {"call": ast.unparse(assign.value), "raises": caught.type.id, "message": right.value}
    return None


def _pinned(fn: ast.FunctionDef) -> list:
    """One of Detective's tests as its records: a value pin, a raise pin, or a golden table of calls."""
    body = fn.body[1:] if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else fn.body
    table = next((d for d in fn.decorator_list if isinstance(d, ast.Call) and ast.unparse(d.func) == "pytest.mark.parametrize"), None)
    if table is not None:
        left, right = _equals(body[0]) if len(body) == 1 else (None, None)
        rows = table.args[1] if len(table.args) == 2 and isinstance(table.args[1], ast.List | ast.Tuple) else None
        star = isinstance(left, ast.Call) and len(left.args) == 1 and isinstance(left.args[0], ast.Starred) and not left.keywords
        if not (star and isinstance(left, ast.Call) and _is_name(right, "expected") and rows is not None and ast.literal_eval(table.args[0]) == "args, expected"):
            return [{"test": fn.name, "unread": "form"}]
        out = []
        for i, row in enumerate(rows.elts):
            if not (isinstance(row, ast.Tuple) and len(row.elts) == 2 and isinstance(row.elts[0], ast.Tuple)):
                return [{"test": fn.name, "unread": "form"}]
            call = ast.Call(func=left.func, args=list(row.elts[0].elts), keywords=[])
            out.append({"test": f"{fn.name}[{i}]", "call": ast.unparse(call), "outcome": {"returns": ast.unparse(row.elts[1])}})
        return out
    if len(body) == 2 and isinstance(body[0], ast.Assign) and len(body[0].targets) == 1 and _is_name(body[0].targets[0], "result") and isinstance(body[0].value, ast.Call):
        left, right = _equals(body[1])
        if _is_name(left, "result") and right is not None:
            return [{"test": fn.name, "call": ast.unparse(body[0].value), "outcome": {"returns": ast.unparse(right)}}]
    if len(body) == 1 and isinstance(body[0], ast.Try):
        raised = _raised(body[0])
        if raised is not None:
            return [{"test": fn.name, "call": raised["call"], "outcome": {"raises": raised["raises"], "message": raised["message"]}}]
    return [{"test": fn.name, "unread": "form"}]


def pins(source: str) -> list:
    """The records Detective pinned in one of its test files (pure), each ``{"test", "call", "outcome"}``: the call as
    written (``clamp(value=1, low=2, high=3)``) and its outcome, ``{"returns": <the literal>}`` or ``{"raises": <type>,
    "message": <the text, or None>}``; a golden table gives one record per row (``<test>[<i>]``). A test in any other
    form is ``{"test", "unread": "form"}``, never guessed; a file that does not parse is ``[{"test": "", "unread":
    "syntax"}]``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [{"test": "", "unread": "syntax"}]
    out: list = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            out.extend(_pinned(node))
    return out


# ── The intent check (pure parts) ────────────────────────────────────────────────────────────────────────────────


def intent_names(source: object) -> list:
    """The intent tests a function's contract states, by name, in order (pure); ``[]`` when its source does not
    parse."""
    try:
        tree = ast.parse(source if isinstance(source, str) else "")
    except SyntaxError:
        return []
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]


def check_form(count: int, tests: list, covers: int) -> dict:
    """The form the intent check's reply is held to for one function (pure): one property per readable record,
    ``r<i>``, each one of ``agrees``, ``cannot_tell``, or ``contradicts:<intent test>`` / ``contradicts:covers[<j>]``
    for each intent test and coverage line the function's contract states; every record answered, nothing else."""
    verdicts = [*VERDICTS, *(f"contradicts:{t}" for t in tests), *(f"contradicts:covers[{j}]" for j in range(max(0, covers)))]
    names = [f"r{i}" for i in range(max(0, count))]
    return {"type": "object", "properties": {n: {"enum": verdicts} for n in names}, "required": names, "additionalProperties": False}


def held_back(qualname: str, records: list, verdicts: dict) -> list:
    """The hand-backs one function's intent check leaves (pure), routed as every hand-back is
    (``decisions.handback_receivers``): each record the check found contradicting its intent
    (``admission:contradicts``, with the intent test or coverage line it named), each it could not tell, and each
    Detective pinned in a form admission does not read (``admission:cannot_tell``). ``verdicts`` maps each readable
    record's ``r<i>`` (counted over the readable records, in order) to its verdict."""
    out: list = []
    readable = 0
    for record in records:
        if "unread" in record:
            verdict = "cannot_tell"
        else:
            verdict = verdicts.get(f"r{readable}", "cannot_tell")
            readable += 1
        if verdict == "agrees":
            continue
        ending = "admission:contradicts" if verdict.startswith("contradicts:") else "admission:cannot_tell"
        evidence = {"pin": record, **({"by": verdict.partition(":")[2]} if ending == "admission:contradicts" else {})}
        out.append({"function": qualname, "ending": ending, "evidence": evidence, "receivers": decisions.handback_receivers(ending), "decisions": list(decisions.RECEIVER_DECISIONS)})
    return out


def _check_evidence(node: dict, qualname: str, records: list) -> dict:
    """What the intent check reads for one function (pure): its docstring, its intent tests and their coverage lines,
    and the readable records Detective pinned, each numbered as the reply answers it."""
    given = node.get("intent_test")
    test: dict = given if isinstance(given, dict) else {}
    covers = [c for c in test.get("covers") or [] if isinstance(c, dict)]
    readable = [r for r in records if "unread" not in r]
    return {
        "qualname": qualname,
        "doc": json.dumps(node.get("doc"), ensure_ascii=False),
        "tests": str(test.get("source") or ""),
        "covers": "\n".join(f"- covers[{j}]: {json.dumps(c, ensure_ascii=False)}" for j, c in enumerate(covers)) or "none",
        "records": "\n".join(f"- r{i}: {r['call']} -> {json.dumps(r['outcome'], ensure_ascii=False)}" for i, r in enumerate(readable)),
    }


# ── The intent check, placement and the replay (each acts) ───────────────────────────────────────────────────────


def intent_check(node: dict, qualname: str, records: list, model_key: str = contract.DEFAULT_MODEL, chat: Callable | None = None) -> tuple:
    """One call, the ``intent-check`` cassette, held to ``check_form`` at decode time: each record Detective pinned for
    the function, checked against its intent tests and docstring (the founder, 2026-10-07, separation [8]). Returns
    ``(code, verdicts, outcome)``: ``checked`` with ``{r<i>: verdict}``, ``nothing_to_check`` when no record is
    readable, ``unknown_model``, or the call's own ``call_<status>:<reason>``."""
    readable = [r for r in records if "unread" not in r]
    if not readable:
        return "nothing_to_check", {}, None
    model = contract.MODELS.get(model_key)
    if model is None:
        return "unknown_model", {}, None
    given_test = node.get("intent_test")
    test: dict = given_test if isinstance(given_test, dict) else {}
    form = check_form(len(readable), intent_names(test.get("source")), len([c for c in test.get("covers") or [] if isinstance(c, dict)]))
    held = {**model, "timeout_s": model.get("timeout_s") or contract._DEFAULT_TIMEOUT_S}
    cassette = daemon._held(find_cassette("intent-check"), held, system="")
    out = invoke(cassette, _check_evidence(node, qualname, records), {"verdicts": form}, None, chat=chat)
    given = out.values.get("verdicts")
    if out.status != "admitted" or not isinstance(given, dict):
        return f"call_{out.status}:{out.reason}", {}, out
    return "checked", given, out


def _gate(target: Path) -> list:
    """The target repository's own gate: its ``scripts/ci_local.sh``, else its test suite under its own interpreter."""
    if (target / "scripts" / "ci_local.sh").exists():
        return ["bash", "scripts/ci_local.sh"]
    python = target / ".venv" / "bin" / "python"
    return [str(python) if python.exists() else sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def placed_files(unit: Path, source: dict, pinned: list) -> list:
    """What placement copies (pure over the unit's folder): ``[[from, to], ...]``, each ``to`` relative to the target:
    the module at its contract path, its intent tests, and each test file Detective pinned for it."""
    path = PurePosixPath(source["path"])
    tests = f"tests/test_{path.stem}.py"
    out = [[str(unit / str(path)), str(path)], [str(unit / tests), tests]]
    return out + [[str(unit / p), p] for p in pinned if p]


def place(unit: Path, source: dict, pinned: list, target: Path, message: str, run: Callable | None = None, *, labels: dict | None = None) -> tuple:
    """Place one measured unit on clean main and restore the paths if its gate, label or commit fails."""
    sh = run or subprocess.run

    def git(*args: str) -> subprocess.CompletedProcess:
        return sh(["git", "-C", str(target), *args], capture_output=True, text=True, check=False)

    top = git("rev-parse", "--show-toplevel")
    if top.returncode or Path(top.stdout.strip()).resolve() != target.resolve():
        return "target:not_a_repository", ""
    git_dir = git("rev-parse", "--absolute-git-dir")
    if git_dir.returncode:
        return "target:not_a_repository", ""
    lock = Path(git_dir.stdout.strip()) / "uroboros-admission.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        return "target:busy", ""
    except OSError as exc:
        return "target:lock", str(exc)
    try:
        return _place_locked(unit, source, pinned, target, message, sh, git, labels)
    finally:
        lock.rmdir()


def _place_locked(unit, source, pinned, target, message, sh, git, labels):
    status = git("status", "--porcelain")
    if status.returncode or status.stdout.strip():
        return "target:dirty", ""
    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":
        return "target:not_main", ""
    module = source["path"]
    ledger = "tests/detective/certificates.json"
    subject = f"admit {module}:"
    revision_base = git("log", "-1", "--fixed-strings", f"--grep={subject}", "--format=%H").stdout.strip()
    previous = git("show", "-s", "--format=%B", revision_base).stdout.splitlines() if revision_base else []
    revises = source.get("revises")
    revision = bool(revises and previous[:2] == [subject, f"contract {revises}"])
    files = placed_files(unit, source, pinned)
    if labels is not None and (unit / f"tests/test_{PurePosixPath(module).stem}_asked.py").is_file():
        asked = f"tests/test_{PurePosixPath(module).stem}_asked.py"
        files.append([str(unit / asked), asked])
    owned = set()
    if revision:
        manifests = [line.removeprefix("files ") for line in previous if line.startswith("files ")]
        if manifests:
            try:
                manifest = json.loads(manifests[-1])
            except ValueError:
                return "target:invalid_manifest", ""
            if not isinstance(manifest, list) or not all(isinstance(name, str) for name in manifest):
                return "target:invalid_manifest", ""
            owned = set(manifest)
        else:
            owned = set(git("show", "--format=", "--name-only", revision_base).stdout.splitlines())
    current = {to for _, to in files}
    stale = sorted(name for name in owned - current if name.startswith("tests/") and name.endswith(".py") and (target / name).is_file())
    carry_ledger = labels is not None and (bool(labels) or (target / ledger).exists())
    paths = [to for _, to in files] + stale + ([ledger] if carry_ledger else [])
    if len(set(paths)) != len(paths):
        return "unit:duplicate_path", ""
    for origin, to in files:
        relative = PurePosixPath(to)
        if relative.is_absolute() or ".." in relative.parts or (not Path(origin).resolve().is_relative_to(unit.resolve())):
            return f"unit:invalid_path:{to}", ""
        if not Path(origin).is_file():
            return f"unit:missing:{origin}", ""
    for to in paths:
        where = target / to
        relative = PurePosixPath(to)
        through_link = where.is_symlink() or any((target / parent).is_symlink() for parent in relative.parents if parent != PurePosixPath("."))
        if relative.is_absolute() or ".." in relative.parts or not where.resolve().is_relative_to(target.resolve()) or through_link:
            return f"target:path:{to}", ""
        if where.exists() and to != ledger and (to not in owned or git("diff", "--quiet", revision_base, "--", to).returncode):
            return f"target:exists:{to}", ""

    created = [to for to in paths if not (target / to).exists()]
    dirs = {str(parent) for to in created for parent in PurePosixPath(to).parents if str(parent) != "." and not (target / parent).exists()}
    deepest_first = sorted(dirs, key=lambda name: (name.count("/"), name), reverse=True)

    def undo(code, detail=""):
        result = undo_placement(str(target), paths, created, deepest_first, sh)
        return (code if result == "undone" else result), {"undo": result, "detail": detail}

    try:
        for origin, to in files:
            where = target / to
            where.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, where)
        for to in stale:
            (target / to).unlink()
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        gate = sh(_gate(target), cwd=str(target), env=env, capture_output=True, text=True, check=False)
        (unit / "admission_gate.log").write_text(f"{gate.stdout}\n--- stderr ---\n{gate.stderr}")
        if gate.returncode:
            return undo(f"gate:{gate.returncode}")
        if carry_ledger:
            code = detective.place_certificates(target, module, labels, detective.engine())
            if code != "placed":
                return undo(code)
        staged = git("add", "-A", "--", *paths)
        if staged.returncode:
            return undo(f"stage:{staged.returncode}", staged.stderr)
        if labels is not None:
            message = f"{message}\nfiles {json.dumps(sorted(current))}"
        done = git("commit", "-q", "--allow-empty", "-m", message, "--", *paths)
        if done.returncode:
            return undo(f"commit:{done.returncode}", done.stderr)
    except KeyboardInterrupt:
        return undo("interrupted")
    except OSError as exc:
        return undo("placement_io", str(exc))
    return "placed", git("rev-parse", "HEAD").stdout.strip()


# ── Undoing a placement ──────────────────────────────────────────────────────────────────────────────────────────
#
# Committed on main only if the gate passes (separation [8]): when a placement fails at the gate or at the commit, the
# target is put back exactly as it was. Not pure: it runs ``git`` and touches the file system, only within what
# placement wrote.


def _git(run, target, *args):
    """Run `git -C <target> <args>` through run; the result, or None when git could not even be started."""
    try:
        return run(["git", "-C", target, *args], capture_output=True, text=True, check=False)
    except OSError:
        return None


def _git_succeeds(run, target, *args):
    """True only when git ran and its return code is 0."""
    done = _git(run, target, *args)
    return done is not None and done.returncode == 0


def _is_clean(run, target):
    """True only when `git status --porcelain` ran, returned 0 and printed nothing."""
    done = _git(run, target, "status", "--porcelain")
    return done is not None and done.returncode == 0 and done.stdout == ""


def _remove_created_files(target, created_files):
    """Delete each created file that exists; False at the first OSError."""
    for relative in created_files:
        try:
            (Path(target) / relative).unlink(missing_ok=True)
        except OSError:
            return False
    return True


def _remove_empty_folders(target, created_dirs):
    """Remove each created folder that exists and is empty, leaving any other; False at the first OSError."""
    for relative in created_dirs:
        folder = Path(target) / relative
        try:
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            return False
    return True


def undo_placement(target, placed, created_files, created_dirs, run=subprocess.run):
    """Put a target repository back as it was before a placement.

    The baseline is the target's HEAD: placement only ever starts on a clean main, so HEAD is what it was.
    target: the repository's path, a str.
    placed: every path placement wrote, relative to target.
    created_files: those placed paths that did not exist before.
    created_dirs: the folders placement created, relative to target, deepest first.
    run: called as run(argv, capture_output=True, text=True, check=False), like subprocess.run.

    The steps, in order. Each one stops the undo at its own code if it fails:
      1. "reset":    git -C <target> reset -q HEAD -- <every placed path>, putting the index back to HEAD for them;
      2. "checkout": git -C <target> checkout -- <the placed paths that are not in created_files>, skipped when there
                     are none;
      3. "remove":   delete each created file that exists;
      4. "rmdir":    remove each created folder that is empty; a folder that is not empty is left;
      5. "status":   git -C <target> status --porcelain must print nothing.
    A git step fails when its return code is not 0. Steps 3 and 4 fail on an OSError.
    Returns "undone", or "undo_failed:<step>". For step 5 the code is "undo_failed:dirty".
    Nothing outside placed, created_files and created_dirs is ever touched.
    """
    # With nothing placed there is no pathspec to give, and `git reset -q HEAD --` on its own would put the whole
    # index back to HEAD, which is outside what placement wrote. So step 1 is skipped, as step 2 is.
    if placed and not _git_succeeds(run, target, "reset", "-q", "HEAD", "--", *placed):
        return "undo_failed:reset"
    existed_before = [path for path in placed if path not in created_files]
    if existed_before and not _git_succeeds(run, target, "checkout", "--", *existed_before):
        return "undo_failed:checkout"
    if not _remove_created_files(target, created_files):
        return "undo_failed:remove"
    if not _remove_empty_folders(target, created_dirs):
        return "undo_failed:rmdir"
    # Step 5 has the one code "dirty": git status failing and git status printing something both leave the undo
    # unproven.
    if not _is_clean(run, target):
        return "undo_failed:dirty"
    return "undone"


def admit(unit: Path, target: Path | None = None, model_key: str = contract.DEFAULT_MODEL, chat: Callable | None = None, run: Callable | None = None) -> dict:
    """Check or place a unit using the contract, Detective's terminal routes and its recorded labels."""
    record: dict = {"unit": str(unit), "outcome": ""}

    def finish(outcome: str) -> dict:
        record["outcome"] = outcome
        if unit.is_dir():
            (unit / "admission.json").write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str))
        return record

    try:
        played = json.loads((unit / "play.json").read_text())
        source = json.loads((unit / "contract.json").read_text())
    except (OSError, ValueError):
        return finish("unreadable")
    verdict = contract.contract_verdict(source)
    if verdict != "ok" or not isinstance(played, dict):
        return finish(f"invalid_contract:{verdict}" if verdict != "ok" else "unreadable")
    record["grant"] = played.get("grant")
    driven = played.get("detective")
    driven = driven if isinstance(driven, dict) else {}
    verdict = decisions.admission_bar(played.get("outcome"), driven.get("setup"), driven.get("functions"), contract.call_order(source))
    record["bar"] = verdict
    if verdict != "pass":
        return finish(f"unclean:{verdict}")
    record["survivors"] = survivors(played)
    module = PurePosixPath(source["path"])
    for path, rendered in ((module, contract.render_module(source)), (PurePosixPath("tests", f"test_{module.stem}.py"), contract.render_tests(source))):
        try:
            actual = (unit / path).read_text()
        except OSError:
            return finish(f"unit:missing:{path}")
        if rendered != ("ok", actual):
            return finish(f"unit:changed:{path}")
    certs = detective._json_file(unit / "tests/detective/certificates.json")
    labels, finals, pinned = {}, [], []
    archived = None
    for qualname, key in contract.detective_targets(source):
        cert = certs.get(key)
        if not isinstance(cert, dict) or cert.get("standing") != "complete" or not cert.get("function_digest"):
            return finish(f"label_missing:{key}")
        runs = driven["functions"][qualname].get("runs") or []
        decision = (runs[-1].get("decision") or {}) if runs else {}
        final = decision.get("final")
        if not isinstance(final, str) or not final.startswith(f"FINAL {key}:"):
            return finish(f"final_missing:{key}")
        labels[key] = cert
        finals.append(f"{key}: {final}")
        run_record = runs[-1]
        if "written_path" not in run_record:
            if archived is None:
                archived = detective.archived_converge(unit)
            run_record = archived.get(key, run_record)
        if run_record.get("function_digest") and run_record["function_digest"] != cert["function_digest"]:
            return finish(f"label_stale:{key}")
        path = run_record.get("written_path") or pinned_file(played, qualname)
        if "written_path" not in run_record and not path:
            return finish(f"pin_evidence_missing:{key}")
        if path:
            origin = (unit / path).resolve()
            if not origin.is_relative_to((unit / "tests/detective").resolve()):
                return finish(f"unit:invalid_pin:{key}")
            if not origin.is_file():
                return finish(f"unit:missing_pin:{key}")
            pinned.append(str(origin.relative_to(unit.resolve())))
    record["contract_digest"] = contract.contract_digest(source)
    if target is None:
        return finish("checked")
    message = "\n".join([f"admit {module}:", f"contract {record['contract_digest']}", *finals, "measured in the unit"])
    code, detail = place(unit, source, pinned, target, message, run, labels=labels)
    record["placement"] = {"code": code, "detail": detail}
    return finish("admitted" if code == "placed" else code)


def replay(unit: Path, out: Path, model_key: str = contract.DEFAULT_MODEL, chat: Callable | None = None, runner: Callable | None = None) -> dict:
    """The receiver's ``machine_wrong`` (the founder, 2026-10-07, separation [8]): the unit played again once into
    ``out``, nothing on the machine changed, from its contract as it was written (every cell filled, so no write call
    is made and Detective runs again). A unit that is itself a replay is not played again: its record's outcome is
    ``second_machine_wrong``, for the founder."""
    try:
        played = json.loads((unit / "play.json").read_text())
    except (OSError, ValueError):
        played = None
    if isinstance(played, dict) and played.get("replay_of"):
        out.mkdir(parents=True, exist_ok=True)
        record = {"replay_of": str(unit), "outcome": "second_machine_wrong", "receivers": ["founder"]}
        (out / "play.json").write_text(json.dumps(record, indent=1))
        return record
    grant = played.get("grant") if isinstance(played, dict) else None
    grant = grant if isinstance(grant, dict) else {}
    record = contract.play(unit / "contract.json", out, model_key, chat=chat, runner=runner, contract_writer=grant.get("contract_writer"), admission=grant.get("admission"))
    record["replay_of"] = str(unit)
    (out / "play.json").write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str))
    return record


def main(argv: list | None = None) -> int:
    """``admit UNIT [--target REPO]`` (the clean check, the survivors, the intent check, and placement when a target is
    given; ``UNIT/admission.json``) · ``replay UNIT --out DIR`` (``machine_wrong``: played again once). Exit codes: 0
    admitted, checked or replayed · 1 held back, unclean, or a second machine_wrong · 2 anything else."""
    parser = argparse.ArgumentParser(prog="uroboros-admission", description="Admit a played unit's code.")
    parser.add_argument("--model", default=contract.DEFAULT_MODEL, choices=sorted(contract.MODELS))
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("admit")
    one.add_argument("unit")
    one.add_argument("--target", default=None, help="the repository the module is placed into")
    again = commands.add_parser("replay")
    again.add_argument("unit")
    again.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "replay":
        result = replay(Path(args.unit), Path(args.out), args.model)
        print(result["outcome"])
        return 1 if result["outcome"] == "second_machine_wrong" else result.get("exit_code", 2)
    result = admit(Path(args.unit), Path(args.target) if args.target else None, args.model)
    print(result["outcome"])
    if result["outcome"] in ("admitted", "checked"):
        return 0
    return 1 if result["outcome"] == "held_back" or result["outcome"].startswith("unclean:") else 2


if __name__ == "__main__":
    raise SystemExit(main())
