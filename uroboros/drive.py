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


# ── verify the resolved env can actually import the code ──────────────────────────────
# The ladder picks the repo's own env when it isolates deps, but a `.venv` existing does not
# mean it's COMPLETE: it can be behind its lock, or a vestige of a repo really run under conda
# (a bare venv missing numpy/scipy). Then the engine imports nothing, the suite can't collect,
# and every function reads as unpinned — a silent, wrong "no tests reach it". So before the
# crawl, probe: can the chosen env import a real target file (running its top-level imports,
# i.e. its whole dependency chain)? If a NON-global engine can't, fall back to the global one
# so the crawl degrades loudly. General: it keys on an actual import failure, not on any repo
# convention (uv/conda/poetry/stale-venv all surface the same way).

_PROBE_FALLBACK_NOTE = (
    "⚠ the resolved environment for {root}\n"
    "  cannot import its own source ({file} → a missing dependency: {miss}). Its test suite\n"
    "  would fail to collect and functions would read as unpinned, so falling back to the global\n"
    "  engine for this crawl. If the repo's deps live in its venv, sync it (e.g. `uv sync`) and\n"
    "  re-run for full fidelity."
)


def _probe_python(prefix):
    """Pure: the python invocation parallel to an engine prefix — swap the trailing
    `detective` for `python`, so a probe runs in exactly the ENGINE's environment. Handles the
    three shapes the ladder emits: `[detective]` / `[uv, run, …, detective]` (last token is the
    literal `detective`) and a resolved `[…/bin/detective]` path."""
    last = prefix[-1]
    if last == "detective":
        return [*prefix[:-1], "python"]
    if last.endswith("/detective"):
        return [last.removesuffix("detective") + "python"]
    return [*prefix, "python"]


def _dotted_module(sample_file, roots):
    """Pure: (path entry, dotted module name) to import `sample_file` AS PART OF ITS PACKAGE —
    the entry is the deepest source root that CONTAINS the file (so a `src/` layout imports
    `pkg.mod`, not `src.pkg.mod`), the name is its path under that root with `/`→`.` and `.py`
    stripped. Importing by dotted name — rather than loading the file standalone — runs the
    package's `__init__`, resolves relative imports, and so exercises transitive deps (a bare
    `spec_from_file_location` load dies on the file's own `from .` before reaching them). None
    if no root contains the file."""
    f = Path(sample_file).resolve()
    best = None
    for r in roots:
        rp = Path(r).resolve()
        try:
            rel = f.relative_to(rp)
        except ValueError:
            continue
        depth = len(rp.parts)
        if best is None or depth > best[0]:
            best = (depth, str(rp), ".".join(rel.with_suffix("").parts))
    return (best[1], best[2]) if best else None


