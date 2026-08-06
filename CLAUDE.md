# Uroboros — working canon

Read `README.md` for the name and the pitch, `AGENTS.md` to orient before touching anything,
`docs/ARCHITECTURE.md` for the symbol-level map. This file is the law.

## Core thesis

A set-and-forget crawler that drives **Detective** (over **Wesker**) function by function until
code is *pure* — behaviour pinned by a minimal, mutation-complete test suite, tangled functions
split at proven seams. It is **SICP-on-a-chip**: deterministic, CPU-only, no inference in the
engine. Its own intelligence is nearly zero; a small local model is a pure function at exactly
one step. The whole value is packaging a rigorous loop so it runs itself.

## The non-negotiable laws

1. **The model never drives. Agency is the bug.** Control flow is the script's; the model is a
   pure `(signature, requirement) -> input` function behind the loop, constrained to *select*
   values into a typed schema — it never writes call syntax, never sees a shell. A model given a
   loop reads the source and gets clever; this design makes that structurally impossible.
2. **One function is the unit.** Detective's own law: anything that scales with the repository is a
   category error. Crawl functions; never compute a whole-repo mutant profile.
3. **`environment_gated` → fixture, never `--input`.** A branch gated by the clock/filesystem/env
   cannot be reached by any argument. Detecting it and routing to the fixture queue (with the
   `--clock` remedy) is the point — a golden pinned to `int(time.time())` is a fragile test, and
   catching it is the professionalization win. NEVER spin the model on it.
4. **Guard against the trap.** The model step is bounded (a per-call wall) and stops on no progress
   (`missing_lines` didn't shrink). A driver that keeps supplying inputs to an unreachable line is
   the failure mode; the guards forbid it.
5. **Proven or nothing.** `decompose --apply` rewrites source only when a generated suite proves
   behaviour survived; `converge` only writes tests. Uroboros applies nothing Detective can't prove.
6. **Verify with the receipt.** A kill count with a red suite is worthless. The generated tests are
   ordinary pytest; green pytest is the only proof.

## Current state (2026-08-06)

- **Built + working:** the full cycle — regime → diagnose → decompose → converge → guarded model
  step → review-bucket routing. Runs as `uroboros`, `--check` preflight, `--no-model` degrade,
  `file.py::func`. Model default `qwen3:4b-instruct` (bake-off winner).
- **The model step is now DO-THIS-driven** (`nextstep.derive_next_step`): a faithful port of
  Detective's own `_derive_inputs`/`_boundary_hint` re-derives the typed next-step from converge
  JSON; the model produces the `--input` that step asks for, kind by kind. The kinds ARE the
  bake-off's difficulty ladder: `witness` = tier-1 pure-script (paste, no model); `lines` = tier-2
  any-dolt; `boundary` = tier-3 complex-but-obvious (the model, default-on — the tier qwen3:4b was
  chosen for); `internal`/`test`/`author` = tier-4 purposivistic (abstain → promote). This subsumes
  Tier-2 with no flag. Buckets: pinned / needs-fixture / **needs-input** (`test`/`author`) / unclosed.
- **diff-mode:** `uroboros --diff [BASE]` crawls only functions changed since BASE ("churn before
  a push"). Built.
- **Engine-runtime resolution:** `det()` runs the engine WHERE the target repo's deps live, not blind
  in Uroboros's own install env (the systematic "real suite can't collect → everything reads unpinned"
  failure). A ladder (`_engine_prefix`, pinned 18/18 by Detective; `_resolve_engine` the impure shell):
  activated `$VIRTUAL_ENV` → repo's own `.venv` (universal: `pip install uroboros-refactor` into it) →
  isolated deps + `uv` (`uv run --no-sync --with detective-spec`, ephemeral, `.venv` untouched) →
  isolated + no uv (global + **degrade with a precise fix, keep crawling**) → not isolated (global).
  uv is an accelerant, never load-bearing; cross-injection is ruled out (3.10 vs 3.14 C-ext lock).
- **Setup + scope + surface:** a startup **setup pass** (`regime --migrate`) so a fresh repo Just Works
  (declares the marker/pythonpath — else the baseline is empty and nothing happens); `enumerate_targets`
  crawls the project's **declared source** (`_source_roots`, robust across repo shapes — not data/vendored
  trees), **module-level functions only** (methods skipped, Detective #25); the crawl **streams live**
  (`[i/N]` + Detective's heartbeat, engine warnings silenced); **`uroboros-launch`** is a
  constrained-output chat launcher (model selects, never drives).
- **Pending:** fleet parallelism (the crawl is still serial); re-enable methods when Detective #25 lands.

## How to run

```bash
uroboros --check          # deps: Detective/Wesker (hard), Ollama + model (soft)
uroboros .                # crawl THIS repo's source (report mode — writes tests, not source)
uroboros . --apply        # + apply proven-safe decompositions (rewrites source)
uroboros --diff [BASE]    # only functions changed since BASE — churn before a push
uroboros-launch           # hands-off chat launcher: say "run uroboros" → runs `. --apply`
```

Every run opens with a **setup pass** (`detective regime --migrate`) that declares the `detective`
pytest marker + pythonpath, so the existing suite is discovered and the code imports — without it the
baseline is empty and nothing happens. It then crawls the project's own **declared source** (module-level
functions; methods skipped until Detective #25) and streams Detective's live heartbeat per function.

## Code standards

- Match Detective's voice: dense docstrings that say *why*, with the failure the rule prevents.
- Edit with Serena's symbolic tools; verify references before deleting (a copy from the skeleton
  once carried a dead `crawl_function`/`classify_guards` island — trimmed after confirming no
  live referrer).
- The trilogy naming is load-bearing and cased exactly: **Detective / Wesker / Uroboros.**
