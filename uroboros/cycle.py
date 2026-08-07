#!/usr/bin/env python3
"""The set-and-forget refactor crawler — the integrated harness.

Walks a Python file/dir function by function and, per function, drives Detective's
deterministic loop, invoking a SMALL model only where it is genuinely needed, and
routing everything else to a human review queue. The model never drives — the
script owns control flow; the model is a pure `(signature, requirement) -> input`
function behind the loop, so it cannot overthink its way into the "agency is the
bug" failure. Point it at a diff before a push, get coffee, come back to clean
code + a short list of decisions to clear.

Per function:
  regime (once per repo) -> diagnose -> route on DO THIS:
    DONE                          -> already pinned, next
    seam (--apply)                -> decompose --apply (proven-safe), then converge
    converge                      -> write the minimal pinning suite
  then close the residual with the model, GUARDED:
    reads the clock/fs/os (environment_gated) -> STOP: the pin is FRAGILE or the gap is
                                                 unreachable by --input. Route to the fixture
                                                 queue with the --clock remedy. NEVER spin.
    pure, line gap                            -> synthesize --input (qwen), with a NO-PROGRESS
                                                 guard so one un-closeable gap can't loop
    candidate-equivalent survivors            -> "done modulo undecidable" (informational; the
                                                 Tier-2 flag-triage queue, not a per-fn action)

Buckets returned for the human: PINNED (done/refactored), NEEDS-FIXTURE (impure — the
professionalization case: a time/state-dependent test is fragile; supply --clock or a
fixture), UNCLOSED (a real pure gap the model couldn't reach). Everything else is pinned.

Reuses crawl.py's deterministic helpers and orchestrator.synthesize_inputs (the one model
call, typed-skeleton, 100% schema-adherence). Model default: qwen3:4b-instruct (the bake-off
winner on internal-condition residuals — 4x the 36B's kills/token).
"""
import argparse
import sys
from pathlib import Path

from .drive import (
    _git_dirty,
    _lint_count,
    _lint_regressed,
    _new_tracked_writes,
    changed_targets,
    det,
    enumerate_targets,
    verify_engine,
)
from .nextstep import derive_next_step
from .preflight import check as preflight_check
from .preflight import report as preflight_report
from .synth import _function_source, synthesize_inputs

MODEL = "qwen3:4b-instruct-2507-q4_K_M"
MODEL_PASSES = 3     # max synthesize->converge rounds before we call a gap unclosed
CALL_WALL = 90       # seconds per model call — one slow generation can't stall the crawl


def _regime_target(path: str) -> str:
    """The target to hand `detective regime`, given a crawl path.

    Pass the FULL `file.py::func` through — regime accepts a target and adds
    target-specific facts. A BARE `file.py` is refused ("target must be
    file.py::function"), which once broke the documented single-function form.
    A dir/whole-file path (no `::`) resolves the whole repo with no target.
    """
    return path if "::" in path else ""


def _ollama_up() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2).read()
        return True
    except Exception:
        return False


