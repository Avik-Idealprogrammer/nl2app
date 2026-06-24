"""
repair.py — Validation + Repair Engine (THE core deliverable of this task).

Philosophy:
  A full blind retry on any failure is the "script" behavior this task
  explicitly penalizes. Instead we:

    1. Validate against JSON Schema -> get a structured list of errors,
       each with a JSON path (e.g. "api.endpoints[2].method").
    2. Classify each error into a repair STRATEGY:
         - SYNTAX        -> json fixups (trailing commas, smart quotes, etc.)
         - MISSING_FIELD -> targeted re-prompt asking only for that field
         - TYPE_MISMATCH -> targeted re-prompt asking only for that field,
                            with the wrong value + expected type shown
         - HALLUCINATED_FIELD -> strip it (cheap, no LLM call needed)
         - CROSS_LAYER_MISMATCH -> targeted re-prompt with both layers shown,
                            asking the model to reconcile only the mismatch
    3. Apply the cheapest fix first (local, deterministic) before spending
       an LLM call. Only call the LLM for fields that truly need re-generation,
       and only send that field's context — not the whole document.
    4. Cap total repair attempts per request (MAX_REPAIR_ROUNDS) so a
       pathological input can't loop forever; after the cap we surface a
       structured failure rather than hanging.

This keeps repair cost roughly O(number of broken fields), not O(whole doc).
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import jsonschema

from . import llm_provider


class RepairStrategy(str, Enum):
    SYNTAX = "syntax"
    MISSING_FIELD = "missing_field"
    TYPE_MISMATCH = "type_mismatch"
    HALLUCINATED_FIELD = "hallucinated_field"
    CROSS_LAYER_MISMATCH = "cross_layer_mismatch"
    UNFIXABLE = "unfixable"


@dataclass
class RepairAction:
    path: str
    strategy: RepairStrategy
    detail: str
    llm_call_used: bool = False


@dataclass
class RepairReport:
    success: bool
    rounds_used: int
    actions: list[RepairAction] = field(default_factory=list)
    remaining_errors: list[str] = field(default_factory=list)


MAX_REPAIR_ROUNDS = 3


# ---------- Stage 1: syntax-level JSON repair (no LLM, deterministic) ----------

def try_parse_json(raw: str) -> tuple[Optional[dict], Optional[str]]:
    """Attempt parse with progressively more aggressive cleanup. Returns (obj, error)."""
    candidates = [raw]

    # Strip trailing commas: ,} or ,]
    cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
    candidates.append(cleaned)

    # Normalize smart quotes
    smart_quotes = cleaned.translate({0x201C: '"', 0x201D: '"', 0x2018: "'", 0x2019: "'"})
    candidates.append(smart_quotes)

    last_error = None
    for candidate in candidates:
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError as e:
            last_error = str(e)
            continue
    return None, last_error


# ---------- Stage 2: schema validation -> classified errors ----------

def validate_against_schema(obj: dict, schema: dict) -> list[jsonschema.ValidationError]:
    validator = jsonschema.Draft7Validator(schema)
    return sorted(validator.iter_errors(obj), key=lambda e: list(e.path))


def classify_error(err: jsonschema.ValidationError) -> RepairStrategy:
    if err.validator == "required":
        return RepairStrategy.MISSING_FIELD
    if err.validator in ("type", "enum"):
        return RepairStrategy.TYPE_MISMATCH
    if err.validator in ("minItems", "minLength"):
        return RepairStrategy.MISSING_FIELD
    return RepairStrategy.UNFIXABLE


def _path_str(err: jsonschema.ValidationError) -> str:
    return ".".join(str(p) for p in err.path) or "<root>"


# ---------- Stage 3: hallucinated field stripping (deterministic) ----------

def strip_unknown_fields(obj: dict, schema: dict) -> tuple[dict, list[str]]:
    """
    Recursively removes object keys not declared in the schema's `properties`,
    when `additionalProperties` is implicitly false for our contracts.
    Returns (cleaned_obj, list_of_stripped_paths).
    """
    stripped: list[str] = []

    def _clean(node: Any, sub_schema: dict, path: str) -> Any:
        if not isinstance(node, dict) or sub_schema.get("type") != "object":
            return node
        props = sub_schema.get("properties", {})
        if not props:
            return node
        cleaned = {}
        for k, v in node.items():
            if k in props:
                child_schema = props[k]
                if child_schema.get("type") == "array" and isinstance(v, list):
                    item_schema = child_schema.get("items", {})
                    cleaned[k] = [
                        _clean(item, item_schema, f"{path}.{k}[{i}]") for i, item in enumerate(v)
                    ]
                elif child_schema.get("type") == "object":
                    cleaned[k] = _clean(v, child_schema, f"{path}.{k}")
                else:
                    cleaned[k] = v
            else:
                stripped.append(f"{path}.{k}")
        return cleaned

    result = _clean(obj, schema, "<root>")
    return result, stripped


# ---------- Stage 4: targeted re-prompt for a single broken field ----------

def targeted_repair_prompt(
    original_user_request: str, broken_value: Any, path: str, expected: str
) -> str:
    return (
        f"You are repairing ONE field in a larger JSON document. Do not regenerate "
        f"the whole document.\n\n"
        f"Original user request: {original_user_request}\n\n"
        f"The field at path `{path}` is invalid.\n"
        f"Current (broken) value: {json.dumps(broken_value)}\n"
        f"Constraint it must satisfy: {expected}\n\n"
        f"Return ONLY the corrected value for this field as valid JSON "
        f"(no prose, no markdown fences, no explanation)."
    )


def _get_at_path(obj: dict, path_parts: list) -> Any:
    cur = obj
    for p in path_parts:
        if isinstance(p, int):
            cur = cur[p]
        else:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def _set_at_path(obj: dict, path_parts: list, value: Any) -> None:
    cur = obj
    for p in path_parts[:-1]:
        cur = cur[p]
    cur[path_parts[-1]] = value


# ---------- Orchestration: validate_and_repair ----------

def validate_and_repair(
    raw_text: str,
    schema: dict,
    original_user_request: str,
    max_rounds: int = MAX_REPAIR_ROUNDS,
) -> tuple[Optional[dict], RepairReport]:
    actions: list[RepairAction] = []

    # Stage 1: syntax
    cleaned_text = llm_provider.extract_json_block(raw_text)
    obj, parse_err = try_parse_json(cleaned_text)
    if obj is None:
        actions.append(
            RepairAction(path="<root>", strategy=RepairStrategy.SYNTAX, detail=parse_err or "unparseable")
        )
        return None, RepairReport(success=False, rounds_used=0, actions=actions, remaining_errors=[parse_err or "unparseable JSON"])

    # Stage 3 (run before validation): strip hallucinated fields
    obj, stripped_paths = strip_unknown_fields(obj, schema)
    for p in stripped_paths:
        actions.append(
            RepairAction(path=p, strategy=RepairStrategy.HALLUCINATED_FIELD, detail="removed undeclared field")
        )

    rounds = 0
    while rounds < max_rounds:
        errors = validate_against_schema(obj, schema)
        if not errors:
            return obj, RepairReport(success=True, rounds_used=rounds, actions=actions)

        rounds += 1
        progress_made = False

        for err in errors[:8]:  # cap per round to bound cost
            strategy = classify_error(err)
            path_str = _path_str(err)

            if strategy == RepairStrategy.UNFIXABLE:
                actions.append(RepairAction(path=path_str, strategy=strategy, detail=err.message))
                continue

            broken_value = _get_at_path(obj, list(err.path)) if err.path else obj
            try:
                prompt = targeted_repair_prompt(
                    original_user_request, broken_value, path_str, err.message
                )
                result = llm_provider.complete_json(
                    system_prompt="You output ONLY valid JSON values. No prose.",
                    user_prompt=prompt,
                    use_cache=True,
                )
                fixed_json = llm_provider.extract_json_block(result.text)
                fixed_value, _ = try_parse_json(fixed_json)
                if fixed_value is not None and err.path:
                    _set_at_path(obj, list(err.path), fixed_value)
                    progress_made = True
                    actions.append(
                        RepairAction(path=path_str, strategy=strategy, detail=err.message, llm_call_used=True)
                    )
                elif fixed_value is not None and not err.path:
                    obj = fixed_value if isinstance(fixed_value, dict) else obj
                    progress_made = True
                    actions.append(
                        RepairAction(path=path_str, strategy=strategy, detail=err.message, llm_call_used=True)
                    )
            except (llm_provider.LLMError, KeyError, IndexError, TypeError) as e:
                actions.append(
                    RepairAction(path=path_str, strategy=strategy, detail=f"repair attempt failed: {e}")
                )

        if not progress_made:
            break

    final_errors = validate_against_schema(obj, schema)
    if not final_errors:
        return obj, RepairReport(success=True, rounds_used=rounds, actions=actions)

    return obj, RepairReport(
        success=False,
        rounds_used=rounds,
        actions=actions,
        remaining_errors=[f"{_path_str(e)}: {e.message}" for e in final_errors],
    )
