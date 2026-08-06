# Architecture

How Uroboros drives Detective, and how the harness is built. Read this to spin up on the
intended design; every symbol named here is real.

## The one idea

**Uroboros owns control flow. The model is a pure function at exactly one step.** A big model
handed a shell and a loop reads the source and gets clever; a big model asked only to *select
test inputs into a typed schema* cannot. So there is no driver's seat: a deterministic loop
reads Detective's `--json` state and computes the next command; the model is woken only to
answer one narrow question — "what input reaches this branch?" — and never sees the loop.

## The Detective loop it drives (the mechanism)

Detective is a CLI whose every subcommand takes `--json` and emits a machine-readable state,
and whose human output ends in a `DO THIS:` line naming the next command. Uroboros drives it
per function, keying off the JSON, not the prose:

```
regime            once per repo — resolves how the repo imports + tests; REFUSES on conflict
  diagnose        reads a function: how many behaviours, how many pinned, is there a SEAM
    if seam & --apply -> decompose --apply   split at a PROVEN-behaviour-preserving seam
  converge        write the minimal suite that pins every KILLABLE mutant; report what's left
```

The state fields Uroboros routes on (all from `converge --json`):

| field | meaning |
|---|---|
| `functionally_complete` + `line_complete` | done — every killable mutant pinned, every line covered |
| `missing_lines` | uncovered lines — a line gap the model may be able to reach with an input |
| `missing_line_guards` | `[line, condition]` — the branch condition that reaches each line |
| `environment_gated` | reads of clock/fs/os that gate reachability (Detective ≥ 0.10.2) — **no `--input` reaches these** |
| `survivor_report.verdicts[]` | per-mutant `killable` / `crash_only` — `killable:false` = candidate-equivalent |
| `signature`, `param_names` | what the model needs to author one real call |

`environment_gated` is the load-bearing addition: it is why Uroboros never spins the model on a
branch gated by `path.exists()` or `time.time()`, which no argument can satisfy.

## The modules

```
uroboros/
  cycle.py      control flow — the ouroboros loop + the review-bucket routing + the guards
  nextstep.py   Detective's typed DO-THIS, re-derived from converge JSON (the model's fodder)
  synth.py      the ONE model call — a typed skeleton the model selects into (no Python emitted)
  drive.py      Detective I/O — `det()` (streams the heartbeat) + `enumerate_targets()` (source-scoped) + diff-mode
  preflight.py  dependency check — Detective/Wesker (hard), Ollama + a model (soft)
  launch.py     the hands-off launcher (`uroboros-launch`) — a constrained chat loop; the model selects, never drives
```

### `nextstep.py` — the typed next-step (same brain, a JSON mouth)
Detective renders one guided next action per state from `cli._derive_inputs` + its
`_boundary_hint` tree. **`derive_next_step(converge_json) -> {kind, items, total}`** is a
FAITHFUL PORT of that derivation, re-run over the `survivor_report` JSON (the stable data
contract) instead of scraping the typeset prose. The `kind` is Detective's own vocabulary — and the
bake-off's **difficulty ladder** for who supplies the input: `witness` = tier-1 pure-script (paste
the engine's found kills, no model) · `lines` = tier-2 any-dolt · `boundary` = tier-3
complex-but-obvious (a proved edge → the model drives it, the tier qwen3:4b was qualified for) ·
`internal`/`test`/`author` = tier-4 purposivistic (**certified abstention → promote to the human**).
The machine closes tiers 1-3; tier 4 becomes the `needs-input` bucket or is left `unclosed`. The
port is pinned by a differential test against Detective's own functions (`tests/test_nextstep.py`);
the DRIFT NOTE in the module is load-bearing — it must move in lockstep with Detective's oracle.

### `drive.py` — Detective I/O
- **`det(cmd, target, root, *extra, stream=False) -> (state, error)`** — runs `detective <cmd> <target>
  --project-root <root> --json <extra>`, returns parsed JSON or an error string. `stream=True` inherits
  Detective's stderr so its live per-mutant heartbeat reaches the reader (engine warnings silenced via
  `PYTHONWARNINGS`); the crawl commands stream, `regime` is captured for its refusal text. A subprocess
  wall (`CONVERGE_WALL`) records a hung engine rather than hanging.
