"""Detective I/O — the thin layer the cycle drives Detective through.

Two functions, no model, no control flow: `det` runs one Detective subcommand with
`--json` and returns (parsed state, error), and `enumerate_targets` lists the functions
to crawl. Everything that decides WHAT to run next lives in `cycle.py`; this file only
knows how to speak to the CLI and how to find work.
"""
import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

CONVERGE_WALL = 240  # per-call subprocess cap; a hit is an engine limit, recorded not hung


# ── engine runtime resolution: run the engine WHERE the project's deps live ──────────
# A globally-installed Uroboros runs Detective in its OWN interpreter (miniconda), which
# cannot import the target repo's dependencies (its `chess`, its `numpy`) when those live
# in the repo's own venv. The symptom is systematic: the real test suite fails to collect,
# and every function reads as unpinned — a red baseline masquerading as "no tests reach it".
# Cross-injecting the repo's site-packages into the engine's interpreter is NOT a fix:
# Python versions differ across repos (a 3.10 venv's compiled numpy cannot load in 3.14),
# so C-extensions are version-locked. The only robust bridge is to run the engine in an
# interpreter that has BOTH the engine and the project's deps.

_DEGRADE_NOTE = (
    "⚠ {root}\n"
    "  isolates its dependencies (a .venv the engine isn't installed in) and `uv` isn't on\n"
    "  PATH — so the engine runs in its OWN environment and can't import this project's deps.\n"
    "  Its real test suite won't collect and functions may read as unpinned below. To fix:\n"
    "  install uv (https://docs.astral.sh/uv/), or `pip install uroboros-refactor` INTO this\n"
    "  repo's venv and run it from there. Crawling what's reachable meanwhile."
)


def _engine_prefix(root, uv, active, venv, isolated):
    """Pure decision: how to invoke the engine, given the detected runtime facts.

    Returns `(argv_prefix, cwd, degraded)` — the tokens that go BEFORE the subcommand,
    the working dir the subprocess needs (only the uv bridge cares — uv discovers the
    project from cwd), and whether we had to fall back to a blind global run.

    The ladder, most-specific first — uv is an accelerant, never load-bearing, so a fresh
    clone on a machine that has never heard of uv still resolves correctly:

    1. `active`  — the engine sits in an activated venv ($VIRTUAL_ENV): honor it outright.
    2. `venv`    — the engine is installed in the repo's own `.venv`: run it there. This is
                   the universal path — someone who `pip install`ed Uroboros into the repo's
                   env gets the deps AND the engine with zero bridging, any Python, no uv.
    3. isolated + uv — the repo isolates deps and the engine is NOT in that env, but uv can
                   bridge: `uv run --with detective-spec` layers the engine's whole closure
                   (Detective, Wesker, pytest) ONTO the repo's env ephemerally, so the repo's
                   deps and the engine coexist without touching the repo's `.venv`.
    4. isolated, no uv — nothing can reach the repo's env: run global and DEGRADE (flag it,
                   keep crawling what is reachable). A blind number is worse than a named gap.
    5. not isolated — no separate env to miss: the global engine is as good as any.
    """
    if active:
        return [active], None, False
    if venv:
        return [venv], None, False
    if isolated and uv:
        return [uv, "run", "--no-sync", "--with", "detective-spec", "detective"], root, False
    if isolated:
        return ["detective"], None, True
    return ["detective"], None, False


_ENGINE_CACHE: dict = {}


def _resolve_engine(root):
    """Impure shell over `_engine_prefix`: probe the runtime ($VIRTUAL_ENV, the repo's
    `.venv`, `which uv`, the isolation markers), decide, and CACHE per root — the hundreds
    of per-function det() calls must not re-stat the tree or reprint the degrade note. The
    note prints once, on the first (cache-miss) resolution, exactly when we fall to case 4.
    """
    key = str(root)
    if key in _ENGINE_CACHE:
        return _ENGINE_CACHE[key]
    rp = Path(root)
    active_dir = os.environ.get("VIRTUAL_ENV")
    active = str(Path(active_dir) / "bin" / "detective") \
        if active_dir and (Path(active_dir) / "bin" / "detective").exists() else None
    venv = str(rp / ".venv" / "bin" / "detective") if (rp / ".venv" / "bin" / "detective").exists() else None
    uv = shutil.which("uv")
    isolated = (rp / ".venv").exists() or (rp / "uv.lock").exists() or (rp / "environment.yml").exists()
    prefix, cwd, degraded = _engine_prefix(key, uv, active, venv, isolated)
    if degraded:
        print(_DEGRADE_NOTE.format(root=key))
    _ENGINE_CACHE[key] = (prefix, cwd)
    return prefix, cwd


