"""uroboros-cassette — inspect and run a declared model operation.

    uroboros-cassette list [--json]                 the cassettes on the library path
    uroboros-cassette show ID                       one cassette: seat, warrant, slots, gate
    uroboros-cassette check [ID ...] [--json]       the gate + the model preflight
    uroboros-cassette run ID [-e KEY=VALUE ...] [--evidence-file F.json]
                            [-v SLOT=a|b|c ...] [--vocab-file SLOT=PATH ...]
                            [--params-file F.json] [--shape-file SLOT=PATH ...] [--dry-run] [--json]

A module is written, and then driven through Detective, by the contract harness:
``python -m uroboros.harness.contract play CONTRACT --out DIR`` (docs/ARCHITECTURE.md).

Exit codes are epistemics (decisions.exit_code): 0 a usable result (admitted /
partial / proposal) · 1 handed back to you, typed · 2 refused — the world is wrong ·
3 failed — the call could not be trusted. Every command ends by naming the next one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import decisions, model
from .cassette import Cassette, find_cassette, list_cassettes, load_cassette
from .run import Outcome, invoke, plan


def _pairs(items: list[str], flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"{flag} expects KEY=VALUE, got {item!r}")
        out[key.strip()] = value
    return out


def _vocabulary(args: argparse.Namespace) -> dict[str, list | dict]:
    vocab: dict[str, list | dict] = {k: [v.strip() for v in val.split("|") if v.strip()] for k, val in _pairs(args.vocab, "-v/--vocab").items()}
    for slot, path in _pairs(args.vocab_file, "--vocab-file").items():
        lines = Path(path).expanduser().read_text().splitlines()
        vocab[slot] = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    for slot, path in _pairs(args.shape_file, "--shape-file").items():
        vocab[slot] = json.loads(Path(path).expanduser().read_text())
    return vocab


def _evidence(args: argparse.Namespace) -> dict[str, str]:
    evidence: dict[str, str] = {}
    if args.evidence_file:
        loaded = json.loads(Path(args.evidence_file).expanduser().read_text())
        evidence.update({str(k): v if isinstance(v, str) else json.dumps(v, indent=2) for k, v in loaded.items()})
    evidence.update(_pairs(args.evidence, "-e/--evidence"))
    return evidence


def _cmd_list(args: argparse.Namespace) -> int:
    rows = []
    for cid, directory in list_cassettes():
        try:
            c = load_cassette(directory)
            rows.append({"id": cid, "category": c.category, "seat": c.seat, "gate": c.verdict(), "demo": c.synthetic, "label": c.label, "path": str(directory)})
        except (OSError, ValueError) as exc:
            rows.append({"id": cid, "category": "", "seat": "", "gate": "unloadable", "demo": False, "label": str(exc), "path": str(directory)})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        demo = "  (demo)" if r["demo"] else ""
        print(f"{r['id']:16} {r['category']:12} {r['seat']:13} gate={r['gate']:32} {r['label']}{demo}")
    print("\nnext: uroboros-cassette show <id>   ·   uroboros-cassette check")
    return 0


def _describe(c: Cassette) -> str:
    lines = [
        f"{c.id} — {c.label}" + ("  (DEMO cartridge)" if c.synthetic else ""),
        f"  category   {c.category}  →  seat: {c.seat}",
        f"  gate       {c.verdict()}",
        f"  warrant    {c.warrant or '(none)'}",
    ]
    if c.seat == "typed_hole":
        lines.append(f"  discharge  {c.discharge}  (what stands between an admitted value and its use)")
    if c.destination:
        lines.append(f"  destination {c.destination}")
    lines += [
        f"  model      {c.model}  format={c.format_mode}  think={c.think}  options={c.options}",
        f"  wire       {model.resolve_api(c.api)} @ {model.resolve_endpoint(model.resolve_api(c.api), c.endpoint)}"
        + ("  (cassette pins the wire)" if c.api else "  ($UROBOROS_LLM_API / $UROBOROS_LLM_ENDPOINT override)"),
        f"  guards     pass_budget={c.pass_budget}  timeout={c.timeout_s}s",
        f"  hand-back  {c.hand_back}  (what an abstention asks of you)",
        f"  path       {c.path}",
    ]
    for s in c.slots:
        vocab = f"{len(s.vocabulary)} values" if s.source != "runtime" else "supplied at run time"
        lines.append(f"  slot       {s.name}: {s.kind} [{s.role}] source={s.source} ({vocab}) abstain={s.allow_abstain} → {s.verdict()}")
    return "\n".join(lines)


def _cmd_show(args: argparse.Namespace) -> int:
    c = find_cassette(args.id)
    print(_describe(c))
    print(f"\nnext: uroboros-cassette run {c.id} --dry-run   (prints the exact request; calls nothing)")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    targets = [find_cassette(i) for i in args.ids] if args.ids else [load_cassette(d) for _, d in list_cassettes()]
    served: dict[str, list[str] | None] = {}
    rows, worst = [], 0
    for c in targets:
        api = model.resolve_api(c.api)
        endpoint = model.resolve_endpoint(api, c.endpoint)
        if endpoint not in served:
            served[endpoint] = model.list_models(endpoint, api=api) if api in decisions.API_FLAVORS else None
        available = served[endpoint]
        wanted = decisions.served_model_name(c.model, api)
        availability = decisions.model_availability(available is not None, available or [], wanted)
        gate = c.verdict()
        if gate != "ok" or availability != "present" or api not in decisions.API_FLAVORS:
            worst = 2
        rows.append({"id": c.id, "gate": gate, "model": c.model, "serves_as": wanted, "api": api, "availability": availability, "endpoint": endpoint})
    if args.json:
        print(json.dumps(rows, indent=2))
        return worst
    for r in rows:
        print(f"{r['id']:16} gate={r['gate']:32} model={r['model']} [{r['availability']}] @ {r['api']} {r['endpoint']}")
    absent = "fix what is not ok/present: a refused gate names its reason; for an absent model, make it available on the configured model server"
    print("\nnext: " + (absent if worst else "uroboros-cassette run <id> --dry-run"))
    return worst


def _render_outcome(o: Outcome) -> str:
    lines = [f"[{o.status}] {o.cassette}  seat={o.seat}  reason={o.reason}"]
    for k, v in o.values.items():
        lines.append(f"  value     {k} = {v!r}")
    for lit in o.rendered:
        lines.append(f"  rendered  {lit}")
    for k, v in o.proposals.items():
        text = str(v).strip().replace("\n", "\n            ")
        lines.append(f"  proposal  {k}: {text}")
    if o.abstained_slots:
        lines.append(f"  handed back ({o.hand_back}): {', '.join(o.abstained_slots)}")
    lines.append(f"  warrant   {o.warrant or '(generative — the dyad itself)'}")
    if o.discharge:
        lines.append(f"  discharge {o.discharge}")
    for r in o.records:
        t = r.telemetry
        extra = f"  error={r.error}" if r.error else ""
        lines.append(f"  pass {r.pass_index}   {r.admission} → {r.move}   {t.get('output_tokens', 0)} out-tok · {t.get('total_ms', 0)} ms · {o.model} @ {o.api}{extra}")
    # An admitted value is well-formed, not warranted: the next move is its discharge.
    use = {
        "downstream": "feed the values to the measurement the warrant names; it judges them, you do not trust them",
        "check": "run the check the warrant names before using the values; a failed check means 'not this'",
        "operator": "confirm or correct before filing — this is a pre-filled selection, not a decision",
    }.get(o.discharge, "discharge the warrant above before using the values")
    nxt = {
        "admitted": use,
        "partial": f"{use}; supply the {o.hand_back} for: {', '.join(o.abstained_slots)}",
        "abstained": f"nothing followed — supply the {o.hand_back} yourself (it is yours to give, not the model's)",
        "proposal": "read it; it is a proposal — keep, edit or discard, never promote it to a value",
        "refused": "the world is wrong: fix the cassette or supply the missing inputs, then re-run",
        "failed": "the call could not be trusted — check the endpoint/model (`uroboros-cassette check`) and re-run",
    }.get(o.status, "re-run")
    lines.append(f"\nnext: {nxt}")
    return "\n".join(lines)


def _cmd_run(args: argparse.Namespace) -> int:
    c = find_cassette(args.id)
    evidence = _evidence(args)
    vocab = _vocabulary(args)
    params = json.loads(Path(args.params_file).expanduser().read_text()) if args.params_file else []
    if args.dry_run:
        p = plan(c, evidence, vocab, params)
        print(json.dumps({k: p[k] for k in ("cassette", "verdict", "invocation", "missing_evidence", "missing_vocabulary", "api", "endpoint", "ignored_options", "payload")}, indent=2))
        ready = p["verdict"] == "ok" and p["invocation"] == "ok"
        print(f"\nnext: {'uroboros-cassette run ' + c.id + ' (same arguments, without --dry-run)' if ready else 'fix: ' + (p['verdict'] if p['verdict'] != 'ok' else p['invocation'])}", file=sys.stderr)
        return 0 if ready else 2
    outcome = invoke(c, evidence, vocab, params)
    print(json.dumps(outcome.to_dict(), indent=2) if args.json else _render_outcome(outcome))
    return decisions.exit_code(outcome.status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uroboros-cassette", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="the cassettes on the library path")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="one cassette: seat, warrant, slots, gate")
    p_show.add_argument("id")
    p_show.set_defaults(func=_cmd_show)

    p_check = sub.add_parser("check", help="the gate + model preflight for cassettes")
    p_check.add_argument("ids", nargs="*")
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=_cmd_check)

    p_run = sub.add_parser("run", help="run one cassette")
    p_run.add_argument("id")
    p_run.add_argument("-e", "--evidence", action="append", default=[], metavar="KEY=VALUE", help="a prompt field")
    p_run.add_argument("--evidence-file", help="a JSON object of prompt fields")
    p_run.add_argument("-v", "--vocab", action="append", default=[], metavar="SLOT=a|b|c", help="a runtime closed vocabulary")
    p_run.add_argument("--vocab-file", action="append", default=[], metavar="SLOT=PATH", help="a runtime vocabulary, one value per line")
    p_run.add_argument("--params-file", help="JSON list of {name,type,pool} for a call_objects slot")
    p_run.add_argument("--shape-file", action="append", default=[], metavar="SLOT=PATH", help="a code slot's shape contract (shape.json), parameters included")
    p_run.add_argument("--dry-run", action="store_true", help="print the exact request; call nothing")
    p_run.add_argument("--json", action="store_true", help="the full Outcome, every pass recorded")
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
