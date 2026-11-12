"""uroboros.harness.decisions — every decision the harness makes, pure and pinned.

Each function is TOTAL over plain data (str / bool / int / list / dict), returns a
NAMED CODE wherever two outcomes need different remedies (``docs/theory/THE_DYAD.md``
Law 10), and takes its parameters positionally — no ``*`` — so Detective's
``--input`` synthesis can reach every branch. The impure shell (``cassette``,
``model``, ``run``, ``cli``) computes the plain inputs and dispatches on the codes;
it decides nothing itself.
"""

from __future__ import annotations

import ast
import json
import keyword
import math
import re
import shlex

# The two categories of semantic content (THE_DYAD Part I). Anything else is refused.
CATEGORIES = ("generative", "correctness")

# The typed-hole kinds the hardware knows how to build (schema.slot_schema). ``code`` is a function body typed inside
# its unit's shape contract (``code_pattern``): the ``function-body`` cassette's, which nothing plays since the
# contract harness writes code (2026-10-06). ``contract`` is a module's whole contract, held to the form the caller
# supplies at run time (``contract.contract_schema``): the ``contract-write`` cassette's, the contract writer's seat
# (SEPARATION_OF_AUTHORITY.md R2). ``form`` is any object held so, for a reply whose shape its caller knows only at run
# time: the ``intent-check`` cassette's, one verdict for each record Detective pinned (the admission seat).
SLOT_KINDS = ("enum", "enum_list", "bool", "int", "float", "text", "call_objects", "code", "contract", "form")

# The slot kinds held to a form their caller supplies at run time (schema.slot_schema, run.resolved_slots).
FORM_KINDS = ("contract", "form")

# What an abstention hands to the human (THE_DYAD II.6).
HAND_BACK_KINDS = ("domain_value", "fixture", "judgment", "definition")

# The wire formats the one model call can speak. `openai` is the OpenAI-compatible
# /v1 shape every current local server exposes (llama.cpp's llama-server, vllm-mlx,
# LM Studio, and Ollama itself), so the engine is a deployment choice, not a
# cassette's. `ollama` is Ollama's native /api/chat, kept because it carries knobs
# the /v1 shape has no field for (num_ctx, keep_alive, think).
API_FLAVORS = ("openai", "ollama")

# Options are written Ollama-style in a cassette; these are the ones the OpenAI
# shape has a field for. Everything else is dropped and NAMED (plan.ignored_options)
# rather than silently discarded — on llama-server, num_ctx is fixed when the model
# loads (the router preset), so it is a server fact, not a per-call one.
_OPENAI_OPTIONS = {"temperature": "temperature", "top_p": "top_p", "num_predict": "max_tokens", "seed": "seed", "stop": "stop"}

# How a correctness cassette's warrant is DISCHARGED — what stands between an admitted
# value and its use (THE_DYAD Law 13). A typed hole guarantees form, never
# correspondence: on a real disc whose true work was absent, a 4 B model with a legal
# null in its schema, asked in so many words to abstain, force-picked 9 of 9.
DISCHARGE_KINDS = ("downstream", "check", "operator")

# Admission codes a retry can help with: the model's reply did not arrive or did not
# parse. Nothing else is retried — an abstention is a result, and an off-vocabulary
# value under a decode-time enum means the constraint itself failed.
_RETRYABLE = ("unparseable", "schema_violation", "transport_error")


def category_code(raw: str) -> str:
    """Normalize a declared content category: ``generative``, ``correctness``, or
    ``unknown``. The category — not the model, the topic, or the difficulty — is what
    assigns the model its seat (THE_DYAD Law 1)."""
    value = raw.strip().lower()
    return value if value in CATEGORIES else "unknown"


def library_path(env_value: str, bundled: str) -> list:
    """The cassette library in search order: every non-blank entry of
    ``$UROBOROS_CASSETTE_PATH`` (colon-separated, first wins), then the bundled
    library. Duplicates are dropped with their first position kept, so a private
    volume can shadow a bundled cassette of the same id — the way
    ``REGENESIS_UNIVERSE_PATH`` lets a project point Regenesis at its own universes."""
    out: list = []
    for part in env_value.split(":"):
        entry = part.strip()
        if entry and entry not in out:
            out.append(entry)
    if bundled.strip() and bundled not in out:
        out.append(bundled)
    return out


def slot_verdict(kind: str, role: str, source: str, vocabulary_size: int) -> str:
    """Is one declared slot a legal typed hole?

    - ``ok``                              a typed, closed slot, a type-locked value (``bool``, ``int``, ``float``,
                                          ``code``) — or a text slot declared a proposal
    - ``unknown_kind``                    not a hole the hardware builds
    - ``text_must_be_proposal``           free text may exist only as a proposal, never a value (Law 8)
    - ``unknown_source``                  an enum's vocabulary must come from ``inline``, ``file`` or ``runtime``
    - ``empty_vocabulary``                a closed set with nothing in it closes nothing
    - ``call_objects_need_runtime_params`` an argument skeleton is shaped by its target, so the caller supplies it
    - ``code_needs_declared_syntax``      a code slot's vocabulary is the global set of allowed keywords and
                                          operators, declared inline or in a file; the unit's shape comes at run time
    - ``contract_needs_runtime_form``     a contract slot is held to the contract's form, which the caller supplies
    - ``form_needs_runtime_form``         a form slot is held to the form its caller supplies at run time

    A ``runtime`` vocabulary is legal here and checked at invocation
    (:func:`invocation_verdict`): the closed set exists, the caller just holds it.
    """
    k = kind.strip().lower()
    if k not in SLOT_KINDS:
        return "unknown_kind"
    if k == "text":
        return "ok" if role.strip().lower() == "proposal" else "text_must_be_proposal"
    s = source.strip().lower()
    if k in ("enum", "enum_list"):
        if s == "runtime":
            return "ok"
        if s not in ("inline", "file"):
            return "unknown_source"
        return "ok" if vocabulary_size > 0 else "empty_vocabulary"
    if k == "call_objects":
        return "ok" if s == "runtime" else "call_objects_need_runtime_params"
    if k == "code":
        if s not in ("inline", "file"):
            return "code_needs_declared_syntax"
        return "ok" if vocabulary_size > 0 else "empty_vocabulary"
    if k in FORM_KINDS:
        return "ok" if s == "runtime" else f"{k}_needs_runtime_form"
    return "ok"  # bool / int / float: type-locked by the schema itself


def discharge_code(raw: str) -> str:
    """Normalize a correctness cassette's declared discharge — what must happen before
    an admitted value is used:

    - ``downstream``  a measurement judges the value by its effect; a wrong value costs
                      one wasted measurement, never a wrong record (a test input under
                      the mutation kill)
    - ``check``       the caller re-verifies the value against an independent
                      deterministic source before using it (a disc-id registry)
    - ``operator``    the human confirms; until then the value is a pre-filled selection
    - ``unknown``     anything else
    """
    value = raw.strip().lower()
    return value if value in DISCHARGE_KINDS else "unknown"


def cassette_verdict(category: str, format_mode: str, slot_codes: list, value_slots: int, proposal_slots: int, warrant: str, discharge: str, destination: str) -> str:
    """THE GATE — may this cassette run at all? The category decides what it must be.

    Correctness-shaped (its output must correspond):
      - ``json_mode_is_not_a_schema``   ``format = "json"`` constrains SHAPE, never VALUES (the I-5 incident)
      - ``correctness_needs_schema``    the model must fill a typed hole, so the format is a schema
      - (a slot's own code)             the first slot that is not a legal hole
      - ``correctness_needs_value_slot`` a cassette of proposals only is generative work in disguise
      - ``no_warrant_declared``         it must say where trust comes from — never the model's fluency (Law 4)
      - ``no_discharge_declared``       it must say what stands between an admitted value and its use (Law 13)
      - ``proposal_addresses_the_warrant`` free text beside an OPERATOR discharge is read by
                                        the very party whose confirmation is the warrant. Measured:
                                        every wrong pick on the AVATAR negatives came with a fluent,
                                        false rationale — mode–warrant opposition aimed at the confirmer

    Generative (its product is plausibility):
      - ``generative_is_free_text``     structure for generative work is the prompt's hidden frame, not a schema (Law 7)
      - ``generative_takes_no_slots``   a typed field belongs to a correctness cassette
      - ``no_destination_declared``     a proposal must say where it lands, so it can never land in a value slot (Law 8)

    ``unknown_category`` outranks everything. Callers treat anything but ``ok`` as a STOP.
    """
    cat = category_code(category)
    if cat == "unknown":
        return "unknown_category"
    fmt = format_mode.strip().lower()
    first_bad = next((code for code in slot_codes if code != "ok"), "")
    if cat == "correctness":
        if fmt == "json":
            return "json_mode_is_not_a_schema"
        if fmt != "schema":
            return "correctness_needs_schema"
        if first_bad:
            return first_bad
        if value_slots <= 0:
            return "correctness_needs_value_slot"
        if not warrant.strip():
            return "no_warrant_declared"
        mode = discharge_code(discharge)
        if mode == "unknown":
            return "no_discharge_declared"
        if mode == "operator" and proposal_slots > 0:
            return "proposal_addresses_the_warrant"
        return "ok"
    if fmt != "none":
        return "generative_is_free_text"
    if slot_codes:
        return "generative_takes_no_slots"
    if not destination.strip():
        return "no_destination_declared"
    return "ok"


def invocation_verdict(api: str, missing_evidence: list, missing_vocabulary: list) -> str:
    """Can this particular call be made? ``unknown_api`` (the wire named by the
    environment or the cassette is not one this hardware speaks — refuse rather than
    fall back), ``evidence_missing`` (the prompt names a field the caller did not
    supply), ``vocabulary_missing`` (a runtime-sourced closed set was not handed
    over), else ``ok``. The wire is checked first: an unreachable shape makes the
    other two moot. Evidence before vocabulary: without evidence there is nothing to
    select about."""
    if api not in API_FLAVORS:
        return "unknown_api"
    if missing_evidence:
        return "evidence_missing"
    if missing_vocabulary:
        return "vocabulary_missing"
    return "ok"


