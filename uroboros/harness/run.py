"""Execute one declared cassette operation and return its typed outcome.

    plan()    everything decided BEFORE a model is touched: the gate, the invocation
              check, the resolved slots, the exact request. ``--dry-run`` prints it.
    invoke()  the loop: call → admit → next_move, until accept / hand back / stop.
              Every pass is recorded raw — collection now, analysis in the fullness
              of time — and the result is an :class:`Outcome` whose status is one of
              six named states that never share a meaning:

        admitted  every value slot filled from its closed set — well-formed, NOT yet
                  warranted: ``discharge`` names what must happen before use
        partial   some filled, the rest handed back
        abstained nothing followed — handed back, typed, to the party who holds it
        proposal  generative output: plausible, labelled, never a value
        refused   the gate or the invocation check said no (the world is wrong)
        failed    the call could not be trusted (transport / budget / contract breach)

The ``chat`` parameter is the seam tests use; production passes nothing and gets
:func:`model.chat`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from . import decisions, model, schema
from .cassette import Cassette

ChatFn = Callable[[str, dict, float, str], tuple[str, dict]]


@dataclass
class CallRecord:
    """One pass, verbatim: the raw reply, the telemetry, and the codes the hardware
    derived from it. Kept whole so any later analysis re-derives rather than trusts."""

    pass_index: int
    content: str
    telemetry: dict
    admission: str
    move: str
    error: str = ""


@dataclass
class Outcome:
    cassette: str
    category: str
    seat: str
    status: str
    reason: str
    warrant: str
    discharge: str
    model: str
    api: str
    endpoint: str
    values: dict = field(default_factory=dict)
    proposals: dict = field(default_factory=dict)
    rendered: list = field(default_factory=list)
    abstained_slots: list = field(default_factory=list)
    hand_back: str = ""
    records: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def resolved_slots(cassette: Cassette, vocabulary: dict, params: list) -> tuple[list, list]:
    """The cassette's slots as plain dicts with every vocabulary in hand, plus the names
    of runtime-sourced slots the caller did not supply. A runtime vocabulary is the
    caller's to source — from the object, a registry, a derivation — which is where the
    warrant is discharged. A ``code`` slot's shape contract is the caller's too
    (``vocabulary[slot]``, a dict); one the harness cannot hold counts as not supplied. So is
    a ``contract`` or ``form`` slot's form (``vocabulary[slot]``, its JSON schema)."""
    slots: list = []
    missing: list = []
    for s in cassette.slots:
        vocab = list(s.vocabulary)
        slot_params: list = []
        shape: dict = {}
        pattern = ""
        form: dict = {}
        if s.kind in decisions.FORM_KINDS:
            supplied = vocabulary.get(s.name)
            if isinstance(supplied, dict) and supplied:
                form = supplied
            else:
                missing.append(s.name)
        elif s.kind == "code":
            # The unit's shape contract comes at run time; the cassette's vocabulary is the global syntax.
            supplied = vocabulary.get(s.name)
            if isinstance(supplied, dict) and decisions.shape_verdict(supplied, vocab) == "ok":
                shape, pattern = supplied, decisions.code_pattern(supplied, vocab)
            else:
                missing.append(s.name)
        elif s.source == "runtime":
            if s.kind == "call_objects":
                slot_params = list(params)
                if not slot_params:
                    missing.append(s.name)
            else:
                vocab = list(vocabulary.get(s.name, []))
                if not vocab:
                    missing.append(s.name)
        slots.append(
            {
                "name": s.name,
                "kind": s.kind,
                "role": s.role,
                "vocabulary": vocab,
                "allow_abstain": s.allow_abstain,
                "max_items": s.max_items,
                "params": slot_params,
                "description": s.description,
                "shape": shape,
                "pattern": pattern,
                "max_chars": int(shape.get("max_chars") or 0),
                "form": form,
            }
        )
    return slots, missing


