"""Preflight — verify the serpent can eat.

Uroboros has no intelligence of its own. It borrows three things and checks all three
are present before it starts to cycle:
  - Detective (the CLI it drives) + Wesker (the mutation engine underneath) — the HARD
    requirement; without them there is nothing to drive.
  - Ollama + a small local model — needed ONLY for the one input-synthesis call. Absent,
    Uroboros still runs deterministically (`--no-model`) and leaves pure gaps unclosed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request

OLLAMA = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"


def _detective() -> tuple[bool, str]:
    if not shutil.which("detective"):
        return False, "not on PATH — `pip install detective-spec`"
    try:
        out = subprocess.run(["detective", "--version"], capture_output=True, text=True, timeout=10)
        return True, out.stdout.strip() or "installed"
    except Exception as e:  # noqa: BLE001
        return False, f"present but not runnable: {e}"


def _ollama() -> tuple[bool, str]:
    try:
        urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=2).read()
        return True, "reachable"
    except Exception:  # noqa: BLE001
        return False, "not reachable — `ollama serve` (only needed for the model step)"


def _model(name: str) -> tuple[bool, str]:
    try:
        tags = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=2).read())
        names = [m.get("name", "") for m in tags.get("models", [])]
        if any(name == n or n.startswith(name) for n in names):
            return True, name
        return False, f"{name} not pulled — `ollama pull {name}` (or pass --model one you have)"
    except Exception:  # noqa: BLE001
        return False, "ollama unreachable"


def check(model: str = DEFAULT_MODEL) -> dict:
    det = _detective()
    oll = _ollama()
    mod = _model(model) if oll[0] else (False, "ollama down")
    return {"detective/wesker": det, "ollama": oll, "model": mod}


def report(model: str = DEFAULT_MODEL) -> bool:
    """Print the preflight table. Returns True iff the HARD requirement (Detective) is met."""
    r = check(model)
    print("uroboros preflight:")
    for k, (ok, msg) in r.items():
        print(f"  {'✓' if ok else '✗'} {k:<16} {msg}")
    if not r["detective/wesker"][0]:
        print("\n✗ Detective is required. Install it, then re-run.")
        return False
    if not r["model"][0]:
        print("\n· model step unavailable — Uroboros will run deterministically (--no-model equivalent).")
    return True