def admission(parsed: bool, missing_slots: int, off_vocabulary: int, abstained: int, filled: int) -> str:
    """Deterministic admission of one model reply, over the counts ``schema.tally``
    computes for the VALUE slots (proposal slots are never counted):

    - ``unparseable``      the reply is not the JSON object the schema asked for
    - ``schema_violation`` a required slot is absent or the wrong type
    - ``off_vocabulary``   a value outside its closed set — under a decode-time enum this
                           means the constraint itself failed: a contract breach, not noise
    - ``abstained``        every value slot abstained — a RESULT, handed back typed (Law 5)
    - ``partial``          some filled, some abstained
    - ``empty``            no value slots were tallied at all — a contract breach
    - ``admitted``         every value slot filled from its closed set
    """
    if not parsed:
        return "unparseable"
    if missing_slots > 0:
        return "schema_violation"
    if off_vocabulary > 0:
        return "off_vocabulary"
    if filled == 0 and abstained > 0:
        return "abstained"
    if abstained > 0:
        return "partial"
    if filled == 0:
        return "empty"
    return "admitted"


def next_move(admission_code: str, pass_index: int, pass_budget: int, previous_code: str) -> str:
    """The inverted-control step: the loop, not the model, decides what happens next.

    - ``accept``               admitted or partial
    - ``hand_back``            abstained — never retried into an answer (a retry would hunt for a guess)
    - ``stop_contract_breach`` off-vocabulary or empty — the constraint failed; stop loudly
    - ``stop_no_progress``     the same retryable failure twice in a row
    - ``stop_budget``          the pass budget is spent
    - ``retry``                a retryable failure (unparseable / schema violation / transport) with budget left
    - ``stop_unknown``         a code this loop does not recognise — refuse rather than guess
    """
    if admission_code in ("admitted", "partial"):
        return "accept"
    if admission_code == "abstained":
        return "hand_back"
    if admission_code in ("off_vocabulary", "empty"):
        return "stop_contract_breach"
    if admission_code not in _RETRYABLE:
        return "stop_unknown"
    if admission_code == previous_code:
        return "stop_no_progress"
    if pass_index + 1 >= pass_budget:
        return "stop_budget"
    return "retry"


def outcome_status(move: str, admission_code: str) -> str:
    """The final status a loop move resolves to: ``admitted`` / ``partial`` (accepted),
    ``abstained`` (handed back), ``pending`` (a retry is still running), else ``failed``.
    (``refused`` and ``proposal`` are set by the shell's two other exits: the gate, and
    the generative seat.)"""
    if move == "accept":
        return "partial" if admission_code == "partial" else "admitted"
    if move == "hand_back":
        return "abstained"
    if move == "retry":
        return "pending"
    return "failed"


def hand_back_kind(declared: str) -> str:
    """Normalize a cassette's declared hand-back: ``domain_value`` (a fact only the
    operator can see), ``fixture`` (the environment a pure answer needs), ``judgment``
    (taste, an undecidable call), ``definition`` (a meaning that is the author's to
    fix), else ``unspecified``. Four kinds because they need four different moves."""
    value = declared.strip().lower()
    return value if value in HAND_BACK_KINDS else "unspecified"


def api_flavor(env_value: str, cassette_value: str) -> str:
    """Which wire this call speaks: ``$UROBOROS_LLM_API`` if it names one, else the
    cassette's pin, else ``openai`` — the shape every local server now exposes, so
    swapping engines is an operator move and no cartridge has to be edited. An
    unrecognised name is ``unknown``, which the gate refuses (never a silent
    fallback to a wire the cassette did not ask for)."""
    for value in (env_value.strip().lower(), cassette_value.strip().lower()):
        if value:
            return value if value in API_FLAVORS else "unknown"
    return "openai"


def served_model_name(model: str, api: str) -> str:
    """The name THIS engine serves a model under.

    One canonical identity lives in the cassette — the tag the model was pulled as,
    which is its provenance. llama.cpp's router cannot serve a name containing
    ``:`` (it reads the suffix as a quantisation tag and rewrites it: measured,
    ``qwen3:4b-instruct-2507-q4_K_M`` came back as ``qwen3:Q4_K_M``), so on the
    OpenAI wire the colon becomes a hyphen. Configure
    the router model alias with this spelling."""
    return model.replace(":", "-") if api == "openai" else model


def default_endpoint(api: str) -> str:
    """Where that wire lives when nothing else says: llama-server's port for the
    OpenAI shape, Ollama's for its own. ``""`` for an unknown flavor."""
    return {"openai": "http://localhost:8080/v1", "ollama": "http://localhost:11434"}.get(api, "")


def context_window(prompt_bytes: int, reply_tokens: int | None, minimum: int | None, maximum: int) -> int | str:
    """Fit a byte-budgeted prompt and its reply in a power-of-two context tier.

    Allow 1,024 extra tokens for the engine's chat template and special tokens.
    Return a window within the model's declared bounds, `context_options_invalid`
    for invalid budgets, or `context_over_capacity` when the prompt will not fit.
    """
    if type(prompt_bytes) is not int or prompt_bytes < 0 or type(reply_tokens) is not int or reply_tokens <= 0:
        return "context_options_invalid"
    if type(minimum) is not int or type(maximum) is not int or not 0 < minimum <= maximum:
        return "context_options_invalid"
    needed = max(minimum, prompt_bytes + reply_tokens + 1024)
    if needed > maximum:
        return "context_over_capacity"
    return min(maximum, 1 << (needed - 1).bit_length())


def unload_request(api: str, endpoint: str, served: str) -> tuple[str, dict]:
    """Where and what to POST to release a model NOW, per wire: Ollama's documented
    unload (``keep_alive: 0`` on ``/api/generate``), or llama-server's router route
    ``/models/unload``, which lives at the server root beside ``/v1``, not under it.
    ``("", {})`` for an unknown flavor."""
    if api == "ollama":
        return f"{endpoint}/api/generate", {"model": served, "keep_alive": 0}
    if api == "openai":
        root = endpoint[: -len("/v1")] if endpoint.endswith("/v1") else endpoint
        return f"{root}/models/unload", {"model": served}
    return "", {}


def release_outcome(status: int, body: str) -> str:
    """What the reply to an unload request means, as a named code (measured 2026-10-04,
    llama-server b10180 and Ollama):

    * ``released`` — any 2xx; Ollama answers 200 even for a model that was not resident;
    * ``not_running`` — the router's 400 "model is not running": nothing left to free;
    * ``unknown_model`` — the router's 400 "model is not found", or Ollama's 404 "model
      'x' not found"; a reply that does not name a model is not this;
    * ``unreachable`` — no reply at all (status 0);
    * ``rejected`` — anything else, e.g. a ``/v1`` server with no unload route, whose
      404 says only "Not Found"."""
    if 200 <= status < 300:
        return "released"
    if status == 0:
        return "unreachable"
    text = body.lower()
    if status == 400 and "not running" in text:
        return "not_running"
    if status in (400, 404) and "not found" in text and "model" in text:
        return "unknown_model"
    return "rejected"


def openai_options(options: dict) -> tuple:
    """Ollama-style decode options → ``(openai fields, dropped names)``. The dropped
    names are reported, never swallowed: a cassette asking for ``num_ctx`` on a
    server where context is fixed at load time deserves to hear that."""
    sent, dropped = {}, []
    for key in sorted(options):
        field = _OPENAI_OPTIONS.get(key, "")
        if field:
            sent[field] = options[key]
        else:
            dropped.append(key)
    return sent, dropped


def endpoint_source(env_value: str, cassette_value: str) -> str:
    """Where the model endpoint comes from: ``env`` (``$UROBOROS_LLM_ENDPOINT`` set —
    the operator's override for this shell), ``cassette`` (the cartridge pins a host),
    else ``default`` (:func:`default_endpoint` for the wire in use)."""
    if env_value.strip():
        return "env"
    if cassette_value.strip():
        return "cassette"
    return "default"


def model_availability(reachable: bool, available: list, requested: str) -> str:
    """Preflight: ``unreachable`` (no endpoint answered), ``absent`` (it answered, the
    model is not pulled), ``present``. A name matches exactly, or as ``<name>:latest``
    — never by prefix, which would silently substitute a different model."""
    if not reachable:
        return "unreachable"
    name = requested.strip()
    if name and (name in available or f"{name}:latest" in available):
        return "present"
    return "absent"


def render_call_args(names: list, call: dict) -> str:
    """The ONLY place call syntax is written: one admitted argument object → a
    positional Python tuple literal, in signature order. ``repr`` of a JSON-derived
    value is a Python literal; a one-argument tuple needs its trailing comma. Anything
    that does not round-trip through ``ast.literal_eval`` renders as ``""`` — so a
    malformed selection never reaches a tool as a broken input, and a string can only
    ever arrive as data, never as code (``clean-code-without-a-gpu`` §4)."""
    if not all(n in call for n in names):
        return ""
    lit = "(" + ", ".join(repr(call[n]) for n in names) + ("," if len(names) == 1 else "") + ")"
    try:
        ast.literal_eval(lit)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return ""
    return lit


def exit_code(status: str) -> int:
    """Exit codes are epistemics, not pass/fail (``communicating-determinism`` §4):
    ``0`` a result the caller may use (admitted / partial / proposal) · ``1`` handed
    back to the human, typed · ``2`` refused — the world is wrong (fix the cassette or
    supply the inputs) · ``3`` failed — the call could not be trusted (re-run)."""
    if status in ("admitted", "partial", "proposal"):
        return 0
    if status == "abstained":
        return 1
    if status == "refused":
        return 2
    return 3


# ── Possession: the model in Detective's body (docs/ARCHITECTURE.md, first section) ────────────────────
#
# Once its module is written, a model acts only through Detective. Each run is read as Detective's typed state and the
# actions it permits (``possession``); these decisions admit a command only if Detective named it, run it under the
# contract's two-sign policy, and class each ending by Detective's code and exit code for the phase's exit code. A run
# that repeats over unchanged ground is doctor's to name (``possession.doctor_ending``), never a rule kept here.

