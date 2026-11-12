# Architecture

Uroboros puts a checked contract between the model that understands a person's request and the local process that builds it. The contract records the intended interfaces, executable behavior, fixed AST structure, and the remaining choices.

## Authority

| Component | Authority |
|---|---|
| Person | Chooses what the program should do and which failures it should expose |
| Frontier author | Designs the functions and dependencies, writes the intent tests and AST choices, and checks the contract |
| Local model | Selects AST values, supplies a specific value when Detective asks, or classifies a hand-back |
| Harness | Validates and renders ASTs, bounds repair, follows Detective's typed decisions, and controls placement |
| Detective and Wesker | Produce behavioral evidence within the tested function, regime and operator universe |

## Execution

`harness.contract` seats and validates the author's form. `call_order` gives a bottom-up schedule. `call_context` includes the current function, its contract and already-written dependencies. `call_schema` constrains an AST-template reply to its declared labels. `fill` validates the returned cells and `render_module` generates Python.

`harness.model` builds the native Ollama or compatible HTTP request. The default Qwen3.5 4B profile budgets the rendered UTF-8 prompt, a reply allowance and chat framing. It selects a window between 16K and 256K and records the request budget beside actual token counts.

`harness.daemon` executes the regime, audit and converge steps named in Detective's typed records. It records each action, test observation and hand-back. `harness.line` bounds each reissue, preserves earlier evidence, and hands contract questions upstream. One replay is available for a machine fault. A second fault, unchanged evidence, abstention, or the configured ceiling ends local repair.

`harness.admission` checks terminal routes, source and intent-test identity, certificate identity and the reported generated tests. With a Git target, it runs the target's gate, writes the certificate with Detective, and commits the admitted files. Failed placement restores the paths it touched. A replacement must reference the exact contract snapshot of the previous admission.

The [legacy crawler architecture](LEGACY_ARCHITECTURE.md) describes the input-only predecessor.
