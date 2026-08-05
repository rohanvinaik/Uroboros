# For agents (and humans) arriving fresh — read this before touching anything

## What this is

Uroboros is a thin harness with almost no intelligence of its own. It drives **Detective** (a
deterministic mutation-testing CLI) over **Wesker** (its engine), function by function, to pin
behaviour and split tangled code — waking a small local model for one narrow job: choosing a test
input to reach a branch. If you find yourself wanting to make the *model* smarter, or to let it
decide what to do next, stop: that is the exact failure this design exists to prevent (see law #1
in `CLAUDE.md`). The intelligence is Detective's; Uroboros just cycles it.

## Ground before acting

1. Run it once so the loop is concrete, not imagined:
   ```bash
   uroboros --check                 # see the three dependencies
   # then, in any small repo with tests:
   detective regime --migrate --project-root .
   uroboros some_file.py --project-root . --no-model
   ```
   Watch the table: `pinned`, `needs-fixture` (impure), `unclosed`. That IS the product.
2. Read `docs/ARCHITECTURE.md` — it names every symbol. The whole cycle is `cycle.process_function`;
   the one model call is `synth.synthesize_inputs`; the CLI plumbing is `drive.det`.
3. Understand the data contract before changing routing: Uroboros keys off `converge --json` fields
   (`functionally_complete`, `line_complete`, `missing_lines`, `environment_gated`,
   `survivor_report.verdicts`). It does NOT parse the human `DO THIS:` prose.

## The mechanism, in one breath

`det()` shells one Detective subcommand with `--json`. `process_function` reads that state and
decides the next command deterministically: seam → `decompose --apply`; line gap on a *pure*
function → wake the model (`synthesize_inputs`) for one `--input`, re-`converge`, repeat under the
guards; reads-the-environment → route to the human with a fixture remedy. Nothing about "which
command runs next" is ever the model's call.

## How to verify instead of trust

- The generated tests are ordinary pytest. After a run, `pytest -q` in the target repo is the
  receipt — a green suite is the only proof the pins are real.
- Editing code here: use Serena's symbolic tools, and `find_referencing_symbols` before deleting.
  A file-copy from the old skeleton left a self-contained dead island once; it was removed only
  after confirming nothing live referenced it. Verify, don't assume.

## Lineage (why the design is what it is)

This is the operational end of a longer thesis: standard accuracy benchmarks stop discriminating
models near the top, so the right question is *behavioural fit to a task's cognitive shape*. Driving
Detective is a compositional, decomposable task — and a bake-off found a 4B instruct model beats a
36B on it, per token. So Uroboros uses the small model, deliberately, and constrains it hard. The
"model as pure function, agency is the bug" law is that finding made structural.