# Detective's body, by capacity: sight, movement (inspection and taste), weapons, the bite.
DAEMON_VERBS = ("regime", "doctor", "diagnose", "plan", "parsimony", "survey", "converge", "audit", "decompose")
# The style pass: optional, within best judgement (the founder, 2026-10-05).
STYLE_VERBS = ("plan", "parsimony", "survey", "decompose")
# The verbs that take ``--two-sign`` (measured from Detective's parser, 2026-10-06). Every contract's intent tests carry
# both signs (the founder, 2026-10-06), so each of these runs over both; Detective's printed commands do not yet carry
# the flag (Detective issue #115), so the harness adds it.
TWO_SIGN_VERBS = ("audit", "converge", "decompose", "diagnose")
# The verbs whose ``--json`` output is one object carrying Detective's decision record (``decision``) or its typed refusal:
# the harness reads that record, never the printed page (docs/builds/2026-10-08/WIRING.md, W2).
JSON_VERBS = ("regime", "audit", "converge", "doctor", "flag")
_PLACEHOLDER = re.compile(r"<[^<>\s]+>")


def daemon_argv(command: str, target: str, unit_file: str, workspace: str) -> list | str:
    """Admit one command into the daemon's body (pure — pinned): the argv after ``detective``, or a refusal code.

    Codes: ``invalid`` · ``unparseable`` · ``placeholder`` (a skeleton such as ``<receipt>``, an ask, not a
    command) · ``not_detective`` · ``forbidden_verb`` (outside DAEMON_VERBS: ``flag``, ``purge``, ``receipt``,
    ``verify-rewrite`` …) · ``forbidden_option`` (``--remove``; ``--apply`` off ``decompose``) ·
    ``foreign_target`` (a ``file::function`` other than the unit's) · ``foreign_path`` (a ``.py`` other than the
    unit's) · ``foreign_root``. ``--project-root`` is appended when Detective's command did not carry it.
    """
    if not all(isinstance(x, str) for x in (command, target, unit_file, workspace)):
        return "invalid"
    if _PLACEHOLDER.search(command):
        return "placeholder"
    try:
        toks = shlex.split(command)
    except ValueError:
        return "unparseable"
    if not toks or toks[0] != "detective":
        return "not_detective"
    args = toks[1:]
    if not args or args[0] not in DAEMON_VERBS:
        return "forbidden_verb"
    if "--remove" in args or ("--apply" in args and args[0] != "decompose"):
        return "forbidden_option"
    for i, a in enumerate(args[1:], start=1):
        prev = args[i - 1]
        if prev == "--project-root":
            if a != workspace:
                return "foreign_root"
            continue
        if prev == "--input":
            continue
        if "::" in a and a != target:
            return "foreign_target"
        if a.endswith(".py") and a != unit_file and "::" not in a:
            return "foreign_path"
    if "--project-root" not in args:
        args = args + ["--project-root", workspace]
    return args


def two_sign_argv(argv: list) -> list | str:
    """An admitted command under the contract's two-sign policy (pure): ``argv`` with ``--two-sign`` added when its verb
    takes the flag (``TWO_SIGN_VERBS``) and does not carry it, else ``argv`` unchanged. Code: ``invalid``."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return "invalid"
    if argv[0] in TWO_SIGN_VERBS and "--two-sign" not in argv:
        return [*argv, "--two-sign"]
    return list(argv)


def json_argv(argv: list) -> list | str:
    """A command that carries ``--json`` (pure): ``argv`` with it added when its verb is one of ``JSON_VERBS`` and it is
    absent, else ``argv`` unchanged. Code: ``invalid``."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return "invalid"
    if argv[0] in JSON_VERBS and "--json" not in argv:
        return [*argv, "--json"]
    return list(argv)


# Detective's codes for a settled run, a completed read or a clean setup (the trace, section 2). A settled converge
# keeps its residual in its ending (``converge:settled:probed``): a certificate modulo candidate-equivalents is
# conditional, and its ending says so.
_CLEAN_ENDINGS = frozenset(
    {
        "regime:clean",
        "regime:migrated",
        "diagnose:settled",
        "audit:done_complete",
        "converge:settled:none",
        "converge:settled:probed",
        "converge:settled:unprobed",
        "converge:settled:crash_only",
        "decompose:applied",
        "decompose:proposed",
        "decompose:already_atomic",
        "plan:advisory",
        "survey:advisory",
    }
)
# A setup fault: the world is wrong (Detective's exit 2; doctor's green herb).
_WORLD_CODES = frozenset({"install_pytest", "preempted_by_setup", "shadowed-target", "conftest-collision"})
# A module the harness wrote that Detective cannot load is handed back, never filed as the world being wrong: the
# receiver decides whether the contract, the code or the machine is wrong, from Detective's refusal and doctor's read
# (the founder, 2026-10-07: «Hand it back»). Detective names it fix_load; a refusal that recorded no code (exit 2,
# unobserved) is the same failure met at collection. The setup's run, regime, reads the folder's layout, not the module.
_LOAD_CODES = frozenset({"fix_load"})
# A measurement that cannot be trusted (Detective's exit 3), and the harness's own words for a state it could not read.
_UNTRUSTED_CODES = frozenset({"fix_verification", "rerun_stale", "repair_measurement", "proof_cut", "ambiguous", "unrecorded"})
_UNTRUSTED_HARNESS = frozenset({"unknown_element", "unknown_code", "disagreement", "invalid"})
_DETECTIVE_VERBS = ("regime", "diagnose", "audit", "converge", "decompose", "plan", "survey", "doctor")


def ending_class(ending: str, exit_code: object) -> str:
    """The epistemic class of one run's ending (pure): Detective's exit code where it speaks (``2`` the world is
    wrong, ``3`` the measurement cannot be trusted, ``1`` a measured gap or a refusal), and where it exits 0 because it
    names the cause, the cause's own code (the trace: ``audit`` exits 0 on every next-action code,
    ``fix_failing_test`` among them).

    ``ending`` is ``<verb>:<code>`` (``possession.ending``) or a harness code. Codes: ``clean`` · ``handed_back`` (a
    person or the audit holds the next move: a failing test, a sample, an open gap, an abstention, an incomplete ask,
    a repeat, a spent budget, a written module Detective cannot load) · ``world_wrong`` · ``untrusted`` · ``invalid``.
    """
    if not isinstance(ending, str) or not ending:
        return "invalid"
    verb, _, rest = ending.partition(":")
    if verb not in _DETECTIVE_VERBS:
        if ending == "detective_unset":
            return "world_wrong"
        if ending in _UNTRUSTED_HARNESS or ending.startswith(("contract_breach:", "call_", "write_refused:")):
            return "untrusted"
        return "handed_back"
    code = rest.partition(":")[0]
    if verb != "regime" and (code in _LOAD_CODES or (code == "unobserved" and exit_code == 2)):
        return "handed_back"
    if exit_code == 2:
        return "world_wrong"
    if exit_code == 3:
        return "untrusted"
    if exit_code == 1:
        return "handed_back"
    if ending in _CLEAN_ENDINGS:
        return "clean"
    if code in _WORLD_CODES:
        return "world_wrong"
    if code in _UNTRUSTED_CODES:
        return "untrusted"
    return "handed_back"


def possession_exit(classes: list) -> int:
    """The exit code of a Detective phase over its endings' classes, the setup's and each function's (pure). Exit codes
    are epistemics (``exit_code``), and the worst class decides: ``2`` a world-wrong ending, or an empty or malformed
    list; ``3`` an untrusted one; ``1`` a handed-back one; ``0`` when every one is clean."""
    if not isinstance(classes, list) or not classes or any(c not in ("clean", "handed_back", "world_wrong", "untrusted") for c in classes):
        return 2
    if "world_wrong" in classes:
        return 2
    if "untrusted" in classes:
        return 3
    return 1 if "handed_back" in classes else 0


# The receiver's decision on a hand-back: one typed record of three values (SEPARATION_OF_AUTHORITY.md R7; the
# founder, 2026-10-07). contract_wrong, any fault in the contract, its intent tests among them, the contract writer
# revises; code_wrong goes back to a code writer as a fresh call for that one function (contract.reissue);
# machine_wrong, whoever holds admission puts right, then plays the unit again.
RECEIVER_DECISIONS = ("contract_wrong", "code_wrong", "machine_wrong")


def handback_receivers(ending: object) -> list:
    """Who receives a handed-back ending, in order (pure). A failing intent test goes to the lead, then to Sol, who
    decides whether the contract or the code is wrong (the founder, 2026-10-07: «the lead, or Sol»); so does a record
    Detective pinned that admission's intent check finds contradicting the intent, the same question
    (``admission:contradicts``); one the check cannot tell goes to the founder (``admission:cannot_tell``; separation
    [8]); so does an ask the judgment seat could not tell (``judge:cannot_tell``: Detective stopped for values and the
    model had none to give or flag); every other hand-back goes to admission, the authority that types what Detective
    leaves (SEPARATION_OF_AUTHORITY.md §2). The harness routes, and the receiver decides."""
    if ending in ("audit:fix_failing_test", "admission:contradicts"):
        return ["lead", "sol"]
    return ["founder"] if ending in ("admission:cannot_tell", "judge:cannot_tell") else ["admission"]


# ── The code slot: a ``code`` value typed inside its unit's shape contract ─────────────────────────────────────────
#
# The ``function-body`` cassette's ``body`` slot (schema.slot_schema ``code``), kept in the registry; nothing plays it
# since the contract harness writes code (2026-10-06). A unit carries two contracts: the intent contract (the stub's
# signature and docstring, and its intent cases) and the shape contract (``shape.json``): every name, attribute, string
# and number the body may contain. ``code_pattern`` turns the shape into the pattern the engine holds while the model
# types.
#
# The syntax (the founder, 2026-10-05: «Tunable global set of sane operations, function-specific contract in
# operation as a narrower scoping»): every token the code subset can ever allow. Which tokens are on is data: the
# ``function-body`` cassette's ``body`` vocabulary is the global set, and a unit's ``shape.json`` may narrow it with
# its own ``syntax`` list.

