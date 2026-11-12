"""Intent-authored tests for the dyad's hardware (``src/squishos/harness/``).

The theory is ``docs/theory/THE_DYAD.md``; each test names the law it holds. The pure
decisions are also Detective-pinned (``tests/detective/``) — those pins characterize
what the code does, these say what it is FOR. The loop is driven with a fake ``chat``
so no model is needed; the live measurements are in ``docs/spine/09-the-harness.md``.
"""

from __future__ import annotations

import ast
import copy
import http.server
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from uroboros.harness import contract, daemon, decisions, detective, schema
from uroboros.harness import model as model_mod
from uroboros.harness.cassette import find_cassette, list_cassettes, load_cassette
from uroboros.harness.cli import main
from uroboros.harness.model import TransportError
from uroboros.harness.run import invoke, plan, resolved_slots
from tests.detective_replay import fixture

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _bundled_library_only(monkeypatch):
    """Every test sees the bundled library and the default wire unless it says otherwise."""
    monkeypatch.delenv("UROBOROS_CASSETTE_PATH", raising=False)
    monkeypatch.delenv("UROBOROS_LLM_API", raising=False)
    monkeypatch.delenv("UROBOROS_LLM_ENDPOINT", raising=False)


@pytest.fixture(autouse=True)
def _no_model_left_recorded():
    """A test may record models through ``chat``; none may reach the real exit hook, which
    would send an unload to whatever server is running on this machine."""
    model_mod._used.clear()
    yield
    model_mod._used.clear()


@pytest.fixture
def unload_server():
    """A local HTTP server answering each POST with the next scripted (status, body), else
    200 ``{}``, and recording every (path, json body) it received."""
    seen: list = []
    replies: list = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            seen.append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            status, body = replies.pop(0) if replies else (200, "{}")
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):  # silence the per-request log; same signature as the base
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen, replies
    server.shutdown()
    server.server_close()


def _cassette(root: Path, cid: str, body: str, prompt: str = "Describe: {thing}\n") -> Path:
    d = root / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "cassette.toml").write_text(f'id = "{cid}"\n' + body)
    (d / "prompt.md").write_text(prompt)
    return d


# Wesker's categories as a test gives them to the contract writer's seat, so that no test reads this machine's Wesker.
_CATEGORIES = {"VALUE": ["Constant"], "BOUNDARY": ["Compare"], "OUTPUT": ["FunctionDef", "Return"]}

_PICK = """
category = "correctness"
warrant = "the object in hand"
discharge = "operator"
hand_back = "domain_value"
[model]
name = "m"
format = "schema"
[[slot]]
name = "family"
kind = "enum"
values = ["cd", "dvd", "bluray"]
allow_abstain = true
"""


class FakeChat:
    """Replays scripted replies; a reply that is an Exception is raised. Records every payload it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0
        self.payloads: list = []

    def __call__(self, endpoint, payload, timeout_s, api="openai"):
        self.calls += 1
        self.api = api
        self.payloads.append(payload)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, {"output_tokens": 5}


# ── Law 1: the category assigns the seat ─────────────────────────────────────


def test_category_is_normalized_and_anything_else_refused():
    assert decisions.category_code(" Correctness ") == "correctness"
    assert decisions.category_code("generative") == "generative"
    assert decisions.category_code("creative") == "unknown"
    assert decisions.cassette_verdict("creative", "schema", [], 1, 0, "w", "operator", "") == "unknown_category"


# ── the gate: each refusal is its own reason ─────────────────────────────────


def test_correctness_gate_refuses_json_mode_before_anything_else():
    """``format: "json"`` constrains shape, never values (the I-5 incident, Law 3)."""
    assert decisions.cassette_verdict("correctness", "json", ["unknown_kind"], 0, 0, "", "", "") == "json_mode_is_not_a_schema"
    assert decisions.cassette_verdict("correctness", "none", [], 1, 0, "w", "operator", "") == "correctness_needs_schema"


def test_correctness_gate_order():
    ok = ["ok"]
    assert decisions.cassette_verdict("correctness", "schema", ["ok", "empty_vocabulary"], 1, 0, "w", "operator", "") == "empty_vocabulary"
    assert decisions.cassette_verdict("correctness", "schema", ["ok"], 0, 1, "w", "operator", "") == "correctness_needs_value_slot"
    assert decisions.cassette_verdict("correctness", "schema", ok, 1, 0, "  ", "operator", "") == "no_warrant_declared"
    assert decisions.cassette_verdict("correctness", "schema", ok, 1, 0, "w", "", "") == "no_discharge_declared"
    assert decisions.cassette_verdict("correctness", "schema", ok, 1, 0, "w", "trust me", "") == "no_discharge_declared"
    assert decisions.cassette_verdict("correctness", "schema", ok, 1, 0, "w", "operator", "") == "ok"


def test_free_text_may_not_address_the_warrant():
    """Law 13: when the operator IS the warrant, nothing fluent is addressed to them.
    Beside a downstream measurement or a deterministic check, nobody who decides reads
    the prose, so a proposal is legal there."""
    ok = ["ok", "ok"]
    assert decisions.cassette_verdict("correctness", "schema", ok, 1, 1, "w", "operator", "") == "proposal_addresses_the_warrant"
    assert decisions.cassette_verdict("correctness", "schema", ok, 1, 1, "w", "downstream", "") == "ok"
    assert decisions.cassette_verdict("correctness", "schema", ok, 1, 1, "w", "check", "") == "ok"


def test_generative_gate():
    """Law 7 (no schema for generative work) and Law 8 (a proposal names where it lands)."""
    assert decisions.cassette_verdict("generative", "schema", [], 0, 0, "", "", "notes") == "generative_is_free_text"
    assert decisions.cassette_verdict("generative", "none", ["ok"], 1, 0, "", "", "notes") == "generative_takes_no_slots"
    assert decisions.cassette_verdict("generative", "none", [], 0, 0, "", "", " ") == "no_destination_declared"
    assert decisions.cassette_verdict("generative", "none", [], 0, 0, "", "", "notes") == "ok"


def test_slot_verdicts():
    assert decisions.slot_verdict("enum", "value", "inline", 3) == "ok"
    assert decisions.slot_verdict("enum", "value", "inline", 0) == "empty_vocabulary"
    assert decisions.slot_verdict("enum", "value", "runtime", 0) == "ok"  # checked at invocation
    assert decisions.slot_verdict("enum", "value", "prompt", 3) == "unknown_source"
    assert decisions.slot_verdict("text", "value", "", 0) == "text_must_be_proposal"
    assert decisions.slot_verdict("text", "proposal", "", 0) == "ok"
    assert decisions.slot_verdict("call_objects", "value", "inline", 0) == "call_objects_need_runtime_params"
    assert decisions.slot_verdict("regex", "value", "inline", 1) == "unknown_kind"
    assert decisions.slot_verdict("int", "value", "", 0) == "ok"


def test_discharge_code():
    assert [decisions.discharge_code(x) for x in ("Downstream", "check", " operator ", "", "vibes")] == ["downstream", "check", "operator", "unknown", "unknown"]


def test_every_bundled_cassette_passes_its_gate():
    """A shipped cartridge that the gate refuses is a broken build, not a runtime surprise."""
    bundled = [load_cassette(d) for _, d in list_cassettes()]
    assert {c.id for c in bundled} >= {"example", "test-input", "intent-check", "contract-write"}
    assert {c.id: c.verdict() for c in bundled} == {c.id: "ok" for c in bundled}


# ── invocation, admission, the loop ──────────────────────────────────────────


def test_invocation_checks_the_wire_then_evidence_then_vocabulary():
    assert decisions.invocation_verdict("grpc", ["x"], ["work"]) == "unknown_api"
    assert decisions.invocation_verdict("openai", ["x"], ["work"]) == "evidence_missing"
    assert decisions.invocation_verdict("ollama", [], ["work"]) == "vocabulary_missing"
    assert decisions.invocation_verdict("openai", [], []) == "ok"


def test_admission_names_every_state():
    assert decisions.admission(False, 0, 0, 0, 1) == "unparseable"
    assert decisions.admission(True, 1, 0, 0, 1) == "schema_violation"
    assert decisions.admission(True, 0, 1, 0, 1) == "off_vocabulary"
    assert decisions.admission(True, 0, 0, 2, 0) == "abstained"
    assert decisions.admission(True, 0, 0, 1, 1) == "partial"
    assert decisions.admission(True, 0, 0, 0, 0) == "empty"
    assert decisions.admission(True, 0, 0, 0, 2) == "admitted"


def test_next_move_never_retries_an_abstention():
    """Law 5: the gap is information. Budget left over changes nothing."""
    assert decisions.next_move("abstained", 0, 99, "") == "hand_back"
    assert decisions.next_move("admitted", 0, 2, "") == "accept"
    assert decisions.next_move("partial", 0, 2, "") == "accept"
    assert decisions.next_move("off_vocabulary", 0, 2, "") == "stop_contract_breach"
    assert decisions.next_move("empty", 0, 2, "") == "stop_contract_breach"
    assert decisions.next_move("mystery", 0, 2, "") == "stop_unknown"
    assert decisions.next_move("unparseable", 1, 5, "unparseable") == "stop_no_progress"
    assert decisions.next_move("unparseable", 1, 2, "transport_error") == "stop_budget"
    assert decisions.next_move("transport_error", 0, 2, "") == "retry"


def test_signs_are_injective():
    """Law 10: four remedies, four exit codes; the loop's statuses never share one."""
    assert [decisions.exit_code(s) for s in ("admitted", "partial", "proposal", "abstained", "refused", "failed")] == [0, 0, 0, 1, 2, 3]
    assert decisions.outcome_status("accept", "partial") == "partial"
    assert decisions.outcome_status("accept", "admitted") == "admitted"
    assert decisions.outcome_status("hand_back", "abstained") == "abstained"
    assert decisions.outcome_status("retry", "unparseable") == "pending"
    assert decisions.outcome_status("stop_no_progress", "unparseable") == "failed"


def test_model_availability_never_matches_by_prefix():
    """A prefix match would silently substitute a different model."""
    assert decisions.model_availability(False, [], "qwen3:4b") == "unreachable"
    assert decisions.model_availability(True, ["qwen3:4b"], "qwen3:4b") == "present"
    assert decisions.model_availability(True, ["gemma3:latest"], "gemma3") == "present"
    assert decisions.model_availability(True, ["qwen3:4b-instruct"], "qwen3:4b") == "absent"
    assert decisions.model_availability(True, ["x"], " ") == "absent"


def test_library_path_env_first_then_bundled_deduplicated():
    assert decisions.library_path("/a: /b ::/a", "/bundled") == ["/a", "/b", "/bundled"]
    assert decisions.library_path("", "/bundled") == ["/bundled"]
    assert decisions.library_path("/bundled", "/bundled") == ["/bundled"]


def test_small_normalizers():
    assert decisions.hand_back_kind(" Fixture ") == "fixture"
    assert decisions.hand_back_kind("vibes") == "unspecified"
    assert decisions.endpoint_source("http://h", "http://c") == "env"
    assert decisions.endpoint_source(" ", "http://c") == "cassette"
    assert decisions.endpoint_source("", "") == "default"


# ── the wire: the engine is the operator's choice, not the cassette's ─────────


def test_api_flavor_never_falls_back_to_a_wire_nobody_asked_for():
    """The environment outranks the cartridge; an unrecognised name is refused, not
    quietly replaced with the default (Law 10)."""
    assert decisions.api_flavor("", "") == "openai"
    assert decisions.api_flavor("", " Ollama ") == "ollama"
    assert decisions.api_flavor("ollama", "openai") == "ollama"
    assert decisions.api_flavor("grpc", "openai") == "unknown"
    assert decisions.api_flavor("", "carrier-pigeon") == "unknown"


def test_served_model_name_and_default_endpoint():
    """One canonical name in the cassette; each engine's own spelling derived from it.
    llama.cpp's router rewrites a name containing ':' (measured), so the colon goes."""
    tag = "qwen3:4b-instruct-2507-q4_K_M"
    assert decisions.served_model_name(tag, "openai") == "qwen3-4b-instruct-2507-q4_K_M"
    assert decisions.served_model_name(tag, "ollama") == tag
    assert decisions.default_endpoint("openai").endswith(":8080/v1")
    assert decisions.default_endpoint("ollama").endswith(":11434")
    assert decisions.default_endpoint("unknown") == ""


def test_openai_options_names_what_it_cannot_carry():
    """num_ctx is a server fact on llama-server (fixed when the model loads), so it is
    reported as dropped rather than silently ignored."""
    sent, dropped = decisions.openai_options({"temperature": 0.1, "num_predict": 64, "num_ctx": 4096})
    assert sent == {"temperature": 0.1, "max_tokens": 64}
    assert dropped == ["num_ctx"]
    assert decisions.openai_options({}) == ({}, [])
    # Key ORDER is pinned, not incidental: the same call must serialise to the same
    # bytes twice, or a request digest cannot be evidence of what was measured.
    # (Equality cannot see this, which is why Detective declined to write it.)
    ordered, dropped = decisions.openai_options({"top_p": 0.9, "num_ctx": 1, "temperature": 0.1, "cheese": 2})
    assert list(ordered) == ["temperature", "top_p"]
    assert dropped == ["cheese", "num_ctx"]


def test_request_payload_per_wire(tmp_path):
    fmt = {"type": "object", "properties": {}}
    opts = {"temperature": 0.1, "num_ctx": 4096, "num_predict": 64}
    ollama, dropped = model_mod.request_payload("ollama", "m:tag", [{"role": "user", "content": "x"}], fmt, opts, False, "10m")
    assert (ollama["model"], ollama["format"], ollama["options"], ollama["think"], ollama["keep_alive"]) == ("m:tag", fmt, opts, False, "10m")
    assert dropped == []
    openai, dropped = model_mod.request_payload("openai", "m:tag", [{"role": "user", "content": "x"}], fmt, opts, False, "10m")
    assert openai["model"] == "m-tag" and "format" not in openai and openai["max_tokens"] == 64
    assert openai["response_format"] == {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": fmt}}
    # `think` is TRANSLATED, never dropped: a thinking model left thinking returns empty
    # content (measured). Only keep_alive has nowhere to go on this wire.
    assert openai["chat_template_kwargs"] == {"enable_thinking": False}
    assert dropped == ["num_ctx", "keep_alive"]
    free, _ = model_mod.request_payload("openai", "m", [], None, {}, None, "")
    assert "response_format" not in free and "chat_template_kwargs" not in free


def test_chat_telemetry_is_the_same_shape_on_both_wires(monkeypatch):
    """A measurement has to survive an engine swap, so the keys never change."""
    monkeypatch.setattr(model_mod, "_post", lambda url, payload, t: {"message": {"content": "hi"}, "eval_count": 7, "prompt_eval_count": 3, "done_reason": "stop"})
    content, tel = model_mod.chat("http://x", {}, 1.0, "ollama")
    assert (content, tel["output_tokens"], tel["done_reason"], tel["api"]) == ("hi", 7, "stop", "ollama")
    monkeypatch.setattr(model_mod, "_post", lambda url, payload, t: {"choices": [{"message": {"content": "hi"}, "finish_reason": "length"}], "usage": {"completion_tokens": 7, "prompt_tokens": 3}})
    content, tel = model_mod.chat("http://x/v1", {}, 1.0, "openai")
    assert (content, tel["output_tokens"], tel["done_reason"], tel["api"]) == ("hi", 7, "length", "openai")
    assert set(tel) == {"prompt_tokens", "output_tokens", "eval_ms", "total_ms", "load_ms", "done_reason", "api"}


# ── residency: a process releases what it used ───────────────────────────────


def test_an_unload_is_addressed_per_wire():
    """The router unloads at its root, beside /v1; Ollama with keep_alive 0 (measured 2026-10-04)."""
    assert decisions.unload_request("openai", "http://h:8080/v1", "qwen3-4b") == ("http://h:8080/models/unload", {"model": "qwen3-4b"})
    assert decisions.unload_request("openai", "http://h:8080", "m") == ("http://h:8080/models/unload", {"model": "m"})
    assert decisions.unload_request("ollama", "http://h:11434", "qwen3:4b") == ("http://h:11434/api/generate", {"model": "qwen3:4b", "keep_alive": 0})
    assert decisions.unload_request("lmstudio", "http://h", "m") == ("", {})


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (200, '{"success": true}', "released"),
        (200, '{"done": true, "done_reason": "unload"}', "released"),  # Ollama, resident or not
        (400, '{"error": {"code": 400, "message": "model is not running"}}', "not_running"),
        (400, '{"error": {"code": 400, "message": "model is not found"}}', "unknown_model"),
        (404, '{"error": "model \'no-such-model\' not found"}', "unknown_model"),
        (404, '{"detail": "Not Found"}', "rejected"),  # a /v1 server with no unload route
        (500, "boom", "rejected"),
        (0, "", "unreachable"),
    ],
)
def test_an_unload_reply_has_a_named_meaning(status, body, code):
    assert decisions.release_outcome(status, body) == code


def test_a_successful_call_records_the_model_it_used(monkeypatch):
    monkeypatch.setattr(model_mod, "_post", lambda url, payload, t: {"choices": [{"message": {"content": "ok"}}]})
    model_mod.chat("http://h:8080/v1", {"model": "qwen3-4b"}, 1.0, "openai")
    assert model_mod._used == {("openai", "http://h:8080/v1", "qwen3-4b")}


def test_a_failed_call_records_nothing(monkeypatch):
    def refuse(url, payload, timeout_s):
        raise TransportError("unreachable: refused")

    monkeypatch.setattr(model_mod, "_post", refuse)
    with pytest.raises(TransportError):
        model_mod.chat("http://h:8080/v1", {"model": "qwen3-4b"}, 1.0, "openai")
    assert model_mod._used == set()


def test_release_unloads_through_the_router_route(unload_server):
    url, seen, _ = unload_server
    model_mod._used.add(("openai", f"{url}/v1", "qwen3-4b"))
    assert model_mod.release("openai", f"{url}/v1", "qwen3-4b") == "released"
    assert seen == [("/models/unload", {"model": "qwen3-4b"})]
    assert model_mod._used == set()


