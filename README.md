# Uroboros

**Point it at a codebase and walk away — it pins every function's behaviour and splits the tangled ones, rewriting nothing it cannot prove.**

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-3367d6.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-3367d6.svg" alt="Python 3.11+"></a>
</p>

`Set-and-forget · CPU-only · the model never drives`

Point a naive test-writer at `stamp_receipt` and it writes `assert stamp_receipt(1) == "1-1785957283"` — green now, red one second later. It pinned the wall clock, not the behaviour, and most generated-test tools will hand you a suite full of these. Uroboros catches it and declines to write the pin:

```
stamp_receipt        needs-fixture    reads the clock via time.time() — try --clock <epoch>
```

Catching that is the point.

---

## The cycle

Point Uroboros at a file, a directory, or a diff. Function by function (and method by method) it drives [Detective](https://github.com/rohanvinaik/Detective)'s deterministic loop — split tangled functions at proven seams, pin behaviour with a minimal generated suite, kill every killable mutant — and wakes a small local model only where Detective needs one real input to reach a branch. It runs unattended and leaves behind pinned behaviour, split functions, and a short list of decisions only you can make.

```
$ uroboros src/ --project-root . --apply

crawl: 41 function(s) · model=qwen3 · decompose=apply

function                         state               kill model  c-eq   seam
------------------------------------------------------------------------------
classify_discount                pinned             26/26     0     0      -
summarize                        pinned               5/5     0     0    yes
route                            pinned             19/19     1     0      -
stamp_receipt                    needs-fixture       7/13     0     6      -
...
------------------------------------------------------------------------------

✓ pinned / refactored:        38
⚠ needs a FIXTURE (impure):    1   stamp_receipt — try --clock <epoch>
⚠ needs INPUT (you supply):    1
· unclosed pure gap:           1   ['gate']
```

The suite it writes is ordinary pytest with no runtime dependency on Uroboros. Green pytest is the receipt; nothing reaches your source that a generated suite did not prove behaviour-preserving.

---

## The model never drives

Uroboros owns control flow; the model is a pure `(signature, requirement) → input` function behind the loop, constrained to *select* values into a typed schema. It never writes call syntax, never sees a shell, never decides what runs next — agency is the bug this design exists to prevent.

It is woken for one job: produce the `--input` Detective's own next-step asks for. That next-step is a difficulty ladder, which is why a small model suffices (a 4B instruct model beat a 36B on this task, per token):

| tier | the input needed | who supplies it |
|---|---|---|
| **1 · script** | a distinguishing call the engine already ran | pasted — no model |
| **2 · any dolt** | reach an un-executed line | the small model |
| **3 · complex-but-obvious** | land on a *proved* boundary edge | the small model |
| **4 · purposivistic** | a domain object, or a value only you know | **you** — Uroboros abstains and promotes |

See [`AGENTS.md`](AGENTS.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## What comes back

Everything is pinned or refactored, except the review buckets — each a *different* human ask, never blurred:

| bucket | meaning | your move |
|---|---|---|
| **pinned / refactored** | behaviour proven-preserved, minimal suite written | nothing |
| **needs a FIXTURE** (impure) | reads clock / filesystem / env — a pin here is *fragile* | supply `--clock`, a tmp fixture, or write it by hand |
| **needs INPUT** | the model can't reach it — it needs a captured object or a value only you know | hand it one real call |
| **unclosed pure gap** | a real branch no input the model tried reached | glance at the one line |
| *(candidate-equivalent)* | undecidable survivors — the honest end state | optional `detective flag` |

---

## Run it

Not yet on PyPI — install from source:

```bash
pip install git+https://github.com/rohanvinaik/Uroboros.git   # pulls detective-spec (which pulls Wesker)
uroboros --check                                              # verify Detective/Wesker + Ollama + a model
ollama serve &                                                # only needed for the model step
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

| Command | Writes | Does |
|---|---|---|
| `uroboros file.py::fn` | test files | pin one function or method (safe — tests, not source) |
| `uroboros src/` | test files | crawl a whole tree, one function to completion, then the next |
| `uroboros --diff [BASE]` | test files | crawl only what changed since BASE (default HEAD) — churn before a push |
| `uroboros src/ --apply` | your source | also apply the decompositions Detective *proves* behaviour-preserving |
| `uroboros file.py --no-model` | test files | deterministic only — leave pure gaps unclosed |
| `uroboros --check` | nothing | dependency preflight and exit |

Requires Python 3.11+ and `detective-spec >= 0.10.2` (installed for you). The model step also needs Ollama running with a small model pulled (default `qwen3:4b-instruct`); absent, the crawl runs deterministically and leaves pure gaps unclosed.

---

## Where it stops

One function at a time, deterministic, narrow on purpose.

- **The unit is one function (or method).** Uroboros crawls functions; it never computes a whole-repo mutant profile. It traverses a codebase one function to completion, then the next; it does not run a fleet of concurrent workers (a deliberate choice — see ARCHITECTURE stage 3).
- **It preserves behaviour, not correctness.** A proof says the rewrite does what the original did. If the original was wrong, the rewrite is wrong the same way — provably. It does not know what your code is *for*.
- **It will not invent a domain value.** When a branch needs an object or a value whose meaning is not in the code, it asks (the `needs-input` bucket) rather than guessing.
- **A method needing a constructed receiver** is that same boundary — it surfaces as `needs-input` / `unclosed` rather than pinned ([#1](https://github.com/rohanvinaik/Uroboros/issues/1), [Detective #25](https://github.com/rohanvinaik/Detective/issues/25)).

---

## The name

**Uroboros** — the serpent eating its own tail, the self-consuming cycle. It grinds the same loop over its own output until what survives is pure: mindless on its own, unstoppable when driven.

---

MIT — Rohan Vinaik. Drives [Detective](https://github.com/rohanvinaik/Detective) over [Wesker](https://github.com/rohanvinaik/Wesker).
