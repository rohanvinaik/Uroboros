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

from .drive import det, enumerate_targets
from .preflight import check as preflight_check, report as preflight_report
from .synth import _function_source, synthesize_inputs

MODEL = "qwen3:4b-instruct-2507-q4_K_M"
MODEL_PASSES = 3     # max synthesize->converge rounds before we call a gap unclosed
CALL_WALL = 90       # seconds per model call — one slow generation can't stall the crawl


def _ollama_up() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2).read()
        return True
    except Exception:
        return False


def _bounded_synth(state, source, func, model):
    """synthesize_inputs with a hard wall so one slow generation can't stall the crawl."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FT
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return ex.submit(synthesize_inputs, state, source, func, model).result(timeout=CALL_WALL)
        except FT:
            return [], {}


def process_function(target, root, model, apply_decompose, use_model):
    """Drive one function to a pinned suite or a labelled residual. Returns a result dict."""
    file, func = target.split("::")
    r = {"target": target, "state": "", "decomposed": False, "killed": 0, "total": 0,
         "model_calls": 0, "candidate_equiv": 0, "impure": (), "unclosed": []}

    diag, err = det("diagnose", target, root)
    if err:
        r["state"] = f"error:diagnose:{err}"
        return r
    if diag.get("decompose_seams") and apply_decompose:
        _, derr = det("decompose", target, root, "--apply")
        r["decomposed"] = derr is None
    elif diag.get("decompose_seams"):
        r["decomposed"] = "available"

    conv, err = det("converge", target, root)
    if err:
        r["state"] = f"error:converge:{err}"
        return r

    def absorb(c):
        r["killed"], r["total"] = c.get("killed", 0) or 0, c.get("total_mutants", 0) or 0
        v = (c.get("survivor_report") or {}).get("verdicts", [])
        r["candidate_equiv"] = sum(1 for x in v if not x.get("killable") and not x.get("crash_only"))

    absorb(conv)
    gated = tuple(conv.get("environment_gated") or ())
    # A function that reads the clock/fs/os is a FIXTURE case whether or not converge "pinned"
    # it: a golden pinned to `int(time.time())` is green now and red next second. Route the
    # whole function to review — never author a fragile pin, never spin --input on an
    # unreachable gap. (Detective 0.10.2 gives us environment_gated to detect this statically.)
    if gated:
        r["impure"], r["state"] = gated, "needs-fixture"
        return r

    complete = conv.get("functionally_complete") and conv.get("line_complete")
    missing = list(conv.get("missing_lines") or [])
    if not complete and missing and use_model:
        inputs, source = [], _function_source(file, func, root)
        for _ in range(MODEL_PASSES):
            if not missing:
                break
            fresh, _tele = _bounded_synth(conv, source, func, model)
            r["model_calls"] += 1
            new = [i for i in fresh if i not in inputs]
            if not new:                                    # model offered nothing new -> stop
                break
            inputs += new
            conv, err = det("converge", target, root, *sum((["--input", i] for i in inputs), []))
            if err:
                break
            if tuple(conv.get("environment_gated") or ()):  # impurity surfaced only mid-loop
                r["impure"], r["state"] = tuple(conv["environment_gated"]), "needs-fixture"
                absorb(conv)
                return r
            before, missing = len(missing), list(conv.get("missing_lines") or [])
            if len(missing) >= before:                      # NO-PROGRESS GUARD — the trap
                break
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
    a = ap.parse_args()

    if a.check:
        return 0 if preflight_report(a.model) else 1
    if not a.path:
        ap.error("a path is required (or use --check)")
    if not preflight_check(a.model)["detective/wesker"][0]:
        print("✗ Detective not found — run `uroboros --check` for setup help.", file=sys.stderr)
        return 1
    root = Path(a.project_root).resolve()

    _, rerr = det("regime", a.path.split("::")[0] if "::" in a.path else "", root)
    if rerr and rerr != "no-json":
        print(f"regime refused: {rerr}\n  run `detective regime --migrate --project-root {root}`", file=sys.stderr)
        return 1

    use_model = not a.no_model and _ollama_up()
    if not a.no_model and not use_model:
        print("⚠ Ollama not reachable — deterministic pass only (pure residuals left unclosed).\n")

    targets = [a.path] if "::" in a.path else list(enumerate_targets(Path(a.path).resolve(), root))
    print(f"crawl: {len(targets)} function(s) · model={'off' if not use_model else a.model.split(':')[0]} "
          f"· decompose={'apply' if a.apply else 'report'}\n")
    print(f"{'function':<32} {'state':<16} {'kill':>7} {'model':>5} {'c-eq':>5} {'seam':>6}")
    print("-" * 78)

    rows = []
    for t in targets:
        r = process_function(t, root, a.model, a.apply, use_model)
        rows.append(r)
        short = r["target"].split("::")[-1][:30]
        seam = "yes" if r["decomposed"] is True else ("avail" if r["decomposed"] else "-")
        kill = f"{r['killed']}/{r['total']}" if r["total"] else "-"
        print(f"{short:<32} {r['state']:<16} {kill:>7} {r['model_calls']:>5} {r['candidate_equiv']:>5} {seam:>6}")

    fixture = [r for r in rows if r["state"] == "needs-fixture"]
    unclosed = [r for r in rows if r["state"] == "unclosed"]
    errored = [r for r in rows if r["state"].startswith("error")]
    pinned = [r for r in rows if r["state"] == "pinned"]
    ceq = sum(r["candidate_equiv"] for r in rows)

    print("-" * 78)
    print(f"\n✓ pinned / refactored:        {len(pinned)}")
    print(f"⚠ needs a FIXTURE (impure):   {len(fixture)}")
    for r in fixture:
        reads = "; ".join(r["impure"][:2])
        hint = " — try --clock <epoch>" if any("clock" in x for x in r["impure"]) else ""
        print(f"     {r['target'].split('::')[-1]:<24} {reads}{hint}")
    print(f"· unclosed pure gap:          {len(unclosed)}   {[r['target'].split('::')[-1] for r in unclosed]}")
    print(f"· candidate-equivalent (Tier-2 flag queue): {ceq} mutant(s) across {sum(1 for r in rows if r['candidate_equiv'])} fn")
    if errored:
        print(f"✗ errored:                    {len(errored)}   {[r['target'].split('::')[-1] for r in errored]}")


if __name__ == "__main__":
    main()