CODE_KEYWORDS = ("and", "break", "continue", "elif", "else", "False", "for", "if", "in", "is", "lambda", "None", "not", "or", "pass", "return", "True")
CODE_OPERATORS = ("**", "//", "==", "!=", "<=", ">=", "+=", "-=", "*=", "+", "-", "*", "/", "%", "<", ">", "=", "|", "&", "(", ")", "[", "]", "{", "}", ",", ":")

# Builtins that reach outside a pure function, or read or run code by name. A shape that grants one is refused before
# anything runs: Detective runs what it measures. (``__import__`` and every other ``__x__`` name are refused as
# dunders.)
_FORBIDDEN_NAMES = frozenset({"breakpoint", "compile", "delattr", "eval", "exec", "exit", "getattr", "globals", "input", "locals", "open", "print", "quit", "setattr", "vars"})
_SHAPE_LISTS = ("names", "attributes", "strings", "numbers")
_SHAPE_FIELDS = _SHAPE_LISTS + ("syntax", "outcomes", "max_chars")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_REGEX_SPECIAL = "\\.^$*+?()[]{}|"


def _hole(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def shape_verdict(shape: dict, syntax: list) -> str:
    """Can the harness hold this shape contract under the global ``syntax`` (pure)? ``syntax`` is the cassette's set
    of allowed keywords and operators. A shape is a JSON object with four lists, ``names``, ``attributes``,
    ``strings`` and ``numbers``, and three optional fields: ``syntax`` (a narrower set for this function),
    ``outcomes`` (the named outcomes the intent contract defines, each of which the shape must permit) and
    ``max_chars``.

    Codes, first match wins: ``syntax_unknown`` (the global set holds a token the harness's table does not) ·
    ``invalid`` (not an object, a list missing or not a list, ``syntax`` or ``outcomes`` not a list) ·
    ``unknown_field`` · ``name_invalid`` (not an ASCII identifier) · ``name_keyword`` · ``name_dunder`` ·
    ``name_forbidden`` · ``attribute_invalid`` (not a public ASCII identifier) · ``string_invalid`` ·
    ``number_invalid`` (a bool, not a number, not finite, or negative: ``-1`` is the operator ``-`` and the number
    ``1``) · ``syntax_not_permitted`` (the shape's set is not within the global one) · ``outcome_not_permitted`` ·
    ``max_chars_invalid`` · ``ok``.
    """
    if not isinstance(syntax, list) or any(t not in CODE_KEYWORDS and t not in CODE_OPERATORS for t in syntax):
        return "syntax_unknown"
    if not isinstance(shape, dict) or not all(isinstance(shape.get(k), list) for k in _SHAPE_LISTS):
        return "invalid"
    if not isinstance(shape.get("syntax", []), list) or not isinstance(shape.get("outcomes", []), list):
        return "invalid"
    if any(k not in _SHAPE_FIELDS for k in shape):
        return "unknown_field"
    for n in shape["names"]:
        if not isinstance(n, str) or _IDENTIFIER.fullmatch(n) is None:
            return "name_invalid"
        if keyword.iskeyword(n):
            return "name_keyword"
        if _dunder(n):
            return "name_dunder"
        if n in _FORBIDDEN_NAMES:
            return "name_forbidden"
    if any(not isinstance(a, str) or _IDENTIFIER.fullmatch(a) is None or a.startswith("_") for a in shape["attributes"]):
        return "attribute_invalid"
    if any(not isinstance(s, str) for s in shape["strings"]):
        return "string_invalid"
    if any(isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x) or x < 0 for x in shape["numbers"]):
        return "number_invalid"
    if any(t not in syntax for t in shape.get("syntax", [])):
        return "syntax_not_permitted"
    if any(o not in shape["strings"] for o in shape.get("outcomes", [])):
        return "outcome_not_permitted"
    if "max_chars" in shape and (isinstance(shape["max_chars"], bool) or not isinstance(shape["max_chars"], int) or shape["max_chars"] < 0):
        return "max_chars_invalid"
    return "ok"


def _regex_literal(text: str) -> str:
    return "".join("\\" + c if c in _REGEX_SPECIAL else c for c in text)


def _code_string(value: str) -> str:
    """One declared string as the engine sees it typed: a single-quoted Python literal, then JSON-escaped."""
    source = "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + "'"
    return json.dumps(source, ensure_ascii=False)[1:-1]


def _alternation(tokens: list) -> str:
    return "(" + "|".join(sorted(set(tokens), key=lambda t: (-len(t), t))) + ")"


def _effective_syntax(shape: dict, syntax: list) -> list:
    """The tokens on for one function: the shape's own ``syntax`` when it declares one, else the global set."""
    narrower = shape.get("syntax") if isinstance(shape, dict) else None
    return list(narrower) if isinstance(narrower, list) else list(syntax)


def code_pattern(shape: dict, syntax: list) -> str:
    """The pattern the engine holds while the model types a body inside ``shape`` (pure).

    It is written over the body's JSON-escaped text, because that is what the engine constrains (measured
    2026-10-05): a line break is the two characters ``\\n``, and strings are single-quoted so no ``\\"`` is needed.
    Every word (a declared name, an allowed keyword, a declared number, an attribute chain) is followed by an
    allowed operator, a space, a line break or a declared string before the next word, so two words can never run
    together into a name the shape did not grant. A shape the harness cannot hold gets ``^$``, which admits no body.
    """
    if shape_verdict(shape, syntax) != "ok":
        return "^$"
    tokens = _effective_syntax(shape, syntax)
    attribute = "\\." + _alternation([_regex_literal(a) for a in shape["attributes"]]) if shape["attributes"] else ""
    keywords = [t for t in tokens if t in CODE_KEYWORDS]
    words = [_regex_literal(n) for n in shape["names"]] + keywords + [_regex_literal(repr(x)) for x in shape["numbers"]]
    word = _alternation(words + ([attribute] if attribute else [])) + (f"({attribute})*" if attribute else "")
    operators = [_regex_literal(t) for t in tokens if t in CODE_OPERATORS]
    separators = operators + [" ", "\\\\n"] + [_regex_literal(_code_string(s)) for s in shape["strings"]]
    separator = _alternation(separators)
    return f"^({separator}|{word}{separator})*({word})?$"


def param_type(annotation: str) -> str:
    """The typed-hole type of one annotation (pure — pinned): the leading builtin of ``list[str]`` and the like, among
    ``str int float bool list dict``, with ``|None`` when the annotation admits ``None`` (``int | None``,
    ``None | int``, ``Optional[int]``), so the hole admits ``null``; ``""`` (any scalar) for everything else."""
    if not isinstance(annotation, str):
        return ""
    text = annotation.strip()
    optional = re.fullmatch(r"(?:typing\.)?Optional\[(.+)\]", text)
    members = [optional.group(1).strip(), "None"] if optional else [m.strip() for m in text.split("|")]
    rest = [m for m in members if m != "None"]
    head = re.split(r"[\[\s]", rest[0], maxsplit=1)[0] if rest else ""
    if head not in ("str", "int", "float", "bool", "list", "dict"):
        return ""
    return f"{head}|None" if len(rest) < len(members) else head


def target_signature(stub: str, name: str) -> list | str:
    """``[[parameter, annotation], ...]`` of the stub's ``name``, its positional parameters in order (positional-only
    among them): what the daemon may know of a target when Detective opens an input slot, never its body (pure —
    pinned). Detective's ``--input`` is a positional tuple, so a variadic or a defaulted keyword-only parameter stays
    out of it, and a REQUIRED keyword-only one makes the call one the tuple cannot write.

    Codes: ``invalid`` · ``syntax_error`` · ``no_function`` · ``keyword_only`` (a required keyword-only parameter)."""
    if not isinstance(stub, str) or not isinstance(name, str):
        return "invalid"
    try:
        fn = _hole(ast.parse(stub), name)
    except SyntaxError:
        return "syntax_error"
    if fn is None:
        return "no_function"
    if any(default is None for default in fn.args.kw_defaults):
        return "keyword_only"
    return [[a.arg, ast.unparse(a.annotation) if a.annotation is not None else ""] for a in [*fn.args.posonlyargs, *fn.args.args]]


# ── The coding line: Detective's decision record, followed by stage ────────────────────────────────────────────────
#
# The harness follows Detective's own decisions and decides none of them (docs/theory/THE_CODING_LINE.md, step 6).
# Detective's ``--json`` carries one decision record per run, ``{"verb", "code", "route", "step", "plan", ...}``. What
# the harness does with a record depends on the stage it is in: ``folder`` (``regime``, once), ``setup`` (``audit``, on
# each function) or ``run`` (``converge``, on each function: the only place a function is done). ``detective_move`` is
# that one decision. The model is woken only where Detective stops for values: ``judgment_form`` holds its answer while
# it is typed, and ``judgment_dispatch`` reads it. The rest name a reply that failed to parse (``reply_failure``), a
# played unit's bar for placement (``admission_bar``) and the local receiver's next step (``receiver_next``). Each is
# total over ``str`` / ``bool`` / ``int`` / ``list`` / ``dict`` and returns a named code.


SUPPLY_KINDS = ("sample", "author", "lines", "boundary", "internal")


TEST_KINDS = ("hand_pin", "repeated")


STEP_KINDS = ("run", "supply", "write_test", "hand_back", "stop", "decide", "done")


STAGES = ("folder", "setup", "run")


_STAGE_VERBS = {"folder": ("regime",), "setup": ("audit",), "run": ("converge", "regime")}


_RAISED_OPEN = "<raised "


