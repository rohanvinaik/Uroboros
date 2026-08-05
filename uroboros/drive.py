"""Detective I/O — the thin layer the cycle drives Detective through.

Two functions, no model, no control flow: `det` runs one Detective subcommand with
`--json` and returns (parsed state, error), and `enumerate_targets` lists the functions
to crawl. Everything that decides WHAT to run next lives in `cycle.py`; this file only
knows how to speak to the CLI and how to find work.
"""
import ast
import json
import re
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


# ── diff-mode: the pitched "churn before push" crawl set ──────────────────────
# Split so the PURE core is pinnable and the impurity is one thin shell: git +
# filesystem reads live only in `changed_targets`; the parse and the range→func
# map are pure functions of their arguments (Detective can pin them outright).

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _changed_line_ranges(diff_text: str) -> dict:
    """Parse `git diff` unified output into {relpath: [(start, end), ...]} on the
    NEW side — the lines that now exist after the change, which is what maps onto
    a current function. Pure: a function of the diff text alone.

    Only new-side spans are kept; a pure deletion (new-count 0) maps to no current
    line, so it is dropped — you cannot pin a function that the diff removed.
    """
    ranges: dict[str, list] = {}
    current = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            current = None if path == "/dev/null" else (path[2:] if path.startswith("b/") else path)
        elif current and line.startswith("@@"):
            m = _HUNK.match(line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count > 0:
                ranges.setdefault(current, []).append((start, start + count - 1))
    return ranges


def _functions_in_ranges(source: str, ranges: list) -> list:
    """Top-level function names whose line span intersects any changed range, in
    source order. Pure: AST over the source text, no I/O. Skips dunders — the
    crawl refactors named source, not `__init__`-style hooks.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
            lo, hi = node.lineno, (node.end_lineno or node.lineno)
            if any(lo <= end and start <= hi for start, end in ranges):
                out.append(node.name)
    return out


def changed_targets(root: Path, base: str = "HEAD"):
    """The changed-function crawl set: `git diff <base>` → the functions those
    hunks touch, as 'relpath::func'. The ONE impure shell — it runs git and reads
    files — over the two pure helpers above. Skips test files, like the file crawl.

    `base` defaults to HEAD (uncommitted work: staged + unstaged). Pass a ref like
    'main' to crawl everything a branch changed before a push.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "--unified=0", base, "--", "*.py"],
            capture_output=True, text=True, timeout=CONVERGE_WALL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    ranges = _changed_line_ranges(proc.stdout)
    for rel, spans in ranges.items():
        name = Path(rel).name
        if name.startswith("test_") or name == "conftest.py":
            continue
        src_path = root / rel
        try:
            source = src_path.read_text()
        except OSError:
            continue
        for func in _functions_in_ranges(source, spans):
            yield f"{rel}::{func}"

