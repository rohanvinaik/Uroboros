# Uroboros

**An assembly line for Python. A conversation supplies the intent. A local build produces the implementation, the tests, and a receipt for what they pin down.**

If you can explain how a bill should be calculated, you should be able to commission the billing code and get a test suite that holds it to the rule. Uroboros aims for mutant-complete specification, where the suite distinguishes the implementation from every non-equivalent mutant in a declared regime.

Uroboros divides that work at the contract. A capable chat model turns the person's brief into interfaces, dependencies, and executable tests of required and forbidden behavior. Once the contract is checked, the machinery runs on hardware the person owns. A small model writes the implementation, [Wesker](https://github.com/rohanvinaik/Wesker) generates the behavioral alternatives, and [Detective](https://github.com/rohanvinaik/Detective) decides what the evidence warrants and what to do next.

## Six bytes where there should be zero

The brief for an image-buffer module fit in a paragraph. Each row occupies the smallest multiple of the requested alignment that can hold it. A nine-byte row aligned to eight bytes occupies sixteen. An empty row occupies zero. The same arithmetic has to work for byte counts beyond the exact integer precision of a float.

A fresh chat-model session read this repository and produced a checked contract in about two minutes. It settled the function signatures and the dependency between them, wrote the intent tests, and left three expressions for Qwen3.5 4B to implement locally. The first implementation allocated six bytes for an empty row. Its syntax tree was valid, and the test for a zero-byte row gave Detective a precise reason to reject it.

```python
# First fill of aligned_bytes(row_bytes, alignment)
return -1 % alignment + row_bytes

# Local reissue of the same AST cell
return -row_bytes % alignment + row_bytes
```

The original trial stopped when the local model repeated its rejected answer. Uroboros now carries the rejected code and Detective's named failures into the reissue. On a full rerun from the original contract, Qwen corrected the arithmetic in one attempt. Detective added a distinguishing test for each function. The resulting suites killed **14 of 14** alignment mutants and **15 of 15** buffer-size mutants under the recorded policy. The target gate passed, Uroboros committed the code and certificates, and an independent acceptance check passed **8,406 cases**.

## Who decides what happens next

The contract author can fix structure already settled by intent and leave expressions, statements, or bodies open. Uroboros schedules functions in dependency order and compiles each set of open AST cells into a schema for constrained decoding. The local model supplies the missing syntax tree. Uroboros validates it and renders ordinary Python.

The intent tests check the behavior the person asked for. Wesker's mutations probe the behavior the implementation actually has. When Detective finds an input that distinguishes a mutant from the candidate, it turns that observation into a test. The certificate records which alternatives the finished suite rules out and which survivors remain.

The next action comes from the same structured record as the verdict. A code defect can reopen the affected AST cell under the same contract. A question about what the program should do goes back to the contract author. When a repair repeats the failure, the run can stop with the rejected implementation and the evidence still attached. An optional Git target gets a verified unit only after its own gate passes.

## What the receipt can tell you

The billing example exercises a different part of the contract. Qwen3.5 4B wrote the excess-usage logic and metered-charge arithmetic. The module passed Git admission, and a separate oracle agreed on **65,536 input combinations**. The receipt still names two candidate-equivalent mutants in the excess-usage helper.

Candidate equivalence records a search result. Detective tried to distinguish the mutant and found no witness. The certificate keeps that qualification beside the kills and identifies the versioned operator regime. The evidence here covers two small modules with expression holes. Reliability and review effort at project scale remain to be measured.

## Run the billing example

Use Python 3.12 or later, Git, uv, and a running Ollama server. Run these commands from the checkout.

```sh
uv sync
ollama pull qwen3.5:4b
run_dir=$(mktemp -d)
uv run uroboros seat examples/usage-bill.form.json --out "$run_dir/contract.json"
uv run uroboros check "$run_dir/contract.json"
uv run uroboros line "$run_dir/contract.json" --out "$run_dir/run"
cat "$run_dir/run/line.json"
```

The [billing contract form](examples/usage-bill.form.json) fixes the interfaces and the base-fee term. The local model supplies the excess-usage and metered-charge expressions. Start with `line.json` and follow it to the implementation, tests, and certificate or hand-back. Pass `--target /path/to/clean/repo` to admit a verified unit into Git.

- Author a fresh contract, inspect the exact model request, or read a receipt with the [coding runbook](docs/CODING.md).
- See how decision records, AST constraints, and context budgets fit together in the [architecture](docs/ARCHITECTURE.md).
- Use the [original crawler](docs/LEGACY_CRAWLER.md) to pin and refactor existing Python.

[MIT](LICENSE) · Rohan Vinaik
