# Uroboros

**Spend a few minutes telling a chat model what your code must do and what it must never do. A model small enough to fit on a phone then writes it, and it comes back mutant-complete: every behaviour pinned by a test that fails the moment that behaviour changes.**

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/rohanvinaik/Uroboros?style=flat-square&amp;color=5b6abf" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12%2B-3776ab?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.12+"></a>
</p>

`Mutant-complete · 4B local model · The model never drives`

Mutant-complete specification is a standard almost no code is held to. It means the test suite tells the code apart from every non-equivalent mutant [Wesker](https://github.com/rohanvinaik/Wesker) can make of it. Flip an operator, move a boundary, swap two arguments or delete a statement, and some test goes red. That is stricter than any coverage gate, and stricter than MC/DC, the bar aviation software is certified against. No sane engineer spends that effort on a billing helper, and almost no team asks them to.

Uroboros makes it the default. A short conversation with a chat model turns an idea into a contract: the function signatures, the order they depend on each other, tests of what each must do and must never do, and typed holes where the implementation goes. Then the cycle runs on hardware you own, without you. A 4B local model fills the holes. Wesker mutates the result. [Detective](https://github.com/rohanvinaik/Detective) hunts down every mutant the tests let through and turns each one into a new test. Code that can't survive goes back to be rewritten. What comes out is ordinary Python, an ordinary pytest suite and a certificate stating exactly what that suite rules out.

---

## Six bytes where there should be zero

The brief was a paragraph about image buffers. Each row occupies the smallest multiple of the requested alignment that can hold it. A nine-byte row aligned to eight occupies sixteen, and an empty row occupies zero. The arithmetic has to hold for byte counts too large for a float to represent exactly.

A fresh chat session read this repository and turned that paragraph into a checked contract in about two minutes. It produced two signatures, the dependency between them, intent tests of both signs and three typed holes: the expressions where the arithmetic goes. Then the chat session's part was over. Everything after that ran locally, on Qwen3.5 4B.

The first answer was wrong:

```python
# aligned_bytes(row_bytes, alignment), first fill
return -1 % alignment + row_bytes
```

It is a well-formed expression, and it even gets the brief's own example right: nine bytes at alignment eight comes out to sixteen. It also allocated six bytes for an empty row. The intent test for the zero-byte row went red, and Detective named it. The harness reopened that one hole and handed the model its rejected code along with the failing test. The model's next answer was:

```python
# the same hole, reissued with the evidence against the first answer
return -row_bytes % alignment + row_bytes
```

Then Detective ran over both functions and wrote a distinguishing test for each surviving mutant. The finished suites killed **14 of 14** alignment mutants and **15 of 15** buffer-size mutants. The target repository's own gate passed, and Uroboros committed the code and its certificates. An acceptance check written separately from the contract, and never shown to the cycle, then passed **8,406 of 8,406** cases.

The first time this ran, the model repeated its rejected answer and the run stopped. That failure is why a reissue now carries the rejected code and Detective's evidence against it. On a full rerun from the same contract, the model fixed the arithmetic in one attempt.

---

## Wesker, without his hand

Wesker is the intelligence in this system. It decides what *correct* means by breaking things. Every mutant asks one question: could the code do this instead? The specification is whatever survives all of them. Detective is how those questions get answered. It derives the input that separates the code from each mutant, writes the test, confirms the test kills the mutant it was written for, and names what it cannot know.

Until now, all of that ran by hand. Detective is a command you drive one function at a time, and someone still had to write the function first. These were Wesker's experiments, one subject at a time.

Uroboros is the version that runs without him. Release it once with a contract and it feeds on its own output:

1. The local model fills each open hole, in dependency order.
2. Wesker generates the mutants of what it wrote.
3. Detective measures them, writes a test for every survivor it can distinguish, and records the ones it can't.
4. Detective names the next move. Wrong code reopens its hole, and the rejected implementation goes back to the model with the evidence against it. A contradiction in the contract goes up to whoever wrote it.
5. The new code goes back to step 2.

Each round's tests become part of the next round's specification. Each rejected implementation, with the evidence against it, becomes part of the next attempt's context. Nobody steers. The cycle ends when nothing survives that shouldn't, or when it reaches a question only a person can answer. In that case it hands the question back, typed, with the evidence attached.

The first Uroboros ran the same cycle in the other direction. Point it at existing Python and it pins every function, splitting tangled ones only where Detective proves behaviour is preserved. It still ships as `uroboros-crawl` ([docs](docs/LEGACY_CRAWLER.md)).

---

## The model never drives

A 4B model is enough because it is never trusted with anything that matters.

The contract fixes whatever the intent already settles: signatures, control flow and dependencies. It leaves typed holes for the rest. Here is everything the billing example leaves open in `excess_units`:

```json
"code":   [{"_type": "Return", "value": {"$slot": "result"}}],
"holes":  {"result": "expr"},
"syntax": {"expr": ["Name", "Constant", "BinOp", "Compare", "IfExp", "Call"],
           "operator": ["Sub"], "cmpop": ["Gt", "GtE", "Lt", "LtE"]}
```

The model writes one expression. It may subtract, compare, branch and call, and nothing else. Each hole compiles to a schema that is enforced while the model is still generating. It cannot produce malformed syntax, a statement where an expression belongs, or an operator the contract left out. The harness checks the returned syntax tree again before rendering a line of Python.

The model never edits a test and never grades its own work. It answers three kinds of question: fill this hole, supply a value Detective asked for, and judge whether a hand-back is a code problem or a contract problem. Every answer is checked downstream, either by what Detective then measures or by the contract's author. A wrong fill costs one measurement. It never produces a wrong record.

That is the inversion. The usual way to make a model's code trustworthy is a bigger model. Uroboros makes the guarantee independent of how capable the model is.

| Who | Holds |
|---|---|
| **You** | What the program should do, and which failures it must expose |
| **The contract author** (a chat model) | Signatures, dependencies, intent tests of both signs, and which holes stay open |
| **The local model** | The contents of the holes, a witness value when asked, and a judgment on a hand-back |
| **The harness** | Validating and rendering syntax trees, bounding repair, following Detective's typed decisions, and placing code in Git |
| **Wesker** | The mutation regime |
| **Detective** | The measurement, the tests, the verdict and the next move |

---

## What comes back

Every run ends in one of a few distinct states. They are never blurred together, because each calls for a different response:

| Ending | What it means | Your move |
|---|---|---|
| **certified** | Code, intent tests, Detective's tests and a certificate. With `--target`, committed after the repository's own gate passes | Read the receipt |
| **reissued** | Code was wrong. The hole was reopened with the rejected code and Detective's evidence | Nothing. It is automatic, up to `--ceiling` attempts per function (default 2) |
| **contract question** | The tests contradict the stated intent, or the contract can't express it | The contract author revises, and the cycle reruns from the revision |
| **stopped with evidence** | A repeated failure, unchanged evidence, an abstention, a second machine fault or the ceiling | Read the hand-back. The rejected implementation and its evidence are attached |

A certificate also says what it could not prove. In the billing example, Qwen3.5 4B wrote the excess-usage and metered-charge expressions. The module passed Git admission, and an independent oracle agreed with it on all **65,536** input combinations. The receipt still lists two *candidate-equivalent* mutants in the excess-usage helper: survivors for which Detective searched and found no distinguishing input. They sit in the certificate next to the kills, labelled for what they are, and are never counted as killed.

---

## Where it stops

- **It pins what you said, exactly.** The intent tests are your brief made executable. Detective's tests pin whatever the implementation actually does. Where the brief is silent, the model's choice gets pinned as written. If the brief was wrong, the code is wrong in exactly the way it specified.
- **Complete means complete within a stated regime.** Every verdict is measured against Wesker's versioned, first-order operator set, and the certificate names it. Mutant-complete means that and nothing wider.
- **Survivors nobody can distinguish stay unproven.** Whether a mutant is truly equivalent is undecidable. A failed search is recorded as a failed search.
- **It inherits Detective's boundaries.** Code that reads the clock, the filesystem or the environment is declined and the remedy is named. A value whose meaning lives in your head is asked for once, not guessed.
- **The evidence is still small.** Two modules, both built from expression holes. Statement, body and whole-function holes work in the schema but have no measured calibration yet. Reliability and review effort at project scale have not been measured.

---

## Run it

You need Python 3.12+, Git, [uv](https://docs.astral.sh/uv/) and a running [Ollama](https://ollama.com) server. Not yet on PyPI. Run from a checkout:

```bash
uv sync
ollama pull qwen3.5:4b
run_dir=$(mktemp -d)
uv run uroboros seat examples/usage-bill.form.json --out "$run_dir/contract.json"
uv run uroboros check "$run_dir/contract.json"
uv run uroboros line "$run_dir/contract.json" --out "$run_dir/run"
cat "$run_dir/run/line.json"
```

`check` prints the build order (`total_cents.excess_units`, then `total_cents`). `line` runs the whole cycle and prints its outcome. Start from `line.json` and follow it to the implementation, the tests and the certificate, or to the hand-back.

| Command | Writes | Does |
|---|---|---|
| `uroboros schema` | nothing | Prints the form a contract author fills in |
| `uroboros seat FORM --out CONTRACT` | contract | Seats the author's form, or refuses it and says why |
| `uroboros check CONTRACT` | nothing | Validates the contract and prints the call order |
| `uroboros request CONTRACT FN` | nothing | Shows the exact request one fill would send |
| `uroboros line CONTRACT --out RUN` | run directory | Runs the whole cycle. Add `--target REPO` to admit a certified unit into Git |
| `uroboros-admission admit PLAY --target REPO` | your repository | Admits a play you reviewed first |
| `uroboros-crawl PATH` | test files | Runs the original direction: pins existing code |

To write a contract from your own brief, give a chat model this repository and the [coding runbook](docs/CODING.md). The runbook also covers model profiles, reissues and admission. How decision records, syntax constraints and context budgets fit together is in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## What the cycle is for

Detective's README ends on a claim: coding stops being a craft and becomes an assembly line. Uroboros is that line, running.

Rigour has always been rationed: by seniority, by headcount, by which organisation could pay someone patient enough to test what happens at exactly fifty dollars. Uroboros removes the ration. The expertise lives in the cycle, the cycle runs on a model small enough to fit in a pocket, and it needs no one's hand to keep going. Anyone who can say what they want can now have it held to a standard almost no engineering team holds itself to.

That is not something anyone can take back.

---

*Wesker asks the question by breaking things. Detective writes down what can be known. Uroboros lets it loose.*

*MIT — Rohan Vinaik.*
