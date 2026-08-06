# Uroboros

**Point it at a Python codebase and walk away — it untangles the functions and locks each one's behaviour under a test suite that catches any change to the code.**

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-3367d6.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-3367d6.svg" alt="Python 3.11+"></a>
</p>

`Set-and-forget · CPU-only · the model never drives`

Uroboros cleans code and proves it stayed the same. It walks a codebase function by function: it splits tangled functions into named helpers, and for each function it writes the minimal test suite that pins what the function does — pinned to *mutation completeness*, meaning the suite catches every small change to the code that would change its behaviour. Once a function is pinned that tightly you can rewrite it however you like, and a suite that stays green is your proof you did not change it. It runs unattended, on CPU.

```
$ uroboros billing.py --project-root .

crawl: 3 function(s) · model=qwen3 · decompose=report

function                         state               kill model  c-eq   seam
------------------------------------------------------------------------------
band                             pinned             29/29     0     0      -
quote                            pinned             19/25     0     6      -
receipt_id                       needs-fixture        3/3     0     0      -
------------------------------------------------------------------------------

✓ pinned / refactored:        2
⚠ needs a FIXTURE (impure):   1
     receipt_id               reads the clock via time.time() — try --clock <epoch>
· candidate-equivalent (Tier-2 flag queue): 6 mutant(s) across 1 fn
```

`band` and `quote` come back pinned — a minimal pytest written for each, every killable mutant caught. `receipt_id` reads the wall clock, so any test pinning its output is green now, red a second later; Uroboros hands it back rather than write a test that rots. What lands is ordinary pytest with no runtime dependency on Uroboros.

## The model never drives

You can leave Uroboros running on your source because the part that could go wrong has no room to. A small local model is woken at exactly one step — it supplies a single test input when the engine needs one specific value to reach a branch — and it does nothing else. It never writes code, never chooses what runs next, never sees your source as something to rewrite. It selects a value into a typed slot; the deterministic harness does everything else. There is no driver's seat for it to climb into.

That one job is a difficulty ladder, which is why a 4B model on a laptop is enough (it beat a 36B on this task, per token):

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

Without `--apply`, source is never rewritten — Uroboros writes tests and reports the splits it would make. Requires Python 3.11+ and `detective-spec >= 0.10.2` (installed for you). The model step needs Ollama running with a small model pulled (default `qwen3:4b-instruct`); absent, the crawl runs deterministically and leaves input-needing gaps unclosed.

## Where it stops

One function at a time, deterministic, narrow on purpose.

- **The unit is one function (or method).** It cleans functions; it never builds a whole-repo profile. It traverses a codebase one function to completion, then the next — not a fleet of concurrent workers (a deliberate choice; see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)).
- **It preserves behaviour, not correctness.** A proof says the rewrite does what the original did. If the original was wrong, the rewrite is wrong the same way — provably. It does not know what your code is *for*.
- **It will not invent a domain value.** When a branch needs an object or a value whose meaning is not in the code, it hands it back (the `needs-input` bucket) rather than guess.
- **A method needing a constructed receiver** is that same boundary — it surfaces as `needs-input` / `unclosed` rather than pinned ([#1](https://github.com/rohanvinaik/Uroboros/issues/1)).

Orientation for a contributor: [`AGENTS.md`](AGENTS.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

**Uroboros** — the serpent eating its own tail. It grinds the same loop over its own output until what survives is clean. The engine underneath is [Detective](https://github.com/rohanvinaik/Detective), over [Wesker](https://github.com/rohanvinaik/Wesker). MIT — Rohan Vinaik.