def plan(cassette: Cassette, evidence: dict, vocabulary: dict | None = None, params: list | None = None) -> dict:
    """Everything decided before the model is touched. Pure over its inputs apart from
    resolving the endpoint from the environment."""
    verdict = cassette.verdict()
    slots, missing_vocab = resolved_slots(cassette, vocabulary or {}, params or [])
    missing_evidence = [f for f in schema.template_fields(cassette.template) if f not in evidence]
    api = model.resolve_api(cassette.api)
    invocation = decisions.invocation_verdict(api, missing_evidence, missing_vocab)
    correctness = decisions.category_code(cassette.category) == "correctness"
    fmt = schema.cassette_schema(slots) if correctness else None
    user = cassette.template.format_map({k: str(v) for k, v in evidence.items()}) if not missing_evidence else ""
    if correctness:
        user = user.rstrip()
        if cassette.answer == "block":
            user = f"{user}\n\n{schema.answer_block(slots)}"
    messages = ([{"role": "system", "content": cassette.system}] if cassette.system else []) + [{"role": "user", "content": user}]
    endpoint = model.resolve_endpoint(api, cassette.endpoint)
    options, budget = model.context_budget(api, messages, cassette.options, cassette.context_limit)
    if invocation == "ok" and budget and isinstance(budget["num_ctx"], str):
        invocation = budget["num_ctx"]
    payload, ignored = model.request_payload(api, cassette.model, messages, fmt, options, cassette.think, cassette.keep_alive)
    return {
        "cassette": cassette.id,
        "verdict": verdict,
        "invocation": invocation,
        "missing_evidence": missing_evidence,
        "missing_vocabulary": missing_vocab,
        "api": api,
        "endpoint": endpoint,
        "ignored_options": ignored,
        "payload": payload,
        "slots": slots,
        **({"context": budget} if budget else {}),
    }


def _parse(content: str) -> dict | None:
    """The reply as the JSON object the schema asked for, or ``None``."""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _admit(generative: bool, content: str, slots: list) -> tuple[str, dict]:
    """Deterministic admission of one reply → (admission code, parsed reply)."""
    if generative:
        return ("admitted" if content.strip() else "unparseable"), {}
    reply = _parse(content)
    if reply is None:
        return "unparseable", {}
    t = schema.tally(slots, reply)
    return decisions.admission(True, t["missing"], t["off_vocabulary"], t["abstained"], t["filled"]), reply


def _settle(out: Outcome, cassette: Cassette, slots: list, reply: dict) -> None:
    """Split an accepted or handed-back reply into values, proposals and abstentions,
    and render deterministically when the cassette names a renderer."""
    for s in slots:
        if s["name"] not in reply:
            continue
        value = reply[s["name"]]
        if s["kind"] == "text":
            out.proposals[s["name"]] = value
        elif value is None or value == []:
            out.abstained_slots.append(s["name"])
        else:
            out.values[s["name"]] = value
            if cassette.render == "python_call_args" and s["kind"] == "call_objects":
                names = [str(p.get("name", "")) for p in s["params"]]
                for call in value:
                    lit = decisions.render_call_args(names, call)
                    if lit and lit not in out.rendered:
                        out.rendered.append(lit)
    if out.abstained_slots:
        out.hand_back = cassette.hand_back


def invoke(cassette: Cassette, evidence: dict, vocabulary: dict | None = None, params: list | None = None, chat: ChatFn | None = None) -> Outcome:
    """Run one cassette to a settled :class:`Outcome`. The loop owns every branch; the
    model is consulted only inside ``call``, and only ever receives messages."""
    p = plan(cassette, evidence, vocabulary, params)
    out = Outcome(
        cassette=cassette.id,
        category=decisions.category_code(cassette.category),
        seat=cassette.seat,
        status="refused",
        reason="",
        warrant=cassette.warrant,
        discharge=cassette.discharge if decisions.category_code(cassette.category) == "correctness" else "",
        model=cassette.model,
        api=p["api"],
        endpoint=p["endpoint"],
    )
    if p["verdict"] != "ok":
        out.reason = p["verdict"]
        return out
    if p["invocation"] != "ok":
        out.reason = p["invocation"]
        return out
    call = chat or model.chat
    generative = out.category == "generative"
    previous = ""
    for i in range(max(1, cassette.pass_budget)):
        try:
            content, telemetry = call(p["endpoint"], p["payload"], cassette.timeout_s, p["api"])
            error = ""
        except model.TransportError as exc:
            content, telemetry, error = "", {}, str(exc)
        if "context" in p:
            telemetry = {**telemetry, "context": p["context"]}
        code, reply = ("transport_error", {}) if error else _admit(generative, content, p["slots"])
        move = decisions.next_move(code, i, cassette.pass_budget, previous)
        out.records.append(CallRecord(i, content, telemetry, code, move, error))
        if move == "retry":
            previous = code
            continue
        out.reason = code if move in ("accept", "hand_back") else move
        if generative and move == "accept":
            out.status = "proposal"
            out.proposals = {"text": content}
            return out
        out.status = decisions.outcome_status(move, code)
        if move in ("accept", "hand_back"):
            _settle(out, cassette, p["slots"], reply)
        return out
    out.status, out.reason = "failed", "stop_budget"  # pragma: no cover  (next_move stops at the budget)
    return out
