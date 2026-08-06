# Uroboros

**Point it at a Python codebase and it hands the code back clean — tangled functions split apart, every behaviour pinned under a minimal test suite that fails on any change to the code.**

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-3367d6.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-3367d6.svg" alt="Python 3.11+"></a>
</p>

`Set-and-forget · CPU-only · the model never drives`

```
$ uroboros billing.py --project-root .

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

Three functions come back **pinned**: for each, the crawl wrote the minimal pytest suite that kills every killable mutant — every small edit to the code that would change its output. The `kill` column is the receipt (`band`: 30 of 30 mutants caught); `c-eq` counts the survivors no input can distinguish, flagged rather than papered over. `quote` also has a proven **seam**: rerun with `--apply` and it is split into a named helper and a caller — a rewrite applied only because the suite proves behaviour survived it. The suite written *before* the split still passes after it; that green run is the proof, not a claim. And `receipt_id` is handed back instead of pinned: it reads the wall clock, so any golden test of its output is green now and red a second later. Uroboros refuses to write a test that rots, and names the remedy.

What lands on disk is ordinary pytest, no runtime dependency on Uroboros:

```python
# tests/detective/test_billing_quote_synth.py
@pytest.mark.detective
def test_quote_value_1():
    """VALUE survivor — distinguishing witness (equivalence search) (confidence 0.95)."""
    result = quote(units=501, rate=1.0, discount=0.0)
    assert result == 47595
```

The functions that most need cleaning are the ones nobody touches, because the only safety net — the tests someone thought to write — checks a handful of cases, and everyone knows it. A mutation-complete suite is a different object: it fails on any change to what the function computes. That turns cleanup from a bet into a mechanical operation, and it turns the suite into a behavioural lockfile — you, a colleague, or a model can rewrite the function freely, and green means identical.

## The model never drives

You can leave Uroboros running on your source because the part that could go wrong has no room to. The loop is deterministic; a small local model is woken at exactly one step — when reaching a branch needs one specific input value — and it does nothing else. It never writes code, never chooses what runs next, never sees your source as something to rewrite. It selects values into a per-function JSON schema mined from the branches themselves, with arity and types structurally locked; the harness writes the call syntax. There is no driver's seat for it to climb into.

That one job sits on a difficulty ladder, which is why a 4B model on a laptop is enough — it beat a 36B on this task, per token:

| tier | the input needed | supplied by |
|---|---|---|
| **1 · script** | a distinguishing call the engine already found | pasted — no model |
| **2 · any dolt** | reach an un-executed line | the small model |
| **3 · complex-but-obvious** | land on a *proved* boundary edge | the small model |
| **4 · purposivistic** | a domain object, or a value only you know | **you** — it abstains and hands it back |

## What comes back

Everything is pinned or refactored, except a short review list — each entry a *different* kind of decision, never blurred:

| bucket | meaning | your move |
|---|---|---|
| **pinned / refactored** | behaviour proven-preserved, minimal suite written | nothing |
| **needs a FIXTURE** (impure) | reads the clock / filesystem / env — no pin here holds | supply `--clock`, a tmp fixture, or write it by hand |
| **needs INPUT** | it needs a captured object or a value only you know | hand it one real call |
| **unclosed pure gap** | a real branch no tried input reached | glance at the one line |
| *(candidate-equivalent)* | changes no input can distinguish — the honest end state | optional `detective flag` |

## Run it

Not yet on PyPI — install from source:

```bash
pip install git+https://github.com/rohanvinaik/Uroboros.git   # pulls the engine (detective-spec, over Wesker)
uroboros --check                                              # verify the engine + Ollama + a model
ollama serve &                                                # only needed for the model step
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

| Command | Writes | Does |
|---|---|---|
| `uroboros file.py::fn` | test files | clean and pin one function or method |
| `uroboros src/` | test files | crawl a whole tree, one function to completion, then the next |
| `uroboros --diff [BASE]` | test files | crawl only what changed since BASE (default HEAD) — churn before a push |
| `uroboros src/ --apply` | your source | also apply the splits it can *prove* behaviour-preserving |
| `uroboros file.py --no-model` | test files | deterministic only — leave gaps that need an input unclosed |
| `uroboros --check` | nothing | dependency preflight and exit |

Without `--apply`, source is never rewritten — Uroboros writes tests and reports the splits it would make. Requires Python 3.11+. Absent Ollama or a pulled model, the crawl runs deterministically and leaves input-needing gaps unclosed.

## Where it stops

One function at a time, deterministic, narrow on purpose.

- **The unit is one function (or method).** It cleans functions; it never builds a whole-repo profile. It traverses a codebase one function to completion, then the next — not a fleet of concurrent workers (a deliberate choice; see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)).
- **It preserves behaviour, not correctness.** A proof says the rewrite does what the original did. If the original was wrong, the rewrite is wrong the same way — provably. It does not know what your code is *for*.
- **It will not invent a domain value.** When a branch needs an object or a value whose meaning is not in the code, it hands it back (the `needs-input` bucket) rather than guess.
- **A method needing a constructed receiver** is that same boundary — it surfaces as `needs-input` / `unclosed` rather than pinned ([#1](https://github.com/rohanvinaik/Uroboros/issues/1)).

Orientation for a contributor: [`AGENTS.md`](AGENTS.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

**Uroboros** — the serpent eating its own tail. It grinds the same loop over its own output until what survives is clean. The engine underneath is [Detective](https://github.com/rohanvinaik/Detective), over [Wesker](https://github.com/rohanvinaik/Wesker). MIT — Rohan Vinaik.
