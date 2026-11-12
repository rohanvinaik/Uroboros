# From a prose brief to verified Python

## Set up the runner

Install Python 3.12 or later, Git, and Ollama. From the Uroboros checkout run `uv sync` and make `qwen3.5:4b` available with `ollama pull qwen3.5:4b`. The runner uses the Python environment that can import Detective, Wesker, pytest, and the dependencies used by the script's tests.

The default installation includes Detective and Wesker. To test local checkouts, set `UROBOROS_DETECTIVE_PATH` to the two checkout roots joined by the platform's path separator, for example `/path/to/Detective:/path/to/Wesker` on macOS. `UROBOROS_DETECTIVE_PYTHON` selects a different interpreter where needed. `UROBOROS_LLM_ENDPOINT` overrides the model endpoint.

## Author the form

1. Keep the prose brief. Resolve questions that change an observable result or a refusal. Make each function small enough to test as a unit, and specify the order of dependencies.
2. Run `uv run uroboros schema` for the JSON form schema. Read [the worked form](../examples/pairs.form.json) for the relationship between `combines`, intent tests, and AST choices. When a function combines another, seating can nest the dependency and give it a qualified name. Use the names printed by `check`.
3. Fill the module path, imports, test imports, docstring, objects, and function entries. Each function needs prose, operation categories, and executable pytest intent tests. `covers` names required behavior. `never` names `OUTPUT` operations whose undesired behavior the tests fence off.
4. For each function, declare an `ast_template` with `signature`, `code`, and `choices`. An object such as `{"$slot": "comparison"}` inserts the value of the chosen label. Leave the function's outer `signature` and `code` at `null`. You can parse a small Python sketch with `ast.parse` and call `uroboros.harness.contract.json_from_ast(node)` to obtain the JSON AST. That function returns a status and value. Use `ok` results.
5. Seat, check and preview the form. An authored revision can use `seat --revises PREVIOUS_SNAPSHOT`, where the snapshot is the exact filled contract from the prior run.

```sh
uv run uroboros seat FORM.json --out CONTRACT.json
uv run uroboros check CONTRACT.json
uv run uroboros request CONTRACT.json QUALNAME
uv run uroboros line CONTRACT.json --out RUN --contract-writer frontier-session
```

`line` uses the local 4B profile unless you select another profile with `uroboros --model PROFILE line`. `--ceiling 2` allows up to two local repair decisions per function. `--mode escalate` records a hand-back from the first play. `play` runs a single pass, and `reissue` clears one function for a manual retry.

## Read the result and admit it

Start with `RUN/line.json`. It points to the last play and records repair rounds or escalation. The play directory contains `play.json`, `contract.json`, `calls.jsonl` when a fill was needed, and `handbacks.jsonl`. A verified play contains generated Python, intent tests, Detective's tests, and `tests/detective/certificates.json`. Use a new output directory for each attempt.

Use `--target /path/to/clean/git/repo` with `line` to admit a verified unit, or run `uv run uroboros-admission admit PLAY --target REPO` after reviewing it. Admission expects a clean target on `main`. It runs the target's `scripts/ci_local.sh` when present, or pytest, then commits the files and certificate. If your target enforces strict pytest markers, register the `detective` marker in its pytest configuration before running admission. Keep the exact admitted snapshot for later revisions. A failed placement returns a receipt describing the failure and rollback.

Contract questions go back to the contract author. A runtime fault can be replayed once. A known Detective edge case can stop a repeated request after a supplied value made progress when the requested witness is unchanged. Retain the hand-back and its evidence for review.

## Test a fresh session

Give a fresh frontier model this repository and a prose brief for a small, unfamiliar module. Ask it to author the form, seat and check it, run the documented local line, and report the terminal result. Use a new output directory and a disposable Git target. Record the original brief, time to a checked contract, any questions, the saved contract and `line.json`, the target gate, and the certificates. A useful authoring target is a few minutes to half an hour. To assess reliability, repeat across unfamiliar tasks and compare review and correction effort with a direct implementation of each brief. Keep independent acceptance cases outside the authored contract until after the run.