def det(cmd, target, root, *extra, wall=CONVERGE_WALL, stream=False):
    """Run one detective subcommand with --json. Returns (parsed, error_str).

    An empty target is omitted — `regime` resolves the whole repo with no target.

    The engine is invoked through `_resolve_engine`, which runs it in an interpreter that
    can import the TARGET repo's dependencies (see the ladder above) — not blindly in
    Uroboros's own install env, where the repo's suite would fail to collect and every
    function would read as unpinned.

    With `stream=True`, Detective's STDERR is inherited, not captured — its live
    per-mutant heartbeat (`… fn: 29/29 mutants · 570/s · done`, the `▸ pass` lines)
    flows straight to the terminal, so a long crawl is never silent. Detective puts
    progress on stderr and the `--json` result on stdout, so only stdout is captured
    and the JSON contract is untouched. `stream=False` captures both, for the callers
    that need the stderr text back as an error string (e.g. regime's refusal).
    """
    prefix, cwd = _resolve_engine(root)
    argv = [*prefix, cmd, *( [str(target)] if target else [] ),
            "--project-root", str(root), "--json", *extra]
    capture = {"stdout": subprocess.PIPE} if stream else {"capture_output": True}
    # Silence the engine's own Python warnings — Wesker still reaches into numpy's deprecated
    # `numpy.core`, which fires a DeprecationWarning per mutant probe and, now that stderr is
    # streamed for the heartbeat, would drown the progress feed. The heartbeat is a print, not a
    # warning, so it survives.
    env = {**os.environ, "PYTHONWARNINGS": "ignore"}
    try:
        proc = subprocess.run(argv, text=True, timeout=wall, env=env, cwd=cwd, **capture)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, (proc.stderr or proc.stdout or "no-json").strip()[:160]


# Directory names that are never your source: virtualenvs, VCS, caches, build/packaging
# trees. Without this filter, `uroboros .` rglobs into `.venv` and pins the whole of
# site-packages — 3000+ functions of Detective/Wesker internals — which is never what
# "crawl this repo" means. Any hidden dir (a leading dot) is excluded too.
_SKIP_DIRS = {
    "venv", "env", "node_modules", "site-packages", "site-packages64",
    "build", "dist", "__pycache__", ".tox", ".nox", "vendor",
}

# Top-level dirs that are packages (have __init__.py) but are never the LIBRARY: tests,
# docs, examples, benchmarks, tooling, data. Excluded from source-root selection so a
# flat-layout repo resolves to its actual code, not its tests/ or benchmarks/ tree.
_AUX_DIRS = {
    "tests", "test", "testing", "docs", "doc", "examples", "example", "samples",
    "benchmarks", "benchmark", "bench", "notebooks", "data", "devtools", "scripts",
    "e2e", "integration", "fixtures",
}


def _is_source(f: Path, root: Path) -> bool:
    """True for a .py file that is the project's own source — not inside a virtualenv,
    VCS, cache, build, or vendored tree. Filters on the directory components between
    `root` and the file; a single explicit file target bypasses this entirely."""
    parts = f.relative_to(root).parts[:-1]   # directory components only, not the filename
    return not any(p.startswith(".") or p in _SKIP_DIRS or p.endswith(".egg-info") for p in parts)


def _source_roots(path: Path) -> list[Path]:
    """The directories that hold the project's OWN source, given a repo or dir —
    what you would point Detective at, not the whole tree beside it.

    * `path` is itself a package (has `__init__.py`) → crawl it directly.
    * src-layout (a `src/` dir) → `src/`.
    * flat-layout → the top-level packages (dirs with `__init__.py`), skipping hidden
      and non-source dirs.
    * otherwise → the dir as-is (a loose-script repo).

    This is why `uroboros .` on a real repo crawls the code and not `data/`, generated
    environment files, `quarantine/`, or a vendored tree sitting next to it.
    """
    if (path / "__init__.py").exists():
        return [path]
    if (path / "src").is_dir():
        return [path / "src"]
    pkgs = [d for d in sorted(path.iterdir())
            if d.is_dir() and (d / "__init__.py").exists()
            and not d.name.startswith(".") and d.name not in _SKIP_DIRS
            and d.name not in _AUX_DIRS]
    return pkgs or [path]


def enumerate_targets(path: Path, root: Path):
    """The module-level functions to crawl — the project's OWN source, as
    'relpath::func' strings in source order.

    Pointed at a directory, it resolves the source roots (`_source_roots`: package /
    src-layout / flat packages / fallback — robust across normal repo shapes) and
    rglobs those, skipping virtualenv / VCS / cache / build trees (`_is_source`) and
    test files. So `uroboros .` crawls your code, never the data/, generated envs, or
    vendored trees beside it. An explicit file target bypasses the resolver.

    METHODS ARE SKIPPED. Detective cannot yet build a receiver for a bound method
    (Detective #25), so a method target comes back unpinnable — pure grind on a
    class-heavy tree. Module-level functions only until that gap closes.
    """
    if path.is_file():
        files = [path]
    else:
        files = sorted(f for r in _source_roots(path) for f in r.rglob("*.py") if _is_source(f, root))
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