def _missing_import(prefix, cwd, sample_file, roots):
    """Impure: the module the engine's env is MISSING in order to import `sample_file`'s module
    (running its package `__init__` + top-level imports — the whole dependency chain), or None
    if it imports fine OR we can't tell (no containing root, a timeout, no python, or a non-import
    error) → never a spurious fall-back. Puts the source roots on `sys.path`, as the engine's
    regime does, then imports the module by dotted name. A miss that is the module's OWN top
    package is a layout/path mismatch, not a missing dependency, and is ignored (the global engine
    would fail it the same way); a miss of a real third-party dep (numpy, chess) triggers fallback."""
    target = _dotted_module(sample_file, roots)
    if target is None:
        return None
    _, dotted = target
    py = _probe_python(prefix)
    path_inject = ",".join(repr(str(r)) for r in roots)
    code = (
        "import importlib, sys\n"
        f"sys.path[:0]=[{path_inject}]\n"
        f"importlib.import_module({dotted!r})\n"
    )
    try:
        p = subprocess.run([*py, "-c", code], capture_output=True, text=True, timeout=60,
                           cwd=cwd, env={**os.environ, "PYTHONWARNINGS": "ignore"}, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode == 0:
        return None
    miss = None
    for ln in p.stderr.splitlines():
        if "ModuleNotFoundError" in ln and "'" in ln:
            miss = ln.split("'")[1]
    if miss is None or miss == dotted.split(".")[0]:
        return None
    return miss


def verify_engine(root, sample_file):
    """Probe the resolved engine against a real target file; if a NON-global engine can't
    import it (a stale/incomplete repo env), overwrite the cached resolution with the global
    engine and say so. Idempotent — call once at crawl start, after the targets are known."""
    key = str(root)
    prefix, cwd = _resolve_engine(root)
    if prefix == ["detective"]:            # already global — nothing better to fall back to
        return
    miss = _missing_import(prefix, cwd, sample_file, [*_source_roots(Path(root)), Path(root)])
    if miss is None:
        return
    print(_PROBE_FALLBACK_NOTE.format(root=key, file=sample_file, miss=miss))
    _ENGINE_CACHE[key] = (["detective"], None)


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


# ── crawl-integrity guard: a crawl must not silently mutate the tree ───────────────────
# Converge runs the repo's OWN suite to trace the baseline. If a test writes to the filesystem
# (a `data/` fixture it rewrites, a `session_universe/` dir it drops under the repo root), the
# crawl mutates the tree — neither the tests it means to write nor a decomposition it applied.
# Snapshotting git before/after surfaces those side effects instead of leaving them in the diff.

def _git_dirty(root) -> set:
    """Impure: the set of paths git reports dirty (modified or untracked) under `root`.
    Best-effort — an empty set when not a git repo or git is unavailable, so the guard never
    breaks a crawl; it only ADDS a warning when it can actually see the tree change."""
    try:
        p = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if p.returncode != 0:
        return set()
    paths = set()
    for ln in p.stdout.splitlines():
        # porcelain: 2-char status, a space, then the path ("orig -> new" for a rename).
        path = ln[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            paths.add(path)
    return paths


def _new_tracked_writes(before, after) -> list:
    """Pure: paths dirtied DURING the crawl (in `after`, not `before`) that are NOT the crawl's
    own outputs — outside `tests/` and the engines' own artifact dirs (`.detective/`, `.wesker/`),
    and not bytecode. These are side effects of running the repo's own suite, which a crawl should
    SURFACE, not silently leave behind. Snapshot `before` AFTER the regime pass, so its declared
    pyproject edit is not mistaken for one. (Found by a random-repo sweep: `.wesker/`, the mutation
    engine's cache, was being false-flagged as a repo side effect.)"""
    grown = set(after) - set(before)
    return sorted(p for p in grown
                  if not (p.startswith(("tests/", ".detective/", ".wesker/")) or "__pycache__" in p))


# ── post-apply verify: proven-preserving is not the same as clean ─────────────────────
# `decompose --apply` proves a split behaviour-preserving before writing it, but proven is not
# mergeable: the rewritten source can still red the repo's OWN lint gate (e.g. an extracted
# helper that dropped its type annotations). A hands-off crawl must never leave the tree failing
# its own CI, so re-lint after applying and revert on a regression.

def _lint_count(file, root):
    """Impure: how many findings the repo's own `ruff` reports for one file, or None if ruff
    isn't available (then there is no gate to hold to). Measured against the PROJECT's ruff
    config, not a fixed ruleset — the gate is whatever the repo's CI already enforces."""
    ruff = shutil.which("ruff")
    if not ruff:
        return None
    try:
        p = subprocess.run([ruff, "check", "--output-format", "json", str(Path(root) / file)],
                           capture_output=True, text=True, timeout=60, cwd=str(root), check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return len(json.loads(p.stdout))
    except (json.JSONDecodeError, TypeError):
        return None


def _lint_regressed(before, after) -> bool:
    """Pure: did applying the decomposition introduce NEW lint findings? True ONLY when both
    counts are known and the count GREW. An unknown count (no linter present) or a non-increase
    is not a regression — the split must red the repo's OWN gate to be reverted, and we never
    invent a gate the repo does not run."""
    return before is not None and after is not None and after > before

