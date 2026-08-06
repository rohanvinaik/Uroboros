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
  drive.py      Detective I/O — `det()` (one --json subcommand) + `enumerate_targets()` + diff-mode
  preflight.py  dependency check — Detective/Wesker (hard), Ollama + a model (soft)
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

### `drive.py` — Detective I/O (2 functions)
- **`det(cmd, target, root, *extra) -> (state, error)`** — runs `detective <cmd> <target> --project-root
  <root> --json <extra>`, returns the parsed JSON or an error string. An empty target is omitted
  (so `regime` can resolve the whole repo). A subprocess wall (`CONVERGE_WALL`) means a hung engine
  is recorded, not a hang.
- **`enumerate_targets(path, root)`** — top-level functions in a file or across a dir (skips
  `test_*`/`conftest`), yielding `relpath::func` in source order.

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

## Boundaries (honest limits)

- The unit is **one function** (Detective's own law — "anything that scales with the repository is a
  category error"). Uroboros crawls functions; it never computes a whole-repo mutant profile.
- The model closes **line-coverage** residuals. It does not invent domain objects — for those,
  Detective captures arguments from one real test you write (its `--input` refuses what it can't parse).
- Impurity handling today is **detect-and-route**, plus Detective's `--clock` for the wall clock.
  Filesystem/env fixtures are the fixture queue's job (Detective issue #24).
