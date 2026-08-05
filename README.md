# Uroboros 🐍

**The serpent that eats convoluted code until only pure behaviour remains.**

`Set-and-forget · CPU-only · the model never drives`

---

## The name

What do you call a thing that cycles *endlessly* on tangled code — breaking it down, pinning
its behaviour, and ruthlessly executing unworthy mutants — until the function is as clean and
as close to perfect as mutation-pinning of its semantic degrees of freedom can make it?

A thing with **no active intelligence of its own**, but which — driven and guided by **Wesker**
and the rest — grinds the same [Detective](https://github.com/rohanvinaik/Detective) cycle over
and over until the code is *pure*?

**Uroboros.** The serpent eating its own tail — the self-consuming cycle. In the lore it is
Wesker's own project: the thing that consumes the unworthy and leaves only what survives. Here
it drives Wesker (the mutation engine) to do exactly that to your code. It completes the trilogy:

> **Detective** — the honest surface that refuses to claim what it can't prove.
> **Wesker** — the engine underneath, weaponizing mutants, inside the unit the whole time.
> **Uroboros** — the endless refinement Wesker unleashes, mindless on its own, unstoppable when driven.

---

## What it does

Point Uroboros at a Python file, a directory, or a diff before you push. Function by function it
drives Detective's deterministic loop — split tangled functions at *proven* seams, pin behaviour
with a minimal generated test suite, kill every killable mutant — and wakes a small local model
**only** where Detective genuinely needs one real input to reach a branch. You get coffee. You
come back to SICP-clean, MC/DC-verifiable code and a short list of decisions to clear in seconds.

The load-bearing rule: **the model never drives.** Uroboros owns control flow; the model is a
pure `(signature, requirement) → input` function behind the loop, so it can't overthink its way
into disaster. Agency is the bug. (See [`AGENTS.md`](AGENTS.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).)

## Quickstart

```bash
pip install -e .            # pulls detective-spec (which pulls Wesker)
uroboros --check            # verify Detective/Wesker + Ollama + a model are present
ollama serve &              # only needed for the model step
ollama pull qwen3:4b-instruct-2507-q4_K_M

uroboros path/to/file.py --project-root .            # report what it would do (safe)
uroboros src/ --project-root . --apply               # apply proven-safe decompositions
uroboros file.py --no-model                          # deterministic only, no model
```

## What comes back

Everything is pinned or refactored, except three review buckets for you:

| bucket | meaning | your move |
|---|---|---|
| **pinned / refactored** | behaviour proven-preserved, minimal suite written | nothing |
| **needs a FIXTURE (impure)** | reads the clock / filesystem / env — a pin here is *fragile* | supply `--clock`, a tmp fixture, or write it by hand |
| **unclosed pure gap** | a real branch the model couldn't reach an input for | glance at the one line |
| *(candidate-equivalent)* | undecidable survivors — done modulo the flag queue | optional `detective flag` |

The **needs-fixture** bucket is the professionalization win: a `def stamp(): return int(time.time())`
whose test is green now and red next second gets *caught*, and you're handed the remedy.

## Requires

- Python 3.11+, `detective-spec >= 0.10.2` (installed for you)
- Ollama + a small model (default `qwen3:4b-instruct` — the bake-off winner) — only for the model step

Details of the loop it drives and the architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Orientation for an agent arriving fresh: [`AGENTS.md`](AGENTS.md).
