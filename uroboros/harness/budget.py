"""Score the structured contract and size local AST replies from measured calls."""

import ast
import math
import warnings


def _json_nodes(value):
    """The JSON nodes of a value: a dict is 1 plus its values, a list 1 plus its items, anything else 1."""
    total, pending = 0, [value]
    while pending:
        node = pending.pop()
        total += 1
        if isinstance(node, dict):
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return total


def _tree_nodes(source, mode):
    """The syntax-tree nodes of one source string, or None when it does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # a warning about the measured text is not a parse failure
            return len(list(ast.walk(ast.parse(source, mode=mode))))
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None


def _list_of_dicts(value):
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def contract_score(context):
    """The complexity of one write call, computed from its context (pure).

    The parts:
      "json":    the JSON nodes of the whole context. A dict counts 1 plus its values, a list 1 plus its items, and any
                 other value 1, so a string counts 1 here however long it is;
      "tests":   the syntax-tree nodes of context["function"]["intent_test"]["source"]:
                 len(list(ast.walk(ast.parse(s))));
      "below":   the same count, summed over the "source" of each entry of context.get("below", []) that has one (a
                 str);
      "intents": the syntax-tree nodes of each object's "intent", where it is a str (ast.parse(s, mode="eval")), summed
                 over context["file"]["objects"] and context["function"]["objects"] where present.

    Returns {"score": the sum of the parts, "parts": {"json", "tests", "below", "intents"}}.
    Codes:
      "invalid": context is not a dict, or context["function"]["intent_test"]["source"] is not a str, or any of these
                 is present and malformed: context["below"] (it must be a list of dicts), context["file"] (a dict),
                 context["file"]["objects"] or context["function"]["objects"] (each a list of dicts). An entry with no
                 "source", or an object with no str "intent", is not malformed: it adds nothing;
      "unparsed:<path>": the first string that does not parse, checked in this order: "function.intent_test.source",
                         "below[<i>].source", "file.objects[<i>].intent", "function.objects[<i>].intent".
    """
    if not isinstance(context, dict):
        return "invalid"
    function = context.get("function")
    if not isinstance(function, dict):
        return "invalid"
    intent_test = function.get("intent_test")
    test = intent_test.get("source") if isinstance(intent_test, dict) else None
    if not isinstance(test, str):
        return "invalid"

    # A part that is absent is empty; a part that is present, even as null, must be well formed. Every shape check
    # runs before any string is parsed, so a malformed context is "invalid" whatever its strings say.
    below = context.get("below", [])
    file = context.get("file", {})
    if not _list_of_dicts(below) or not isinstance(file, dict):
        return "invalid"
    file_objects, function_objects = file.get("objects", []), function.get("objects", [])
    if not (_list_of_dicts(file_objects) and _list_of_dicts(function_objects)):
        return "invalid"

    # The strings to measure, in the order "unparsed" names them: (part, path, text, parse mode). An entry or object
    # without a str there adds nothing, and the index <i> is the position in its list.
    work = [("tests", "function.intent_test.source", test, "exec")]
    for i, entry in enumerate(below):
        if isinstance(entry.get("source"), str):
            work.append(("below", f"below[{i}].source", entry["source"], "exec"))
    for where, objects in (("file", file_objects), ("function", function_objects)):
        for i, obj in enumerate(objects):
            if isinstance(obj.get("intent"), str):
                work.append(("intents", f"{where}.objects[{i}].intent", obj["intent"], "eval"))

    parts = {"json": 0, "tests": 0, "below": 0, "intents": 0}
    for part, path, text, mode in work:
        nodes = _tree_nodes(text, mode)
        if nodes is None:
            return f"unparsed:{path}"
        parts[part] += nodes
    parts["json"] = _json_nodes(context)
    return {"score": sum(parts.values()), "parts": parts}


def _is_int(value):
    # A bool is never an int here.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_count(value):
    return _is_int(value) and value >= 0


def _is_finite(value):
    # An int is finite by nature (math.isfinite would itself raise OverflowError on a huge one);
    # only a float can be infinite or NaN. A bool is never a number here.
    return _is_int(value) or (isinstance(value, float) and math.isfinite(value))


def _is_positive_number(value):
    return _is_finite(value) and value > 0


def write_cap(score, prompt_chars, calibration):
    """The output cap for one write call, from its complexity, with the window check (pure).

    calibration: {"tokens_per_point": a number > 0, "chars_per_token": a number > 0, "window": an int > 0,
                  "overhead_tokens": an int >= 0 (optional, default 0)}. tokens_per_point is already the ratio for this
                  setup (thinking or not), margin included. A bool is never a number or an int here, and neither is
                  an infinite or NaN float (math.isfinite).

    num_predict            = ceil(score * tokens_per_point)
    prompt_tokens_estimate = ceil(prompt_chars / chars_per_token) + overhead_tokens
    needed                 = prompt_tokens_estimate + num_predict
    Returns {"num_predict": ..., "prompt_tokens_estimate": ..., "needed": ...}.

    Codes, checked in this order:
      "invalid": score or prompt_chars is not an int >= 0; calibration is not a dict; overhead_tokens is present and
                 not an int >= 0;
      "uncalibrated:tokens_per_point", "uncalibrated:chars_per_token", "uncalibrated:window": that key is missing,
                 None, not a number (for window: not an int), or not > 0;
      "over_capacity:<needed>/<window>": needed > window.
    The function never raises. A computation that overflows (OverflowError), or gives a value that is not finite, is
    "invalid".
    """
    if not (_is_count(score) and _is_count(prompt_chars) and isinstance(calibration, dict)):
        return "invalid"
    # "Present" means the key exists: a present None is not an int >= 0, so it is "invalid".
    overhead_tokens = calibration.get("overhead_tokens", 0)
    if not _is_count(overhead_tokens):
        return "invalid"

    tokens_per_point = calibration.get("tokens_per_point")
    if not _is_positive_number(tokens_per_point):
        return "uncalibrated:tokens_per_point"
    chars_per_token = calibration.get("chars_per_token")
    if not _is_positive_number(chars_per_token):
        return "uncalibrated:chars_per_token"
    window = calibration.get("window")
    if not (_is_int(window) and window > 0):
        return "uncalibrated:window"

    try:
        predicted = score * tokens_per_point
        prompt_estimate = prompt_chars / chars_per_token
        if not (_is_finite(predicted) and _is_finite(prompt_estimate)):
            return "invalid"
        num_predict = math.ceil(predicted)
        prompt_tokens_estimate = math.ceil(prompt_estimate) + overhead_tokens
        needed = prompt_tokens_estimate + num_predict
        if needed > window:
            return f"over_capacity:{needed}/{window}"
    except (ArithmeticError, ValueError):
        # OverflowError (a number too large to convert or to ceil) and ValueError (an int too long to print).
        return "invalid"
    return {"num_predict": num_predict, "prompt_tokens_estimate": prompt_tokens_estimate, "needed": needed}


_INT_KEYS = ("score", "output_tokens", "prompt_chars", "prompt_tokens")
_BOOL_KEYS = ("correct", "thinking")


def _margin_ok(margin):
    """True for a finite int or float >= 1 that is not a bool."""
    if isinstance(margin, bool) or not isinstance(margin, (int, float)):
        return False
    try:
        return math.isfinite(margin) and margin >= 1
    except OverflowError:  # an int too large to be a float is no usable finite margin
        return False


def _measure(row):
    """(tokens per point, chars per token, correct, thinking) of a valid row; None when the row is skipped."""
    if not isinstance(row, dict):  # no keys at all: a row missing every key
        return None
    score, out, chars, toks = (row.get(key) for key in _INT_KEYS)  # .get: a missing key is None, nothing is inserted
    correct, thinking = (row.get(key) for key in _BOOL_KEYS)
    if not all(_is_int(v) and v > 0 for v in (score, out, chars, toks)):
        return None
    if not isinstance(correct, bool) or not isinstance(thinking, bool):
        return None
    try:
        return out / score, chars / toks, correct, thinking
    except OverflowError:  # a ratio too large for a float cannot be measured: out of range
        return None


def calibrate(rows, margin):
    """The measured ratios write_cap needs, from recorded calls (pure).

    rows: a list of dicts holding "score", "output_tokens", "prompt_chars" and "prompt_tokens" (each an int > 0),
          "correct" and "thinking" (each a bool). Other keys are ignored. A bool is never an int here. A row missing a
          key, or with a value of the wrong type or out of range, is skipped and counted. The caller passes rows of one
          model, one operation and one cell form only. This function does not separate them.
    margin: a finite number >= 1 (not a bool; math.isfinite).

    tokens_per_point, per setup: the largest output_tokens / score over the CORRECT valid rows of that setup, times
      margin. "plain" comes from rows with thinking False, "thinking" from rows with thinking True. A setup with no
      correct row gets None, and is named in "missing".
    chars_per_token: the smallest prompt_chars / prompt_tokens over all valid rows. This is the cautious end, since it
      estimates more tokens.

    Returns {"plain": ..., "thinking": ..., "chars_per_token": ..., "rows": <the number of valid rows>,
             "skipped": <the number of invalid rows>, "missing": [names, in the order "plain", "thinking"]}.
    Codes: "invalid" (rows is not a list, or margin is not a number >= 1); "no_rows" (there is no valid row).
    """
    if not isinstance(rows, list) or not _margin_ok(margin):
        return "invalid"
    measured = [m for m in map(_measure, rows) if m is not None]
    if not measured:
        return "no_rows"
    tokens_per_point = {}
    missing = []
    for name, wants_thinking in (("plain", False), ("thinking", True)):
        ratios = [tpp for tpp, _, correct, thinking in measured if correct and thinking == wants_thinking]
        tokens_per_point[name] = max(ratios) * margin if ratios else None
        if not ratios:
            missing.append(name)
    return {
        "plain": tokens_per_point["plain"],
        "thinking": tokens_per_point["thinking"],
        "chars_per_token": min(cpt for _, cpt, _, _ in measured),
        "rows": len(measured),
        "skipped": len(rows) - len(measured),
        "missing": missing,
    }
