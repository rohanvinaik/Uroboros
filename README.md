# Uroboros

A person describes a program in prose. A frontier model turns the description into a contract. Uroboros runs the contract locally and returns Python, tests, and evidence of the behavior Detective verified.

The person supplies the intent and resolves questions that change what the program should do. They can do that in ordinary language without knowing Python. The frontier model supplies the coding expertise needed to design interfaces, state obligations, write intent tests, and express the implementation as an AST with open choices. The local model can be small because each call receives one function and a bounded decision. The shipped Ollama profile uses Qwen3.5 4B.

## How the loop works

1. **Author the contract.** A frontier model such as ChatGPT or Claude decomposes the work into small Python functions, writes positive and negative intent tests, and checks the result with `uroboros seat` and `uroboros check`.
2. **Fill the AST.** The local model selects among declared choices. The harness validates the result and renders Python. It sizes the context window for the actual request.
3. **Follow Detective.** Detective uses Wesker to probe behavior and emits typed next steps. Uroboros follows those steps and records the result. A model can supply a value or classify a hand-back where the next decision needs one.
4. **Repair or return evidence.** A rejected implementation can be reissued under the same contract. A contract defect goes back to its author. A completed unit carries the code, intent tests, generated tests and scoped certificates. An explicit Git target also runs its own gate before admission.

The demonstrated scope is a Python script or module with explicit behavior, including functions that compose. Certificate records identify the measured regime, operator universe and residuals. Larger project integration and consistency across unfamiliar briefs are separate empirical questions.

## Quick start

Use Python 3.12 or later, Git, and an Ollama server with the default model available.

```sh
uv sync
ollama pull qwen3.5:4b
uv run uroboros seat examples/pairs.form.json --out /tmp/pairs.contract.json
uv run uroboros check /tmp/pairs.contract.json
uv run uroboros line /tmp/pairs.contract.json --out /tmp/pairs-run
```

Choose a new output directory for each run. Open `line.json` first, then follow the recorded play directory for the generated module, tests, certificate and hand-backs. The [coding runbook](docs/CODING.md) explains how to author a fresh contract, use local checkouts of Detective and Wesker, and admit into a target repository. The [architecture](docs/ARCHITECTURE.md) describes the authority and evidence boundaries.

The original input-only refactoring workflow is available as `uroboros-crawl`. Its [guide](docs/LEGACY_CRAWLER.md) describes that workflow.
