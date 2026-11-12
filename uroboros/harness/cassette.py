"""uroboros.harness.cassette — the operation cartridge.

A cassette declares one model operation in data. It supplies the typed slots, decode settings, evidence
projection, and hand-back. The local coding line uses cassettes for scoped operations such as witness
selection and receipt classification. The AST writing schema is generated directly from the function's
contract by ``contract.call_schema``.

    $UROBOROS_CASSETTE_PATH   extra libraries, colon-separated, searched first
                              (default: only the bundled ``cassettes/``)

    <library>/<id>/
      cassette.toml     the cartridge declares the operation and schema
      prompt.md         the prompt frame, with {evidence} placeholders
      vocab/<slot>.txt  a file-sourced closed vocabulary, one value per line

**A cassette is data, never code.** Unlike Peitho's adapter packages, nothing in a
cassette is imported or executed, so a cartridge cannot carry logic past the gate.
Operation-specific computation — mining a vocabulary, verifying an answer — belongs
to the calling layer, which is exactly where the warrant is discharged.

**Resolve at call time, never as a module constant** (the archive cartridge's rule):
the library path and the bundled directory are recomputed on every lookup.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import decisions

_MANIFEST = "cassette.toml"


def bundled_library() -> Path:
    """The library that ships with the code. A FUNCTION, not a constant."""
    return Path(__file__).resolve().parent / "cassettes"


def library_dirs() -> list[Path]:
    """The cassette search path, resolved now: ``$UROBOROS_CASSETTE_PATH`` then the
    bundled library (see :func:`decisions.library_path`)."""
    entries = decisions.library_path(os.environ.get("UROBOROS_CASSETTE_PATH", ""), str(bundled_library()))
    return [Path(e).expanduser() for e in entries]


@dataclass(frozen=True)
class Slot:
    """One field of a cassette's typed hole."""

    name: str
    kind: str
    role: str = "value"  # "value" | "proposal" (only text slots may be proposals)
    source: str = "inline"  # "inline" | "file" | "runtime"
    vocabulary: tuple = ()
    allow_abstain: bool = False
    max_items: int = 1
    description: str = ""

    @property
    def is_value(self) -> bool:
        """A value slot is a correctness claim; a text slot is only ever a proposal."""
        return self.kind != "text"

    def verdict(self) -> str:
        """This slot's legality as a typed hole (:func:`decisions.slot_verdict`)."""
        return decisions.slot_verdict(self.kind, self.role, self.source, len(self.vocabulary))


@dataclass(frozen=True)
class Cassette:
    """A loaded cassette. Immutable; never mutates its directory."""

    id: str
    path: Path
    label: str
    category: str
    warrant: str
    discharge: str
    destination: str
    hand_back: str
    render: str
    synthetic: bool
    model: str
    api: str
    endpoint: str
    format_mode: str
    think: bool | None
    keep_alive: str
    timeout_s: float
    options: dict
    pass_budget: int
    system: str
    template: str
    slots: tuple = field(default_factory=tuple)
    answer: str = "block"  # "block" | "none": whether the standard answer text follows the prompt (``run.plan``)
    context_limit: int | None = None  # assigned by the model profile when the coding line takes this seat

    def verdict(self) -> str:
        """THE GATE for this cassette (:func:`decisions.cassette_verdict`)."""
        return decisions.cassette_verdict(
            self.category,
            self.format_mode,
            [s.verdict() for s in self.slots],
            sum(1 for s in self.slots if s.is_value),
            sum(1 for s in self.slots if not s.is_value),
            self.warrant,
            self.discharge,
            self.destination,
        )

    @property
    def seat(self) -> str:
        """The seat the category assigns: ``typed_hole``, ``collaborator``, or ``none``."""
        return {"correctness": "typed_hole", "generative": "collaborator"}.get(decisions.category_code(self.category), "none")


def _read_vocabulary(path: Path) -> tuple:
    """A file-sourced vocabulary: one value per line; blanks and ``#`` comments skipped.
    An absent file is an EMPTY vocabulary, which the gate refuses — never a silent pass."""
    if not path.is_file():
        return ()
    values = []
    for line in path.read_text().splitlines():
        v = line.strip()
        if v and not v.startswith("#"):
            values.append(v)
    return tuple(values)


def _slot(raw: dict, cassette_dir: Path) -> Slot:
    source = str(raw.get("source", "inline"))
    if source == "file":
        vocab = _read_vocabulary(cassette_dir / str(raw.get("file") or f"vocab/{raw.get('name', '')}.txt"))
    elif source == "inline":
        vocab = tuple(raw.get("values") or ())
    else:
        vocab = ()
    return Slot(
        name=str(raw.get("name", "")),
        kind=str(raw.get("kind", "")),
        role=str(raw.get("role", "value")),
        source=source,
        vocabulary=vocab,
        allow_abstain=bool(raw.get("allow_abstain", False)),
        max_items=int(raw.get("max_items", 1)),
        description=str(raw.get("description", "")),
    )


def load_cassette(path: Path | str) -> Cassette:
    """Load one cassette directory. Raises ``FileNotFoundError`` when it does not
    declare itself; legality is NOT checked here — that is the gate's job, and a
    refused cassette must still be loadable so ``check`` can say why."""
    root = Path(path)
    manifest_path = root / _MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"not a cassette (no {_MANIFEST}): {root}")
    with open(manifest_path, "rb") as fh:
        m = tomllib.load(fh)
    model = m.get("model", {})
    prompt = m.get("prompt", {})
    template_file = root / str(prompt.get("template", "prompt.md"))
    think = model.get("think")
    return Cassette(
        id=str(m.get("id") or root.name),
        path=root,
        label=str(m.get("label", "")),
        category=str(m.get("category", "")),
        warrant=str(m.get("warrant", "")),
        discharge=decisions.discharge_code(str(m.get("discharge", ""))),
        destination=str(m.get("destination", "")),
        hand_back=decisions.hand_back_kind(str(m.get("hand_back", ""))),
        render=str(m.get("render", "none")),
        synthetic=bool(m.get("synthetic", False)),
        model=str(model.get("name", "")),
        api=str(model.get("api", "")),
        endpoint=str(model.get("endpoint", "")),
        format_mode=str(model.get("format", "schema")),
        think=think if isinstance(think, bool) else None,
        keep_alive=str(model.get("keep_alive", "")),
        timeout_s=float(model.get("timeout_s", 120)),
        options=dict(model.get("options", {})),
        pass_budget=int(m.get("guards", {}).get("pass_budget", 2)),
        system=str(prompt.get("system", "")),
        template=template_file.read_text() if template_file.is_file() else "",
        slots=tuple(_slot(s, root) for s in m.get("slot", [])),
        answer=str(prompt.get("answer", "block")),
    )


def list_cassettes() -> list[tuple[str, Path]]:
    """Every cassette on the library path as ``(id, directory)``, first occurrence of
    an id winning (a private volume shadows a bundled cassette)."""
    seen: dict[str, Path] = {}
    for lib in library_dirs():
        if not lib.is_dir():
            continue
        for d in sorted(lib.iterdir()):
            if (d / _MANIFEST).is_file() and d.name not in seen:
                seen[d.name] = d
    return list(seen.items())


def find_cassette(cassette_id: str) -> Cassette:
    """The cassette with this id, from the first library that holds it. Callers name
    an operation, never a path — the same rule as ``rip --name``."""
    for cid, directory in list_cassettes():
        if cid == cassette_id:
            return load_cassette(directory)
    searched = ", ".join(str(p) for p in library_dirs())
    raise LookupError(f"no cassette {cassette_id!r} on the library path ({searched})")