def item_key(kind, item):
    """The key one item of an ask is remembered by, so that an abstention is never asked again (pure).

    "lines": f"line:{item['line']}", where line is an int (not a bool); "boundary": f"relation:{item['relation']}";
    "internal": f"condition:{item['condition']}" (each a non-empty str); "sample" and "author": the kind itself,
    whatever item is. "invalid" for any other kind, or for an item of the wrong shape.
    """
    if kind in ("sample", "author"):
        return kind
    if not isinstance(item, dict):
        return "invalid"
    if kind == "lines":
        line = item.get("line")
        if isinstance(line, int) and not isinstance(line, bool):
            return f"line:{line}"
    elif kind == "boundary":
        relation = item.get("relation")
        if isinstance(relation, str) and relation:
            return f"relation:{relation}"
    elif kind == "internal":
        condition = item.get("condition")
        if isinstance(condition, str) and condition:
            return f"condition:{condition}"
    return "invalid"


def _raised(outcome):
    """(T, has_message) when the outcome is raised, else None."""
    if not (outcome.startswith(_RAISED_OPEN) and outcome.endswith(">")):
        return None
    body = outcome[len(_RAISED_OPEN) : -1]
    type_name, separator, _message = body.partition(": ")
    return type_name, separator != ""


def pin_case(original, mutant):
    """Whether one observed call may be pinned by Detective's own test, by the founder's round-7 rulings (pure).

    original, mutant: Detective's encodings of the two outcomes: a value's repr, or "<raised T>" or "<raised T: message>".
    An outcome is raised when it starts with "<raised " and ends with ">". Its T is the text after "<raised " up to the
    first ": " or, when there is none, up to the closing ">". It has a message when it holds ": " after T.

    Checked in this order:
      "invalid": either is not a str;
      "same_print": original == mutant;
      "only_mutant_raises": original is not raised and mutant is raised;
      "same_type_mutant_message": both are raised with the same T, original without a message, mutant with one;
      "pinnable": otherwise.
    """
    if not isinstance(original, str) or not isinstance(mutant, str):
        return "invalid"
    if original == mutant:
        return "same_print"
    original_raised = _raised(original)
    mutant_raised = _raised(mutant)
    if original_raised is None and mutant_raised is not None:
        return "only_mutant_raises"
    if original_raised is not None and mutant_raised is not None:
        original_type, original_has_message = original_raised
        mutant_type, mutant_has_message = mutant_raised
        if original_type == mutant_type and not original_has_message and mutant_has_message:
            return "same_type_mutant_message"
    return "pinnable"


def _invalid(code):
    return {"move": "invalid", "code": code}


def _is_str_list(value):
    return isinstance(value, list) and all(isinstance(entry, str) for entry in value)


def _is_command(value):
    """A non-empty list of str: the shape of an argv and of a then."""
    return _is_str_list(value) and len(value) > 0


def _routed(code, route):
    """The code with f":{route}" appended when the route is a non-empty str (the rules' "+route")."""
    if isinstance(route, str) and route:
        return f"{code}:{route}"
    return code


def _run_move(step, stage):
    """The run move in this stage when the step's argv is a non-empty list of str (rule 4's check), else invalid."""
    argv = step.get("argv")
    if not _is_command(argv):
        return _invalid("run_without_argv")
    return {"move": "run", "argv": argv, "stage": stage}


def _supply_move(decision, step, has_params, abstained):
    """The supply rule."""
    verb = decision["verb"]
    code = decision["code"]
    plan = decision.get("plan")
    if not isinstance(plan, dict) or plan.get("kind") not in SUPPLY_KINDS or not isinstance(plan.get("items"), list):
        return _invalid("supply_plan")
    kind = plan["kind"]
    items = plan["items"]
    keys = [item_key(kind, item) for item in items]
    if "invalid" in keys:
        return _invalid("supply_plan")
    then = step.get("then")
    if not _is_command(then):
        return _invalid("without_then")
    if not has_params:
        return {"move": "hand_back", "code": f"{verb}:{code}:no_params"}
    if kind in ("sample", "author"):
        keys = [kind]
    if any(key in abstained for key in keys):
        return {"move": "hand_back", "code": f"{verb}:{code}:abstained"}
    return {"move": "judge", "kind": kind, "items": items, "then": then, "stage": "run"}


def _test_item_well_formed(item):
    """One item of a write_test plan, as the write rule's first bullet reads it."""
    if not isinstance(item, dict):
        return False
    if not isinstance(item.get("original"), str) or not isinstance(item.get("mutant"), str):
        return False
    if "test" not in item:
        return False
    test = item["test"]
    status = item.get("test_status")
    if test is None:
        return status == "declined" or (isinstance(status, str) and status.startswith("render_failed:"))
    if isinstance(test, str):
        return status == "rendered"
    return False


def _write_move(decision, step):
    """The write rule."""
    code = decision["code"]
    plan = decision.get("plan")
    if not isinstance(plan, dict) or plan.get("kind") not in TEST_KINDS:
        return _invalid("test_plan")
    items = plan.get("items")
    if not isinstance(items, list) or not items or not all(_test_item_well_formed(item) for item in items):
        return _invalid("test_plan")
    then = step.get("then")
    if not _is_command(then):
        return _invalid("without_then")
    for index, item in enumerate(items):
        case = pin_case(item["original"], item["mutant"])
        if case != "pinnable":
            return {"move": "hand_back", "code": f"converge:{code}:{case}:{index}"}
        if item["test"] is None:
            return {"move": "hand_back", "code": f"converge:{code}:{item['test_status']}:{index}"}
    return {"move": "write_tests", "tests": [item["test"] for item in items], "then": then, "stage": "run"}


def _hand_back_remedy(decision, step):
    """X of rule 7's hand_back: the step's remedy, else the plan's kind, else "ask" (each a non-empty str)."""
    remedy = step.get("remedy")
    if isinstance(remedy, str) and remedy:
        return remedy
    plan = decision.get("plan")
    if isinstance(plan, dict):
        kind = plan.get("kind")
        if isinstance(kind, str) and kind:
            return kind
    return "ask"


def _folder_move(decision, step, kind):
    """Rule 4."""
    code = decision["code"]
    if kind == "done":
        return {"move": "setup_clean", "code": f"regime:{code}"}
    if kind == "run":
        return _run_move(step, "folder")
    if kind == "stop":
        return {"move": "end", "code": _routed(f"regime:{code}", decision.get("route"))}
    return _invalid("kind_out_of_stage")


def _setup_move(decision, step, kind, has_params, abstained):
    """Rule 5."""
    code = decision["code"]
    if kind == "run":
        argv = step.get("argv")
        if _is_command(argv) and argv[0] == "converge":
            return {"move": "run", "argv": argv, "stage": "run"}
        return _invalid("setup_run_not_converge")
    if kind == "supply":
        return _supply_move(decision, step, has_params, abstained)
    if kind == "decide":
        return {"move": "hand_back", "code": f"audit:{code}"}
    if kind == "stop":
        return {"move": "end", "code": _routed(f"audit:{code}", decision.get("route"))}
    return _invalid("kind_out_of_stage")


def _regime_in_run_move(decision, step, kind):
    """Rule 6."""
    code = decision["code"]
    if kind == "done":
        return {"move": "resume", "code": f"regime:{code}"}
    if kind == "run":
        return _run_move(step, "run")
    if kind == "stop":
        return {"move": "end", "code": _routed(f"regime:{code}", decision.get("route"))}
    return _invalid("kind_out_of_stage")


def _converge_move(decision, step, kind, has_params, abstained):
    """Rule 7. All seven STEP_KINDS are handled here; "stop" is the last one left."""
    code = decision["code"]
    if kind == "done":
        return {"move": "done", "code": f"converge:{code}"}
    if kind == "run":
        return _run_move(step, "run")
    if kind == "supply":
        return _supply_move(decision, step, has_params, abstained)
    if kind == "write_test":
        return _write_move(decision, step)
    if kind == "hand_back":
        return {"move": "hand_back", "code": f"converge:{code}:{_hand_back_remedy(decision, step)}"}
    if kind == "decide":
        return {"move": "hand_back", "code": f"converge:{code}"}
    return {"move": "end", "code": _routed(f"converge:{code}", decision.get("route"))}