def _bounded_synth(state, source, func, model, focus_items):
    """synthesize_inputs with a hard wall so one slow generation can't stall the crawl."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FT
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return ex.submit(synthesize_inputs, state, source, func, model, focus_items).result(timeout=CALL_WALL)
        except FT:
            return [], {}


def process_function(target, root, model, apply_decompose, use_model):
    """Drive one function to a pinned suite or a labelled residual. Returns a result dict.

    The model step FOLLOWS Detective's own typed next-step (`nextstep.derive_next_step`,
    re-derived from the converge JSON), routing each by the DIFFICULTY TIER of the input it
    asks for — the ladder the model bake-off was built around:

      1. purely script-killable  → `witness`  — the engine already RAN the input; paste it, no model
      2. any-dolt / core do-next  → `lines`    — reach the branch; the small model's staple
      3. complex-but-obvious      → `boundary` — author a call landing on a PROVED edge; the tier the
                                                 bake-off qualified the model (qwen3:4b) for
      4. genuinely purposivistic  → `internal` / `test` / `author` — needs human intent; ABSTAIN and
                                                 promote (never spin: derived local, captured object,
                                                 or a value only the caller knows)

    Tiers 1-3 the machine closes; tier 4 goes to the human (`needs-input`, or left `unclosed`).
    """
    file, func = target.split("::")
    r = {"target": target, "state": "", "decomposed": False, "killed": 0, "total": 0,
         "model_calls": 0, "candidate_equiv": 0, "impure": (), "unclosed": [], "needs_input": None}

    diag, err = det("diagnose", target, root, stream=True)
    if err:
        r["state"] = f"error:diagnose:{err}"
        return r
    if diag.get("decompose_seams") and apply_decompose:
        fpath = root / file
        before_src = fpath.read_text() if fpath.exists() else None
        lint_before = _lint_count(file, root)
        _, derr = det("decompose", target, root, "--apply", stream=True)
        applied = derr is None
        # Post-apply verify: Detective PROVED the split behaviour-preserving before writing it,
        # but proven is not clean — a dropped annotation can red the repo's own lint gate. Re-lint
        # and REVERT on regression, so a hands-off crawl never leaves the tree failing its own CI.
        if applied and before_src is not None and _lint_regressed(lint_before, _lint_count(file, root)):
            fpath.write_text(before_src)
            r["decomposed"] = "reverted"
            print("  ↩ decomposition reverted: behaviour-preserving but reds the repo's lint gate "
                  "— proposed, not applied")
        else:
            r["decomposed"] = applied
    elif diag.get("decompose_seams"):
        r["decomposed"] = "available"

    conv, err = det("converge", target, root, stream=True)
    if err:
        r["state"] = f"error:converge:{err}"
        return r

    def absorb(c):
        r["killed"], r["total"] = c.get("killed", 0) or 0, c.get("total_mutants", 0) or 0
        v = (c.get("survivor_report") or {}).get("verdicts", [])
        r["candidate_equiv"] = sum(1 for x in v if not x.get("killable") and not x.get("crash_only"))

    def impure(c):
        # A function that reads the clock/fs/os is a FIXTURE case whether or not converge "pinned"
        # it: a golden pinned to `int(time.time())` is green now and red next second. Route the
        # whole function to review — never author a fragile pin, never spin --input on it.
        g = tuple(c.get("environment_gated") or ())
        if g:
            r["impure"], r["state"] = g, "needs-fixture"
        return bool(g)

    absorb(conv)
    if impure(conv):
        return r

    # Follow Detective's DO THIS, kind by kind (the tier ladder above), producing the --input it
    # asks for. Guarded three ways: a per-call wall (_bounded_synth), a bounded pass count
    # (MODEL_PASSES), and a NO-PROGRESS stop — the derived next-step must CHANGE, or we are spinning.
    if use_model:
        inputs, source, prev = [], _function_source(file, func, root), None
        for _ in range(MODEL_PASSES):
            if conv.get("functionally_complete") and conv.get("line_complete"):
                break
            ns = derive_next_step(conv)
            kind, items = ns["kind"], ns["items"]
            if ns == prev:                                   # DO THIS unchanged -> no progress
                break
            prev = ns
            if kind == "witness":                            # tier 1 — engine found the kill; paste
                new = [i for i in items if i not in inputs]
            elif kind in ("lines", "boundary"):              # tiers 2-3 — the one model call
                fresh, _t = _bounded_synth(conv, source, func, model, items)
                r["model_calls"] += 1
                new = [i for i in fresh if i not in inputs]
            elif kind in ("test", "author"):                 # tier 4 — needs a human value/object
                absorb(conv)
                r["needs_input"], r["state"] = ns, "needs-input"
                return r
            else:                                            # tier 4 — `internal`: derived local, never spin
                break
            if not new:
                break
            inputs += new
            conv, err = det("converge", target, root, *sum((["--input", i] for i in inputs), []), stream=True)
            if err:
                break
            if impure(conv):                                 # impurity surfaced only mid-loop
                absorb(conv)
                return r
        absorb(conv)

    if conv.get("functionally_complete") and conv.get("line_complete"):
        r["state"] = "pinned"
    elif conv.get("missing_lines"):
        r["state"], r["unclosed"] = "unclosed", list(conv.get("missing_lines"))
    else:
        r["state"] = "pinned"    # complete modulo candidate-equivalent — done, not a gap
    return r


def main():
    ap = argparse.ArgumentParser(description="Set-and-forget Detective refactor crawler.")
    ap.add_argument("path", nargs="?", help="a .py file, a dir, or file.py::func")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--apply", action="store_true", help="APPLY proven-safe decompositions (rewrites source)")
    ap.add_argument("--no-model", action="store_true", help="deterministic only — skip the model residual step")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--check", action="store_true", help="verify deps (Detective/Wesker, Ollama, model) and exit")
    ap.add_argument("--diff", nargs="?", const="HEAD", default=None, metavar="BASE",
                    help="crawl ONLY functions changed since BASE (default HEAD) — churn before a push")
    a = ap.parse_args()

    # Line-buffer so the crawl STREAMS through a pipe (a launcher, Goose, tee) instead of
    # going silent until the buffer fills — the per-function rows and Detective's inherited
    # stderr heartbeat both reach the reader live.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    if a.check:
        return 0 if preflight_report(a.model) else 1
    if not a.path and not a.diff:
        ap.error("a path is required (or use --diff / --check)")
    if not preflight_check(a.model)["detective/wesker"][0]:
        print("✗ Detective not found — run `uroboros --check` for setup help.", file=sys.stderr)
        return 1
    root = Path(a.project_root).resolve()

    # Setup pass. Detective's regime resolves how the repo imports its code and runs its tests, and
    # --migrate DECLARES the `detective` pytest marker + pythonpath in pyproject so the existing suite
    # is discovered and the code is importable. Without it the baseline is empty and the crawl does
    # nothing. --migrate only ever writes Detective's own declarative config — never your code — and is
    # idempotent. A genuine conflict it cannot resolve still refuses: a number measured against the
    # wrong file is worse than no number.
    print("· setting up the testing regime (detective regime --migrate) …")
    _, rerr = det("regime", "" if a.diff else _regime_target(a.path), root, "--migrate", stream=True)
    if rerr and rerr != "no-json":
        print(f"regime refused: {rerr}\n  resolve the conflict (see `detective regime --project-root "
              f"{root}`), then re-run.", file=sys.stderr)
        return 1

    use_model = not a.no_model and _ollama_up()
    if not a.no_model and not use_model:
        print("⚠ Ollama not reachable — deterministic pass only (pure residuals left unclosed).\n")

    if a.diff:
        targets = list(changed_targets(root, a.diff))
        if not targets:
            print(f"no changed functions since {a.diff} — nothing to crawl.")
            return 0
    elif "::" in a.path:
        targets = [a.path]
    else:
        targets = list(enumerate_targets(Path(a.path).resolve(), root))
    # Verify the resolved engine can import the code before crawling: a repo's own .venv can be
    # stale/incomplete (deps behind its lock, or a bare venv beside a conda-run repo), and then the
    # suite silently fails to collect and every function reads as unpinned. Fall back to the global
    # engine loudly instead. A sample target file exercises the whole dependency chain.
    if targets:
        verify_engine(root, root / targets[0].split("::")[0])
    print(f"crawl: {len(targets)} function(s) · model={'off' if not use_model else a.model.split(':')[0]} "
          f"· decompose={'apply' if a.apply else 'report'}{f' · diff={a.diff}' if a.diff else ''}\n")
    print(f"{'function':<32} {'state':<16} {'kill':>7} {'model':>5} {'c-eq':>5} {'seam':>6}")
    print("-" * 78)

    # Baseline for the crawl-integrity guard: snapshot AFTER regime (so its pyproject edit is not
    # flagged), so anything new outside tests/ afterward is a side effect of running the suite.
    dirty_before = _git_dirty(root)
    rows = []
    for i, t in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {t.split('::')[-1]}")   # heartbeat: where it is, that it's alive
        r = process_function(t, root, a.model, a.apply, use_model)
        rows.append(r)
        short = r["target"].split("::")[-1][:30]
        seam = {True: "yes", "available": "avail", "reverted": "rvrt"}.get(r["decomposed"], "-")
        kill = f"{r['killed']}/{r['total']}" if r["total"] else "-"
        print(f"{short:<32} {r['state']:<16} {kill:>7} {r['model_calls']:>5} {r['candidate_equiv']:>5} {seam:>6}")

    fixture = [r for r in rows if r["state"] == "needs-fixture"]
    unclosed = [r for r in rows if r["state"] == "unclosed"]
    errored = [r for r in rows if r["state"].startswith("error")]
    needs_input = [r for r in rows if r["state"] == "needs-input"]
    pinned = [r for r in rows if r["state"] == "pinned"]
    ceq = sum(r["candidate_equiv"] for r in rows)

    print("-" * 78)
    print(f"\n✓ pinned / refactored:        {len(pinned)}")
    print(f"⚠ needs a FIXTURE (impure):   {len(fixture)}")
    for r in fixture:
        reads = "; ".join(r["impure"][:2])
        hint = " — try --clock <epoch>" if any("clock" in x for x in r["impure"]) else ""
        print(f"     {r['target'].split('::')[-1]:<24} {reads}{hint}")
    print(f"⚠ needs INPUT (you supply):   {len(needs_input)}")
    for r in needs_input:
        ns = r["needs_input"] or {}
        ask = "write a test that calls it with" if ns.get("kind") == "test" else "author a call —"
        print(f"     {r['target'].split('::')[-1]:<24} {ask} {'; '.join(ns.get('items', [])[:2])}")
    print(f"· unclosed pure gap:          {len(unclosed)}   {[r['target'].split('::')[-1] for r in unclosed]}")
    print(f"· candidate-equivalent (Tier-2 flag queue): {ceq} mutant(s) across {sum(1 for r in rows if r['candidate_equiv'])} fn")
    if errored:
        print(f"✗ errored:                    {len(errored)}   {[r['target'].split('::')[-1] for r in errored]}")

    # Crawl-integrity guard: the repo's own suite may write to the tree (a test that rewrites a
    # data/ fixture, drops files under the root). Surface those so they aren't mistaken for the
    # crawl's intended output — a crawl should never silently mutate the tree it was pointed at.
    intruders = _new_tracked_writes(dirty_before, _git_dirty(root))
    if intruders:
        print(f"\n⚠ the crawl mutated {len(intruders)} path(s) OUTSIDE tests/ — the repo's own suite "
              f"wrote to the tree:")
        for p in intruders[:8]:
            print(f"     {p}")
        if len(intruders) > 8:
            print(f"     … and {len(intruders) - 8} more")
        print("  review/revert these before trusting the diff (an impure test, not a Uroboros edit).")


if __name__ == "__main__":
    main()