- **`enumerate_targets(path, root)`** — the crawl set: the project's OWN source, module-level functions
  only, `relpath::func` in source order. `_source_roots` resolves where the source lives (a package /
  `src/`-layout / the top-level packages / fallback — robust across repo shapes); `_is_source` excludes
  virtualenv·VCS·cache·build and aux trees (`tests`/`docs`/`examples`/`data`); **methods are skipped**
  (Detective can't build a receiver yet — #25). `changed_targets(root, base)` is the diff-mode variant.

### `launch.py` — the hands-off launcher (`uroboros-launch`)
A constrained chat loop: each turn the model fills one typed schema — a prose `reply` and one boolean,
`run_uroboros` — and nothing else. No shell, no file access, no tool calls, so it cannot wander off and
edit your source (the failure a free-form agent loop invites from a 4B model). When the flag is set (or
the word "uroboros" appears), the HARNESS runs `uroboros . --apply` and streams the crawl. Same law as
`synth.synthesize_inputs`, one layer up: the model selects, the harness drives.

### `synth.py` — the one model call
The public entry is **`synthesize_inputs(state, source, func, model, requirement=None) -> (inputs, tele)`**.
Everything else builds the constraint that makes it safe:
- **`_signature_spec`** → `[(param, base_type)]` from the def's annotations (AST, no exec).
- **`_mine_literals`** / **`_mine_mapping_keys`** → the boundary values and dict keys the branches
  actually test against — the closed vocabulary, mined not guessed.
- **`_scalar_pool`** / **`_slot_schema`** / **`build_synth_schema`** → a per-target JSON schema where
  each call is an OBJECT keyed by param name (`additionalProperties:false`, every param required, so
  **arity is structurally locked**), and each scalar slot is `anyOf:[{enum: mined_pool}, {type}]` (so
  **type is locked**). The model selects into this; it cannot emit a malformed call.
- **`_focus_block(requirement)`** → when a specific line is targeted, names the ONE condition to
  drive ("reach line N by making `discount > 0.6` true") so the model reasons backward to inputs.
- **`_tuple_literal`** → the ONLY place call syntax is written — deterministically, from the model's
  JSON, via `repr` + `ast.literal_eval` round-trip. A malformed synthesis can never reach Detective.

The Ollama call uses `format=<schema>` and `think:false` — the model returns schema-valid JSON or
nothing. (Measured: 100% arity-adherence across four models; constraint beats persuasion.)

### `cycle.py` — the ouroboros loop
**`process_function(target, root, model, apply_decompose, use_model) -> result`** is the whole cycle:

1. `diagnose`; if a seam and `--apply`, `decompose --apply` (proven-safe).
2. `converge` blind. Absorb `killed/total` and the candidate-equivalent count.
3. **Impurity gate** — if `environment_gated` is non-empty, route to **needs-fixture** immediately,
   *whether or not converge "pinned" it*: a golden pinned to `int(time.time())` is green now and red
   next second. Never author a fragile pin; never spin `--input` on an unreachable gap. Hand the human
   the remedy (`--clock`).
4. **The guarded model step** (pure functions with a line gap only): up to `MODEL_PASSES` rounds of
   `synthesize_inputs` → `converge --input`. Three guards:
   - **bounded call** (`_bounded_synth`, `CALL_WALL`s) — one slow generation can't stall the crawl;
   - **no-progress guard** — if `missing_lines` doesn't shrink, stop (the impure-line trap defence);
   - **mid-loop impurity** — if `environment_gated` surfaces only after an input runs, route to fixture.
5. Final state: `pinned` / `unclosed` (a real pure gap) — candidate-equivalents are "done modulo
   undecidable" (informational, the Tier-2 flag queue), not a per-function action.

**`main()`** runs the preflight gate (Detective is hard-required), `regime`, enumerates, processes each,
and prints the table + the review buckets. `--check` runs the preflight report and exits; `--no-model`
and no-Ollama both degrade to the deterministic pass.

## The output contract (what a human clears over coffee)

- **pinned / refactored** — proven, minimal suite written. Nothing to do.
- **needs a FIXTURE (impure)** — reads clock/fs/os; the professionalization case. Supply `--clock`,
  a tmp fixture, or write it by hand.
- **unclosed pure gap** — one branch the model couldn't reach; glance at the line.
- **candidate-equivalent** — undecidable survivors; optional `detective flag`.

## The three stages of scope (unit → codebase → fleet)

Uroboros scopes work at exactly three levels, and deliberately stops at the second:

1. **The function — the unit (stage 1).** One function is driven to a pinned suite or a labelled
   residual by the DO-THIS loop (`process_function`). *Within* a function the loop is necessarily
   SERIAL: each `converge --input` pass consumes the previous pass's result, so there is nothing
   to parallelize and it would be wrong to try.

2. **The codebase — the traversal (stage 2, built).** After a one-time **setup pass** (`detective
   regime --migrate` — declares the pytest marker + pythonpath so the existing suite is discovered and
   the code imports; without it the baseline is empty and nothing happens), `enumerate_targets` resolves
   the project's OWN source (`_source_roots`: a package / `src/`-layout / the top-level packages — robust
   across repo shapes, never the data/generated/vendored trees beside it), crawls its module-level
   functions (methods skipped until Detective #25), and runs them ONE AT A TIME TO COMPLETION. Serial by
   design: the mutant state lives in memory and a function is stateless or stateful depending on the
   step, so one-at-a-time keeps the state handling tractable.

3. **The fleet — concurrency (stage 3, deliberately unbuilt).** K workers on *different* functions
   AT ONCE. The in-memory mutant state, the AST rewrites, and the stateful/stateless-per-step model
   make the coordination cost wildly disproportionate to the speed-up on an already-cheap crawl
   (CPU-bound Detective + one small resident model). Out of scope by choice, not oversight.

## Boundaries (honest limits)

- The unit is **one function** (Detective's own law — "anything that scales with the repository is a
  category error"). Uroboros crawls functions; it never computes a whole-repo mutant profile.
- The model closes **line-coverage** residuals. It does not invent domain objects — for those,
  Detective captures arguments from one real test you write (its `--input` refuses what it can't parse).
- Impurity handling today is **detect-and-route**, plus Detective's `--clock` for the wall clock.
  Filesystem/env fixtures are the fixture queue's job (Detective issue #24).
- The stage-2 crawl (`enumerate_targets`) crawls the project's declared source — **module-level
  functions only**. Methods are skipped: Detective cannot yet construct a receiver (a real `self`) for
  a bound method (#25), so a method target is unpinnable and crawling it is pure grind on a class-heavy
  tree. Nested classes and async defs are likewise not descended. Re-enable methods once Detective's
  receiver synthesis lands.
