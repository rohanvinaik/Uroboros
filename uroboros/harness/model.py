"""uroboros.harness.model — the one wire to a language model. Stdlib only.

The harness supports Ollama's native ``/api/chat`` and the OpenAI-compatible ``/v1`` wire for local servers.
The configured server enforces the reply schema during decoding. The native Ollama wire also carries
the adaptive ``num_ctx`` reservation, ``keep_alive``, and ``think``.

Either way the request is built from what the cassette declares plus the typed hole
the hardware computed, and the reply comes back verbatim with its telemetry. There is
no tool calling, no streaming, no shell — the model receives messages and returns
text, and nothing else crosses the wire.

A process releases what it used. llama-server's router has no idle timeout (b10180),
so a model stayed resident until ``--models-max`` evicted it: a qwen3-4b sat in RAM for
22 days (2026-10-04). Every successful call records its ``(api, endpoint, model)``, and
an exit hook registered at import unloads each one (:func:`release_used`);
``UROBOROS_LLM_KEEP_LOADED=1`` keeps them, for a loop of single runs that wants the
model warm. Residue, named: the router unloads a model for every client at once, and a
process killed outright (SIGKILL) runs no exit hook.

The bake-off's settings are cassette parameters, not constants here: the format is
the full schema (never ``"json"``), ``think: false`` for a thinking model (its budget
otherwise vanishes into reasoning), a low temperature for correctness, an explicit
context (``clean-code-without-a-gpu`` §4, §9). Options are written Ollama-style; on
the OpenAI wire :func:`decisions.openai_options` translates them and NAMES what it
had to drop.
"""

from __future__ import annotations

import atexit
import json
import os
import time
import urllib.error
import urllib.request

from . import decisions

#: Every (api, endpoint, served model) a successful call in this process used and has not released.
_used: set[tuple[str, str, str]] = set()


class TransportError(Exception):
    """The call did not complete — unreachable host, HTTP error, timeout, or a reply
    that was not the JSON envelope Ollama sends. Distinct from a reply that arrived and
    failed admission: the remedy differs (THE_DYAD Law 10)."""


def resolve_api(cassette_api: str) -> str:
    """The wire for this call, resolved NOW: ``$UROBOROS_LLM_API``, else the cassette's
    pin, else ``openai`` (:func:`decisions.api_flavor`)."""
    return decisions.api_flavor(os.environ.get("UROBOROS_LLM_API", ""), cassette_api)


def resolve_endpoint(api: str, cassette_endpoint: str) -> str:
    """The endpoint for this call, resolved NOW: ``$UROBOROS_LLM_ENDPOINT``, else the
    cassette's pinned host, else the default for this wire."""
    env = os.environ.get("UROBOROS_LLM_ENDPOINT", "")
    source = decisions.endpoint_source(env, cassette_endpoint)
    chosen = {"env": env, "cassette": cassette_endpoint, "default": decisions.default_endpoint(api)}[source]
    return chosen.strip().rstrip("/")


def context_budget(api: str, messages: list, options: dict, maximum: int | None) -> tuple[dict, dict | None]:
    """Size the assembled text request for a profile that declares an adaptive Ollama context."""
    if api != "ollama" or maximum is None:
        return options, None
    # A byte-level tokenizer can spend one token per byte. Budget the actual text,
    # including any long literals, tests, child source and repair evidence.
    prompt_bytes = sum(len(message["content"].encode("utf-8", errors="surrogatepass")) for message in messages)
    minimum = options.get("num_ctx")
    reply_tokens = options.get("num_predict")
    window = decisions.context_window(prompt_bytes, reply_tokens, minimum, maximum)
    report = {"prompt_bytes": prompt_bytes, "reply_tokens": reply_tokens, "minimum": minimum, "maximum": maximum, "num_ctx": window}
    return ({**options, "num_ctx": window} if isinstance(window, int) else options), report