def test_a_release_that_fails_keeps_the_record(unload_server):
    url, _, replies = unload_server
    replies.append((404, '{"detail": "Not Found"}'))
    model_mod._used.add(("openai", f"{url}/v1", "m"))
    assert model_mod.release("openai", f"{url}/v1", "m") == "rejected"
    assert model_mod._used == {("openai", f"{url}/v1", "m")}


def test_an_unreachable_server_is_named_not_raised():
    assert model_mod.release("ollama", "http://127.0.0.1:9", "m", timeout_s=2.0) == "unreachable"


def test_keep_loaded_skips_the_exit_release(monkeypatch, unload_server):
    url, seen, _ = unload_server
    model_mod._used.add(("ollama", url, "m"))
    monkeypatch.setenv("UROBOROS_LLM_KEEP_LOADED", "1")
    assert model_mod.release_used() == {}
    assert seen == []


def test_the_process_releases_its_models_at_exit(unload_server):
    """End to end: a fresh interpreter calls the real wire and exits; the server hears the unload."""
    url, seen, replies = unload_server
    replies.append((200, '{"choices": [{"message": {"content": "ok"}}]}'))
    code = textwrap.dedent(
        f"""
        from uroboros.harness import model
        model.chat("{url}/v1", {{"model": "qwen3-4b", "messages": []}}, 5.0, "openai")
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "UROBOROS_LLM_KEEP_LOADED"}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False, env=env)
    assert result.returncode == 0, result.stderr
    assert [path for path, _ in seen] == ["/v1/chat/completions", "/models/unload"]
    assert seen[1][1] == {"model": "qwen3-4b"}


# ── the renderer: the only place syntax is written ───────────────────────────


def test_render_call_args_writes_data_never_code():
    assert decisions.render_call_args(["a"], {"a": 1}) == "(1,)"
    assert decisions.render_call_args(["a", "b"], {"b": [1], "a": "x"}) == "('x', [1])"
    assert decisions.render_call_args(["a", "b"], {"a": 1}) == ""
    hostile = "__import__('os').system('rm -rf /')"
    assert decisions.render_call_args(["a"], {"a": hostile}) == repr((hostile,))  # a quoted literal, inert


def test_render_drops_what_does_not_round_trip():
    """The reply parser (``json.loads``) admits ``NaN``/``Infinity``; their ``repr`` is
    not a Python literal. Such a selection is dropped, never rendered as a broken input."""
    assert decisions.render_call_args(["a"], json.loads('{"a": Infinity}')) == ""
    assert decisions.render_call_args(["a"], {"a": float("inf")}) == ""
    assert decisions.render_call_args(["a", "b"], {"a": 1, "b": float("nan")}) == ""


# ── the typed hole ───────────────────────────────────────────────────────────


def test_cassette_schema_locks_every_field():
    slots = [
        {"name": "family", "kind": "enum", "vocabulary": ["cd", "dvd"], "allow_abstain": True},
        {"name": "tags", "kind": "enum_list", "vocabulary": ["a"], "allow_abstain": False, "max_items": 3},
        {"name": "why", "kind": "text", "role": "proposal"},
    ]
    s = schema.cassette_schema(slots)
    assert s["required"] == ["family", "tags", "why"] and s["additionalProperties"] is False
    assert s["properties"]["family"] == {"anyOf": [{"enum": ["cd", "dvd"]}, {"type": "null"}]}
    assert s["properties"]["tags"] == {"type": "array", "items": {"enum": ["a"]}, "maxItems": 3, "minItems": 1}
    assert s["properties"]["why"] == {"type": "string"}


def test_call_objects_are_arity_locked_with_type_locked_wildcards():
    params = [{"name": "code", "type": "str", "pool": ["x", "y"]}, {"name": "n", "type": "int", "pool": []}, {"name": "z", "type": "Weird"}]
    s = schema.slot_schema("call_objects", [], True, 4, params)
    call = s["items"]
    assert call["required"] == ["code", "n", "z"] and call["additionalProperties"] is False
    assert call["properties"]["code"] == {"anyOf": [{"enum": ["x", "y"]}, {"type": "string"}]}
    assert call["properties"]["n"] == {"type": "integer"}
    assert {"type": "string"} in call["properties"]["z"]["anyOf"]
    assert s["maxItems"] == 4 and "minItems" not in s
    assert schema.slot_schema("regex", [], False, 1, []) == {}


def test_param_schema_pool_beside_a_type_lock():
    """The mined pool is offered; the wildcard is locked to the declared type, and so is
    the pool: a pooled value of another type is left out, so the pool never widens the
    hole. With no usable type the value is still a JSON scalar — arity never opens
    (THE_DYAD II.4). A type that admits None admits null. An untyped parameter takes
    any JSON value (the founder, 2026-10-06).
    Called through the module attribute so a mutation of the name is what runs."""
    scalar_types = [{"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}]
    assert schema.param_schema({}) == {"anyOf": scalar_types}
    assert schema.param_schema({"type": "Weird", "pool": ["x", [1], None]}) == {"anyOf": [{"enum": ["x"]}, *scalar_types]}
    assert schema.param_schema({"type": "int", "pool": [1, 2]}) == {"anyOf": [{"enum": [1, 2]}, {"type": "integer"}]}
    assert schema.param_schema({"type": "int", "pool": [0, "VIP-", True, 1.5]}) == {"anyOf": [{"enum": [0]}, {"type": "integer"}]}
    assert schema.param_schema({"type": "float", "pool": [1, 1.5, "x"]}) == {"anyOf": [{"enum": [1, 1.5]}, {"type": "number"}]}
    assert schema.param_schema({"type": "str", "pool": [1]}) == {"type": "string"}
    assert schema.param_schema({"type": " str ", "pool": []}) == {"type": "string"}
    assert schema.param_schema({"type": "bool", "pool": None}) == {"type": "boolean"}
    assert schema.param_schema({"type": "list", "pool": [[1]]}) == {"anyOf": [{"enum": [[1]]}, {"type": "array"}]}
    assert schema.param_schema({"type": "int|None", "pool": [0, None]}) == {"anyOf": [{"enum": [0, None]}, {"type": "integer"}, {"type": "null"}]}
    assert schema.param_schema({"type": "Weird|None"}) == {"anyOf": [*scalar_types, {"type": "null"}]}
    anything = [{"type": t} for t in ("string", "integer", "number", "boolean", "array", "object", "null")]
    assert schema.param_schema({"type": schema.ANY_JSON}) == {"anyOf": anything}
    assert schema.param_schema({"type": " any ", "pool": [[1], "VIP-", (1, 2)]}) == {"anyOf": [{"enum": [[1], "VIP-"]}, *anything]}


def test_tally_never_counts_a_proposal():
    """Law 8: free text is not a value, whatever it says."""
    slots = [
        {"name": "family", "kind": "enum", "vocabulary": ["cd"]},
        {"name": "n", "kind": "int"},
        {"name": "why", "kind": "text", "role": "proposal"},
    ]
    assert schema.tally(slots, {"family": "cd", "n": 2, "why": "cd"}) == {"missing": 0, "off_vocabulary": 0, "abstained": 0, "filled": 2}
    assert schema.tally(slots, {"family": "vhs", "n": None}) == {"missing": 0, "off_vocabulary": 1, "abstained": 1, "filled": 0}
    assert schema.tally(slots, {"n": True}) == {"missing": 2, "off_vocabulary": 0, "abstained": 0, "filled": 0}


def test_template_fields():
    assert schema.template_fields("{a} and {b} and {a} {{literal}}") == ["a", "b"]
    assert schema.template_fields("unclosed {a") == []


def test_answer_block_offers_the_null():
    block = schema.answer_block([{"name": "family", "kind": "enum", "vocabulary": ["cd"], "allow_abstain": True}])
    assert "never invent" in block and "or null if none applies" in block


# ── the loader and the library path ──────────────────────────────────────────


def test_file_vocabulary_and_its_absence(tmp_path):
    body = _PICK.replace('values = ["cd", "dvd", "bluray"]', 'source = "file"')
    d = _cassette(tmp_path, "filed", body)
    assert load_cassette(d).verdict() == "empty_vocabulary"  # absent file: refused, never a silent pass
    (d / "vocab").mkdir()
    (d / "vocab" / "family.txt").write_text("# the families\ncd\n\ndvd\n")
    c = load_cassette(d)
    assert c.slots[0].vocabulary == ("cd", "dvd") and c.verdict() == "ok"


def test_private_library_shadows_bundled(tmp_path, monkeypatch):
    _cassette(tmp_path, "example", _PICK.replace('warrant = "the object in hand"', 'warrant = "PRIVATE"'))
    monkeypatch.setenv("UROBOROS_CASSETTE_PATH", str(tmp_path))
    assert find_cassette("example").warrant == "PRIVATE"
    with pytest.raises(LookupError):
        find_cassette("no-such-operation")


def test_not_a_cassette(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_cassette(tmp_path)


# ── the loop, end to end with a fake model ───────────────────────────────────


def test_admitted_carries_its_discharge(tmp_path):
    c = load_cassette(_cassette(tmp_path, "pick", _PICK))
    chat = FakeChat('{"family": "dvd"}')
    o = invoke(c, {"thing": "a disc"}, chat=chat)
    assert (o.status, o.values, o.discharge, chat.calls) == ("admitted", {"family": "dvd"}, "operator", 1)


def test_abstention_is_handed_back_typed_and_never_retried(tmp_path):
    c = load_cassette(_cassette(tmp_path, "pick", _PICK))
    chat = FakeChat('{"family": null}', '{"family": "cd"}')
    o = invoke(c, {"thing": "?"}, chat=chat)
    assert (o.status, o.abstained_slots, o.hand_back, chat.calls) == ("abstained", ["family"], "domain_value", 1)


def test_unparseable_retries_then_admits(tmp_path):
    c = load_cassette(_cassette(tmp_path, "pick", _PICK))
    o = invoke(c, {"thing": "x"}, chat=FakeChat('{"family": "d', '{"family": "cd"}'))
    assert o.status == "admitted" and [r.admission for r in o.records] == ["unparseable", "admitted"]


def test_off_vocabulary_is_a_contract_breach(tmp_path):
    c = load_cassette(_cassette(tmp_path, "pick", _PICK))
    chat = FakeChat('{"family": "vhs"}', '{"family": "cd"}')
    o = invoke(c, {"thing": "x"}, chat=chat)
    assert (o.status, o.reason, chat.calls) == ("failed", "stop_contract_breach", 1)


def test_transport_twice_is_no_progress(tmp_path):
    c = load_cassette(_cassette(tmp_path, "pick", _PICK + "[guards]\npass_budget = 5\n"))
    o = invoke(c, {"thing": "x"}, chat=FakeChat(TransportError("down"), TransportError("down")))
    assert (o.status, o.reason, len(o.records)) == ("failed", "stop_no_progress", 2)
    assert o.records[0].error == "down"


def test_refusals_never_touch_the_model(tmp_path):
    chat = FakeChat()
    pick = load_cassette(_cassette(tmp_path, "pick", _PICK))
    assert (invoke(pick, {}, chat=chat).reason, chat.calls) == ("evidence_missing", 0)
    runtime = load_cassette(_cassette(tmp_path, "rt", _PICK.replace('values = ["cd", "dvd", "bluray"]', 'source = "runtime"')))
    assert invoke(runtime, {"thing": "x"}, chat=chat).reason == "vocabulary_missing"
    refused = load_cassette(_cassette(tmp_path, "bad", _PICK.replace('discharge = "operator"', "")))
    o = invoke(refused, {"thing": "x"}, chat=chat)
    assert (o.status, o.reason, chat.calls) == ("refused", "no_discharge_declared", 0)


def test_runtime_vocabulary_is_the_decode_time_enum(tmp_path):
    c = load_cassette(_cassette(tmp_path, "rt", _PICK.replace('values = ["cd", "dvd", "bluray"]', 'source = "runtime"')))
    p = plan(c, {"thing": "x"}, {"family": ["laserdisc"]})
    sent = p["payload"]["response_format"]["json_schema"]["schema"]
    assert sent["properties"]["family"]["anyOf"][0] == {"enum": ["laserdisc"]}
    assert "Describe: x" in p["payload"]["messages"][-1]["content"]
    assert (p["api"], p["endpoint"]) == ("openai", "http://localhost:8080/v1")


def test_the_operator_swaps_the_engine_without_touching_a_cassette(tmp_path, monkeypatch):
    c = load_cassette(_cassette(tmp_path, "pick", _PICK))
    monkeypatch.setenv("UROBOROS_LLM_API", "ollama")
    monkeypatch.setenv("UROBOROS_LLM_ENDPOINT", "http://elsewhere:11434")
    p = plan(c, {"thing": "x"})
    assert (p["api"], p["endpoint"]) == ("ollama", "http://elsewhere:11434")
    assert "format" in p["payload"] and "response_format" not in p["payload"]
    monkeypatch.setenv("UROBOROS_LLM_API", "carrier-pigeon")
    chat = FakeChat()
    assert invoke(c, {"thing": "x"}, chat=chat).reason == "unknown_api"
    assert chat.calls == 0


def test_generative_output_is_a_proposal_and_never_a_value(tmp_path):
    body = 'category = "generative"\ndestination = "notes.proposed"\n[model]\nname = "m"\nformat = "none"\n'
    c = load_cassette(_cassette(tmp_path, "note", body))
    assert "response_format" not in plan(c, {"thing": "x"})["payload"]
    o = invoke(c, {"thing": "x"}, chat=FakeChat("A lovely disc."))
    assert (o.status, o.values, o.proposals, o.discharge) == ("proposal", {}, {"text": "A lovely disc."}, "")


def test_call_objects_render_through_the_harness(tmp_path):
    body = """
category = "correctness"
warrant = "the mutation kill"
discharge = "downstream"
render = "python_call_args"
[model]
name = "m"
[[slot]]
name = "calls"
kind = "call_objects"
source = "runtime"
allow_abstain = true
max_items = 3
[[slot]]
name = "note"
kind = "text"
role = "proposal"
"""
    c = load_cassette(_cassette(tmp_path, "ti", body))
    params = [{"name": "a", "type": "str", "pool": ["x"]}, {"name": "b", "type": "int", "pool": [0]}]
    reply = json.dumps({"calls": [{"a": "x", "b": 0}, {"a": "x", "b": 0}, {"a": "y", "b": 7}], "note": "edge"})
    o = invoke(c, {"thing": "f"}, params=params, chat=FakeChat(reply))
    assert o.status == "admitted" and o.rendered == ["('x', 0)", "('y', 7)"]
    assert o.proposals == {"note": "edge"} and "note" not in o.values


# ── the real command, offline ────────────────────────────────────────────────


def test_cli_dry_run_prints_the_exact_request(capsys):
    assert main(["run", "example", "-e", "description=a shiny blue-laser disc", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "ok"
    assert out["payload"]["response_format"]["json_schema"]["schema"]["required"] == ["family"]
    assert out["ignored_options"] == ["num_ctx", "keep_alive"]  # Ollama-only knobs, named not swallowed


def test_cli_refusals_exit_2(capsys):
    assert main(["run", "example"]) == 2  # evidence missing: refused before any model is touched
    assert "[refused]" in capsys.readouterr().out
    assert main(["show", "no-such-operation"]) == 2


def test_cli_show_names_the_discharge(capsys):
    assert main(["show", "example"]) == 0
    assert "discharge  operator" in capsys.readouterr().out


# ── the writing call: a typed hole held to its unit's shape contract ────────────────────────────────────────────
#
# The founder, 2026-10-05: the writing call falls «under the same global ruleset»; «Write up a JSON contract of the
# permissible constrained shape alongside the "intent" contract»; «Tunable global set of sane operations,
# function-specific contract in operation as a narrower scoping».

_POLARITY_STUB = '''HOLE = "polarity"


def polarity(clause_sign: int, prefix_code: str) -> str:
    """A claim's composed sign."""
    raise NotImplementedError
'''

_POLARITY_BODY = """if not isinstance(clause_sign, int) or isinstance(clause_sign, bool):
    return 'invalid'
if clause_sign not in (1, -1) or prefix_code not in ('none', 'negation', 'reversal'):
    return 'invalid'
if (clause_sign == 1) == (prefix_code == 'negation'):
    return 'negative'
return 'positive'"""


def _global_syntax() -> list:
    return list(next(s.vocabulary for s in find_cassette("function-body").slots if s.kind == "code"))


def _polarity_shape(**extra) -> dict:
    shape = {
        "names": ["isinstance", "int", "bool", "clause_sign", "prefix_code"],
        "attributes": [],
        "strings": ["none", "negation", "reversal", "positive", "negative", "invalid"],
        "numbers": [1],
        "outcomes": ["positive", "negative", "invalid"],
    }
    shape.update(extra)
    return shape


def _typed(body: str) -> str:
    """A body's text as the engine sees it typed: JSON-escaped, non-ASCII kept (measured 2026-10-05)."""
    return json.dumps(body, ensure_ascii=False)[1:-1]


def test_a_code_slot_declares_its_global_syntax():
    """The syntax is data on the slot, tunable; the unit's shape arrives at run time."""
    assert decisions.slot_verdict("code", "value", "inline", 47) == "ok"
    assert decisions.slot_verdict("code", "value", "file", 3) == "ok"
    assert decisions.slot_verdict("code", "value", "inline", 1) == "ok"
    assert decisions.slot_verdict("code", "value", "inline", 0) == "empty_vocabulary"
    assert decisions.slot_verdict("code", "value", "runtime", 0) == "code_needs_declared_syntax"
    assert decisions.shape_verdict(_polarity_shape(), _global_syntax()) == "ok"


def test_shape_verdict_names_every_fault():
    syntax = _global_syntax()
    cases = [
        (_polarity_shape(), syntax + ["while"], "syntax_unknown"),
        ({"names": [], "attributes": [], "strings": []}, syntax, "invalid"),
        ("shape", syntax, "invalid"),
        (_polarity_shape(outcomes="positive"), syntax, "invalid"),
        (_polarity_shape(notes="x"), syntax, "unknown_field"),
        (_polarity_shape(names=["1x"]), syntax, "name_invalid"),
        (_polarity_shape(names=["café"]), syntax, "name_invalid"),
        (_polarity_shape(names=["for"]), syntax, "name_keyword"),
        (_polarity_shape(names=["__class__"]), syntax, "name_dunder"),
        (_polarity_shape(names=["eval"]), syntax, "name_forbidden"),
        (_polarity_shape(attributes=["_secret"]), syntax, "attribute_invalid"),
        (_polarity_shape(strings=[1]), syntax, "string_invalid"),
        (_polarity_shape(numbers=[True]), syntax, "number_invalid"),
        (_polarity_shape(numbers=[-1]), syntax, "number_invalid"),
        (_polarity_shape(numbers=[float("inf")]), syntax, "number_invalid"),
        (_polarity_shape(syntax=["if", "while"]), syntax, "syntax_not_permitted"),
        (_polarity_shape(outcomes=["maybe"]), syntax, "outcome_not_permitted"),
        (_polarity_shape(max_chars=-5), syntax, "max_chars_invalid"),
        (_polarity_shape(max_chars=True), syntax, "max_chars_invalid"),
        (_polarity_shape(max_chars=1500), syntax, "ok"),
    ]
    assert [decisions.shape_verdict(s, g) for s, g, _ in cases] == [c for _, _, c in cases]


def test_the_pattern_holds_the_shape_and_nothing_else():
    """Constrained output (structured-web §3a): while it is typed, a body holds only what its shape grants."""
    import re

    syntax = _global_syntax()
    pattern = decisions.code_pattern(_polarity_shape(), syntax)

    def held(body: str, p: str = pattern) -> bool:
        return re.fullmatch(p, _typed(body)) is not None

    assert held(_POLARITY_BODY)
    assert held(_POLARITY_BODY + "\n")
    assert not held("import os\nreturn 'invalid'")  # import is not in the syntax
    assert not held("return 'maybe'")  # a string the shape does not grant
    assert not held("return prefix_codeclause_sign")  # two granted names cannot run together
    assert not held("while clause_sign:\n    pass")  # while is not in the syntax
    assert not held("return len(prefix_code)")  # a name the shape does not grant
    assert not held("return 2")  # a number the shape does not grant
    assert not held("return 'invalid'  # done")  # a comment cannot be typed
    assert not held("return 'invalid'\treturn 'invalid'")  # nor a tab
    narrowed = decisions.code_pattern(_polarity_shape(syntax=["if", "not", "or", "in", "return", "==", "-", "(", ")", ",", ":"]), syntax)
    assert held(_POLARITY_BODY, narrowed)
    assert not held("return clause_sign and 'invalid'", narrowed)  # and is on globally, off for this function
    assert decisions.code_pattern({"names": "x"}, syntax) == "^$"


def test_declared_strings_are_spelled_right_at_every_layer():
    """A declared string reaches the pattern as Python source, then JSON, then regex (Codex, 2026-10-05)."""
    import ast
    import re

    strings = ["it's", "a.b", 'say "x"', "’", "back\\slash", "tab\there"]
    pattern = decisions.code_pattern({"names": ["s"], "attributes": [], "strings": strings, "numbers": []}, _global_syntax())
    for value in strings:
        literal = json.loads('"' + decisions._code_string(value) + '"')
        assert ast.literal_eval(literal) == value
        assert re.fullmatch(pattern, _typed("return " + literal))
    assert not re.fullmatch(pattern, _typed("return 'aXb'"))  # the dot is a dot


def test_a_code_value_is_rechecked_as_it_was_typed():
    pattern = decisions.code_pattern(_polarity_shape(), _global_syntax())
    assert schema._check_value("code", _POLARITY_BODY, [], [], pattern) == "filled"
    assert schema._check_value("code", "return 'maybe'", [], [], pattern) == "off_vocabulary"
    assert schema._check_value("code", None, [], [], pattern) == "abstained"
    assert schema._check_value("code", "   ", [], [], pattern) == "violation"
    assert schema._check_value("code", 3, [], [], pattern) == "violation"


def _authoring_evidence() -> dict:
    return {"contract": "(in the stub's docstring)", "stub": _POLARITY_STUB, "tests": "assert polarity(1, 'none') == 'positive'"}


def test_the_writing_call_is_a_typed_hole_held_to_its_shape():
    """Same global ruleset: format is the schema, the body's pattern is the unit's shape, and admission re-checks."""
    c = find_cassette("function-body")
    assert (c.verdict(), c.seat, c.format_mode) == ("ok", "typed_hole", "schema")
    shape = _polarity_shape()
    p = plan(c, _authoring_evidence(), {"body": shape})
    assert p["invocation"] == "ok"
    assert p["payload"]["format"]["properties"]["body"]["anyOf"][0]["pattern"] == decisions.code_pattern(shape, _global_syntax())
    o = invoke(c, _authoring_evidence(), {"body": shape}, chat=FakeChat(json.dumps({"body": _POLARITY_BODY})))
    assert (o.status, o.values) == ("admitted", {"body": _POLARITY_BODY})
    bad = invoke(c, _authoring_evidence(), {"body": shape}, chat=FakeChat(json.dumps({"body": "return 'maybe'"})))
    assert (bad.status, bad.reason) == ("failed", "stop_contract_breach")
    null = invoke(c, _authoring_evidence(), {"body": shape}, chat=FakeChat(json.dumps({"body": None})))
    assert (null.status, null.hand_back) == ("abstained", "definition")
    assert invoke(c, _authoring_evidence(), {}, chat=FakeChat()).reason == "vocabulary_missing"
    forbidden = {"names": ["eval"], "attributes": [], "strings": [], "numbers": []}
    assert invoke(c, _authoring_evidence(), {"body": forbidden}, chat=FakeChat()).reason == "vocabulary_missing"


def test_cli_dry_run_shows_the_writing_calls_pattern(tmp_path, capsys):
    shape_file = tmp_path / "shape.json"
    shape_file.write_text(json.dumps(_polarity_shape()))
    args = ["run", "function-body", "-e", "contract=c", "-e", f"stub={_POLARITY_STUB}", "-e", "tests=t", "--shape-file", f"body={shape_file}", "--dry-run"]
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["payload"]["format"]["properties"]["body"]["anyOf"][0]["pattern"] == decisions.code_pattern(_polarity_shape(), _global_syntax())


def test_the_pattern_holds_declared_attributes_only():
    import re

    pattern = decisions.code_pattern(_polarity_shape(attributes=["upper", "strip"]), _global_syntax())

    def held(body: str) -> bool:
        return re.fullmatch(pattern, _typed(body)) is not None

    assert held("return prefix_code.strip().upper()")
    assert not held("return prefix_code.lower()")
    assert not held("return prefix_code.upperx()")


def test_a_code_slot_schema_carries_its_pattern_and_cap():
    assert schema.slot_schema("code", [], False, 1, [], "^x$", 120) == {"type": "string", "minLength": 1, "pattern": "^x$", "maxLength": 120}
    assert schema.slot_schema("code", [], True, 1, [], "", 0) == {"anyOf": [{"type": "string", "minLength": 1, "pattern": "^$"}, {"type": "null"}]}
    whole = schema.cassette_schema([{"name": "body", "kind": "code", "vocabulary": ["return"], "allow_abstain": False, "pattern": "^x$", "max_chars": 9}])
    assert whole["properties"]["body"] == {"type": "string", "minLength": 1, "pattern": "^x$", "maxLength": 9}


def test_the_answer_block_lists_what_a_body_may_contain():
    shape = _polarity_shape(attributes=["upper"], syntax=["return", "(", ")"])
    block = schema.answer_block([{"name": "body", "kind": "code", "vocabulary": _global_syntax(), "allow_abstain": True, "shape": shape, "description": "the body"}])
    assert "(or null if the contract does not determine it) — the body" in block
    assert "the names ['isinstance', 'int', 'bool', 'clause_sign', 'prefix_code']" in block
    assert "after a dot, the attributes ['upper']" in block
    assert "the numbers [1]" in block and "and the syntax ['return', '(', ')']" in block
    plain = schema.answer_block([{"name": "body", "kind": "code", "vocabulary": ["return"], "allow_abstain": False}])
    assert "or null" not in plain and "the names []" in plain and "and the syntax ['return']" in plain


def test_a_code_value_keeps_non_ascii_and_needs_its_pattern():
    pattern = decisions.code_pattern(_polarity_shape(strings=["’", "invalid"], outcomes=["invalid"]), _global_syntax())
    assert schema._check_value("code", "return '’'", [], [], pattern) == "filled"
    # with no pattern, nothing is admitted, whatever the body says
    assert [schema._check_value("code", body, [], []) for body in ("return 1", "mutated", "x")] == ["off_vocabulary"] * 3


def test_tally_counts_a_code_body_against_its_pattern():
    slots = [{"name": "body", "kind": "code", "pattern": "^return x$"}]
    assert schema.tally(slots, {"body": "return x"}) == {"missing": 0, "off_vocabulary": 0, "abstained": 0, "filled": 1}
    assert schema.tally(slots, {"body": "return y"}) == {"missing": 0, "off_vocabulary": 1, "abstained": 0, "filled": 0}
    assert schema.tally(slots, {}) == {"missing": 1, "off_vocabulary": 0, "abstained": 0, "filled": 0}


# ── the Detective phase's pure readings (the phase itself: tests/test_harness_detective_phase.py) ──


def test_the_model_sees_the_inputs_detective_holds_for_the_function():
    """The founder, 2026-10-06 («G4. Yes»): beside its state the model sees Detective's record of the inputs supplied
    for the function: how the store read (Detective's own codes; a file it could not read is never an empty store),
    every input the store holds for it, and the inputs the last run was given, labelled apart. Information, never a
    filter."""
    store = {"read": "readable", "store": {"m.py::f": ["(1,)", "(2,)"], "m.py::g": ["('x',)"]}}
    entry = {"args": {"input": ["(2,)"], "two_sign": True}}
    assert decisions.supplied_inputs(store, "m.py::f", entry) == {"store": "readable", "remembered": ["(1,)", "(2,)"], "last_run": ["(2,)"]}
    assert decisions.supplied_inputs(store, "m.py::h", {"args": {}}) == {"store": "readable", "remembered": [], "last_run": []}
    assert decisions.supplied_inputs(store, "m.py::f", None)["last_run"] is None  # no run yet
    for read in ("absent", "unreadable", "corrupt", "undecided"):
        assert decisions.supplied_inputs({"read": read, "store": {}}, "m.py::f", entry) == {"store": read, "remembered": [], "last_run": ["(2,)"]}
    for missing in (None, {}, {"read": "of no kind", "store": []}):
        assert decisions.supplied_inputs(missing, "m.py::f", entry)["store"] == "unrecorded"
    assert decisions.supplied_inputs({"read": "readable", "store": {"m.py::f": "(1,)"}}, "m.py::f", entry)["remembered"] == []
    assert decisions.supplied_inputs({"read": "readable", "store": {"m.py::f": ["(1,)", 2]}}, "m.py::f", {"args": {"input": "(1,)"}}) == {
        "store": "readable",
        "remembered": ["(1,)"],
        "last_run": None,
    }


@pytest.mark.skipif(detective.engine()["state"] != "ok", reason="Detective's checkout is not on this machine")
def test_detective_s_store_is_read_without_touching_it(tmp_path):
    """Detective's own reader sets a corrupt store aside; the harness reads it as it lies, and Detective's own decision
    names what it found."""
    eng = detective.engine()
    assert detective.samples(tmp_path, eng) == {"read": "absent", "store": {}}
    held = tmp_path / ".detective" / "inputs.json"
    held.parent.mkdir()
    held.write_text(json.dumps({"m.py::f": ["(1,)"]}))
    assert detective.samples(tmp_path, eng) == {"read": "readable", "store": {"m.py::f": ["(1,)"]}}
    for text in ("{not json", "[1, 2]"):
        held.write_text(text)
        assert detective.samples(tmp_path, eng) == {"read": "corrupt", "store": {}} and held.read_text() == text
    assert detective.samples(tmp_path, {"pythonpath": ""}) == {"read": "undecided", "store": {}}


def test_the_values_a_hole_offers_are_mined_from_the_code_detective_printed():
    """The Uroboros thesis (the founder pointed to it, 2026-10-06): values are selected into a per-function schema mined
    from the branches themselves, arity and types locked, and the harness writes the call. Detective prints each
    guard, relation and condition as code; a literal goes to a parameter when both are operands of one comparison, or
    it is an argument of a method called on the parameter. A literal beside no parameter is not attributed, and a
    condition that is not one expression is named, never an empty pool."""
    lines = [{"guard": "code.startswith('VIP-')", "line": 11}]
    assert decisions.ask_pools(decisions.pool_ask("lines", lines, ["code"])) == {"code": ["VIP-"]}  # the recorded ask of the `label` fixture
    assert decisions.ask_pools(decisions.pool_ask("sample", [], ["order"])) == {"order": []}  # a sample ask states no code

    def ask(*relations, names=("x",)):
        return {"kind": "boundary", "params": [{"name": n, "annotation": ""} for n in names], "relations": list(relations)}

    assert decisions.ask_pools(ask("x == 0")) == {"x": [0]}
    assert decisions.ask_pools(ask("0 == x", "x == -1")) == {"x": [0, -1]}
    assert decisions.ask_pools(ask("x in (1, 2)", "x not in ['a']")) == {"x": [1, 2, "a"]}
    assert decisions.ask_pools(ask("0 < x < 10")) == {"x": [0, 10]}
    assert decisions.ask_pools(ask("x == 1", "x == True")) == {"x": [1, True]}
    assert decisions.ask_pools(ask("'a' in x")) == {"x": ["a"]}
    assert decisions.ask_pools(ask("x == (1, 2)", "len(x) > 3", "x.size == 4", "total > 100")) == {"x": []}
    assert decisions.ask_pools(ask("qty == 0 and code.startswith('VIP-')", names=("qty", "code"))) == {"qty": [0], "code": ["VIP-"]}
    assert decisions.ask_pools({"kind": "internal", "params": [{"name": "x", "annotation": "int"}], "conditions": ["x.bit_length() > 3"]}) == {"x": []}
    assert decisions.ask_pools({"kind": "lines", "params": [{"name": "x", "annotation": ""}], "lines": [{"line": 4, "when": ""}, {"line": 5, "when": "x is None"}]}) == {"x": [None]}
    assert decisions.ask_pools(ask("x == (")) == "unparseable"
    assert decisions.ask_pools(None) == decisions.ask_pools({"kind": "lines"}) == "invalid"


def test_a_literal_operand_states_its_json_scalars():
    """Pinned at Detective's ask (WRITE TEST, 2026-10-06: «call the target with the object/call below … a Constant …, a
    bool: False», observed ``['VIP-']``): the scalars a literal operand states, each member of a literal collection on
    the right of an ``in``; a value JSON cannot carry, and an operand that is no literal, state none."""
    assert decisions._constants(ast.Constant("VIP-"), False) == ["VIP-"]
    assert decisions._constants(ast.parse("(1, 'a', None, 1.5, True)", mode="eval").body, True) == [1, "a", None, 1.5, True]
    assert decisions._constants(ast.parse("[1, (2,)]", mode="eval").body, True) == [1]
    assert decisions._constants(ast.parse("{3}", mode="eval").body, True) == [3]
    assert decisions._constants(ast.parse("(1, 2)", mode="eval").body, False) == []
    assert decisions._constants(ast.parse("-1", mode="eval").body, False) == [-1]
    assert decisions._constants(ast.parse("b'x'", mode="eval").body, False) == []
    assert decisions._constants(ast.parse("x", mode="eval").body, False) == []


def test_the_daemon_runs_only_the_command_detective_named():
    """Its acts are Detective's verbs on its own unit; the harness owns the executable and the root."""
    target, unit_file, ws = "unit.py::f", "unit.py", "/ws/u1"
    cases = [
        ("detective converge 'unit.py::f'", ["converge", "unit.py::f", "--project-root", ws]),
        ("detective decompose 'unit.py::f' --apply --project-root /ws/u1", ["decompose", "unit.py::f", "--apply", "--project-root", ws]),
        ("detective converge 'unit.py::f' --input \"(1, 'x.py')\"", ["converge", "unit.py::f", "--input", "(1, 'x.py')", "--project-root", ws]),
        ("detective plan 'unit.py'", ["plan", "unit.py", "--project-root", ws]),
        ("detective verify-rewrite <receipt> 'unit.py::f' --budget", "placeholder"),
        ("detective converge 'unit.py::f", "unparseable"),
        ("rm -rf /", "not_detective"),
        ("", "not_detective"),
        ("detective", "forbidden_verb"),
        ("detective flag 'unit.py::f' VALUE_1", "forbidden_verb"),
        ("detective purge", "forbidden_verb"),
        ("detective audit 'unit.py::f' --remove", "forbidden_option"),
        ("detective converge 'unit.py::f' --apply", "forbidden_option"),
        ("detective converge 'other.py::g'", "foreign_target"),
        ("detective plan 'other.py'", "foreign_path"),
        ("detective converge 'unit.py::f' --project-root /elsewhere", "foreign_root"),
    ]
    assert [decisions.daemon_argv(c, target, unit_file, ws) for c, _ in cases] == [want for _, want in cases]
    assert decisions.daemon_argv(None, target, unit_file, ws) == "invalid"


def test_the_daemon_knows_a_target_only_by_its_signature():
    """Its positional parameters, in order, positional-only among them, because Detective's ``--input`` is a positional
    tuple; a parameter that admits None keeps it, so its hole admits null. A required keyword-only parameter makes the
    call one the tuple cannot write, and is named."""
    stub = "def f(a: int, b, c: list[str] | None = None) -> str:\n    '''doc'''\n    raise NotImplementedError\n"
    assert decisions.target_signature(stub, "f") == [["a", "int"], ["b", ""], ["c", "list[str] | None"]]
    assert decisions.target_signature("def g(p: int, /, q, *rest, k=1, **more):\n    pass\n", "g") == [["p", "int"], ["q", ""]]
    assert decisions.target_signature("def h(a, *, k):\n    pass\n", "h") == "keyword_only"
    assert decisions.target_signature(stub, "g") == "no_function"
    assert decisions.target_signature("def f(:", "f") == "syntax_error"
    assert decisions.target_signature(None, "f") == "invalid"
    annotations = ("int", "list[str]", "dict | None", " str ", "Foo", "", None, "Optional[int]", "None | str", "int | str", "list[int | None] | None", "dict[str, int | None]", "Foo | None")
    assert [decisions.param_type(a) for a in annotations] == ["int", "list", "dict|None", "str", "", "", "", "int|None", "str|None", "int", "list|None", "dict", ""]


def test_every_ending_has_its_class_and_the_phase_its_exit_code():
    """A run ends on Detective's own code, its exit code one field of it (audit exits 0 on a failing intent test), or on
    the harness code that ended it. The class says who holds the next move, and the worst class is the phase's exit
    code, exit codes being epistemics: 0 clean, 1 handed back, 2 the world is wrong, 3 not to be trusted."""
    # Detective's own codes as the driver ends on them, each under the exit code it was recorded with
    # (tests/fixtures/detective_json/)
    detective_endings = {
        ("regime:migrated", 0): "clean",
        ("regime:conftest-collision", 2): "world_wrong",
        ("audit:fix_failing_test", 0): "handed_back",
        ("audit:refused:collection_errors", 2): "world_wrong",
        ("audit:provide_sample:no_params", 0): "handed_back",
        ("audit:provide_sample:abstained", 0): "handed_back",
        ("converge:fix_verification", 3): "untrusted",
        ("converge:close_the_gap:supplied_did_not_reach", 0): "handed_back",
        ("converge:refused:target_not_found", 1): "handed_back",
    }
    assert {pair: decisions.ending_class(*pair) for pair in detective_endings} == detective_endings
    harness = {
        "detective_unset": "world_wrong",
        "contract_breach:foreign_root": "untrusted",
        "call_failed:stop_no_progress": "untrusted",
        "write_refused:syntax": "untrusted",
        "input_unavailable:no_function": "handed_back",
        "write_unavailable": "handed_back",
        "judge:cannot_tell": "handed_back",
        "judge:flag_refused:BOUNDARY_1": "handed_back",
        "detective:unparsed": "handed_back",
        "audit:no_decision": "handed_back",
        "invalid:kind_out_of_stage": "handed_back",
        "budget": "handed_back",
    }
    assert {ending: decisions.ending_class(ending, None) for ending in harness} == harness
    # a run is clean only when Detective's own done ended it (a function's converge settled, the folder's regime clean): the
    # code `converge:settled` alone reads as a hand-back anywhere else
    assert [decisions.run_class("done", "converge:settled", 0), decisions.run_class("done", "regime:clean", 0)] == ["clean", "clean"]
    assert [decisions.run_class("handback", "converge:settled", 0), decisions.run_class("handback", "regime:clean", 0)] == ["handed_back", "handed_back"]
    assert [decisions.run_class("handback", "converge:fix_verification", 3), decisions.run_class("handback", "regime:conftest-collision", 2)] == ["untrusted", "world_wrong"]
    assert [decisions.run_class("done", "", 0), decisions.run_class("handback", "", 0), decisions.run_class("stuck", "budget", None)] == ["clean", "invalid", "invalid"]
    assert decisions.ending_class("", 0) == decisions.ending_class(None, 0) == "invalid"
    # a written module Detective cannot load is handed back, whatever its exit code (the founder, 2026-10-07: «Hand it
    # back»); the setup's regime reads the folder, not the module, and keeps its class, as does a setup fault
    assert decisions.ending_class("converge:fix_load", 0) == decisions.ending_class("audit:fix_load", 2) == "handed_back"
    assert decisions.ending_class("regime:unobserved", 2) == decisions.ending_class("converge:install_pytest", 0) == "world_wrong"
    assert decisions.ending_class("audit:unobserved", 0) == "handed_back"
    assert decisions.possession_exit(["clean", "clean"]) == 0
    assert decisions.possession_exit(["clean", "handed_back"]) == 1
    assert decisions.possession_exit(["handed_back", "untrusted"]) == 3
    assert decisions.possession_exit(["untrusted", "world_wrong"]) == 2
    for malformed in ([], "clean", ["exit:0"], ["invalid"]):
        assert decisions.possession_exit(malformed) == 2


def test_every_command_runs_over_both_signs():
    """Every contract's intent tests carry both signs (the founder, 2026-10-06), so each verb that takes ``--two-sign``
    runs with it; Detective's printed commands drop it (Detective issue #115), so the harness adds it."""
    ws = ["--project-root", "/ws"]
    for verb in ("audit", "converge", "decompose", "diagnose"):
        assert decisions.two_sign_argv([verb, "u.py::f", *ws]) == [verb, "u.py::f", *ws, "--two-sign"]
        assert decisions.two_sign_argv([verb, "u.py::f", "--two-sign", *ws]) == [verb, "u.py::f", "--two-sign", *ws]
    for verb in ("regime", "plan", "survey", "parsimony", "doctor"):
        assert decisions.two_sign_argv([verb, *ws]) == [verb, *ws]
    for bad in ([], "audit", [1], None):
        assert decisions.two_sign_argv(bad) == "invalid"


def test_a_hand_back_carries_its_last_detective_record_and_doctors_read_as_evidence():
    """WIRING W8, its first bullet: a record for the folder's setup and for each function whose route is not ``done``,
    in the phase's order, the setup first. The evidence a receiver reads is the function's last Detective record (the
    ``decision`` of its last run, never an earlier one) and doctor's payload, read after the run ended and kept on the
    function's result. A function that ran nothing, or whose last run holds no record, carries none; the setup carries
    no doctor. What routes ``done`` is no hand-back, whatever else its record says."""

    def run(code):
        return {"argv": ["converge", "m.py::f"], "exit": 0, "seconds": 0.0, "stage": "run", "decision": {"code": code} if code else None, "move": None}

    functions = {
        "first": {"route": "handback", "class": "handed_back", "ending": "audit:fix_failing_test", "runs": [run("close_the_gap"), run("fix_failing_test")], "doctor": {"read": "first"}},
        "second": {"route": "done", "class": "clean", "ending": "converge:settled", "runs": [run("settled")], "doctor": None},
        "third": {"route": "handback", "class": "handed_back", "ending": "budget", "runs": [run("provide_sample")], "doctor": None},
        "fourth": {"route": "handback", "class": "world_wrong", "ending": "contract_breach:foreign_target", "runs": [], "doctor": {"read": "fourth"}},
        "fifth": {"route": "handback", "class": "untrusted", "ending": "detective:unparsed", "runs": [run("close_the_gap"), run(None)], "doctor": None},
    }
    driven = {"setup": {"route": "done", "class": "clean", "ending": "regime:migrated", "runs": [run("migrated")]}, "functions": functions}
    shape = list(decisions.RECEIVER_DECISIONS)
    assert contract.handbacks(driven) == [
        {"function": "first", "ending": "audit:fix_failing_test", "decision": {"code": "fix_failing_test"}, "doctor": {"read": "first"}, "receivers": ["lead", "sol"], "decisions": shape},
        {"function": "third", "ending": "budget", "decision": {"code": "provide_sample"}, "doctor": None, "receivers": ["admission"], "decisions": shape},
        {
            "function": "fourth",
            "ending": "contract_breach:foreign_target",
            "decision": None,
            "doctor": {"read": "fourth"},
            "receivers": decisions.handback_receivers("contract_breach:foreign_target"),
            "decisions": shape,
        },
        {"function": "fifth", "ending": "detective:unparsed", "decision": None, "doctor": None, "receivers": decisions.handback_receivers("detective:unparsed"), "decisions": shape},
    ]
    stopped = {"setup": {"route": "handback", "class": "world_wrong", "ending": "regime:conftest-collision", "runs": [run("migration_available"), run("conftest-collision")]}, "functions": {}}
    assert contract.handbacks(stopped) == [
        {
            "function": "",
            "ending": "regime:conftest-collision",
            "decision": {"code": "conftest-collision"},
            "doctor": None,
            "receivers": decisions.handback_receivers("regime:conftest-collision"),
            "decisions": shape,
        }
    ]
    assert [set(h) for h in contract.handbacks(driven)] == [{"function", "ending", "decision", "doctor", "receivers", "decisions"}] * 4
    assert contract.handbacks(None) == contract.handbacks({"setup": 3, "functions": []}) == contract.handbacks({"functions": {"f": "x"}}) == []
    unrouted = contract.handbacks({"setup": {"ending": "regime:ok", "runs": "x"}, "functions": {"f": {"route": "handback", "ending": "budget", "runs": [3]}}})
    assert [(h["function"], h["decision"], h["doctor"]) for h in unrouted] == [("", None, None), ("f", None, None)]


def test_play_on_the_command_line_records_the_grant(tmp_path, monkeypatch):
    """The contract writer and admission are given to play by its caller and only recorded (R1)."""
    seen: dict = {}

    def stand_in(path, out, model, **grant):
        seen.update(grant)
        return {"outcome": "unreadable", "skipped": []}

    monkeypatch.setattr(contract, "play", stand_in)
    assert contract.main(["play", "c.json", "--out", str(tmp_path), "--contract-writer", "lead", "--admission", "sol"]) == 2
    assert seen == {"contract_writer": "lead", "admission": "sol"}
    assert contract.main(["play", "c.json", "--out", str(tmp_path)]) == 2 and seen == {"contract_writer": None, "admission": None}


def test_a_contract_template_seats_and_admits_only_its_choices():
    """The contract owns the AST and the model supplies only the selected slot labels."""
    root = Path(__file__).parent / "fixtures" / "corpus" / "clamp"
    source = json.loads((root / "contract.json").read_text())
    fn = ast.parse((root / "reference.py").read_text()).body[-1]
    assert isinstance(fn, ast.FunctionDef)
    code = contract.json_from_ast(fn.body)[1]
    comparison = next(n for n in code if n["_type"] == "If")["test"]
    comparison["ops"][0] = {"$slot": "guard"}
    source["body"][0]["ast_template"] = {
        "signature": {"args": contract.json_from_ast(fn.args)[1], "returns": None, "decorator_list": []},
        "code": code,
        "choices": {"guard": {"inverted": "Gt", "ordered": "Lt"}},
    }
    assert contract.seat_contract(contract.seat_form(source)) == ("ok", source)
    record = contract.MODELS[contract.DEFAULT_MODEL]
    request = contract.call_payload(source, "clamp", record, "ollama")["payload"]
    assert [message["role"] for message in request["messages"]] == ["user"]
    assert json.loads(request["messages"][0]["content"])["function"]["ast_template"] == source["body"][0]["ast_template"]
    assert request["format"]["properties"]["choices"]["properties"] == {"guard": {"enum": ["inverted", "ordered"]}}
    for answer, reason in (
        ({"choices": {"guard": "unknown"}}, "invalid_selection"),
        ({"choices": {}}, "invalid_selection"),
        ({"choices": {"guard": "inverted"}, "code": code}, "choices"),
    ):
        assert contract.fill(source, "clamp", answer) == (f"bad_reply:{reason}", source)
    status, filled = contract.fill(source, "clamp", {"choices": {"guard": "inverted"}})
    assert status == "filled"
    status, rendered = contract.render_module(filled)
    assert status == "ok"
    assert "if low > high:" in rendered and "$slot" not in rendered
    assert source["body"][0]["code"] is None
    assert contract.fill(filled, "clamp", {"choices": {"guard": "ordered"}}) == ("bad_reply:final:code", filled)
    assert contract.reissue(filled, "clamp")[1] == source | {"added_imports": []}


def _holds(value, sch, root=None, path="$"):
    """The part of JSON Schema the contract's form uses: ``$ref`` into ``$defs``, ``anyOf``, ``const``, ``enum``,
    ``type``, ``pattern``, ``minLength``, ``properties`` with ``required`` and ``additionalProperties``, ``items`` with
    ``minItems``. The first path that does not hold, or ``""``."""
    root = root or sch
    if "$ref" in sch:
        return _holds(value, root["$defs"][sch["$ref"].rpartition("/")[2]], root, path)
    if "anyOf" in sch:
        return "" if any(_holds(value, s, root, path) == "" for s in sch["anyOf"]) else path
    if ("const" in sch and value != sch["const"]) or ("enum" in sch and value not in sch["enum"]):
        return path
    kinds = {"object": dict, "array": list, "string": str, "null": type(None)}
    if "type" in sch and not isinstance(value, kinds[sch["type"]]):
        return path
    if isinstance(value, str) and (len(value) < sch.get("minLength", 0) or ("pattern" in sch and not re.search(sch["pattern"], value))):
        return path
    if isinstance(value, dict):
        props = sch.get("properties", {})
        if any(k not in value for k in sch.get("required", [])) or (sch.get("additionalProperties") is False and any(k not in props for k in value)):
            return path
        for key, item in value.items():
            bad = _holds(item, props.get(key, {}), root, f"{path}.{key}")
            if bad:
                return bad
    if isinstance(value, list):
        if len(value) < sch.get("minItems", 0):
            return path
        for i, item in enumerate(value):
            bad = _holds(item, sch.get("items", {}), root, f"{path}[{i}]")
            if bad:
                return bad
    return ""


def _emptied(given: dict) -> dict:
    """A contract as its writer leaves it: every cell null, nothing a call added."""
    out = copy.deepcopy(given)
    out.pop("added_imports", None)
    for _q, kind, node, _lineage in contract.elements(out):
        if kind == "function":
            node["signature"] = node["code"] = None
        elif kind == "object":
            node["value"] = None
    return out


def test_the_contract_writers_form_holds_every_contract_and_no_written_cell():
    """The contract writer's seat is held at decode time to its form (SEPARATION_OF_AUTHORITY.md R2; his rulings,
    separation [7]): the obligations it writes and the empty structure, every function at its scope's top naming the
    ones it combines, its coverage as what it must do and what it must never do, every signature and body null. Every
    contract the harness has played and every contract of the corpus holds in that form (``seat_form``) and comes back
    whole from it (``seat_contract``); a written cell does not hold, nor a coverage line under a category or an
    operator the vocabularies do not hold, nor a function with no line of one sign. The form does not carry the typed
    order (WIRING W7): the seat sets it."""
    form = contract.contract_schema()
    corpus = Path(__file__).parent / "fixtures" / "corpus"
    samples = [_written_contract(), fixture("clamp")["contract"], *(json.loads(p.read_text()) for p in sorted(corpus.glob("*/contract.json")))]
    for given in samples:
        emptied = _emptied(given)
        assert contract.contract_verdict(emptied) == "ok" and _holds(contract.seat_form(emptied), form) == "", given["path"]
        assert "detective" not in contract.seat_form(emptied), given["path"]
        assert contract.seat_contract(contract.seat_form(emptied)) == ("ok", emptied), given["path"]
    seat = contract.seat_form(_emptied(samples[0]))
    written = copy.deepcopy(seat)
    written["body"][0]["code"] = []
    assert _holds(written, form) == "$.body[0]"
    wrong = copy.deepcopy(seat)
    wrong["body"][0]["intent_test"]["never"][0]["operator"] = "nonsense"
    assert _holds(wrong, form) == "$.body[0]"
    wrong["body"][0]["intent_test"]["covers"] = [{"category": "MAGIC", "subject": "x", "range": "y"}]
    assert _holds(wrong, form) == "$.body[0]"
    for sign in ("covers", "never"):
        bare = copy.deepcopy(seat)
        bare["body"][0]["intent_test"][sign] = []
        assert _holds(bare, form) == "$.body[0]", sign
    function = form["$defs"]["function"]["properties"]
    assert function["signature"] == function["code"] == {"type": "null"} and function["combines"] == {"type": "array", "items": {"type": "string", "pattern": f"^{contract._IDENT}$"}}
    assert form["properties"]["format"] == {"const": contract.FORMAT} and "detective" not in form["properties"] and "detective" not in form["required"]


def test_the_seat_nests_each_function_below_the_one_that_names_it():
    """``contract.seat_contract`` (his ruling, separation [7]): the contract writer names the functions each function
    combines, and the seat places each below the one that names it, within its own scope, its two coverage lists
    merged into one, what it must do first. A name its scope does not define, a function two others combine, a
    function that combines itself, directly or through others, and two functions of one name are each refused under
    their own code; what it does not read it leaves to the contract's verdict."""

    def fn(name: str, combines: tuple = ()) -> dict:
        return {
            "function": name,
            "doc": {"prose": name, "operations": []},
            "intent_test": {"source": "s", "covers": ["does"], "never": ["never"]},
            "objects": [],
            "combines": list(combines),
            "signature": None,
            "code": None,
        }

    def stored(name: str, functions: tuple = ()) -> dict:
        return {
            "function": name,
            "doc": {"prose": name, "operations": []},
            "intent_test": {"source": "s", "covers": ["does", "never"]},
            "objects": [],
            "signature": None,
            "code": None,
            "functions": list(functions),
        }

    held = {"object": "X", "doc": "x", "intent": "1", "used_by": [], "value": None}
    reply = {"format": contract.FORMAT, "body": [fn("c"), fn("b", ("c",)), fn("a", ("b", "d")), fn("d"), {"class": "K", "doc": "k", "bases": [], "members": [fn("m", ("n",)), fn("n"), held]}]}
    nested = [stored("a", (stored("b", (stored("c"),)), stored("d"))), {"class": "K", "doc": "k", "bases": [], "members": [stored("m", (stored("n"),)), held]}]
    assert contract.seat_contract(reply) == ("ok", {"format": contract.FORMAT, "body": nested, "detective": ["regime", "audit"]})
    given = {
        **reply,
        "objects": [{**held, "used_by": ["b", "K.n", "nowhere"]}],
        "body": [*reply["body"][:4], {**reply["body"][4], "members": [*reply["body"][4]["members"][:2], {**held, "used_by": ["K.n"]}]}],
    }
    before = copy.deepcopy(given)
    status, built = contract.seat_contract(given)
    assert given == before  # the reply it was given is left as it came
    assert (status, built["objects"][0]["used_by"], built["body"][1]["members"][1]["used_by"]) == ("ok", ["a.b", "K.m.n", "nowhere"], ["K.m.n"])
    assert contract.seat_form(built)["objects"][0]["used_by"] == ["b", "K.n", "nowhere"]
    assert contract.seat_contract({"body": [fn("a", ("zz",))]}) == ("combines_unknown:a:zz", None)
    assert contract.seat_contract({"body": [{"class": "K", "doc": "", "bases": [], "members": [fn("m", ("a",))]}, fn("a")]}) == ("combines_unknown:K.m:a", None)
    assert contract.seat_contract({"body": [fn("a", ("c",)), fn("b", ("c",)), fn("c")]}) == ("combined_twice:c", None)
    assert contract.seat_contract({"body": [fn("a", ("a",))]}) == ("loop:a", None)
    assert contract.seat_contract({"body": [fn("a", ("b",)), fn("b", ("a",))]}) == ("loop:a", None)
    assert contract.seat_contract({"body": [fn("a"), fn("a")]}) == ("name_clash:a", None)
    assert contract.seat_contract({"body": "not a list"}) == ("ok", {"body": "not a list"}) and contract.seat_contract(None) == ("ok", None)


def test_a_contract_slot_is_held_to_the_form_its_caller_supplies():
    """The cassette hardware's ``contract`` slot: its form comes at run time, like a code slot's shape; it is held as
    the whole schema with its definitions at the root, where its references point; a null is an abstention."""
    assert decisions.slot_verdict("contract", "value", "runtime", 0) == "ok"
    assert decisions.slot_verdict("contract", "value", "inline", 3) == "contract_needs_runtime_form"
    form = contract.contract_schema()
    held = schema.slot_schema("contract", [], True, 1, [], form=form)
    assert held == {"anyOf": [{k: v for k, v in form.items() if k != "$defs"}, {"type": "null"}]}
    assert schema.slot_schema("contract", [], False, 1, []) == {"type": "object"}
    whole = schema.cassette_schema([{"name": "contract", "kind": "contract", "allow_abstain": True, "form": form}])
    assert whole["$defs"] == form["$defs"] and whole["required"] == ["contract"]
    assert schema.tally([{"name": "contract", "kind": "contract"}], {"contract": {"format": "x"}})["filled"] == 1
    assert schema.tally([{"name": "contract", "kind": "contract"}], {"contract": None})["abstained"] == 1
    assert schema.tally([{"name": "contract", "kind": "contract"}], {"contract": []})["missing"] == 1
    seat = find_cassette("contract-write")
    slots, missing = resolved_slots(seat, {}, [])
    assert missing == ["contract"] and slots[0]["form"] == {}
    slots, missing = resolved_slots(seat, {"contract": form}, [])
    assert missing == [] and slots[0]["form"] == form
    assert '"contract": the module\'s contract' in schema.answer_block(slots)


def test_a_form_slot_is_held_to_the_form_its_caller_supplies():
    """The hardware's ``form`` slot: any object held to the schema its caller supplies at run time, as a contract slot
    is, for a reply whose shape the caller knows only then (the intent check's one verdict per pinned record). Its
    form must come at run time; a null is an abstention, and anything but an object is no answer."""
    assert decisions.slot_verdict("form", "value", "runtime", 0) == "ok"
    assert decisions.slot_verdict("form", "value", "inline", 3) == "form_needs_runtime_form"
    assert decisions.slot_verdict("contract", "value", "file", 3) == "contract_needs_runtime_form"
    form = {"type": "object", "properties": {"r0": {"enum": ["agrees", "cannot_tell"]}}, "required": ["r0"], "additionalProperties": False}
    assert schema.slot_schema("form", [], True, 1, [], form=form) == {"anyOf": [form, {"type": "null"}]}
    assert schema.slot_schema("form", [], False, 1, [], form=form) == form
    assert schema.slot_schema("form", [], False, 1, []) == {"type": "object"}
    whole = schema.cassette_schema([{"name": "verdicts", "kind": "form", "allow_abstain": False, "form": {**form, "$defs": {"x": {"type": "string"}}}}])
    assert whole["properties"]["verdicts"] == form and whole["$defs"] == {"x": {"type": "string"}}
    slot = [{"name": "verdicts", "kind": "form"}]
    assert [schema.tally(slot, {"verdicts": given}) for given in ({"r0": "agrees"}, None, [])] == [
        {"missing": 0, "off_vocabulary": 0, "abstained": 0, "filled": 1},
        {"missing": 0, "off_vocabulary": 0, "abstained": 1, "filled": 0},
        {"missing": 1, "off_vocabulary": 0, "abstained": 0, "filled": 0},
    ]
    block = schema.answer_block([{"name": "verdicts", "kind": "form", "allow_abstain": True, "description": "one per record"}])
    assert '- "verdicts": one object, in the form the schema holds (or null if none can be given) — one per record' in block
    assert "or null" not in schema.answer_block([{"name": "verdicts", "kind": "form", "allow_abstain": False}])


def test_the_contract_writers_seat_writes_one_module_in_one_call():
    """The seat (R2): one call reads his instructions and the module's imports and globals as they stand, its reply
    held to the seat's form and the contract built from it (``seat_contract``); what neither reads, the seat reads
    after (``seat_verdict``). The contract writer never writes code, keeps what the module holds, and writes this
    module and no other; a reply the seat cannot build is refused as it came."""
    emptied = _emptied(_written_contract())
    chat = FakeChat(json.dumps({"contract": contract.seat_form(emptied)}))
    code, written, _out = contract.write_contract("A claim's composed sign.", "unit.py", "qwen3-coder-next", chat=chat, categories=_CATEGORIES)
    assert (code, written, chat.calls) == ("written", emptied, 1)
    payload = chat.payloads[0]
    assert payload["format"]["$defs"] == contract.contract_schema()["$defs"] and list(payload["format"]["properties"]) == ["contract"]
    assert [m["role"] for m in payload["messages"]] == ["user"]
    record = json.loads(payload["messages"][0]["content"])
    assert (record["instructions"], record["path"], record["stem"]) == ("A claim's composed sign.", "unit.py", "unit")
    assert record["categories"] == _CATEGORIES and record["output_operators"]["none"] == "the output must exist"
    assert contract.seat_verdict(emptied, "unit.py", [], []) == "ok"
    assert contract.seat_verdict(emptied, "other.py", [], []) == "moved"
    assert contract.seat_verdict(emptied, "unit.py", ["import math"], []) == "dropped_import:0"
    rate = {"object": "RATE", "doc": "The rate.", "intent": "2", "used_by": ["polarity"], "value": None}
    assert contract.seat_verdict(emptied, "unit.py", [], [rate]) == "dropped_object:RATE"
    assert contract.seat_verdict({**emptied, "objects": [{**rate, "intent": "3"}]}, "unit.py", [], [rate]) == "dropped_object:RATE"
    assert contract.seat_verdict({**emptied, "objects": [rate]}, "unit.py", [], [rate]) == "ok"
    assert contract.seat_verdict(_written_contract(), "unit.py", [], []) == "filled:polarity"
    no_negative = copy.deepcopy(emptied)
    no_negative["body"][0]["intent_test"]["covers"] = no_negative["body"][0]["intent_test"]["covers"][:2]
    assert contract.seat_verdict(no_negative, "unit.py", [], []) == "missing_negative:polarity"
    assert contract.seat_verdict(None, "unit.py", [], []) == "invalid:format"
    code, refused, _out = contract.write_contract("x", "unit.py", "qwen3-coder-next", chat=FakeChat(json.dumps({"contract": contract.seat_form(no_negative)})), categories=_CATEGORIES)
    assert (code, refused) == ("missing_negative:polarity", no_negative)
    looped = contract.seat_form(emptied)
    looped["body"][0]["combines"] = ["polarity"]
    code, refused, _out = contract.write_contract("x", "unit.py", "qwen3-coder-next", chat=FakeChat(json.dumps({"contract": looped})), categories=_CATEGORIES)
    assert (code, refused) == ("loop:polarity", looped)
    assert contract.write_contract("x", "unit.py", "qwen3-coder-next", chat=FakeChat(json.dumps({"contract": None})), categories=_CATEGORIES)[:2] == ("call_abstained:abstained", None)
    assert contract.write_contract("x", "unit.py", "nowhere")[:2] == ("unknown_model", None)


def test_the_contract_writers_seat_on_the_command_line(tmp_path, monkeypatch):
    """``contract-write INSTRUCTIONS --path MODULE.py --out CONTRACT``: a written contract lands at ``CONTRACT`` and
    exits 0; a refused one lands beside it, named refused, and exits 1; a failed call writes nothing and exits 2."""
    instructions = tmp_path / "unit.md"
    instructions.write_text("A claim's composed sign.")
    emptied = _emptied(_written_contract())
    for code, written, exit_code, name in (("written", emptied, 0, "unit.contract.json"), ("moved", emptied, 1, "unit.contract.json.refused.json"), ("call_failed:transport_error", None, 2, "")):
        monkeypatch.setattr(contract, "write_contract", lambda *_a, c=code, w=written, **_k: (c, w, None))
        out = tmp_path / code / "unit.contract.json"
        assert contract.main(["contract-write", str(instructions), "--path", "unit.py", "--out", str(out)]) == exit_code
        assert sorted(p.name for p in out.parent.glob("*")) == ([name] if name else [])
    assert contract.main(["contract-write", str(tmp_path / "absent.md"), "--path", "unit.py", "--out", str(tmp_path / "x.json")]) == 2


def test_a_form_its_driver_writes_is_seated_on_the_command_line(tmp_path, capsys):
    """WIRING W7b: ``seat FORM --out CONTRACT`` seats a contract its driver wrote in the contract writer's form, with
    no model call. Each corpus contract, its form taken by ``seat_form`` and seated again by the command, seats with
    the contract's verdict ok and the same functions in the same call order; the command prints ``seated``, then each
    function in call order with its weight, the lines ``check`` prints, and exits 0."""
    corpus = sorted((Path(__file__).parent / "fixtures" / "corpus").glob("*/contract.json"))
    assert len(corpus) >= 5
    for source in corpus:
        given = json.loads(source.read_text())
        form, out = tmp_path / source.parent.name / "form.json", tmp_path / source.parent.name / "seated" / "contract.json"
        form.parent.mkdir()
        form.write_text(json.dumps(contract.seat_form(given)))
        capsys.readouterr()
        assert contract.main(["seat", str(form), "--out", str(out)]) == 0, source
        printed = capsys.readouterr().out.splitlines()
        written = json.loads(out.read_text())
        assert contract.contract_verdict(written) == "ok" and written["detective"] == ["regime", "audit"], source
        assert contract.call_order(written) == contract.call_order(given), source
        assert [(q, kind) for q, kind, _n, _l in contract.elements(written)] == [(q, kind) for q, kind, _n, _l in contract.elements(given)], source
        assert written == _emptied(given), source
        assert contract.main(["check", str(out)]) == 0
        checked = capsys.readouterr().out.splitlines()
        assert printed[0] == "seated" and checked[0] == "ok" and printed[1:] == checked[1:] and len(printed) == 1 + len(contract.call_order(written)), source
        assert sorted(f.name for f in out.parent.iterdir()) == ["contract.json"], source


def test_a_form_the_seat_refuses_is_kept_beside_the_contract_and_named(tmp_path, capsys):
    """WIRING W7b: a form the build refuses (a function that combines itself, two functions of one name in one scope)
    prints its code and is kept as it came in ``CONTRACT.refused.json``; a contract the verdict refuses (a filled cell,
    or any of the contract's own defects) prints its code and is kept as built. Either exits 1 and writes no
    ``CONTRACT``; an existing one is left as it is. A ``FORM`` that cannot be read as JSON prints ``unreadable``, exits
    2 and writes nothing."""
    emptied, filled = _emptied(_written_contract()), _written_contract()
    good = contract.seat_form(emptied)
    looped = copy.deepcopy(good)
    looped["body"][0]["combines"] = ["polarity"]
    clashed = {**copy.deepcopy(good), "body": [*copy.deepcopy(good["body"]), *copy.deepcopy(good["body"])]}
    written_cell = copy.deepcopy(good)
    written_cell["body"][0]["signature"], written_cell["body"][0]["code"] = filled["body"][0]["signature"], filled["body"][0]["code"]
    no_negative = copy.deepcopy(good)
    no_negative["body"][0]["intent_test"]["never"] = []
    cases = (
        ("loop", looped, "loop:polarity", looped),
        ("clash", clashed, "name_clash:polarity", clashed),
        ("filled", written_cell, "filled:polarity", contract.seat_contract(written_cell)[1]),
        ("negative", no_negative, "missing_negative:polarity", contract.seat_contract(no_negative)[1]),
        ("nothing", None, "invalid:format", None),
        ("bodiless", {"format": contract.FORMAT, "path": 3}, "invalid:path", {"format": contract.FORMAT, "path": 3}),
    )
    for name, form, code, kept in cases:
        source, out = tmp_path / name / "form.json", tmp_path / name / "out" / "contract.json"
        source.parent.mkdir()
        source.write_text(json.dumps(form))
        capsys.readouterr()
        assert contract.main(["seat", str(source), "--out", str(out)]) == 1, name
        assert capsys.readouterr().out.splitlines() == [code], name
        assert sorted(f.name for f in out.parent.iterdir()) == ["contract.json.refused.json"], name
        assert json.loads(Path(f"{out}.refused.json").read_text()) == kept, name
    assert contract.seat_contract(written_cell)[1] != written_cell and contract.seat_contract(written_cell)[1]["detective"] == ["regime", "audit"]
    standing = tmp_path / "loop" / "out" / "contract.json"
    standing.write_text("as it is")
    assert contract.main(["seat", str(tmp_path / "loop" / "form.json"), "--out", str(standing)]) == 1 and standing.read_text() == "as it is"
    not_json = tmp_path / "not.json"
    not_json.write_text("{")
    for unreadable in (tmp_path / "absent.json", not_json):
        capsys.readouterr()
        assert contract.main(["seat", str(unreadable), "--out", str(tmp_path / "unread" / "contract.json")]) == 2
        assert capsys.readouterr().out.splitlines() == ["unreadable"] and not (tmp_path / "unread").exists()
    source = tmp_path / "good.json"
    source.write_text(json.dumps(good))
    assert contract.main(["seat", str(source), "--out", str(standing)]) == 0 and json.loads(standing.read_text()) == emptied
    assert Path(f"{standing}.refused.json").exists()  # one left by an earlier refusal is not this command's to delete


def test_the_runbooks_worked_example_seats(tmp_path, capsys):
    form = Path(__file__).resolve().parents[1] / "examples" / "pairs.form.json"
    out = tmp_path / "contract.json"
    assert contract.main(["seat", str(form), "--out", str(out)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "seated"
    seated = json.loads(out.read_text())
    assert contract.contract_verdict(seated) == "ok"
    assert contract.call_order(seated) == ["read_settings.read_pair", "read_settings"]
    assert all(contract.call_context(seated, fn)["function"]["ast_template"] for fn in contract.call_order(seated))


def test_schema_command_prints_the_authoring_form(capsys):
    assert contract.main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out) == contract.contract_schema()


def test_what_the_seat_keeps_is_the_modules_own_or_refused_before_any_call():
    """The imports and globals the seat is given to keep are the module's own, in the contract's form. One that is not
    is its caller's to fix (``bad_given``), refused before any call, never charged to the contract writer as a drop."""
    rate = {"object": "RATE", "doc": "The rate.", "intent": "2", "used_by": ["polarity"], "value": None}
    written = {**_emptied(_written_contract()), "imports": ["import math"], "objects": [rate]}
    for imports, objects, code in (
        (["import math"], [rate], "ok"),
        (["import math("], [], "bad_given:imports[0]"),
        (["import math", "x = 1"], [], "bad_given:imports[1]"),
        ([5], [], "bad_given:imports[0]"),
        ([], [{}], "bad_given:objects[0]"),
        ([], ["RATE"], "bad_given:objects[0]"),
        ([], [rate, {**rate, "object": "1st"}], "bad_given:objects[1]"),
        ([], [{**rate, "intent": "2 +"}], "bad_given:objects[0]"),
        ([], [{"function": "RATE", **rate}], "bad_given:objects[0]"),
    ):
        assert (contract._given_verdict(imports, objects), contract.seat_verdict(written, "unit.py", imports, objects)) == (code, code)
    chat = FakeChat()
    assert (contract.write_contract("x", "unit.py", "qwen3-coder-next", objects=[{}], chat=chat), chat.calls) == (("bad_given:objects[0]", None, None), 0)


def test_the_contract_writers_request_holds_its_record_and_its_schema_alone():
    """WIRING W7 (FOUNDING_PROMPTS [13], [16]: «The prompt is NOT English»): the contract writer's whole request, built
    and not sent, holds no text outside its record and its schema. There is no system message; the one user message is
    the record's text exactly; the format is the schema. The record is one JSON object of data: his instructions, the
    module's path, stem, imports and globals, its contract as it stands and the hand-back (null for a new module), and
    the two vocabularies, Wesker's categories as its caller read them and the output operators."""
    rate = {"object": "RATE", "doc": "The rate — per hour.", "intent": "2", "used_by": ["polarity"], "value": None}
    evidence = contract._seat_evidence("A claim's composed sign.", "pkg/unit.py", ["from café import x"], [rate], _CATEGORIES)
    assert list(evidence) == ["record"]
    record = json.loads(evidence["record"])
    assert list(record) == ["instructions", "path", "stem", "imports", "globals", "current", "handback", "categories", "output_operators"]
    assert record == {
        "instructions": "A claim's composed sign.",
        "path": "pkg/unit.py",
        "stem": "unit",
        "imports": ["from café import x"],
        "globals": [rate],
        "current": None,
        "handback": None,
        "categories": _CATEGORIES,
        "output_operators": contract.OUTPUT_OPERATORS,
    }
    assert evidence["record"] == json.dumps(record, ensure_ascii=False, indent=1) and "café" in evidence["record"] and "—" in evidence["record"]
    filled, handback = _orders_filled(), {"function": "order_total", "ending": "audit:fix_failing_test", "at": Path("unit")}
    revised = json.loads(contract._seat_evidence("x", "orders.py", [], [], _CATEGORIES, filled, handback)["record"])
    assert (revised["current"], revised["handback"]) == (contract.seat_form(filled), {**handback, "at": "unit"})
    held = {**contract.MODELS["qwen3-coder-next"], "timeout_s": 900}
    seat = daemon._held(find_cassette("contract-write"), held, system="")
    assert (seat.answer, seat.system, seat.template.strip()) == ("none", "", "{record}")
    built = plan(seat, evidence, {"contract": contract.contract_schema()})
    assert (built["verdict"], built["invocation"]) == ("ok", "ok")
    assert built["payload"]["messages"] == [{"role": "user", "content": evidence["record"]}]
    assert built["payload"]["format"] == schema.cassette_schema(built["slots"])
    chat = FakeChat(json.dumps({"contract": contract.seat_form(_emptied(_written_contract()))}))
    assert contract.write_contract("A claim's composed sign.", "unit.py", "qwen3-coder-next", chat=chat, categories=_CATEGORIES)[0] == "written"
    sent = chat.payloads[0]
    assert sent["messages"] == [{"role": "user", "content": contract._seat_evidence("A claim's composed sign.", "unit.py", [], [], _CATEGORIES)["record"]}]
    assert sent["format"] == schema.cassette_schema(resolved_slots(seat, {"contract": contract.contract_schema()}, [])[0])


def test_the_seat_reads_weskers_categories_from_wesker_or_refuses_before_any_call(tmp_path, monkeypatch):
    """WIRING W7: the categories in the contract writer's record are read from Wesker's own definition, resolved when
    the seat is called (``detective.wesker_categories`` over ``detective.engine()``), never from the copied table. Where
    they cannot be read the seat refuses under ``categories_unavailable:<code>`` before any call."""
    chat = FakeChat()
    monkeypatch.setattr(detective, "engine", lambda: {"pythonpath": str(tmp_path), "python": sys.executable, "state": "ok"})
    assert (contract.write_contract("x", "unit.py", "qwen3-coder-next", chat=chat), chat.calls) == (("categories_unavailable:wesker_unset", None, None), 0)
    (tmp_path / "Wesker").mkdir()
    (tmp_path / "Wesker" / "engine.py").write_text("class MutationCategory:\n    SWAP = 'SWAP'\n")
    assert (contract.write_contract("x", "unit.py", "qwen3-coder-next", chat=chat), chat.calls) == (("categories_unavailable:no_mutator:SWAP", None, None), 0)
    filled, handback = _orders_filled(), {"function": "order_total", "ending": "audit:fix_failing_test"}
    assert (contract.revise_contract("x", filled, handback, "qwen3-coder-next", chat=chat)[:3], chat.calls) == (("categories_unavailable:no_mutator:SWAP", None, {}), 0)
    (tmp_path / "Wesker" / "engine.py").write_text("class MutationCategory:\n    SWAP = 'SWAP'\n\nclass _SwapMutator:\n    def visit_Call(self, node):\n        return node\n")
    chat = FakeChat(json.dumps({"contract": contract.seat_form(_emptied(_written_contract()))}))
    assert (contract.write_contract("x", "unit.py", "qwen3-coder-next", chat=chat)[0], chat.calls) == ("written", 1)
    assert json.loads(chat.payloads[0]["messages"][0]["content"])["categories"] == {"SWAP": ["Call"]}


def test_a_cassette_declares_whether_the_answer_text_follows_its_prompt(tmp_path):
    """WIRING W7: a cassette's ``[prompt]`` table carries ``answer``, ``block`` unless it says otherwise. The standard
    answer text follows the prompt only under ``block``; under ``none`` the user message is the prompt alone."""
    block = load_cassette(_cassette(tmp_path, "pick", _PICK))
    none = load_cassette(_cassette(tmp_path, "bare", _PICK + '[prompt]\nanswer = "none"\n'))
    assert (block.answer, none.answer, none.verdict()) == ("block", "none", "ok")
    with_block, without = plan(block, {"thing": "x"}), plan(none, {"thing": "x"})
    assert with_block["payload"]["messages"] == [{"role": "user", "content": f"Describe: x\n\n{schema.answer_block(with_block['slots'])}"}]
    assert without["payload"]["messages"] == [{"role": "user", "content": "Describe: x"}]
    sent = "response_format"
    assert with_block["payload"][sent] == without["payload"][sent]  # the schema holds the reply either way


def test_every_other_cassettes_request_still_ends_with_its_answer_block():
    """WIRING W7: only the coding seats drop the answer text. Every other bundled cassette that fills a typed hole keeps
    ``answer = "block"`` by default, and its request is what it was: its prompt, then the answer block."""
    seats = {"contract-write", "daemon-judge", "receiver"}
    assert {cid for cid, _ in list_cassettes() if find_cassette(cid).answer == "none"} == seats
    others = [find_cassette(cid) for cid, _ in list_cassettes() if cid not in seats]
    typed = [c for c in others if c.seat == "typed_hole"]
    assert {c.id for c in typed} >= {"intent-check", "test-input"}
    for cassette in typed:
        evidence = {name: f"<{name}>" for name in schema.template_fields(cassette.template)}
        built = plan(cassette, evidence)
        content = built["payload"]["messages"][-1]["content"]
        assert content == f"{cassette.template.format_map(evidence).rstrip()}\n\n{schema.answer_block(built['slots'])}", cassette.id
        assert content.endswith(schema.answer_block(built["slots"])) and "Answer with ONE JSON object." in content, cassette.id


def test_each_slot_is_resolved_with_what_its_caller_supplies_or_named_missing(tmp_path):
    """``run.resolved_slots``: each slot as the record the schema and the answer block read, every field in hand. A
    runtime slot its caller leaves unsupplied, or supplies in a form the harness cannot hold, is named missing."""
    plain = {"role": "value", "allow_abstain": True, "max_items": 1, "params": [], "description": "", "shape": {}, "pattern": "", "max_chars": 0, "form": {}}
    pick = load_cassette(_cassette(tmp_path, "pick", _PICK))
    assert resolved_slots(pick, {}, []) == ([{"name": "family", "kind": "enum", "vocabulary": ["cd", "dvd", "bluray"], **plain}], [])
    runtime = _PICK.replace('values = ["cd", "dvd", "bluray"]', 'source = "runtime"')
    rt = load_cassette(_cassette(tmp_path, "rt", runtime))
    assert resolved_slots(rt, {}, []) == ([{"name": "family", "kind": "enum", "vocabulary": [], **plain}], ["family"])
    assert resolved_slots(rt, {"family": ["laserdisc"]}, []) == ([{"name": "family", "kind": "enum", "vocabulary": ["laserdisc"], **plain}], [])
    calls = load_cassette(_cassette(tmp_path, "calls", runtime.replace('kind = "enum"', 'kind = "call_objects"')))
    params = [{"name": "a", "type": "str"}]
    assert resolved_slots(calls, {}, []) == ([{"name": "family", "kind": "call_objects", "vocabulary": [], **plain}], ["family"])
    assert resolved_slots(calls, {}, params) == ([{"name": "family", "kind": "call_objects", "vocabulary": [], **plain, "params": params}], [])
    seat = find_cassette("contract-write")
    for supplied in ("x", {}, ["x"]):
        slots, missing = resolved_slots(seat, {"contract": supplied}, [])
        assert (missing, slots[0]["form"]) == (["contract"], {})
    body = find_cassette("function-body")
    shape = _polarity_shape(max_chars=1500)
    slots, missing = resolved_slots(body, {"body": shape}, [])
    assert (missing, slots[0]["shape"], slots[0]["pattern"], slots[0]["max_chars"]) == ([], shape, decisions.code_pattern(shape, _global_syntax()), 1500)
    for supplied in (_polarity_shape(notes="x"), "shape"):
        slots, missing = resolved_slots(body, {"body": supplied}, [])
        assert (missing, slots[0]["shape"], slots[0]["pattern"], slots[0]["max_chars"]) == (["body"], {}, "", 0)


def test_a_syntax_tree_becomes_the_contracts_json_and_comes_back_as_it_went():
    """``contract.json_from_ast``, the inverse of ``ast_from_json``: every statement of the corpus's reference code, and
    each constant JSON cannot hold, comes back through the grammar as Python's own parser made it. A node or a constant
    the grammar does not hold is refused at its path."""
    corpus = Path(__file__).parent / "fixtures" / "corpus"
    for reference in sorted(corpus.glob("*/reference.py")):
        for node in ast.parse(reference.read_text()).body:
            code, value = contract.json_from_ast(node)
            assert code == "ok" and ast.dump(contract.ast_from_json(value, "stmt")[1]) == ast.dump(node)
    for text in ("...", "b'\\xff\\x00a'", "3j", "1.5", "None", "u'é'", "-2", "f'{x!r:>{w}}'", "[*a, *b]", "lambda *a, k=1, **kw: k"):
        expr = ast.parse(text, mode="eval").body
        code, value = contract.json_from_ast(expr)
        assert code == "ok" and ast.dump(contract.ast_from_json(value, "expr")[1]) == ast.dump(expr)
    assert contract.json_from_ast(ast.parse("...", mode="eval").body) == ("ok", {"_type": "Constant", "value": {"ellipsis": True}})
    assert contract.json_from_ast(ast.parse("f(x=1)", mode="eval").body)[1] == {
        "_type": "Call",
        "func": {"_type": "Name", "id": "f", "ctx": "Load"},
        "keywords": [{"arg": "x", "value": {"_type": "Constant", "value": 1}}],
    }
    assert contract.json_from_ast(ast.Constant(value=frozenset())) == ("bad:$.value", None)
    assert contract.json_from_ast(ast.parse("x = 1")) == ("bad:$", None)
    assert contract.json_from_ast([ast.parse("1", mode="eval").body, ast.Module(body=[], type_ignores=[])]) == ("bad:$[1]", None)


@pytest.mark.parametrize(
    ("node", "where"),
    [(ast.parse("x = 1"), "$"), ([ast.parse("1", mode="eval").body, ast.parse("x = 1")], "$[1]"), (ast.Expr(value=ast.parse("x = 1")), "$.value")],
)
def test_a_node_the_grammar_does_not_name_is_refused_at_its_path(node, where):
    """The inverse's walk (``contract._json``) refuses a node the grammar does not name at that node's own path, inside
    a list or a field alike (Detective's WRITE TEST, in its raise form)."""
    try:
        contract._json(node, "$")
    except contract._Bad as _exc:
        assert str(_exc) == where
    except BaseException as other:  # noqa: BLE001 — any other raise is the wrong outcome, named
        pytest.fail(f"{type(other).__name__} where the grammar's refusal at {where} belongs")
    else:
        pytest.fail(f"returned where the grammar refuses at {where}")


def test_a_constant_the_format_cannot_write_is_refused_at_its_path():
    """``contract._constant_json`` writes the constants the format names and refuses any other at its path (Detective's
    WRITE TEST, in its raise form); a field left at its absent default is left out (``kw_defaults``, quantifier ``*?``)."""
    try:
        contract._constant_json((1, 2), "$.value")
    except contract._Bad as _exc:
        assert str(_exc) == "$.value"
    except BaseException as other:  # noqa: BLE001 — any other raise is the wrong outcome, named
        pytest.fail(f"{type(other).__name__} where the grammar's refusal at $.value belongs")
    else:
        pytest.fail("returned where the grammar refuses at $.value")
    assert contract._json(ast.parse("def f(a):\n    return a\n").body[0], "$") == {
        "_type": "FunctionDef",
        "name": "f",
        "args": {"args": [{"arg": "a"}]},
        "body": [{"_type": "Return", "value": {"_type": "Name", "id": "a", "ctx": "Load"}}],
    }


def test_the_functions_a_function_combines_are_the_ones_below_it():
    """``contract.callees``: what the contract's nesting states, each function that combines others with the names of
    the functions below it, and nothing for one that combines none; a member that is no function is not one."""
    corpus = Path(__file__).parent / "fixtures" / "corpus"
    found = {m: contract.callees(json.loads((corpus / m / "contract.json").read_text())) for m in ("clamp", "orders", "durations", "pairs", "grades")}
    assert found == {
        "clamp": {},
        "orders": {"order_total": ["line_total"]},
        "durations": {"title_kind": ["seconds_of"]},
        "pairs": {"read_settings": ["read_pair"]},
        "grades": {"pass_rate": ["passes"]},
    }
    tree = {
        "body": [
            {"function": "a", "functions": [{"function": "b", "functions": [{"function": "c"}]}, {"object": "K"}, "junk"]},
            {"class": "C", "members": [{"function": "m", "functions": [{"function": "n"}]}]},
        ]
    }
    assert contract.callees(tree) == {"a": ["b"], "a.b": ["c"], "C.m": ["n"]}
    assert contract.callees({"body": [{"function": "a", "functions": "not a list"}]}) == {}


def test_a_reissue_removes_the_globals_only_that_call_added():
    """A fresh call starts from its function's cells cleared: its signature, its code and its own objects' values. The
    globals its earlier call added are removed, except one that other written code reads, which stays; the imports it
    added stay, since the contract does not record who added an import (the founder, 2026-10-07: «Remove its
    globals»)."""
    opened, reply = _open_contract()
    one = {"_type": "Constant", "value": 1}
    pi = {"_type": "ImportFrom", "module": "math", "names": [{"name": "pi"}], "level": 0}
    opened["body"][0]["objects"] = [{"object": "LIMIT", "doc": "The edge.", "intent": "0", "value": None}, {"object": "OPEN", "doc": "Left open.", "intent": "1", "value": None}]
    added = {"RATE": one, "BASE": one, "SPARE": one, "LONE": {"_type": "Name", "id": "BASE", "ctx": "Load"}}
    new_objects = [{"name": n, "doc": "d", "value": v} for n, v in added.items()]
    code, filled = contract.fill(opened, "polarity", {**reply, "imports": [pi], "new_objects": new_objects, "objects": {"polarity.LIMIT": one}})
    assert code == "filled"
    filled["objects"].append({"object": "TWICE", "doc": "The rate, twice.", "intent": "RATE * 2", "used_by": ["polarity"], "value": None})
    assert contract.contract_verdict(filled) == "ok"
    given = copy.deepcopy(filled)
    want = copy.deepcopy(filled)
    want["body"][0].update(signature=None, code=None)
    want["body"][0]["objects"][0]["value"] = None
    want["objects"] = [o for o in want["objects"] if o["object"] in ("RATE", "TWICE")]
    # RATE is read by TWICE, a global the contract writer wrote; BASE only by LONE, which goes too; SPARE by nothing
    assert contract.reissue(filled, "polarity") == (
        "reissued",
        want,
        {"cleared": ["polarity.signature", "polarity.code", "polarity.LIMIT"], "removed": ["BASE", "SPARE", "LONE"], "kept": ["RATE"]},
    )
    assert filled == given  # the contract given is never changed
    assert want["added_imports"] == [pi]
    assert contract.fill(want, "polarity", {**reply, "objects": {"polarity.LIMIT": one}})[0] == "filled"


_PI = {"_type": "ImportFrom", "module": "math", "names": [{"name": "pi"}], "level": 0}


def _orders_filled(rate_read: bool = False) -> dict:
    """The corpus's orders module with both functions written through ``fill`` from its reference code: line_total's
    call adds the global RATE and the import of pi; order_total's code reads RATE when ``rate_read``."""
    root = Path(__file__).parent / "fixtures" / "corpus" / "orders"
    tree = ast.parse((root / "reference.py").read_text())

    def cells(name: str) -> dict:
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        body = fn.body
        if rate_read and isinstance(body[0], ast.Return) and body[0].value is not None:
            body = [ast.Return(value=ast.BinOp(left=body[0].value, op=ast.Mult(), right=ast.Name(id="RATE", ctx=ast.Load())))]
        return {"signature": {"args": contract.json_from_ast(fn.args)[1], "returns": None, "decorator_list": []}, "code": contract.json_from_ast(body)[1]}

    rate = {"name": "RATE", "doc": "A rate.", "value": {"_type": "Constant", "value": 1}}
    status, filled = contract.fill(json.loads((root / "contract.json").read_text()), "order_total.line_total", {**cells("line_total"), "imports": [_PI], "new_objects": [rate]})
    assert status == "filled"
    status, filled = contract.fill(filled, "order_total", {**cells("order_total"), "imports": [], "new_objects": []})
    assert status == "filled"
    return filled


def test_a_revision_clears_each_function_whose_contract_it_changes_and_keeps_the_rest():
    """The receiver's contract_wrong (R7; his rulings, separation [7] and [10]): the contract writer writes the
    contract's next version, and each function whose own entry it changes starts with its cells empty, as a reissue
    clears one; every other function keeps its cells, wherever the revision places it. A global a call added goes with
    its function unless code that stays reads it, and stays with a function that stays; the imports the calls added
    stay. A revision that changes nothing is named so: it goes back to the receiver, not to a play."""
    filled = _orders_filled()
    given = copy.deepcopy(filled)
    line, total = contract._find(filled, "order_total.line_total")[0], contract._find(filled, "order_total")[0]

    def revised(change, current: dict = filled) -> tuple:
        seat = copy.deepcopy(contract.seat_form(current))
        change(seat)
        written = contract.seat_contract(seat)[1]
        before = copy.deepcopy(written)
        result = contract.revision(current, written)
        assert written == before  # the contract the seat wrote is never changed
        return result

    def more_tests(seat: dict) -> None:
        seat["body"][1]["intent_test"]["source"] += "\n\ndef test_order_total_of_one_line():\n    assert order_total([(1, 2.0)]) == 2.0\n"

    def reworded(seat: dict) -> None:
        seat["body"][0]["doc"]["prose"] += " Never negative."

    def unnested(seat: dict) -> None:
        seat["body"][1]["combines"] = []

    def dropped(seat: dict) -> None:
        seat["body"] = [{**seat["body"][1], "combines": []}]

    assert revised(lambda seat: None) == ("unchanged", filled, {"kept": ["order_total.line_total", "order_total"], "changed": [], "dropped": [], "removed": [], "carried": ["RATE"]})
    status, new, done = revised(more_tests)
    assert (status, done) == ("revised", {"kept": ["order_total.line_total"], "changed": ["order_total"], "dropped": [], "removed": [], "carried": ["RATE"]})
    assert new["revises"] == contract.contract_digest(filled)
    kept, cleared = contract._find(new, "order_total.line_total")[0], contract._find(new, "order_total")[0]
    assert (kept["signature"], kept["code"], cleared["signature"], cleared["code"]) == (line["signature"], line["code"], None, None)
    assert ([o["object"] for o in new["objects"]], new["added_imports"]) == (["RATE"], [_PI])
    status, new, done = revised(reworded)
    assert (status, done) == ("revised", {"kept": ["order_total"], "changed": ["order_total.line_total"], "dropped": [], "removed": ["RATE"], "carried": []})
    assert (new["objects"], new["added_imports"], contract._find(new, "order_total")[0]["code"]) == ([], [_PI], total["code"])
    status, new, done = revised(unnested)
    assert (status, done) == ("revised", {"kept": ["line_total"], "changed": ["order_total"], "dropped": [], "removed": [], "carried": ["RATE"]})
    assert contract._find(new, "line_total")[0]["code"] == line["code"]
    assert (new["objects"][0]["added_by"], new["objects"][0]["used_by"]) == ("line_total", ["line_total"])
    status, new, done = revised(dropped)
    assert (status, done) == ("revised", {"kept": [], "changed": ["order_total"], "dropped": ["order_total.line_total"], "removed": ["RATE"], "carried": []})
    reading = _orders_filled(rate_read=True)
    status, new, done = revised(reworded, reading)
    assert (status, done["removed"], done["carried"]) == ("revised", [], ["RATE"])  # order_total's code, which stays, reads it
    assert filled == given  # neither contract given is changed


def test_a_revision_is_refused_under_its_own_code():
    """What a revision cannot assemble is refused, each under its own code, with the contract the seat wrote: a contract
    as it stands that the harness cannot play, a written one it cannot play, a revision of another module, and a next
    version whose verdict fails once the cells and the calls' globals are carried in."""
    filled = _orders_filled()
    written = contract.seat_contract(contract.seat_form(filled))[1]
    assert contract.revision({"format": "x"}, written) == ("current:invalid:format", written, {})
    assert contract.revision(filled, {"format": "x"}) == ("invalid:format", {"format": "x"}, {})
    assert contract.revision(filled, {**written, "path": "other.py"}) == ("moved", {**written, "path": "other.py"}, {})
    rate = {"object": "RATE", "doc": "The writer's rate.", "intent": "2", "used_by": [], "value": None}
    clash = {**written, "objects": [rate]}
    assert contract.revision(filled, clash) == ("version:duplicate:RATE", clash, {})  # a global a call added holds the name


def test_the_seat_revises_from_the_instructions_the_contract_and_the_hand_back():
    """``revise_contract`` (his rulings, separation [7] and [10]): one call reads his instructions, the contract as it
    stands in the seat's form and the hand-back record that found it wrong, and the next version is assembled from what
    it writes. A first write reads null for each of the two. A contract the harness cannot play, or no hand-back record naming a
    function, is refused before any call; what the seat refuses comes back with what it refused."""
    filled = _orders_filled()
    seat = contract.seat_form(filled)
    seat["body"][1]["intent_test"]["source"] += "\n\ndef test_order_total_of_one_line():\n    assert order_total([(1, 2.0)]) == 2.0\n"
    handback = {
        "function": "order_total",
        "ending": "audit:fix_failing_test",
        "state": {"next": "fix_failing_test"},
        "doctor": None,
        "receivers": ["lead", "sol"],
        "decisions": list(decisions.RECEIVER_DECISIONS),
    }
    chat = FakeChat(json.dumps({"contract": seat}))
    code, new, done, _out = contract.revise_contract("Order arithmetic.", filled, handback, "qwen3-coder-next", chat=chat, categories=_CATEGORIES)
    assert (code, done["kept"], done["changed"], chat.calls) == ("revised", ["order_total.line_total"], ["order_total"], 1)
    assert contract._find(new, "order_total.line_total")[0]["code"] == contract._find(filled, "order_total.line_total")[0]["code"]
    record = json.loads(chat.payloads[0]["messages"][0]["content"])
    assert (record["instructions"], record["current"], record["handback"]) == ("Order arithmetic.", contract.seat_form(filled), handback)
    first = json.loads(contract._seat_evidence("Order arithmetic.", "orders.py", [], [], _CATEGORIES)["record"])
    assert (first["current"], first["handback"]) == (None, None)
    chat = FakeChat()
    assert contract.revise_contract("x", {"format": "x"}, handback, "qwen3-coder-next", chat=chat)[:3] == ("bad_given:current:invalid:format", None, {})
    assert contract.revise_contract("x", filled, {"ending": "audit:fix_failing_test"}, "qwen3-coder-next", chat=chat)[:3] == ("bad_given:handback", None, {})
    assert chat.calls == 0
    moved = {**seat, "path": "other.py"}
    assert contract.revise_contract("x", filled, handback, "qwen3-coder-next", chat=FakeChat(json.dumps({"contract": moved})), categories=_CATEGORIES)[:3] == (
        "moved",
        contract.seat_contract(moved)[1],
        {},
    )


def test_the_command_line_tells_a_revision_from_a_first_write():
    """``_seat_mode``: a first write takes ``--path`` alone; a revision takes ``--current`` with ``--handback`` and
    ``--function``; any other mix is neither."""
    for given, mode in (
        (("m.py", None, None, None), "write"),
        ((None, "c.json", "h.jsonl", "f"), "revise"),
        (("m.py", "c.json", "h.jsonl", "f"), "mixed"),
        ((None, "c.json", None, "f"), "mixed"),
        ((None, "c.json", "h.jsonl", None), "mixed"),
        (("m.py", None, "h.jsonl", None), "mixed"),
        (("m.py", None, None, "f"), "mixed"),
        ((None, None, None, None), "mixed"),
    ):
        assert contract._seat_mode(*given) == mode, given


def test_a_revision_on_the_command_line(tmp_path, monkeypatch):
    """``contract-write INSTRUCTIONS --current CONTRACT --handback HANDBACKS --function QUALNAME --out NEXT``: the next
    version lands at NEXT, recorded beside it with the decision, the contract it came from, the hand-back and what each
    function became, and exits 0; one that changes nothing lands too and exits 1, for the receiver; a refused one lands
    beside it, named refused, and exits 1; an unreadable input, no hand-back for the function, or a refusal before any
    call writes nothing and exits 2. A command line mixing a first write's arguments with a revision's is refused."""
    instructions = tmp_path / "orders.md"
    instructions.write_text("Order arithmetic.")
    filled = _orders_filled()
    current = tmp_path / "orders.contract.json"
    current.write_text(json.dumps(filled))
    record = {"function": "order_total", "ending": "audit:fix_failing_test"}
    handbacks = tmp_path / "handbacks.jsonl"
    handbacks.write_text(json.dumps({"function": "", "ending": "x"}) + "\n" + json.dumps(record) + "\n")
    seen: list = []
    done = {"kept": ["order_total.line_total"], "changed": ["order_total"], "dropped": [], "removed": [], "carried": ["RATE"]}
    cases = (
        ("revised", filled, done, 0, ["next.json", "next.json.revision.json"]),
        ("unchanged", filled, done, 1, ["next.json", "next.json.revision.json"]),
        ("moved", filled, {}, 1, ["next.json.refused.json"]),
        ("bad_given:handback", None, {}, 2, []),
    )
    for code, new, what, exit_code, names in cases:
        monkeypatch.setattr(contract, "revise_contract", lambda *a, c=code, n=new, w=what, **_k: (seen.append(a), (c, n, w, None))[1])
        out = tmp_path / code / "next.json"
        argv = ["contract-write", str(instructions), "--current", str(current), "--handback", str(handbacks), "--function", "order_total", "--out", str(out)]
        assert contract.main(argv) == exit_code, code
        assert sorted(p.name for p in out.parent.glob("*")) == names, code
    assert seen[0] == ("Order arithmetic.", filled, record, contract.DEFAULT_MODEL)
    sidecar = json.loads((tmp_path / "revised" / "next.json.revision.json").read_text())
    assert sidecar == {"decision": "contract_wrong", "function": "order_total", "from": str(current.resolve()), "handback": record, **done}
    base = ["contract-write", str(instructions), "--current", str(current), "--handback", str(handbacks), "--out", str(tmp_path / "x.json")]
    assert contract.main([*base, "--function", "nowhere"]) == 2
    assert contract.main([*base[:4], "--handback", str(tmp_path / "absent.jsonl"), "--function", "order_total", "--out", str(tmp_path / "x.json")]) == 2
    with pytest.raises(SystemExit) as stopped:
        contract.main(base)  # a revision without --function
    assert stopped.value.code == 2
    with pytest.raises(SystemExit) as stopped:
        contract.main(["contract-write", str(instructions), "--path", "orders.py", "--function", "order_total", "--out", str(tmp_path / "x.json")])
    assert stopped.value.code == 2


def _written_contract() -> dict:
    """A one-function module with its cells written: play makes no call, writes the file and hands it to Detective.
    Its intent tests carry both signs: what polarity must do, and (under OUTPUT, ``const``) what it must never do."""

    def name(i):
        return {"_type": "Name", "id": i, "ctx": "Load"}

    def const(v):
        return {"_type": "Constant", "value": v}

    args = {"posonlyargs": [], "args": [{"arg": "clause_sign", "annotation": name("int")}, {"arg": "prefix_code", "annotation": name("str")}], "kwonlyargs": [], "kw_defaults": [], "defaults": []}
    sign = {"_type": "Compare", "left": name("clause_sign"), "ops": ["Gt"], "comparators": [const(0)]}
    tests = (
        "def test_polarity():\n    assert polarity(1, 'none') == 'positive'\n    assert polarity(-1, 'none') == 'negative'\n\n\n"
        "def test_polarity_depends_on_its_sign():\n    assert polarity(1, 'none') != polarity(-1, 'none')\n"
    )
    covers = [
        {"category": "BOUNDARY", "subject": "clause_sign > 0", "operator": "Gt", "range": "-1, 1"},
        {"category": "VALUE", "subject": "the outcome word", "range": "positive, negative"},
        {"category": "OUTPUT", "subject": "the outcome word", "operator": "const", "range": "clause_sign in {-1, 1}"},
    ]
    return {
        "format": contract.FORMAT,
        "path": "unit.py",
        "doc": "A claim's composed sign.",
        "imports": [],
        "objects": [],
        "body": [
            {
                "function": "polarity",
                "doc": {"prose": "A claim's composed sign.", "operations": [{"category": "BOUNDARY", "what": "the sign's edge at zero"}, {"category": "VALUE", "what": "the outcome word"}]},
                "intent_test": {"source": tests, "covers": covers},
                "objects": [],
                "functions": [],
                "signature": {"args": args, "returns": name("str"), "decorator_list": []},
                "code": [{"_type": "Return", "value": {"_type": "IfExp", "test": sign, "body": const("positive"), "orelse": const("negative")}}],
            }
        ],
        "test_imports": ["from unit import polarity"],
        "detective": ["regime", "audit"],
    }


def test_the_contract_carries_detectives_typed_order():
    """«Make it part of the JSON input, a standard form that gets written, with a typed order to run, starting with
    regime» (the founder, 2026-10-06): regime once for the folder, then audit on each function, whose clean record names
    converge in Detective's own record ([21], [22]). The order is written in one place (``DETECTIVE_ORDER``, WIRING W7):
    the seat sets it, and a contract holding any other is refused. Two-sign is no option of the order: it is inherent to
    the intent tests every contract carries (the founder, 2026-10-06)."""
    good = _written_contract()
    assert contract.DETECTIVE_ORDER == ("regime", "audit") and good["detective"] == list(contract.DETECTIVE_ORDER)
    assert contract.contract_verdict(good) == "ok"
    assert contract.seat_contract(contract.seat_form(_emptied(good)))[1]["detective"] == ["regime", "audit"]
    assert contract.contract_verdict({k: v for k, v in good.items() if k != "detective"}) == "invalid:detective"
    others = ([], ["regime"], ["converge"], ["converge", "regime"], ["regime", "flag"], ["regime", "converge", "converge"], ["regime", 1], "regime", ["regime", "audit --two-sign"])
    for order in (*others, ["regime", "diagnose", "converge"], ["regime", "converge"], ["regime", "audit", "converge"], ("regime", "audit")):
        assert contract.contract_verdict({**good, "detective": order}) == "invalid:detective", order


def test_a_functions_intent_tests_carry_both_signs():
    """The founder, 2026-10-06: the intent-test contract inherently carries the tests of what a function must do and of
    what it must never do. The latter are named in the coverage line under Wesker's OUTPUT category, each by the output
    operator it rules out (the negative-specification paper's μ⁻); both are requisite."""
    good = _written_contract()
    node = good["body"][0]
    covers = node["intent_test"]["covers"]
    positive = [c for c in covers if c["category"] != "OUTPUT"]
    negative = [c for c in covers if c["category"] == "OUTPUT"]

    def with_covers(cs):
        return {**good, "body": [{**node, "intent_test": {**node["intent_test"], "covers": cs}}]}

    assert positive and negative and all(c["operator"] in contract.OUTPUT_OPERATORS for c in negative)
    assert contract.contract_verdict(with_covers(positive)) == "missing_negative:polarity"
    assert contract.contract_verdict(with_covers(negative)) == "missing_coverage:polarity"
    assert contract.contract_verdict(with_covers([*positive, {**negative[0], "operator": "swap"}])) == "unknown_output_operator:polarity"
    assert contract.contract_verdict(with_covers([*positive, {k: v for k, v in negative[0].items() if k != "operator"}])) == "unknown_output_operator:polarity"
    status, tests = contract.render_tests(good)
    assert status == "ok" and "def test_polarity_depends_on_its_sign" in tests and "def test_polarity()" in tests
    seen = contract.call_context(good, "polarity")
    assert isinstance(seen, dict) and seen["function"]["intent_test"] == node["intent_test"] and set(seen["function"]) == {"qualname", "name", "doc", "intent_test", "objects"}


def test_an_unparseable_intent_test_is_named_bad_test():
    """Pinned at Detective's ask (WRITE TEST, 2026-10-06: it refused to write this golden itself): an intent-test source
    that does not parse is ``bad_test``, never ``ok``."""
    doc = {"prose": "p", "operations": [{"category": "VALUE", "what": "w"}]}
    covers = [{"category": "VALUE"}, {"category": "OUTPUT", "operator": "const"}]
    node = {"function": "f", "doc": doc, "intent_test": {"source": "def (:", "covers": covers}}
    assert contract._function_verdict(node, "f") == "bad_test:f"


def _open_contract() -> tuple:
    """The written contract with polarity's cells open, and the reply that writes them back: a call's answer for fill."""
    written = _written_contract()
    node = written["body"][0]
    reply = {"signature": node["signature"], "code": node["code"], "imports": [], "new_objects": []}
    return {**written, "body": [{**node, "signature": None, "code": None}]}, reply


def test_a_required_suite_holds_a_statement():
    """Python's syntax requires a block to hold a statement, though its abstract grammar writes ``stmt*``. The contract's
    grammar says so (``+``), in the schema the model is held to and in the build, so an empty block is never written;
    an ``else`` or a ``finally`` may still be empty (SEPARATION_OF_AUTHORITY.md R6; measured 2026-10-07: eight empty
    blocks, from both local models)."""
    test = {"_type": "Name", "id": "x", "ctx": "Load"}
    assert contract.ast_from_json([{"_type": "If", "test": test, "body": [], "orelse": []}], "stmt", "*")[0] == "bad:$[0].body"
    assert contract.ast_from_json([{"_type": "If", "test": test, "orelse": []}], "stmt", "*")[0] == "bad:$[0].body"
    assert contract.ast_from_json([{"_type": "If", "test": test, "body": [{"_type": "Pass"}], "orelse": []}], "stmt", "*")[0] == "ok"
    loop = {"_type": "For", "target": {"_type": "Name", "id": "i", "ctx": "Store"}, "iter": test, "body": [], "orelse": []}
    assert contract.ast_from_json([loop], "stmt", "*")[0] == "bad:$[0].body"
    defs = contract.grammar_defs()
    for node in ("If", "For", "AsyncFor", "While", "With", "AsyncWith", "Try", "TryStar", "FunctionDef", "AsyncFunctionDef", "ClassDef", "ExceptHandler", "match_case"):
        assert defs[node]["properties"]["body"].get("minItems") == 1 and "body" in defs[node]["required"], node
    for node, field in (("If", "orelse"), ("For", "orelse"), ("Try", "finalbody"), ("Try", "handlers"), ("With", "items")):
        assert "minItems" not in defs[node]["properties"][field] and field not in defs[node]["required"], (node, field)


def test_a_python_keyword_is_never_a_name():
    """Every identifier the grammar admits is one Python accepts: a keyword (``raise``, ``True``) is refused wherever a
    name stands, a soft keyword (``match``) is not (R6, construction; measured 2026-10-07: a call to a name ``raise``)."""

    def call(fn):
        return [{"_type": "Expr", "value": {"_type": "Call", "func": {"_type": "Name", "id": fn, "ctx": "Load"}, "args": [], "keywords": []}}]

    assert contract.ast_from_json(call("raise"), "stmt", "*")[0] == "bad:$[0].value.func.id"
    assert contract.ast_from_json(call("True"), "stmt", "*")[0] == "bad:$[0].value.func.id"
    assert contract.ast_from_json(call("match"), "stmt", "*")[0] == "ok"
    assert contract.ast_from_json({"_type": "Import", "names": [{"name": "os.class"}]}, "stmt")[0] == "bad:$.names[0].name"
    assert contract.ast_from_json({"_type": "Import", "names": [{"name": "os.path"}]}, "stmt")[0] == "ok"


@pytest.mark.parametrize(
    ("args", "path"),
    [
        ((2, "expr_context", "?", "", (1,)), ""),
        ((0, "", "abc", "", (1,)), ""),
        ((3, "stmt", "abc", "a", (1,)), "a"),
        ((1, "identifier", "", "a", (1,)), "a"),
        ((0, "string", "a", "a", (1,)), "a"),
        ((-1, "arguments", "x", "", (1,)), ""),
        ((1, "string", "", "abc", (1,)), "abc"),
        ((-1, "identifier", "*", "", (1,)), ""),
        ((True, "int", "", "p", ()), "p"),
    ],
)
def test_a_value_the_grammar_does_not_hold_is_refused_at_its_path(args, path):
    """Pinned at Detective's ask (WRITE TEST, 2026-10-07, ``converge _build``): each call is one Detective ran, where the
    build refuses with ``_Bad`` carrying the path and a mutant raises another type or returns. The pin is Detective's
    own form (``converge._raises_witness_property``): the type caught and the path asserted, any other error and a return
    each a declared failure."""
    try:
        _result = contract._build(*args)
    except contract._Bad as _exc:
        assert str(_exc) == path
    except BaseException as _exc:
        pytest.fail(f"expected _Bad({path!r}), got {_exc!r}")
    else:
        pytest.fail(f"expected _Bad({path!r}), returned {_result!r}")


_ARGUMENTS = (("posonlyargs", "arg", "*"), ("args", "arg", "*"), ("vararg", "arg", "?"), ("kwonlyargs", "arg", "*"), ("kw_defaults", "expr", "*?"), ("kwarg", "arg", "?"), ("defaults", "expr", "*"))
_PRODUCT = {"_type": "BinOp", "left": {"_type": "Name", "id": "x", "ctx": "Load"}, "op": "Mult", "right": {"_type": "Constant", "value": 2}}


@pytest.mark.parametrize(
    ("args", "source"),
    [
        (("arguments", _ARGUMENTS, {"posonlyargs": [], "args": [{"arg": "x", "annotation": None}], "kwonlyargs": [], "kw_defaults": [], "defaults": []}, "$", ()), "def f(x): pass"),
        (("Return", (("value", "expr", "?"),), {"_type": "Return", "value": _PRODUCT}, "$[0]", ("Import", "ImportFrom")), "return x * 2"),
    ],
)
def test_a_node_is_made_from_its_fields_as_pythons_own_parser_makes_it(args, source):
    """Pinned at Detective's ask (WRITE TEST, 2026-10-07, ``converge _make`` on the two inputs its DO THIS named): its
    goldens were refused, since a node's repr carries its address, so each made node is pinned by its dump against the
    node Python's own parser makes from the same source."""
    parsed = ast.parse(source).body[0]
    assert ast.dump(contract._make(*args)) == ast.dump(parsed.args if args[0] == "arguments" else parsed)


def test_a_field_the_grammar_does_not_name_is_refused_at_its_path():
    """Pinned at Detective's ask (WRITE TEST, 2026-10-07, ``converge _make``): real raises ``_Bad`` at the field's path
    where a mutant raises another type. Detective's own raise form."""
    try:
        _result = contract._make("Return", (("value", "expr", "?"),), {"_type": "Return", "where": 1}, "$", ())
    except contract._Bad as _exc:
        assert str(_exc) == "$.where"
    except BaseException as _exc:
        pytest.fail(f"expected _Bad('$.where'), got {_exc!r}")
    else:
        pytest.fail(f"expected _Bad('$.where'), returned {_result!r}")


def test_two_parameters_never_share_a_name():
    """Python refuses a signature whose parameters share a name («duplicate argument»), and so does the signature cell
    (measured 2026-10-07: ``def line_total(quantity, quantity)``)."""

    def sig(args):
        return {"args": args, "returns": None, "decorator_list": []}

    assert contract._signature_verdict(sig({"args": [{"arg": "quantity"}, {"arg": "quantity"}]})) == "bad:args:repeated"
    assert contract._signature_verdict(sig({"args": [{"arg": "a"}], "vararg": {"arg": "a"}})) == "bad:args:repeated"
    assert contract._signature_verdict(sig({"posonlyargs": [{"arg": "a"}], "kwonlyargs": [{"arg": "a"}], "kw_defaults": [None]})) == "bad:args:repeated"
    assert contract._signature_verdict(sig({"args": [{"arg": "quantity"}, {"arg": "unit_price"}]})) == "ok"


def test_a_reply_is_admitted_whole_or_not_at_all():
    """A reply is checked as the whole change it makes, before any cell becomes final, and statically: nothing is
    imported here, since Detective's first run is the load check (the founder, 2026-10-07). Each refusal is typed, and
    the contract comes back unchanged (SEPARATION_OF_AUTHORITY.md R6):
    - an import that names the module itself, or binds a name the module already defines, is an element defined twice;
    - a name the module's own scope reads that nothing defines would stop it loading;
    - a change the contract's own verdict refuses would leave it unplayable;
    - code Python's compiler refuses is never written.

    A reply free of all four is filled (measured 2026-10-07: `from orders import line_total` inside orders.py)."""
    opened, reply = _open_contract()
    code, written = contract.fill(opened, "polarity", reply)
    assert code == "filled" and written["body"][0]["code"] == reply["code"]

    def imp(module, *names):
        return {"_type": "ImportFrom", "module": module, "names": [{"name": n} for n in names], "level": 0}

    for imports, want in (
        ([imp("unit", "polarity")], "bad_reply:imports[0]:self"),
        ([{"_type": "Import", "names": [{"name": "unit"}]}], "bad_reply:imports[0]:self"),
        ([{"_type": "Import", "names": [{"name": "polarity"}]}], "bad_reply:imports[0]:bound:polarity"),
        ([imp("math", "pi"), imp("cmath", "pi")], "bad_reply:imports[1]:bound:pi"),
    ):
        assert contract.fill(opened, "polarity", {**reply, "imports": imports}) == (want, opened), imports
    assert contract.fill(opened, "polarity", {**reply, "imports": [imp("math", "pi")]})[0] == "filled"

    def obj(name, value):
        return {"name": name, "doc": "d", "value": value}

    def load(name):
        return {"_type": "Name", "id": name, "ctx": "Load"}

    assert contract.fill(opened, "polarity", {**reply, "new_objects": [obj("table", load("nowhere"))]}) == ("bad_reply:module_names:nowhere", opened)
    assert contract.fill(opened, "polarity", {**reply, "new_objects": [obj("table", load("len"))]})[0] == "filled"
    assert contract.fill(opened, "polarity", {**reply, "new_objects": [obj("class", load("len"))]}) == ("bad_reply:new_objects[0]:class", opened)
    # a name the contract's own imports bind is bound once, by an added import or a new global alike
    with_math = {**opened, "imports": ["import math"]}
    assert contract.fill(with_math, "polarity", {**reply, "imports": [imp("cmath", "math")]}) == ("bad_reply:imports[0]:bound:math", with_math)
    assert contract.fill(with_math, "polarity", {**reply, "new_objects": [obj("math", load("len"))]}) == ("bad_reply:new_objects[0]:math", with_math)
    starred = {"_type": "Starred", "value": load("len"), "ctx": "Load"}
    assert contract.fill(opened, "polarity", {**reply, "new_objects": [obj("table", starred)]}) == ("bad_reply:verdict:bad_intent:table", opened)
    code, same = contract.fill(opened, "polarity", {**reply, "code": [{"_type": "Break"}]})
    assert same == opened and code.startswith("bad_reply:module:syntax:SyntaxError: 'break' outside loop")


def test_a_reply_answers_only_for_the_names_it_brings():
    """The reference check judges a reply on its own change (R1): a name the module read undefined before the reply is
    the contract writer's, and Detective's first run finds it, so no reply is refused for it. A name the reply itself
    brings into the module's own scope undefined, in a new object or in its signature's decorators, is refused, and
    alone."""
    opened, reply = _open_contract()
    rate = {"object": "RATE", "doc": "The rate.", "intent": "rate_table", "used_by": ["polarity"], "value": None}
    owed = {**opened, "objects": [rate]}
    assert contract.contract_verdict(owed) == "ok"
    assert contract.fill(owed, "polarity", reply)[0] == "filled"
    nowhere = {"_type": "Name", "id": "nowhere", "ctx": "Load"}
    table = {"name": "table", "doc": "d", "value": nowhere}
    assert contract.fill(owed, "polarity", {**reply, "new_objects": [table]}) == ("bad_reply:module_names:nowhere", owed)
    decorated = {**reply, "signature": {**reply["signature"], "decorator_list": [nowhere]}}
    assert contract.fill(opened, "polarity", decorated) == ("bad_reply:module_names:nowhere", opened)


def _met_cases() -> list:
    """The eleven calls of Detective's WRITE TEST on ``fill`` (2026-10-07, round 3), each with the outcome it observed."""
    opened, reply = _open_contract()
    written = _written_contract()
    node = written["body"][0]
    one = {"_type": "Constant", "value": 1}
    pi = {"_type": "ImportFrom", "module": "math", "names": [{"name": "pi"}], "level": 0}
    e = {"_type": "ImportFrom", "module": "cmath", "names": [{"name": "e"}], "level": 0}
    os_ = {"_type": "Import", "names": [{"name": "os"}]}
    os_path = {"_type": "Import", "names": [{"name": "os.path"}]}
    with_os_path = {**opened, "imports": ["import os.path"]}
    two = copy.deepcopy(opened)
    two["body"][0]["objects"] = [{"object": "LIMIT", "doc": "The edge.", "intent": "0", "value": None}, {"object": "OTHER", "doc": "Another.", "intent": "1", "value": None}]
    two_filled = copy.deepcopy(two)
    two_filled["body"][0].update(signature=node["signature"], code=node["code"])
    two_filled["body"][0]["objects"][1]["value"] = one
    without = {k: v for k, v in reply.items() if k != "signature"}
    without_code = {k: v for k, v in reply.items() if k != "code"}
    return [
        ({**written, "body": [{**node, "code": None}]}, without, ("filled", {**written, "added_imports": []})),
        ({**written, "body": [{**node, "signature": None}]}, without_code, ("filled", {**written, "added_imports": []})),
        (opened, {**reply, "new_objects": [{"name": "a", "doc": "d", "value": one, "extra": 1}]}, ("bad_reply:new_objects[0]", opened)),
        (two, {**reply, "objects": {"polarity.LIMIT": None, "polarity.OTHER": one}}, ("filled", {**two_filled, "added_imports": []})),
        (opened, {**reply, "imports": [pi, pi]}, ("filled", {**written, "added_imports": [pi]})),
        (opened, {**reply, "new_objects": [{"name": "a-b", "doc": "d", "value": one}]}, ("bad_reply:new_objects[0]:a-b", opened)),
        (opened, {**reply, "a_extra": 1, "z_extra": 1}, ("bad_reply:a_extra", opened)),
        (with_os_path, {**reply, "imports": [os_]}, ("bad_reply:imports[0]:bound:os", with_os_path)),
        ({**opened, "added_imports": [pi]}, {**reply, "imports": [e]}, ("filled", {**written, "added_imports": [pi, e]})),
        (opened, {**reply, "imports": [os_path, os_]}, ("bad_reply:imports[1]:bound:os", opened)),
        (with_os_path, {**reply, "new_objects": [{"name": "os", "doc": "d", "value": one}]}, ("bad_reply:new_objects[0]:os", with_os_path)),
    ]


@pytest.mark.parametrize(("given", "reply", "want"), _met_cases())
def test_a_reply_meets_what_the_contract_already_holds(given, reply, want):
    """Pinned at Detective's ask (WRITE TEST, 2026-10-07, ``converge fill``, round 3): eleven calls its search had not
    built, where a reply meets what the contract already holds, each with the outcome observed.
    - A cell the contract writer filled is not asked again: a reply carrying only the other cell is filled.
    - A new global is exactly a name, a docstring and a value; the first stray key, in order, names the refusal; a name
      Python would not take is refused as the reply's, before the contract's own verdict sees it.
    - Of two open objects a reply may fill one and leave the other open.
    - An import a reply repeats is written once, and an import already added stays.
    - A name an import binds (``import os.path`` binds ``os``), the contract's or the reply's, is never bound again."""
    assert contract.fill(given, "polarity", reply) == want


def test_an_answer_to_a_final_cell_is_named_apart_from_a_malformed_one():
    """A filled cell is final. A reply answering one is refused as ``bad_reply:final:<cell>``, and a malformed answer to an
    open cell as ``bad_reply:<cell>``: the first is put right by leaving the cell out, the second by answering it well, so
    the two never share a code."""
    written = _written_contract()
    node = written["body"][0]
    opened, reply = _open_contract()
    code_open = {**written, "body": [{**node, "code": None}]}
    sig_open = {**written, "body": [{**node, "signature": None}]}
    assert contract.fill(code_open, "polarity", reply) == ("bad_reply:final:signature", code_open)
    assert contract.fill(sig_open, "polarity", reply) == ("bad_reply:final:code", sig_open)
    assert contract.fill(opened, "polarity", {**reply, "signature": {"args": "x"}}) == ("bad_reply:signature", opened)


def test_a_reply_is_judged_with_every_other_open_cell_stubbed():
    """Calls go bottom-up, so a function below another is written while the one above is still open. The module the
    reply is judged in holds a stub in every cell still open (R6, construction), so the first reply is filled, and the
    one above is asked next."""
    opened, reply = _open_contract()
    node = opened["body"][0]
    nested = {**opened, "body": [{**node, "functions": [{**node, "function": "helper"}]}]}
    assert contract.call_order(nested) == ["polarity.helper", "polarity"]
    code, below = contract.fill(nested, "polarity.helper", reply)
    assert code == "filled"
    assert below["body"][0]["functions"][0]["code"] == reply["code"] and below["body"][0]["code"] is None
    code, whole = contract.fill(below, "polarity", reply)
    assert code == "filled" and contract.render_module(whole)[0] == "ok"


def test_detective_knows_each_function_by_its_name_in_the_written_file():
    """The file is written flat: a function below another sits beside it, one below a class's member is a method."""
    module = {
        "path": "m.py",
        "objects": [],
        "body": [{"function": "top", "functions": [{"function": "low"}]}, {"class": "C", "members": [{"function": "m", "functions": [{"function": "h"}]}]}],
    }
    assert contract.detective_targets(module) == [["top.low", "m.py::low"], ["top", "m.py::top"], ["C.m.h", "m.py::C.h"], ["C.m", "m.py::C.m"]]


def test_adaptive_context_grows_for_prompt_text_and_repair_evidence(tmp_path):
    source, _reply = _open_contract()
    profile = contract.MODELS[contract.LINE_MODEL]
    small = contract.call_payload(source, "polarity", profile, "ollama")
    padded = copy.deepcopy(source)
    padded["body"][0]["doc"]["prose"] += "é" * 8_000
    large = contract.call_payload(padded, "polarity", profile, "ollama")
    repaired = contract.call_payload(source, "polarity", profile, "ollama", {"rejected": [], "evidence": "é" * 8_000})

    assert small["weight"] == large["weight"]
    assert large["context"]["prompt_bytes"] - small["context"]["prompt_bytes"] == 16_000
    assert [request["payload"]["options"]["num_ctx"] for request in (small, large, repaired)] == [16_384, 32_768, 32_768]
    assert profile["options"]["num_ctx"] == 16_384
    assert decisions.context_window(13_312, 2048, 16_384, 262_144) == 16_384
    assert decisions.context_window(13_313, 2048, 16_384, 262_144) == 32_768
    assert decisions.context_window(200_000, 2048, 16_384, 262_144) == 262_144
    assert decisions.context_window(0, None, 16_384, 262_144) == "context_options_invalid"
    assert decisions.context_window(0, 2048, 32_768, 16_384) == "context_options_invalid"

    padded["body"][0]["doc"]["prose"] += "x" * profile["context_limit"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(padded))
    out = tmp_path / "play"
    result = contract.play(path, out, contract.LINE_MODEL, chat=lambda *_args: pytest.fail("over-capacity call reached the model"))
    assert result["calls"] == 0 and result["skipped"] == ["context_over_capacity@polarity"]
    assert json.loads((out / "handbacks.jsonl").read_text())["ending"] == "context_over_capacity"


def test_coding_seat_sizes_its_rendered_request_with_its_own_reply_cap():
    profile = contract.MODELS[contract.LINE_MODEL]
    seat = daemon._seat(find_cassette("receiver"), profile)
    evidence = {"record": "x" * 20_000}
    calls = []

    def chat(_endpoint, payload, _timeout, _api):
        calls.append(payload)
        return '{"decision":"cannot_tell"}', {"prompt_tokens": 2501}

    result = invoke(seat, evidence, {"decision": decisions.DECISIONS}, chat=chat)
    assert result.status == "admitted"
    assert calls[0]["options"]["num_ctx"] == 32_768
    telemetry = result.records[0].telemetry
    assert telemetry["prompt_tokens"] == 2501
    assert telemetry["context"]["prompt_bytes"] == 20_000
    assert telemetry["context"]["reply_tokens"] == seat.options["num_predict"] == 64

    oversized = {"record": "x" * profile["context_limit"]}
    refused = invoke(seat, oversized, {"decision": decisions.DECISIONS}, chat=chat)
    assert (refused.status, refused.reason, len(calls)) == ("refused", "context_over_capacity", 1)
