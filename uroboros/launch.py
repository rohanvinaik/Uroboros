#!/usr/bin/env python3
"""The launcher — constrained output, the same discipline as the rest of Uroboros.

A thin interactive loop. Each turn, the user's message goes to a small local model
whose output is CONSTRAINED to a typed schema (Ollama `format=<schema>`, `think:false`):
it writes a prose `reply` and SELECTS two booleans — `run_uroboros` (run it at all) and
`apply` (rewrite source, or just report). That is the whole of its agency. It has no shell,
no file access, no tool calls — so it cannot wander off and start editing your source, which
is what a free-form agent loop lets a 4B model do.

When the model selects `run_uroboros`, the HARNESS (not the model) runs Uroboros over the
current repo and streams the crawl live. The DEFAULT is report-mode (`uroboros .`: writes
tests, reports seams, never touches source); `--apply` is added ONLY when the model set
`apply` from an EXPLICIT rewrite request — a hands-off launcher must not rewrite your source
on a bare "run it". The model decides *whether* and *report-vs-apply*; it never composes the
command, and it never drives. Same law as `synth.synthesize_inputs`, one layer up.
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

# The typed skeleton. The model fills exactly these three slots — a prose reply and two
# boolean decisions — and can emit nothing else (additionalProperties:false, all required).
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "run_uroboros": {"type": "boolean"},
        "apply": {"type": "boolean"},
    },
    "required": ["reply", "run_uroboros", "apply"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are the launcher for Uroboros, a tool that pins every function's behaviour under a "
    "generated test suite and reports the seams it could split. Reply to the user in `reply`, "
    "briefly. Set `run_uroboros` to true ONLY when the user is asking to run it over the current "
    "codebase — they say \"uroboros\", \"run it\", \"check this\", \"clean this up\". "
    "Set `apply` to true ONLY when the user EXPLICITLY asks to rewrite the source in place — "
    "\"apply the splits\", \"rewrite it\", \"--apply\". A plain run request is report-only "
    "(apply=false): it writes tests and reports seams but never edits source. When unsure, "
    "apply=false. You never run anything yourself; the harness does."
)


def decide(history: list[dict], model: str = MODEL) -> tuple[str, bool, bool]:
    """One constrained model call. Returns (reply, run_uroboros, apply). The model cannot
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
        return str(d.get("reply", "")), bool(d.get("run_uroboros")), bool(d.get("apply"))
    except (json.JSONDecodeError, AttributeError):
        return content, False, False


def _launch_argv(root, apply: bool) -> list[str]:
    """Pure: the exact command the harness runs. Report by default (`uroboros .` — writes
    tests, reports seams, never touches source); the source-rewriting `--apply` is present
    ONLY when the model set `apply` from an explicit ask. The model flips the flag; it never
    composes the command — so a bare "run uroboros" can never rewrite your source."""
    apply_flag = ["--apply"] if apply else []
    return ["uroboros", ".", *apply_flag, "--project-root", str(root)]


def run_uroboros(root: Path, apply: bool = False) -> int:
    """The harness runs the ONE command `_launch_argv` composes and streams its progress
    live. The model never composes this — it only selects report-vs-apply."""
    argv = _launch_argv(root, apply)
    print(f"\n── uroboros .{' --apply' if apply else ''} · {root} ──\n", flush=True)
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(root),
    )
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    return proc.wait()


def main() -> None:
    root = Path.cwd()
    model = sys.argv[1] if len(sys.argv) > 1 else MODEL
    history = [{"role": "system", "content": SYSTEM}]
    print(f"uroboros launcher · {model} · {root}\n"
          "(say “run uroboros” to crawl this repo — report only; ask to “apply” to rewrite source; Ctrl-D to exit)")
    while True:
        try:
            msg = input("\n› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        history.append({"role": "user", "content": msg})
        reply, model_run, model_apply = decide(history, model)
        print(reply)
        history.append({"role": "assistant", "content": reply})
        # The literal trigger word is a DETERMINISTIC override for RUNNING, and it always runs
        # REPORT-mode — the safe default. Source-rewriting `--apply` never rides on the trigger
        # word or a 4B model's loose judgment of a bare "run it"; it requires the model's explicit
        # `apply`, which the schema/system prompt reserve for an EXPLICIT rewrite request.
        if model_run or TRIGGER in msg.lower():
            run_uroboros(root, apply=model_apply)


if __name__ == "__main__":
    main()
