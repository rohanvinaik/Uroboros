#!/usr/bin/env python3
"""The launcher — constrained output, the same discipline as the rest of Uroboros.

A thin interactive loop. Each turn, the user's message goes to a small local model
whose output is CONSTRAINED to a typed schema (Ollama `format=<schema>`, `think:false`):
it writes a prose `reply`, and it SELECTS one boolean, `run_uroboros`. That is the whole
of its agency. It has no shell, no file access, no tool calls — so it cannot wander off
and start editing your source, which is what a free-form agent loop lets a 4B model do.

When the model selects `run_uroboros`, the HARNESS (not the model) runs
`uroboros . --apply` over the current repo and streams the crawl's progress live. The
model decides *whether* to run the one fixed command; it never composes it, and it never
drives. Same law as `synth.synthesize_inputs`, one layer up.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:4b"
TRIGGER = "uroboros"   # the literal word is a deterministic run override — see main()

# The typed skeleton. The model fills exactly these two slots — a prose reply and one
# boolean decision — and can emit nothing else (additionalProperties:false, both required).
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "run_uroboros": {"type": "boolean"},
    },
    "required": ["reply", "run_uroboros"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are the launcher for Uroboros, a tool that refactors a Python codebase and pins "
    "every function's behaviour under a generated test suite. Reply to the user in `reply`, "
    "briefly. Set `run_uroboros` to true ONLY when the user is asking to run it over the "
    "current codebase — they say \"uroboros\", \"run it\", \"refactor this\", \"clean this up\". "
    "Otherwise set it false. You never run anything yourself; the harness does."
)


def decide(history: list[dict], model: str = MODEL) -> tuple[str, bool]:
    """One constrained model call. Returns (reply, run_uroboros). The model cannot
    return anything but a valid instance of DECISION_SCHEMA, so it cannot 'act'."""
    payload = json.dumps({
        "model": model,
        "messages": history,
        "stream": False,
        "format": DECISION_SCHEMA,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.3, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    raw = json.loads(urllib.request.urlopen(req, timeout=120).read())
    content = raw.get("message", {}).get("content", "")
    try:
        d = json.loads(content)
        return str(d.get("reply", "")), bool(d.get("run_uroboros"))
    except (json.JSONDecodeError, AttributeError):
        return content, False


def run_uroboros(root: Path) -> int:
    """The harness runs the ONE fixed command and streams its progress live. The model
    never composes this — the command never varies."""
    print(f"\n── uroboros . --apply · {root} ──\n", flush=True)
    proc = subprocess.Popen(
        ["uroboros", ".", "--apply", "--project-root", str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(root),
    )
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    return proc.wait()


def main() -> None:
    root = Path.cwd()
    model = sys.argv[1] if len(sys.argv) > 1 else MODEL
    history = [{"role": "system", "content": SYSTEM}]
    print(f"uroboros launcher · {model} · {root}\n(say “run uroboros” to refactor this repo; Ctrl-D to exit)")
    while True:
        try:
            msg = input("\n› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        history.append({"role": "user", "content": msg})
        reply, model_run = decide(history, model)
        print(reply)
        history.append({"role": "assistant", "content": reply})
        # The literal trigger word is a DETERMINISTIC override: the explicit ask
        # ("run uroboros") never rides on a 4B model's judgment. The model's boolean
        # only adds the paraphrases that omit the word ("clean up this repo").
        if model_run or TRIGGER in msg.lower():
            run_uroboros(root)


if __name__ == "__main__":
    main()
