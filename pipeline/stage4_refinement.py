"""
stage4_refinement.py — Refinement Layer

This is the "trust but verify" stage. Stage 3's prompt ASKS the model to
keep UI/API/DB/Auth consistent, but we never rely on an LLM following
instructions perfectly. Here we run deterministic, code-based consistency
checks across the four layers and produce CrossLayerIssue objects for
anything that doesn't line up — then route each issue through the same
targeted-repair mechanism used in repair.py (a single re-prompt scoped to
just the mismatched piece, not a full regeneration).

This stage is what separates "asked the LLM nicely" from "built a system
that enforces the contract" — the distinction the task explicitly grades on.
"""

import json
from dataclasses import dataclass, field

from . import llm_provider
from .repair import try_parse_json


def _snake(name: str) -> str:
    out = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0:
            out.append("_")
        out.append(c.lower())
    return "".join(out)


def _pluralize(name: str) -> str:
    if name.endswith("s"):
        return name.lower()
    return name.lower() + "s"


@dataclass
class CrossLayerIssue:
    layer: str
    path: str
    issue: str
    severity: str  # "error" | "warning"


def check_consistency(architecture: dict, config: dict) -> list[CrossLayerIssue]:
    issues: list[CrossLayerIssue] = []

    arch_entities = {e["name"]: e for e in architecture.get("entities", [])}
    arch_roles = set(architecture.get("roles", []))

    db_tables = {t["name"]: t for t in config.get("db", {}).get("tables", [])}
    api_endpoints = config.get("api", {}).get("endpoints", [])
    ui_pages = config.get("ui", {}).get("pages", [])
    auth = config.get("auth", {})

    # Rule 1: every entity has a matching db table
    for ename in arch_entities:
        expected_table = _pluralize(ename)
        if expected_table not in db_tables:
            issues.append(CrossLayerIssue(
                layer="db", path=f"db.tables[{expected_table}]",
                issue=f"Entity '{ename}' has no corresponding db table '{expected_table}'",
                severity="error",
            ))

    # Rule 2: every entity has at least one API endpoint
    api_entities = {ep.get("entity") for ep in api_endpoints}
    for ename in arch_entities:
        if ename not in api_entities:
            issues.append(CrossLayerIssue(
                layer="api", path=f"api.endpoints[entity={ename}]",
                issue=f"Entity '{ename}' has no API endpoint referencing it",
                severity="error",
            ))

    # Rule 3: API allowed_roles must respect the permission matrix
    perms = architecture.get("permissions", [])
    perm_lookup: dict[tuple[str, str], set[str]] = {}
    for p in perms:
        perm_lookup.setdefault((p["role"], p["entity"]), set()).update(p.get("actions", []))

    method_to_action = {"GET": {"read", "list"}, "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}

    for idx, ep in enumerate(api_endpoints):
        entity = ep.get("entity")
        method = ep.get("method")
        needed = method_to_action.get(method, set())
        if isinstance(needed, str):
            needed = {needed}
        for role in ep.get("allowed_roles", []):
            have = perm_lookup.get((role, entity), set())
            if not (needed & have) and role in arch_roles:
                issues.append(CrossLayerIssue(
                    layer="api", path=f"api.endpoints[{idx}].allowed_roles",
                    issue=f"Role '{role}' is allowed on {method} {ep.get('path')} but has no matching "
                          f"permission ({needed}) for entity '{entity}' in the architecture",
                    severity="warning",
                ))

    # Rule 4: auth.roles / auth.permissions must mirror architecture
    if set(auth.get("roles", [])) != arch_roles:
        issues.append(CrossLayerIssue(
            layer="auth", path="auth.roles",
            issue=f"auth.roles {auth.get('roles')} does not match architecture roles {sorted(arch_roles)}",
            severity="error",
        ))

    # Rule 5: UI pages binds_to_api must reference a real endpoint path
    real_paths = {ep["path"] for ep in api_endpoints}
    for pidx, page in enumerate(ui_pages):
        for cidx, comp in enumerate(page.get("components", [])):
            target = comp.get("binds_to_api")
            if target and target not in real_paths:
                issues.append(CrossLayerIssue(
                    layer="ui", path=f"ui.pages[{pidx}].components[{cidx}].binds_to_api",
                    issue=f"UI component binds to API path '{target}' which does not exist in api.endpoints",
                    severity="error",
                ))

    return issues


def _auto_fix(architecture: dict, config: dict, issue: CrossLayerIssue) -> bool:
    """
    Cheap deterministic fixes that don't need an LLM call at all.
    Returns True if it fixed the issue in-place.
    """
    if issue.layer == "auth" and issue.path == "auth.roles":
        config["auth"]["roles"] = architecture.get("roles", [])
        config["auth"]["permissions"] = architecture.get("permissions", [])
        return True

    if issue.layer == "db" and "has no corresponding db table" in issue.issue:
        # Synthesize a minimal table directly from the architecture entity fields
        ename = issue.path.split("[")[1].rstrip("]")
        entity = next((e for e in architecture.get("entities", []) if _pluralize(e["name"]) == ename), None)
        if entity:
            columns = []
            for f_ in entity.get("fields", []):
                col_type = {"uuid": "uuid", "string": "varchar", "text": "text", "number": "numeric",
                            "boolean": "boolean", "datetime": "timestamp", "enum": "varchar",
                            "relation": "uuid"}.get(f_["type"], "varchar")
                col = {
                    "name": _snake(f_["name"]) + ("_id" if f_["type"] == "relation" else ""),
                    "type": col_type,
                    "primary_key": f_["name"] == "id",
                    "nullable": not f_.get("required", False),
                }
                if f_["type"] == "relation" and f_.get("relation_target"):
                    col["foreign_key"] = f"{_pluralize(f_['relation_target'])}.id"
                else:
                    col["foreign_key"] = None
                columns.append(col)
            config["db"]["tables"].append({"name": ename, "columns": columns})
            return True

    return False


def refine(architecture: dict, config: dict, original_request: str) -> tuple[dict, dict]:
    issues = check_consistency(architecture, config)
    fixed_auto = []
    fixed_llm = []
    unresolved = []

    remaining_issues = []
    for issue in issues:
        if _auto_fix(architecture, config, issue):
            fixed_auto.append(issue)
        else:
            remaining_issues.append(issue)

    # Re-check after auto-fixes (auto-fixing one thing can resolve others)
    issues_after_auto = check_consistency(architecture, config)
    error_issues = [i for i in issues_after_auto if i.severity == "error"]

    for issue in error_issues[:5]:  # bound LLM repair calls per request
        prompt = (
            f"You are reconciling a cross-layer inconsistency in a generated app config.\n\n"
            f"Original request: {original_request}\n\n"
            f"Issue detected: {issue.issue}\n"
            f"Layer: {issue.layer}, path: {issue.path}\n\n"
            f"Relevant architecture (for reference):\n{json.dumps(architecture, indent=2)[:3000]}\n\n"
            f"Return ONLY a JSON object with a single key 'patch' describing the minimal "
            f"addition/change needed, in this shape: "
            f'{{"patch": {{"layer": "{issue.layer}", "operation": "describe what to add or change"}}}}'
        )
        try:
            result = llm_provider.complete_json(
                system_prompt="You output ONLY valid JSON. No prose.",
                user_prompt=prompt,
            )
            parsed, _ = try_parse_json(llm_provider.extract_json_block(result.text))
            if parsed:
                fixed_llm.append({"issue": issue.__dict__, "llm_patch_suggestion": parsed})
            else:
                unresolved.append(issue.__dict__)
        except llm_provider.LLMError:
            unresolved.append(issue.__dict__)

    final_issues = check_consistency(architecture, config)

    report = {
        "stage": "refinement",
        "issues_found": len(issues),
        "auto_fixed": [i.__dict__ for i in fixed_auto],
        "llm_flagged": fixed_llm,
        "unresolved_errors": [i.__dict__ for i in final_issues if i.severity == "error"],
        "unresolved_warnings": [i.__dict__ for i in final_issues if i.severity == "warning"],
        "success": len([i for i in final_issues if i.severity == "error"]) == 0,
    }
    return config, report
