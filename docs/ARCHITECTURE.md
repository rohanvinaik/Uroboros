# Architecture

Uroboros puts a checked contract between the frontier author and a local build driven by Detective over Wesker. The contract records the intended interfaces, dependencies, and executable obligations. It can fix AST structure and declare which values or subtrees remain open.

## Authority

| Component | Authority |
|---|---|
| Person | Chooses what the program should do and which failures it should expose |
| Frontier author | Defines the functions and dependencies, writes intent tests of both signs, and checks the contract |
| Local model | Fills open AST cells, supplies a scoped witness when Detective asks, or classifies a hand-back |
| Harness | Validates and renders ASTs, bounds repair, follows Detective's typed decisions, and controls placement |
| Wesker | Generates the AST mutations for the declared regime |
| Detective | Measures the alternatives, verifies pins, and emits the typed next action or residual |

## Execution

`harness.contract` seats and validates the author's form. `call_order` gives a bottom-up schedule. `call_context` includes the current function, its contract and already-written dependencies. `call_schema` constrains the AST during decoding. A template can expose finite labels and typed expression, statement, or body subtrees. The same grammar tables drive decoding and `fill` checks. `render_module` generates Python from the checked AST.

`harness.model` builds the native Ollama or compatible HTTP request. The write budget scores the structured contract, test ASTs, dependency ASTs, and object expressions. For a measured reply form, the profile derives the output allowance from that score and the calibration for the model and thinking setting. Uncalibrated forms record the profile fallback. The Ollama context window fits the assembled prompt, reply reservation, and chat framing within the declared model capacity.

`harness.daemon` follows Detective's structured decisions over Wesker. After setup it runs the action Detective names, supplies a typed witness when requested, or carries the ending forward with its evidence. It records each action, test observation and hand-back. A reissued fill receives the rejected code rendered from its saved AST, the receiver's `code_wrong` judgment, and Detective's named failing tests or other observed evidence. The response stays constrained to the contract's AST schema. `harness.line` bounds reissues and hands contract questions upstream. One replay is available for a machine fault. A second fault, unchanged evidence, abstention, or the configured ceiling ends local repair. When several audits report the same failing authored test, the loop gives the function that owns the test the first repair opportunity and remeasures through Detective.

`harness.admission` checks terminal routes, source and intent-test identity, certificate identity and the reported generated tests. With a Git target, it runs the target's gate, writes the certificate with Detective, and commits the admitted files. Failed placement restores the paths it touched. A replacement must reference the exact contract snapshot of the previous admission.

The [legacy crawler architecture](LEGACY_ARCHITECTURE.md) describes the input-only predecessor.

Detective's [Communicating Determinism](https://github.com/rohanvinaik/Detective/blob/main/docs/theory/COMMUNICATING_DETERMINISM.md) supplies the record design. Distinct remedies have distinct typed states. The loop consumes Detective's decision and keeps its evidence and qualifications through reissue and admission.