def request_payload(api: str, model: str, messages: list, fmt: dict | None, options: dict, think: bool | None, keep_alive: str) -> tuple[dict, list]:
    """The exact request body for this wire, plus the option names it could not carry.

    The schema and ``think`` are sent only when declared: a generative call is free
    text (no format), and a model that does not think is not told how to. On the
    OpenAI wire the schema rides in ``response_format`` and ``think`` / ``keep_alive``
    have no field, so they are reported as dropped rather than silently lost."""
    served = decisions.served_model_name(model, api)
    if api == "ollama":
        payload: dict = {"model": served, "messages": messages, "stream": False, "options": dict(options)}
        if fmt is not None:
            payload["format"] = fmt
        if think is not None:
            payload["think"] = think
        if keep_alive:
            payload["keep_alive"] = keep_alive
        return payload, []
    sent, dropped = decisions.openai_options(options)
    payload = {"model": served, "messages": messages, "stream": False, **sent}
    if fmt is not None:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": fmt}}
    if think is not None:
        # Not a dropped knob: llama.cpp passes this into the chat template, and it is
        # load-bearing. Measured 2026-09-11 — a thinking model with thinking left on
        # spent its whole budget reasoning and returned EMPTY content (SmolLM3 and
        # qwen3:8b, 96/96 tokens, finish_reason=length); with it off, 15 and 12 tokens
        # and a correct answer.
        payload["chat_template_kwargs"] = {"enable_thinking": think}
    if keep_alive:
        dropped.append("keep_alive")  # residency is the server's business here (--models-max)
    return payload, dropped


def _post(url: str, payload: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        raise TransportError(f"http_{exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransportError(f"unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TransportError(f"bad_envelope: {exc}") from exc


def chat(endpoint: str, payload: dict, timeout_s: float, api: str = "openai") -> tuple[str, dict]:
    """POST one chat request; return ``(content, telemetry)``. The per-call wall is the
    guard against one slow generation stalling a caller. Telemetry carries the same
    named keys whichever wire answered, so a measurement survives an engine swap.
    Raises :class:`TransportError`."""
    started = time.monotonic()
    if api == "ollama":
        raw = _post(f"{endpoint}/api/chat", payload, timeout_s)
        _note_used(api, endpoint, payload)
        telemetry = {
            "prompt_tokens": raw.get("prompt_eval_count", 0),
            "output_tokens": raw.get("eval_count", 0),
            "eval_ms": round(raw.get("eval_duration", 0) / 1e6, 1),
            "total_ms": round(raw.get("total_duration", 0) / 1e6, 1),
            "load_ms": round(raw.get("load_duration", 0) / 1e6, 1),
            "done_reason": raw.get("done_reason", ""),
            "api": "ollama",
        }
        return str(raw.get("message", {}).get("content", "")), telemetry
    raw = _post(f"{endpoint}/chat/completions", payload, timeout_s)
    _note_used(api, endpoint, payload)
    usage = raw.get("usage", {}) or {}
    choice = (raw.get("choices") or [{}])[0]
    telemetry = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "eval_ms": 0.0,
        "total_ms": round((time.monotonic() - started) * 1000, 1),
        "load_ms": 0.0,
        "done_reason": str(choice.get("finish_reason", "")),
        "api": "openai",
    }
    return str((choice.get("message") or {}).get("content") or ""), telemetry


def _note_used(api: str, endpoint: str, payload: dict) -> None:
    served = str(payload.get("model", ""))
    if served:
        _used.add((api, endpoint, served))


def _post_status(url: str, payload: dict, timeout_s: float) -> tuple[int, str]:
    """POST and return ``(status, body)`` whatever the reply; status 0 when none came."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, ""


def release(api: str, endpoint: str, served: str, timeout_s: float = 10.0) -> str:
    """Unload one model NOW; the outcome is :func:`decisions.release_outcome`'s named code.
    Never raises, so it is safe at exit. A model that is gone either way (released, not
    running, unknown) leaves the record; one that could not be released stays in it."""
    url, body = decisions.unload_request(api, endpoint, served)
    outcome = decisions.release_outcome(*_post_status(url, body, timeout_s)) if url else "rejected"
    if outcome in ("released", "not_running", "unknown_model"):
        _used.discard((api, endpoint, served))
    return outcome


def release_used() -> dict:
    """The exit hook: release every model this process used, one failure not stopping the
    rest, unless ``UROBOROS_LLM_KEEP_LOADED`` is set. Returns each one's outcome."""
    if os.environ.get("UROBOROS_LLM_KEEP_LOADED"):
        return {}
    return {key: release(*key) for key in sorted(_used)}


atexit.register(release_used)


def list_models(endpoint: str, timeout_s: float = 3.0, api: str = "openai") -> list[str] | None:
    """The models an endpoint serves, or ``None`` when it does not answer — which is a
    different fact from answering with an empty list (Law 10)."""
    path, key = ("/api/tags", "models") if api == "ollama" else ("/models", "data")
    try:
        with urllib.request.urlopen(f"{endpoint}{path}", timeout=timeout_s) as resp:
            raw = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    field = "name" if api == "ollama" else "id"
    return [str(m.get(field, "")) for m in raw.get(key, [])]
