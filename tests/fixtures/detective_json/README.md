# Recorded Detective runs, as the harness runs them

Real Detective, run through the harness's own runner (`detective.run`: `python -m Detective`, `--json` added, both signs,
the folder as `--project-root`) on the files `contract.play` renders from a filled contract. The harness's tests replay them
through the runner seam (`tests/detective_replay.py`), so a replay is also the check that the harness ran exactly the
commands Detective's record named. They are recordings, not constructions: where a test needs a state no recording holds,
it builds that record from a recorded one and says so in its docstring.

These fixtures were captured in SquishOS with its `scripts/record_detective_json.py`. The embedded contracts use the Uroboros format tag. The engine records are preserved for replay.
Each file is one case: `contract` and `files` (what it was recorded from), `steps` (each exactly the record `detective.run`
returned: `argv`, `exit`, `payload`, `ledger`, `outcome`, `certificates`, `samples`, and `stdout` only where there was no
payload), `asked` (the asked-test file the line wrote, through `contract.write_asked_test`), `interpreter` and `two_sign`,
and `versions` (the Detective and Wesker commits it ran). The folder is `{root}`, this machine's home `{home}`. Mutant ids
differ between interpreters (Detective's own and the SquishOS venv's), so a flag recorded under one does not carry to the
other; every case here ran under the SquishOS venv unless its `interpreter` says `detective`.

| case | the states it holds |
|---|---|
| `pairs`, `grades`, `clamp` | the reference play: regime `migration_available` and `migrated`; a clean two-sign audit that names converge (`close_the_gap`, and `remove_redundant`, which without `--two-sign` names `--remove`); converge `settled` with its residual |
| `clamp_failing` | audit `fix_failing_test` (step `decide`), and a `doctor --json` |
| `clamp_unloadable` | audit refused `collection_errors` (exit 2), and a `doctor --json` naming the module it could not import |
| `label` | converge `close_the_gap` with a `lines` ask (supply), then settled once `--input` is given |
| `orders_failing` | converge `fix_verification` (stop, exit 3); audit `provide_sample` (supply, a `sample` ask); the converge run with the values, which Detective answers with its own hand-back `supplied_did_not_reach`: a true repeat (the same ask, 0 of 11 mutants killed) |
| `route` | converge `close_the_gap` with a `lines` ask on a line behind a hashed key (11 of 33 killed); a value that misses it, answered `supplied_did_not_reach` with the ask, the candidates and the kills unchanged: a true repeat, the one case Detective hands back by his ruling (a) (the D1b builder's `route`, in the lead's lab) |
| `bucket` | audit `flag_equivalents` that names converge; converge `settled`; `flag` refused (`no_surviving_mutant`) and `flag` recorded; converge again |
| `counter` | converge `close_the_gap` with a `witness` (step `run`, Detective's own `--input`); the run that answers with a `write_test` carrying Detective's rendered sources (`test_status` `rendered`); the asked-test file written; converge again, which asks for the line the environment guards (a `lines` ask on a method) |
| `target_missing` | Detective's text-only refusal: no JSON, the ledger names `target_not_found`, exit 1 |
| `lead_internal` | NOT recorded by the script: the lead's check of D1b (`~/Archives/squishos-build-2026-10-08/d1b_check/bucket_detective_venv`), imported. Under Detective's own interpreter and without `--two-sign`, converge asks about the internal condition `h == 500` with the mutant it came from (an `internal` ask, the one ask whose items carry `mutants` and `form`), then hands back `supplied_did_not_reach` on a call that misses it, then settles. Its ledger, certificates and samples were not kept there. **Its hand-back (step 1) is a progress case, 16 of 22 killed before the value and 18 of 22 after it, which his ruling (a) reverses: reversed by his (a); re-record when the D1b-(a) fix lands.** No test replays that step; the tests use its ask (step 0) and its settled run (step 2) |

**Not produced** (no recording; a test that needs one builds it from a recorded record):
- audit `fix_load`, and converge `fix_load`: a module that cannot be imported also fails the suite's collection, which Detective
  refuses as `collection_errors` before it can say `fix_load` (`clamp_unloadable`);
- a regime repair inside the run (converge naming `regime` or `regime --migrate`, and its record ending in `done`);
- `provide_sample` for a function whose tests pass: Detective traces the real calls the tests make, so a function that
  passes is never short of a sample (`orders_failing` is a function whose tests fail);
- a `write_test` whose rendering is declined or failed (`test_status` other than `rendered`), and the asks `hand_pin`,
  `fixture` and `test`;
- the `internal` and `boundary` asks under the harness's own command (`--two-sign`); only `lead_internal` holds one;
- a `supplied_did_not_reach` on an `internal`, `boundary` or `author` ask that kills nothing: every one recorded there killed
  more (`lead_internal` step 1, and the `author` ask after a `flag --fence`, 24 of 30 then 26 then 29), progress that his
  ruling (a) reverses, so none is kept. The true repeats are `route` (`lines`) and `orders_failing` (`sample`).
