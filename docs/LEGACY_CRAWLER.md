> Historical documentation for the input-only refactoring crawler. Use `uroboros-crawl` for this command. For the coding line, see the [README](../README.md).

# Original Uroboros crawler

The crawler runs Detective on existing Python, derives pins within the measured mutation regime, and reports behavior-preserving seams.

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-3367d6.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-3367d6.svg" alt="Python 3.12+"></a>
</p>

`Set-and-forget · CPU-only · the model never drives`

```
$ uroboros-crawl billing.py --project-root .

crawl: 4 function(s) · model=qwen3 · decompose=report

function                         state               kill model  c-eq   seam
------------------------------------------------------------------------------
band                             pinned             30/30     0     0      -
quote                            pinned             50/58     0     8  avail
invoice_line                     pinned             17/18     0     1      -
receipt_id                       needs-fixture        5/5     0     0      -
------------------------------------------------------------------------------

✓ pinned / refactored:        3
⚠ needs a FIXTURE (impure):   1
     receipt_id               reads the clock via time.time() — try --clock <epoch>
⚠ needs INPUT (you supply):   0
· unclosed pure gap:          0   []
· candidate-equivalent (Tier-2 flag queue): 9 mutant(s) across 2 fn
```

Three functions come back **pinned**: for each, the crawler wrote a small pytest suite that kills the mutants classified as killable in that run. The `kill` column is the receipt (`band`: 30 of 30 mutants caught); `c-eq` counts candidate-equivalent survivors for which the search found no distinguishing input. `quote` also has a proven **seam**: rerun with `--apply` and it is split into a named helper and a caller — a rewrite qualified by Detective's preservation check. The suite written *before* the split still passes after it. And `receipt_id` is handed back instead of pinned: it reads the wall clock, so any golden test of its output is green now and red a second later. Uroboros refuses to write a test that rots, and names the remedy.

What lands on disk is ordinary pytest, no runtime dependency on Uroboros:

```python
# tests/detective/test_billing_quote_synth.py
@pytest.mark.detective
def test_quote_value_1():
    """VALUE survivor — distinguishing witness (equivalence search) (confidence 0.95)."""
    result = quote(units=501, rate=1.0, discount=0.0)
    assert result == 47595
```

The functions that most need cleaning often have thin test suites. A mutation-complete suite distinguishes all non-equivalent mutants in its declared regime. A passing suite preserves the tested distinctions, and the crawler checks a proposed split separately with Detective's preservation check.

This is the discipline of provable composition — small pieces, pinned behaviour, tangles split only at proven seams — run as a background process instead of held by hand. [Detective](https://github.com/rohanvinaik/Detective) makes that a command you drive, one function at a time; Uroboros drives it for you, across a whole repository, until Detective settles the function or reports a residual. The deterministic loop can run without the model, leaving questions that need a witness open.

## The model never drives

The crawler's deterministic loop calls a small local model when a branch needs an input value. The model selects values into a per-function JSON schema mined from the branches. The harness fixes arity and types, writes the call syntax, and drives Detective.

That one job sits on a difficulty ladder, which is why a 4B model on a laptop is enough — it beat a 36B on this task, per token:

| tier | the input needed | supplied by |
|---|---|---|
| **1 · script** | a distinguishing call the engine already found | pasted — no model |
| **2 · low-lift** | reach an un-executed line | the small model |
| **3 · complex-but-obvious** | land on a *proved* boundary edge | the small model |
| **4 · human-intent** | a domain object, or a value only you know | **you** — it abstains and hands it back |

## What comes back

Everything is pinned or refactored, except a short review list — each entry a *different* kind of decision, never blurred:

| bucket | meaning | your move |
|---|---|---|
| **pinned / refactored** | behaviour proven-preserved, minimal suite written | nothing |
| **needs a FIXTURE** (impure) | reads the clock / filesystem / env — no pin here holds | supply `--clock`, a tmp fixture, or write it by hand |
| **needs INPUT** | it needs a captured object or a value only you know | hand it one real call |
| **unclosed pure gap** | a real branch no tried input reached | glance at the one line |
| *(candidate-equivalent)* | the search found no distinguishing input in this run | review the evidence before any `detective flag` |

## Run it

Not yet on PyPI — install from source:

```bash
pip install git+https://github.com/rohanvinaik/Uroboros.git   # pulls the engine (detective-spec, over Wesker)
uroboros-crawl --check                                              # verify the engine + Ollama + a model
ollama serve &                                                # only needed for the model step
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

| Command | Writes | Does |
|---|---|---|
| `uroboros-crawl .` | test files | **crawl the current repo** — its declared source, one function to completion, then the next |
| `uroboros-crawl . --apply` | your source | same, and apply the splits it can *prove* behaviour-preserving |
| `uroboros-crawl file.py::fn` | test files | clean and pin one function or method |
| `uroboros-crawl src/` | test files | crawl a whole tree, one function to completion, then the next |
| `uroboros-crawl --diff [BASE]` | test files | crawl only what changed since BASE (default HEAD) — churn before a push |
| `uroboros-crawl src/ --apply` | your source | also apply the splits it can *prove* behaviour-preserving |
| `uroboros-crawl file.py --no-model` | test files | deterministic only — leave gaps that need an input unclosed |
| `uroboros-crawl --check` | nothing | dependency preflight and exit |

Without `--apply`, source is never rewritten — Uroboros writes tests and reports the splits it would make. The current package requires Python 3.12+. Absent Ollama or a pulled model, the crawl runs deterministically and leaves input-needing gaps unclosed.

## Where it stops

One function at a time, deterministic, narrow on purpose.

- **The unit is one function (or method).** It cleans functions; it never builds a whole-repo profile. It traverses a codebase one function to completion, then the next — not a fleet of concurrent workers (a deliberate choice; see [`LEGACY_ARCHITECTURE.md`](LEGACY_ARCHITECTURE.md)).
- **It preserves behaviour, not correctness.** A proof says the rewrite does what the original did. If the original was wrong, the rewrite is wrong the same way — provably. It does not know what your code is *for*.
- **It will not invent a domain value.** When a branch needs an object or a value whose meaning is not in the code, it hands it back (the `needs-input` bucket) rather than guess.
- **A method needing a constructed receiver** is that same boundary — it surfaces as `needs-input` / `unclosed` rather than pinned ([#1](https://github.com/rohanvinaik/Uroboros/issues/1)).
- **Style is Detective's job, not Uroboros's.** Whether a function is *doing too much* is a judgment, not a proof, so the crawl never acts on it — it pins and splits what is provable and hands the rest back. The advisory read (`detective parsimony`) lives one layer down, where a human or model drives it.

Orientation for a contributor: [`AGENTS.md`](../AGENTS.md) and [`LEGACY_ARCHITECTURE.md`](LEGACY_ARCHITECTURE.md).

---

The crawler runs [Detective](https://github.com/rohanvinaik/Detective) over [Wesker](https://github.com/rohanvinaik/Wesker). MIT — Rohan Vinaik.
