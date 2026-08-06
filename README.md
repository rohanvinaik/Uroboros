# Uroboros

**Refactor a Python codebase without betting you didn't break it.**

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-3367d6.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-3367d6.svg" alt="Python 3.11+"></a>
</p>

Point Uroboros at a module. It extracts a helper and writes the suite that holds the result in place:

```diff
- def settle(lines, tax_rate, cap):
-     total = 0
-     for amount in lines:
-         total += amount
-     total += int(total * tax_rate)
-     if total > cap: total = cap
-     if total < 0:   total = 0
-     return total
+ def _compute_total(lines, tax_rate, cap):
+     total = 0
+     for amount in lines:
+         total += amount
+     total += int(total * tax_rate)
+     if total > cap: total = cap
+     if total < 0:   total = 0
+     return total
+
+ def settle(lines, tax_rate, cap):
+     return _compute_total(lines, tax_rate, cap)
```

```python
# Uroboros wrote this. Change what settle computes, and it fails.
def test_settle_value_0():
    assert settle(lines=[1], tax_rate=1.0625, cap=1) == 1
```

The functions that most need cleaning up are the ones nobody touches. Untangling them is not the hard part; proving the cleanup changed nothing is. A passing test suite checks the cases somebody thought to write, so a refactor is a bet that the untested behavior held — and the safe move is to leave the mess in place. Uroboros removes the bet. It works through a codebase one function at a time, unattended: it splits tangled functions into helpers, and for each function it writes the smallest suite that pins the current behavior to mutation-testing completeness — a suite that fails on any change to the code. The split above was applied only because that suite proved it preserved behavior: seven of `settle`'s seven mutants are caught. Pin a function that tightly and you can rewrite it freely; a green suite is the proof you did not change it. The tests are ordinary pytest, with no runtime dependency on Uroboros.

## Installation

```bash
pip install git+https://github.com/rohanvinaik/Uroboros.git
```

Uroboros requires Python 3.11+. The input step runs a small local model through [Ollama](https://ollama.com); `uroboros --check` verifies the dependencies.

## Usage

| Command | Effect |
|---|---|
| `uroboros file.py::function` | pin one function or method |
| `uroboros src/` | pin every function in a directory |
| `uroboros src/ --apply` | apply the splits it proves behavior-preserving (rewrites source) |
| `uroboros --diff [ref]` | pin only the functions changed since `ref` (default `HEAD`) |
| `uroboros file.py --no-model` | run deterministically, without the model step |
| `uroboros --check` | verify dependencies and exit |

Without `--apply`, Uroboros writes tests and reports the splits it would make; source is untouched.

## How it works

Uroboros drives [Detective](https://github.com/rohanvinaik/Detective) one function at a time. For each function it writes the pytest suite that kills every mutant — every small change to the code that would alter its output — and, with `--apply`, extracts helpers at seams Detective proves behavior-preserving.

The loop is deterministic. A small local model enters at one step: when reaching a branch requires a specific input value, the model supplies that value and nothing else. It does not choose what runs, and it does not edit code, which is what makes it safe to leave running on your source.

## What it reports

Most functions are pinned or refactored without intervention. The rest are grouped by the decision they need:

| Result | Meaning | Your move |
|---|---|---|
| Pinned / refactored | Behavior pinned, minimal suite written | None |
| Needs a fixture | Reads the clock, filesystem, or environment; no test pins it reliably | Supply `--clock`, a fixture, or a hand-written test |
| Needs input | A branch needs an object or value not derivable from the code | Provide one example call |
| Unclosed | A branch no generated input reached | Review the line |

## Limitations

- Uroboros works one function or method at a time. It does not run concurrent workers across a codebase.
- It preserves behavior, not correctness. A wrong function is refactored into a function that is wrong the same way.
- It does not build domain objects. A function that needs a constructed instance or a caller-specific value is reported for review, not pinned.

## License

MIT © Rohan Vinaik. Built on [Detective](https://github.com/rohanvinaik/Detective) and [Wesker](https://github.com/rohanvinaik/Wesker).
