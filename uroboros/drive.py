"""Detective I/O — the thin layer the cycle drives Detective through.

Two functions, no model, no control flow: `det` runs one Detective subcommand with
`--json` and returns (parsed state, error), and `enumerate_targets` lists the functions
to crawl. Everything that decides WHAT to run next lives in `cycle.py`; this file only
knows how to speak to the CLI and how to find work.
"""
import ast
import json
import subprocess
from pathlib import Path

CONVERGE_WALL = 240  # per-call subprocess cap; a hit is an engine limit, recorded not hung


def det(cmd, target, root, *extra, wall=CONVERGE_WALL):
    """Run one detective subcommand with --json. Returns (parsed, error_str).

    An empty target is omitted — `regime` resolves the whole repo with no target.
    """
    argv = ["detective", cmd, *( [str(target)] if target else [] ),
            "--project-root", str(root), "--json", *extra]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=wall)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, (proc.stderr or proc.stdout or "no-json").strip()[:160]


def enumerate_targets(path: Path, root: Path):
    """Top-level functions in a .py file, or across every .py under a dir.

    Skips dunder and test files — the crawl refactors source, not its own tests.
    Yields 'relpath::func' strings in source order (a stable, cheap-first-ish crawl).
    """
    files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    for f in files:
        if f.name.startswith("test_") or f.name == "conftest.py":
            continue
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = f.relative_to(root)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                yield f"{rel}::{node.name}"