def detective_move(decision, stage, has_params, abstained):
    """What the harness does with one Detective decision record, in the stage it is in (pure).

    decision: the record. stage: one of STAGES. has_params: whether the function takes parameters (a bool).
    abstained: the item keys this function's model has already answered cannot_tell (a list of str).
    In the codes below, VERB and CODE are the record's "verb" and "code", and ROUTE is its "route".
    "+route" means: with f":{ROUTE}" appended when ROUTE is a non-empty str.

    Returns a dict, by the first rule that applies:
      1. {"move": "invalid", "code": "malformed"}: decision is not a dict; its "verb" or "code" is not a str; its "step"
         is not a dict whose "kind" is a str; stage is not in STAGES; has_params is not a bool; abstained is not a list
         of str.
      2. {"move": "invalid", "code": "unknown_step"}: the step's kind is not in STEP_KINDS.
      3. {"move": "invalid", "code": "verb_out_of_stage"}: stage "folder" and VERB is not "regime"; stage "setup" and
         VERB is not "audit"; stage "run" and VERB is neither "converge" nor "regime".
      4. Stage "folder":
         - kind "done": {"move": "setup_clean", "code": f"regime:{CODE}"};
         - kind "run": {"move": "run", "argv": <the step's argv>, "stage": "folder"} when argv is a non-empty list of
           str, else {"move": "invalid", "code": "run_without_argv"};
         - kind "stop": {"move": "end", "code": f"regime:{CODE}"} +route;
         - any other kind: {"move": "invalid", "code": "kind_out_of_stage"}.
      5. Stage "setup":
         - kind "run": {"move": "run", "argv": <the step's argv>, "stage": "run"} when argv is a non-empty list of str
           whose first token is "converge" (the run begins with the converge Detective names, D1b §11); else
           {"move": "invalid", "code": "setup_run_not_converge"} (a test the contract owns is never deleted, [15]);
         - kind "supply": the supply rule below, its judge move's "stage" being "run";
         - kind "decide": {"move": "hand_back", "code": f"audit:{CODE}"};
         - kind "stop": {"move": "end", "code": f"audit:{CODE}"} +route;
         - any other kind ("done" among them): {"move": "invalid", "code": "kind_out_of_stage"}.
      6. Stage "run", VERB "regime" (a repair converge named):
         - kind "done": {"move": "resume", "code": f"regime:{CODE}"};
         - kind "run": {"move": "run", "argv": <argv>, "stage": "run"}, argv checked as in rule 4;
         - kind "stop": {"move": "end", "code": f"regime:{CODE}"} +route;
         - any other kind: {"move": "invalid", "code": "kind_out_of_stage"}.
      7. Stage "run", VERB "converge":
         - kind "done": {"move": "done", "code": f"converge:{CODE}"};
         - kind "run": {"move": "run", "argv": <argv>, "stage": "run"}, argv checked as in rule 4;
         - kind "supply": the supply rule below, its judge move's "stage" being "run";
         - kind "write_test": the write rule below;
         - kind "hand_back": {"move": "hand_back", "code": f"converge:{CODE}:{X}"}, X being the step's remedy when it is
           a non-empty str (Detective's own `supplied_did_not_reach` among them), else the plan's kind when the plan is
           a dict with a non-empty str kind, else "ask";
         - kind "decide": {"move": "hand_back", "code": f"converge:{CODE}"};
         - kind "stop": {"move": "end", "code": f"converge:{CODE}"} +route.

    The supply rule, in order:
      - {"move": "invalid", "code": "supply_plan"}: the plan is not a dict, its kind is not in SUPPLY_KINDS, its items
        are not a list, or an item's item_key is "invalid";
      - {"move": "invalid", "code": "without_then"}: the step's then is not a non-empty list of str;
      - {"move": "hand_back", "code": f"{VERB}:{CODE}:no_params"}: has_params is False;
      - {"move": "hand_back", "code": f"{VERB}:{CODE}:abstained"}: an item's key is in abstained (for sample and author,
        the kind is the key, whether or not there are items);
      - otherwise {"move": "judge", "kind": <the plan's kind>, "items": <the plan's items>, "then": <the step's then>,
        "stage": "run"}.

    The write rule, in order:
      - {"move": "invalid", "code": "test_plan"}: the plan is not a dict, its kind is not in TEST_KINDS, or its items are
        not a non-empty list of dicts each with a str "original" and "mutant" and a "test" key whose value is a str or
        None (an item without the "test" key is malformed), or an item whose test is None does not carry a
        "test_status" that is "declined" or a str starting with "render_failed:", or an item whose test is a str does
        not carry a "test_status" of "rendered" (an item with no "test_status" key among them);
      - {"move": "invalid", "code": "without_then"}: the step's then is not a non-empty list of str;
      - for each item in order: pin_case(original, mutant) other than "pinnable" gives
        {"move": "hand_back", "code": f"converge:{CODE}:{case}:{i}"}; a test of None gives
        {"move": "hand_back", "code": f"converge:{CODE}:{status}:{i}"}, status being the item's "test_status";
      - otherwise {"move": "write_tests", "tests": [each item's test, in order], "then": <the step's then>,
        "stage": "run"}.
    """
    if not isinstance(decision, dict):
        return _invalid("malformed")
    verb = decision.get("verb")
    code = decision.get("code")
    step = decision.get("step")
    if not isinstance(verb, str) or not isinstance(code, str):
        return _invalid("malformed")
    if not isinstance(step, dict) or not isinstance(step.get("kind"), str):
        return _invalid("malformed")
    if stage not in STAGES or not isinstance(has_params, bool) or not _is_str_list(abstained):
        return _invalid("malformed")
    kind = step["kind"]
    if kind not in STEP_KINDS:
        return _invalid("unknown_step")
    if verb not in _STAGE_VERBS[stage]:
        return _invalid("verb_out_of_stage")
    if stage == "folder":
        return _folder_move(decision, step, kind)
    if stage == "setup":
        return _setup_move(decision, step, kind, has_params, abstained)
    if verb == "regime":
        return _regime_in_run_move(decision, step, kind)
    return _converge_move(decision, step, kind, has_params, abstained)


FREEDOM_KINDS = ("boundary", "internal")


