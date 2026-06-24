"""
stage3_schemas.py — Schema Generation

Projects the Architecture (stage 2) into the four concrete artifacts the
task spec requires: UI schema, API schema, DB schema, and Auth rules, plus
a business_logic block for things like premium gating.

Key design decision: we generate ALL FOUR in a SINGLE call here, not four
separate calls. Why: cross-layer consistency (API fields matching DB columns,
UI components binding to real API endpoints) is dramatically easier to get
right when one generation pass can see all four layers simultaneously and
keep names in sync, vs. stitching together 4 independent generations and
hoping the names line up. The Refinement stage (stage4) then double-checks
this consistency mechanically and repairs any drift — defense in depth
rather than relying on the LLM alone.
"""

import json

from . import llm_provider
from .repair import validate_and_repair
from .schemas import FULL_CONFIG_SCHEMA

SYSTEM_PROMPT = """You are the Schema Generation stage of a natural-language-to-application compiler.

Input: an application architecture (entities with fields, roles, permissions, flows).
Output: STRICT JSON (no prose, no markdown fences) with four sections: ui, api, db, auth, business_logic.

Shape:
{
  "ui": {
    "pages": [
      {
        "name": "Dashboard",
        "route": "/dashboard",
        "allowed_roles": ["admin", "user"],
        "components": [
          {"type": "table", "binds_to_entity": "Contact", "binds_to_api": "/api/contacts"}
        ]
      }
    ]
  },
  "api": {
    "endpoints": [
      {
        "path": "/api/contacts",
        "method": "GET",
        "entity": "Contact",
        "auth_required": true,
        "allowed_roles": ["admin", "user"],
        "request_fields": [],
        "response_fields": ["id", "name", "owner", "status"]
      }
    ]
  },
  "db": {
    "tables": [
      {
        "name": "contacts",
        "columns": [
          {"name": "id", "type": "uuid", "primary_key": true, "nullable": false},
          {"name": "name", "type": "varchar", "nullable": false},
          {"name": "owner_id", "type": "uuid", "foreign_key": "users.id", "nullable": false}
        ]
      }
    ]
  },
  "auth": {
    "roles": ["admin", "user"],
    "permissions": [
      {"role": "admin", "entity": "Contact", "actions": ["create", "read", "update", "delete", "list"]}
    ]
  },
  "business_logic": {
    "rules": [
      {"name": "premium_gating", "condition": "user.plan != 'premium'", "effect": "deny access to analytics endpoints"}
    ]
  }
}

STRICT cross-layer consistency rules (these will be mechanically checked):
1. Every entity in the architecture MUST have a corresponding db table (snake_case, pluralized) with a column for every field (snake_case).
2. Every db table MUST have at least one API endpoint (a GET list endpoint at minimum) whose `entity` matches the architecture entity name exactly.
3. Every api endpoint's response_fields MUST be a subset of that entity's field names (as defined in the architecture), expressed in the same casing as the architecture (not snake_case) for response_fields.
4. Every api endpoint's allowed_roles MUST be consistent with the permissions in stage 2 (a role can only call an endpoint if it has the matching action permission for that entity: GET/list->read or list, POST->create, PUT/PATCH->update, DELETE->delete).
5. Every UI page's allowed_roles MUST be a subset of the roles that have at least "read" or "list" access to every entity that page's components bind to.
6. Every UI component with binds_to_api MUST reference a path that exists in api.endpoints.
7. auth.roles and auth.permissions MUST exactly mirror the architecture's roles and permissions (copy them over, do not invent new ones).
8. If the architecture includes a Subscription/Payment entity, business_logic.rules MUST include a premium-gating rule referencing it.

Output ONLY the JSON object."""


def generate_schemas(architecture: dict, original_request: str) -> tuple[dict | None, dict]:
    try:
        result = llm_provider.complete_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=f"Architecture object:\n{json.dumps(architecture, indent=2)}",
            max_tokens=8192,
        )
    except llm_provider.LLMError as e:
        return None, {
            "stage": "schema_generation",
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

    obj, report = validate_and_repair(result.text, FULL_CONFIG_SCHEMA, original_request)

    meta = {
        "stage": "schema_generation",
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
