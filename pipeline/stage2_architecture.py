"""
stage2_architecture.py — System Design Layer

Converts validated Intent -> App Architecture: concrete entity field
definitions, role list, permission matrix, and key user flows.

This is deliberately separate from Schema Generation (stage 3). Here we
decide WHAT the data model and permission structure look like in the
abstract; stage 3 then mechanically projects that abstraction into three
concrete, technology-shaped artifacts (UI/API/DB). Splitting these means
a permission bug and a "the API doesn't match the DB" bug are caught and
fixed independently instead of being entangled in one giant repair.
"""

import json

from . import llm_provider
from .repair import validate_and_repair
from .schemas import ARCHITECTURE_SCHEMA

SYSTEM_PROMPT = """You are the System Design stage of a natural-language-to-application compiler.

Input: a structured intent object (entities, roles, features, monetization).
Output: a concrete application architecture as STRICT JSON (no prose, no markdown fences):

{
  "entities": [
    {
      "name": "Contact",
      "fields": [
        {"name": "id", "type": "uuid", "required": true},
        {"name": "name", "type": "string", "required": true},
        {"name": "owner", "type": "relation", "relation_target": "User", "required": true},
        {"name": "status", "type": "enum", "enum_values": ["lead", "customer"], "required": true}
      ]
    }
  ],
  "roles": ["admin", "user"],
  "permissions": [
    {"role": "admin", "entity": "Contact", "actions": ["create", "read", "update", "delete", "list"]},
    {"role": "user", "entity": "Contact", "actions": ["create", "read", "update", "list"]}
  ],
  "flows": [
    {"name": "Create contact", "steps": ["User opens dashboard", "Clicks New Contact", "Fills form", "Submits", "Contact appears in list"]}
  ]
}

Rules:
- field "type" must be one of: string, number, boolean, datetime, enum, relation, text, uuid
- Every entity MUST have an "id" field of type uuid as the first field.
- If monetization.has_payments is true in the intent, you MUST include a "Subscription" or "Payment" entity with at least: id, user (relation to User), plan (enum), status (enum), and a permission rule restricting premium features to a "premium" or paid role/plan.
- If roles include "admin", admin MUST have full CRUD on every entity. Non-admin roles must have a strictly equal-or-smaller permission set than admin for the same entity.
- Every role from the intent must appear in "roles" and have at least one permissions entry.
- Output ONLY the JSON object."""


def design_architecture(intent: dict) -> tuple[dict | None, dict]:
    user_request_summary = intent.get("summary", "")
    try:
        result = llm_provider.complete_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=f"Intent object:\n{json.dumps(intent, indent=2)}",
        )
    except llm_provider.LLMError as e:
        return None, {
            "stage": "system_design",
            "latency_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "cache_hit": False,
            "repair_rounds": 0,
            "repair_actions": [],
            "success": False,
            "remaining_errors": [str(e)],
        }

    obj, report = validate_and_repair(result.text, ARCHITECTURE_SCHEMA, user_request_summary)

    meta = {
        "stage": "system_design",
        "latency_seconds": result.latency_seconds,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "estimated_cost_usd": result.estimated_cost_usd(),
        "cache_hit": result.cache_hit,
        "repair_rounds": report.rounds_used,
        "repair_actions": [a.__dict__ for a in report.actions],
        "success": report.success,
        "remaining_errors": report.remaining_errors,
    }
    return obj, meta