def judgment_form(kind, items, params, types):
    """The JSON schema a judgment reply is held to while the model writes it (pure).

    One answer per item of the ask, keyed by the item's index as a str ("0", "1", ...). A sample or author ask has no
    items and takes one answer, under "0".
    kind: one of SUPPLY_KINDS. items: Detective's typed items, a list of dicts; [] for sample and author.
    params: the function's parameter names, a non-empty list of distinct str. types: a dict giving each param's JSON
    schema (a dict).

    Each answer is {"anyOf": [SUPPLY, FREEDOM, CANNOT]}, FREEDOM only for an item of a FREEDOM_KINDS ask whose "mutants"
    is a non-empty list:
      SUPPLY  = {"type": "object",
                 "properties": {"supply": {"type": "object", "properties": {p: types[p] for each p in params},
                                           "required": params, "additionalProperties": False}},
                 "required": ["supply"], "additionalProperties": False}
      FREEDOM = {"type": "object", "properties": {"freedom": {"type": "string", "minLength": 1}},
                 "required": ["freedom"], "additionalProperties": False}
      CANNOT  = {"type": "object", "properties": {"cannot_tell": {"type": "null"}},
                 "required": ["cannot_tell"], "additionalProperties": False}
    Returns {"type": "object",
             "properties": {"answers": {"type": "object", "properties": {<key>: <answer>, ...},
                                        "required": [<every key, in order>], "additionalProperties": False}},
             "required": ["answers"], "additionalProperties": False}.
    Returns "invalid" when:
      - kind is not in SUPPLY_KINDS;
      - items is not a list of dicts, or is non-empty for sample or author, or empty for lines, boundary or internal;
      - for a FREEDOM_KINDS ask, an item holds "mutants" that is not a list of dicts each with a non-empty str "id";
      - params is not a non-empty list of distinct str;
      - types lacks a param, or gives one a schema that is not a dict.
    """
    if kind not in SUPPLY_KINDS:
        return "invalid"
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return "invalid"
    if kind in ("sample", "author"):
        if items:
            return "invalid"
    elif not items:
        return "invalid"
    if kind in FREEDOM_KINDS and any(
        "mutants" in item and not (isinstance(item["mutants"], list) and all(isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"] != "" for m in item["mutants"])) for item in items
    ):
        return "invalid"
    if not isinstance(params, list) or not params or not all(isinstance(p, str) for p in params) or len(set(params)) != len(params):
        return "invalid"
    if not isinstance(types, dict) or not all(isinstance(types.get(p), dict) for p in params):
        return "invalid"

    answers = {}
    for index in range(len(items) or 1):  # a sample or author ask has no items and takes one answer, under "0"
        branches = [
            {
                "type": "object",
                "properties": {
                    "supply": {
                        "type": "object",
                        "properties": {p: types[p] for p in params},
                        "required": list(params),
                        "additionalProperties": False,
                    }
                },
                "required": ["supply"],
                "additionalProperties": False,
            }
        ]
        # a freedom is recorded on mutants, so it is offered only where the ask names them
        if kind in FREEDOM_KINDS and isinstance(items[index].get("mutants"), list) and items[index]["mutants"]:
            branches.append(
                {
                    "type": "object",
                    "properties": {"freedom": {"type": "string", "minLength": 1}},
                    "required": ["freedom"],
                    "additionalProperties": False,
                }
            )
        branches.append(
            {
                "type": "object",
                "properties": {"cannot_tell": {"type": "null"}},
                "required": ["cannot_tell"],
                "additionalProperties": False,
            }
        )
        answers[str(index)] = {"anyOf": branches}
    return {
        "type": "object",
        "properties": {
            "answers": {
                "type": "object",
                "properties": answers,
                "required": list(answers),
                "additionalProperties": False,
            }
        },
        "required": ["answers"],
        "additionalProperties": False,
    }


def judgment_dispatch(reply, kind, items, params):
    """What the harness does with a judgment reply (pure).

    reply: the model's reply, already held to judgment_form while it was written. kind, items and params: as
    judgment_form takes them.
    Returns {"inputs": [{"item": i, "values": <the supply dict>}, ...],
             "flags": [{"item": i, "ids": [each of the item's mutants' "id", in order], "note": <the note>}, ...],
             "abstained": [i, ...]}, each list in item order, with i an int; or a code, checked in this order:
      "invalid:params": params is not a non-empty list of distinct str;
      "invalid:stop": kind or items is not as judgment_form takes them;
      "invalid:shape": reply is not a dict with exactly one key, "answers", mapping exactly the expected keys;
      then, for each item in order:
      "invalid:answer:<i>": the answer is not a dict with exactly one key among "supply", "freedom", "cannot_tell";
      "invalid:supply_keys:<i>": a supply that is not a dict whose keys are exactly params;
      "invalid:freedom_unavailable:<i>": a freedom for an item that judgment_form offers none;
      "invalid:freedom_note:<i>": a freedom whose note is not a non-empty str;
      "invalid:cannot_tell:<i>": a cannot_tell whose value is not None.
    """
    if not isinstance(params, list) or not params or not all(isinstance(p, str) for p in params) or len(set(params)) != len(params):
        return "invalid:params"
    # the form is the one statement of what the ask takes and of which items offer a freedom; the stub types only
    # satisfy its last check, since params are already known good and the dispatch has no types of its own
    form = judgment_form(kind, items, params, {p: {} for p in params})
    if form == "invalid":
        return "invalid:stop"
    expected = form["properties"]["answers"]["properties"]
    if not (isinstance(reply, dict) and list(reply) == ["answers"] and isinstance(reply["answers"], dict) and set(reply["answers"]) == set(expected)):
        return "invalid:shape"

    inputs = []
    flags = []
    abstained = []
    for i, key in enumerate(expected):
        answer = reply["answers"][key]
        if not isinstance(answer, dict) or len(answer) != 1 or next(iter(answer)) not in ("supply", "freedom", "cannot_tell"):
            return f"invalid:answer:{i}"
        which, value = next(iter(answer.items()))
        if which == "supply":
            if not isinstance(value, dict) or set(value) != set(params):
                return f"invalid:supply_keys:{i}"
            inputs.append({"item": i, "values": value})
        elif which == "freedom":
            if not any("freedom" in branch["properties"] for branch in expected[key]["anyOf"]):
                return f"invalid:freedom_unavailable:{i}"
            if not isinstance(value, str) or not value:
                return f"invalid:freedom_note:{i}"
            flags.append({"item": i, "ids": [mutant["id"] for mutant in items[i]["mutants"]], "note": value})
        else:
            if value is not None:
                return f"invalid:cannot_tell:{i}"
            abstained.append(i)
    return {"inputs": inputs, "flags": flags, "abstained": abstained}


def expand_ast_template(template, selection, subtrees=None):
    """Substitute declared labels and typed subtree values into a signature and body."""
    required = {"signature", "code", "choices"}
    if not isinstance(template, dict) or not required <= template.keys() <= required | {"holes", "syntax"}:
        return "invalid_template", {}
    choices, holes = template["choices"], template.get("holes", {})
    if not isinstance(choices, dict) or not isinstance(holes, dict) or not (choices or holes):
        return "invalid_template", {}
    if choices.keys() & holes.keys():
        return "invalid_template", {}
    for name, options in choices.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) or not isinstance(options, dict) or not options:
            return "invalid_template", {}
        if any(not isinstance(label, str) or not label for label in options):
            return "invalid_template", {}
    if selection is None:
        selection = {name: next(iter(options)) for name, options in choices.items()}
    if not isinstance(selection, dict) or set(selection) != set(choices):
        return "invalid_selection", {}
    if any(not isinstance(label, str) or label not in choices[name] for name, label in selection.items()):
        return "invalid_selection", {}
    if subtrees is None:
        subtrees = {}
    if not isinstance(subtrees, dict) or set(subtrees) != set(holes):
        return "invalid_subtrees", {}

    values = {name: choices[name][label] for name, label in selection.items()} | subtrees
    used = set()

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$slot" not in value:
            return {key: expand(item) for key, item in value.items()}
        name = value["$slot"]
        if len(value) != 1 or not isinstance(name, str) or name not in values:
            raise ValueError("invalid slot")
        used.add(name)
        return values[name]

    try:
        cells = {key: expand(template[key]) for key in ("signature", "code")}
    except (ValueError, RecursionError):
        return "invalid_template", {}
    if used != set(values):
        return "invalid_template", {}
    return "expanded", cells


def reply_failure(done_reason):
    """The name of a model reply that has ALREADY failed to parse as JSON (pure): "truncated" when the call's own
    done_reason is exactly the str "length" (generation stopped at its token cap), "unparseable" for anything else."""
    if type(done_reason) is str and done_reason == "length":
        return "truncated"
    return "unparseable"


def _is_route(entry):
    """Whether entry is a dict with a str "route" and a str "ending" included (extra keys are allowed)."""
    return isinstance(entry, dict) and isinstance(entry.get("route"), str) and isinstance(entry.get("ending"), str)


def _shape_ok(outcome, setup, routes, expected):
    """Whether every argument has its documented shape and expected names each function once."""
    if not isinstance(outcome, str) or not _is_route(setup):
        return False
    if not isinstance(routes, dict) or not isinstance(expected, list):
        return False
    if not all(isinstance(qualname, str) and _is_route(entry) for qualname, entry in routes.items()):
        return False
    if not all(isinstance(qualname, str) for qualname in expected):
        return False
    return len(set(expected)) == len(expected)


def admission_bar(outcome, setup, routes, expected):
    """Whether a played unit meets the bar for placement (pure).

    outcome: the play's outcome, a str.
    setup: {"route": str, "ending": str} for the folder's setup run.
    routes: a dict from qualname to {"route": str, "ending": str}, one entry per function the play's Detective phase
        reached. A route is "done" or "handback".
    expected: the contract's functions, in call order, a list of distinct str. An empty list is legitimate: the
        contract's own verdict accepts a module with no function, and such a unit is judged by its setup alone.

    Returns the first that applies, checked in this order:
      "invalid": any argument has the wrong shape (a route entry that is not a dict with a str "route" and a str
                 "ending" included), or expected repeats a name;
      "not_written:<outcome>": outcome is not "written";
      "setup:<ending>": the setup's route is not "done";
      "missing:<qualname>": the first expected function that has no route;
      "not_done:<qualname>:<ending>": the first expected function whose route is not "done";
      "unexpected:<qualname>": the first routed function, in sorted order, that expected does not name;
      "pass".
    """
    if not _shape_ok(outcome, setup, routes, expected):
        return "invalid"
    if outcome != "written":
        return f"not_written:{outcome}"
    if setup["route"] != "done":
        return f"setup:{setup['ending']}"
    for qualname in expected:
        if qualname not in routes:
            return f"missing:{qualname}"
    for qualname in expected:
        if routes[qualname]["route"] != "done":
            return f"not_done:{qualname}:{routes[qualname]['ending']}"
    named = set(expected)
    for qualname in sorted(routes):
        if qualname not in named:
            return f"unexpected:{qualname}"
    return "pass"


DECISIONS = ("contract_wrong", "code_wrong", "machine_wrong", "cannot_tell")


def _is_count(value, minimum):
    """True for an int (never a bool) that is at least minimum."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_record(entry, keys):
    """True for a dict that holds every one of keys as a str."""
    return isinstance(entry, dict) and all(isinstance(entry.get(key), str) for key in keys)


def receiver_next(decision, current, history, unit_replays, ceiling):
    """The local path's next step for one hand-back (pure).

    decision: the local receiver's decision, one of DECISIONS.
    current: {"ending": str, "evidence": str} for the hand-back being decided. evidence is a digest of its evidence.
    history: the earlier rounds for the same function, oldest first, each {"decision": str, "ending": str,
        "evidence": str}.
    unit_replays: an int >= 0 (not a bool): how many times the whole unit has already been played again for a machine
        fault.
    ceiling: an int >= 1 (not a bool): the most local rounds one function may take.

    Rules, in this order:
      1. cannot_tell gives "escalate:cannot_tell";
      2. machine_wrong gives "escalate:second_machine_fault" when unit_replays >= 1, else "replay";
      3. an earlier round with the same ending and the same evidence as current gives "escalate:no_new_evidence";
      4. len(history) >= ceiling gives "escalate:ceiling";
      5. contract_wrong gives "revise"; code_wrong gives "reissue".
    Returns "invalid" for a decision not in DECISIONS, a current or history entry of the wrong shape, or an
    unit_replays or ceiling out of type or range.
    """
    # Shape first: a malformed input gets no verdict, whatever the decision says.
    if not (isinstance(decision, str) and decision in DECISIONS):
        return "invalid"
    if not _is_record(current, ("ending", "evidence")):
        return "invalid"
    if not isinstance(history, list):
        return "invalid"
    if not all(_is_record(entry, ("decision", "ending", "evidence")) for entry in history):
        return "invalid"
    if not (_is_count(unit_replays, 0) and _is_count(ceiling, 1)):
        return "invalid"

    # Rule 1: an abstention is never retried into an answer.
    if decision == "cannot_tell":
        return "escalate:cannot_tell"
    # Rule 2: the unit is played again once for a machine fault; the count is per unit.
    if decision == "machine_wrong":
        return "escalate:second_machine_fault" if unit_replays >= 1 else "replay"
    # Rule 3: a repeat of an earlier ending with the same evidence has nothing new to go on.
    for entry in history:
        if entry["ending"] == current["ending"] and entry["evidence"] == current["evidence"]:
            return "escalate:no_new_evidence"
    # Rule 4: the ceiling on rounds for one function.
    if len(history) >= ceiling:
        return "escalate:ceiling"
    # Rule 5: only contract_wrong and code_wrong are left.
    if decision == "contract_wrong":
        return "revise"
    return "reissue"


# ── The Detective phase's readings (docs/builds/2026-10-08/WIRING.md, W3 and W4) ───────────────────────────────────
#
# What the driver reads from one run's ``--json`` payload, the class of a finished run, and what the judgment seat is
# shown and asked. All are total over ``str`` / ``bool`` / ``int`` / ``list`` / ``dict``.

# How Detective's store of the inputs supplied read (``detective.samples``: Detective's own ``store_read_disposition``,
# ``undecided`` when that decision could not be loaded), and ``unrecorded`` when the run carried no reading of it.
STORE_READS = ("absent", "unreadable", "corrupt", "readable", "undecided", "unrecorded")


def supplied_inputs(samples: object, key: str, entry: object) -> dict:
    """Detective's record of the inputs supplied for one function, shown to the model beside its stop (pure; the
    founder, 2026-10-06: the whole list and the latest run's, labelled apart). ``samples`` is a run's reading of
    Detective's store (``detective.samples``), ``key`` the function's key in it (converge's own, ``<path>::<qualname>``),
    ``entry`` the run's ledger entry.

    - ``store``: how the store read (``STORE_READS``). A file Detective's decision names ``unreadable`` or ``corrupt``
      is never an empty store; ``absent`` is no file.
    - ``remembered``: every input the store holds for the function, from every run and session, each in the
      ``--input`` form. Converge replays each one its input grammar accepts, on every run.
    - ``last_run``: the inputs the run was given (its ledger entry's ``args.input``): ``[]`` when it was given none,
      ``None`` with no entry.

    Information, never a filter: an input already held may be supplied again."""
    read = samples.get("read") if isinstance(samples, dict) else None
    store = samples.get("store") if isinstance(samples, dict) else None
    held = store.get(key) if isinstance(store, dict) else None
    args = entry.get("args") if isinstance(entry, dict) else None
    given = args.get("input", []) if isinstance(args, dict) else None
    return {
        "store": read if read in STORE_READS else "unrecorded",
        "remembered": [h for h in held if isinstance(h, str)] if isinstance(held, list) else [],
        "last_run": [str(g) for g in given] if isinstance(given, list) else None,
    }


def _constants(node: ast.AST, members: bool) -> list:
    """The JSON scalars a literal operand states: itself, or with ``members`` (an ``in`` operand) each scalar of a
    literal collection; ``[]`` when the operand is no literal."""
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return []
    values = list(value) if members and isinstance(value, tuple | list | set | frozenset) else [value]
    return [v for v in values if v is None or isinstance(v, str | int | float | bool)]


def ask_pools(ask: object) -> dict | str:
    """The values each parameter's hole offers, mined from the code an ask prints (pure): the guard of each unreached
    line, the boundary relations, the internal conditions, each parsed as a Python expression. The rule is direct and
    shallow: a literal goes to a parameter when both are operands of one comparison (``qty == 0``; ``x in (1, 2)``
    gives each member), or the literal is an argument of a method called on the parameter (``code.startswith('VIP-')``).
    A literal beside no parameter is not attributed. The pool offers values and never narrows the hole: the parameter's
    type lock (``schema.param_schema``) admits a pooled value only of its type, and the typed wildcard stays beside it.

    ``{<parameter>: [values]}`` for each of the ask's parameters. Codes: ``invalid`` · ``unparseable`` (a printed
    condition that is not one Python expression: its values are unknown, never empty)."""
    if not isinstance(ask, dict) or not isinstance(ask.get("params"), list):
        return "invalid"
    names = [str(p.get("name")) for p in ask["params"] if isinstance(p, dict) and p.get("name")]
    texts = [str(g.get("when") or "") for g in ask.get("lines") or [] if isinstance(g, dict)]
    texts += [str(t) for t in [*(ask.get("relations") or []), *(ask.get("conditions") or [])]]
    pools: dict = {n: [] for n in names}

    def give(name: str, values: list) -> None:
        for v in values:
            if name in pools and (type(v), repr(v)) not in {(type(x), repr(x)) for x in pools[name]}:
                pools[name].append(v)

    for text in (t for t in texts if t.strip()):
        try:
            tree = ast.parse(text.strip(), mode="eval")
        except (SyntaxError, ValueError):
            return "unparseable"
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                for i, operand in enumerate(operands):
                    if isinstance(operand, ast.Name):
                        for j, other in enumerate(operands):
                            # the right side of the parameter's own ``in`` lists its members
                            members = j == i + 1 and isinstance(node.ops[i], ast.In | ast.NotIn)
                            if j != i:
                                give(operand.id, _constants(other, members))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                give(node.func.value.id, [v for a in node.args for v in _constants(a, False)])
    return pools


def pool_ask(kind: str, items: list, names: list) -> dict | str:
    """The ask ``ask_pools`` mines, built from Detective's typed items (pure): ``{"params": [{"name": <p>}, ...],
    "lines": [{"when": <a line's guard>}, ...], "relations": [<a boundary's relation>, ...], "conditions": [<an
    internal condition whose form is ``expression``>, ...]}``. A condition whose form is ``prose`` gives no pool (its
    text is no expression), and a sample or author ask has no items. Code: ``invalid`` (``kind`` is not one of
    ``SUPPLY_KINDS``, ``items`` is not a list of dicts, ``names`` is not a list of str)."""
    if kind not in SUPPLY_KINDS or not isinstance(items, list) or not all(isinstance(item, dict) for item in items) or not _is_str_list(names):
        return "invalid"
    ask: dict = {"params": [{"name": name} for name in names], "lines": [], "relations": [], "conditions": []}
    for item in items:
        if kind == "lines" and isinstance(item.get("guard"), str):
            ask["lines"].append({"when": item["guard"]})
        elif kind == "boundary" and isinstance(item.get("relation"), str):
            ask["relations"].append(item["relation"])
        elif kind == "internal" and item.get("form") == "expression" and isinstance(item.get("condition"), str):
            ask["conditions"].append(item["condition"])
    return ask


def abstained_keys(kind: str, items: list, indices: list) -> list | str:
    """The keys of the items a judgment answered ``cannot_tell`` (pure), so Detective's next ask for them is handed
    back and never put to the model twice: ``item_key(kind, items[i])`` for each index ``i``; for a ``sample`` or
    ``author`` ask, which has no items, the kind itself. Code: ``invalid`` (``kind`` is not one of ``SUPPLY_KINDS``,
    ``items`` or ``indices`` is not a list, an index is not an ``int`` (never a ``bool``) within the items, or an item
    has no key)."""
    if kind not in SUPPLY_KINDS or not isinstance(items, list) or not isinstance(indices, list):
        return "invalid"
    keys: list = []
    for index in indices:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            return "invalid"
        if kind in ("sample", "author"):
            keys.append(kind)
        elif index < len(items):
            keys.append(item_key(kind, items[index]))
        else:
            return "invalid"
    return "invalid" if "invalid" in keys else keys


def flag_argv(target: str, mutant_id: str, note: str, root: str) -> list | str:
    """The command that flags one of an ask's mutants as equivalent (pure): ``["flag", <target>, <mutant id>, "--note",
    <note>, "--json", "--project-root", <root>]``, built by the harness from Detective's own record and never from the
    model's text (``daemon_argv`` is not widened to ``flag``). Code: ``invalid`` (any of the four is not a non-empty
    str)."""
    if not all(isinstance(part, str) and part for part in (target, mutant_id, note, root)):
        return "invalid"
    return ["flag", target, mutant_id, "--note", note, "--json", "--project-root", root]


def payload_decision(verb: str, payload: object, refusal: object) -> dict | str:
    """The decision record one Detective ``--json`` run carries, or the ending that stands in its place (pure).
    ``verb`` is the command run, ``payload`` its stdout read as one JSON object (``None`` when it was not one),
    ``refusal`` the ``refusal`` its ledger entry names (``None`` or ``""`` when it names none: Detective's text-only
    exits, a target not found or a malformed target, record theirs there and print no JSON).

    Returns the record (a dict) when the payload holds one under ``decision``; else a code: ``<verb>:refused:<refusal>``
    for no payload with a ledger refusal · ``detective:unparsed`` for no payload and none · ``<verb>:refused:<reason>``
    for a typed refusal (``{"verdict": "REFUSED", "reason": ...}``; ``unnamed`` when it names no reason) ·
    ``<verb>:no_decision`` for a payload with no dict under ``decision``. ``invalid`` when ``verb`` is no non-empty
    str."""
    if not isinstance(verb, str) or not verb:
        return "invalid"
    if not isinstance(payload, dict):
        return f"{verb}:refused:{refusal}" if isinstance(refusal, str) and refusal else "detective:unparsed"
    if payload.get("verdict") == "REFUSED":
        reason = payload.get("reason")
        return f"{verb}:refused:{reason if isinstance(reason, str) and reason else 'unnamed'}"
    record = payload.get("decision")
    return record if isinstance(record, dict) else f"{verb}:no_decision"


def run_class(route: str, ending: str, exit_code: object) -> str:
    """The class of a finished run, a function's or the folder's (pure): ``clean`` when its route is ``done``; when it is
    ``handback``, the ending's own class (``ending_class``) with ``clean`` read as ``handed_back``: a run that did not
    reach Detective's done is never clean, whatever its last code said. Code: ``invalid`` (any other route, or an
    ending ``ending_class`` cannot read)."""
    if route == "done":
        return "clean"
    if route != "handback":
        return "invalid"
    held = ending_class(ending, exit_code)
    return "handed_back" if held == "clean" else held


# ── Wesker's categories, read from its source (docs/builds/2026-10-08/B6_wesker_categories.md; WIRING W7) ───────────
#
# The contract writer's record carries Wesker's operator categories as data, read from Wesker's own definition and
# never from a copied table. Wesker's engine.py imports its siblings relatively, so it is read as text: nothing of
# Wesker's is executed. The shell that finds the file is ``detective.wesker_categories``.


def wesker_categories(source):
    """Wesker's mutation categories, each with the syntax nodes its mutator visits, read from the text of Wesker's
    engine.py (pure; nothing of Wesker's is executed).

    source: the text of Wesker/engine.py.

    Returns a dict, {<category>: [<node name>, ...]}, in the order MutationCategory declares its members, each list
    sorted and without repeats; or one code (a str):
      "invalid"               source is not a str;
      "syntax_error"          source does not parse as Python;
      "no_categories"         the module has no top-level class named MutationCategory, or that class declares no member;
      "no_mutator:<MEMBER>"   a member whose mutator class the module does not define at its top level (the first
                              such member, in declaration order).

    The rules:
      1. A member of MutationCategory is a statement directly in the class body of the form NAME = "<text>": an
         assignment with exactly one target, a plain name, whose value is a str constant. Every other statement in the
         class body (a docstring, a comment, a method, an annotated or non-string assignment) is ignored. The category
         is the assigned text; MEMBER is the target's name.
      2. A member's mutator is the top-level class named "_" + MEMBER.capitalize() + "Mutator" (VALUE -> _ValueMutator,
         DATAFLOW -> _DataflowMutator).
      3. The nodes a mutator visits are the names, after "visit_", that the class's own body binds: a function
         defined directly in it (def or async def), or an assignment directly in it with exactly one target, a plain
         name (Wesker writes visit_AsyncFunctionDef = visit_FunctionDef), whose name starts with "visit_" and is
         longer than "visit_". What it only inherits is not read; a mutator that binds none gives [].
      4. Only top-level classes are read: a class nested in a function or another class is not MutationCategory and is
         not a mutator. When the module defines two top-level classes of one name, the later one is the one read, as
         Python binds the name.
    """
    if not isinstance(source, str):
        return "invalid"
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return "syntax_error"

    classes = {stmt.name: stmt for stmt in tree.body if isinstance(stmt, ast.ClassDef)}

    category_class = classes.get("MutationCategory")
    if category_class is None:
        return "no_categories"

    members = []
    for stmt in category_class.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            members.append((stmt.targets[0].id, stmt.value.value))
    if not members:
        return "no_categories"

    result = {}
    for member, category in members:
        mutator = classes.get("_" + member.capitalize() + "Mutator")
        if mutator is None:
            return "no_mutator:" + member
        names = set()
        for stmt in mutator.body:
            bound = None
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound = stmt.name
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                bound = stmt.targets[0].id
            if bound is not None and bound.startswith("visit_") and len(bound) > len("visit_"):
                names.add(bound[len("visit_") :])
        result[category] = sorted(names)
    return result
