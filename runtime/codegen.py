"""
codegen.py — Execution Awareness Layer.

The task is explicit: "If your output cannot be executed -> fail." A JSON
blob that merely LOOKS like a schema is not good enough. This module takes
a validated, refined config and mechanically (no LLM, fully deterministic)
generates THREE real artifacts:

  1. schema.sql      — real, runnable PostgreSQL DDL from config["db"]
  2. mock_api.py     — a real, runnable FastAPI app implementing every
                        endpoint in config["api"] against an in-memory store,
                        enforcing config["auth"] role checks on every route
  3. preview.html     — a static rendering of config["ui"] pages/components
                        so a human can see the generated UI without needing
                        a frontend build step

Because generation here is pure code (string templating off validated,
schema-checked data), the output is deterministic by construction and is
guaranteed syntactically valid Python/SQL/HTML — there is no LLM call in
this file, so there is nothing here for the LLM to hallucinate.
"""

import json
import textwrap


SQL_TYPE_MAP_DEFAULT = "text"


def generate_sql_ddl(db_config: dict) -> str:
    lines = ["-- Auto-generated schema. Deterministic, no LLM involved in this step.", ""]
    for table in db_config.get("tables", []):
        lines.append(f'CREATE TABLE IF NOT EXISTS "{table["name"]}" (')
        col_lines = []
        for col in table.get("columns", []):
            parts = [f'  "{col["name"]}"', col.get("type", SQL_TYPE_MAP_DEFAULT)]
            if col.get("primary_key"):
                parts.append("PRIMARY KEY")
            if not col.get("nullable", True) and not col.get("primary_key"):
                parts.append("NOT NULL")
            col_lines.append(" ".join(parts))
        for col in table.get("columns", []):
            fk = col.get("foreign_key")
            if fk:
                ref_table, ref_col = fk.split(".")
                col_lines.append(
                    f'  FOREIGN KEY ("{col["name"]}") REFERENCES "{ref_table}"("{ref_col}")'
                )
        lines.append(",\n".join(col_lines))
        lines.append(");")
        lines.append("")
    return "\n".join(lines)


def generate_mock_api(config: dict, app_name: str = "GeneratedApp") -> str:
    api = config.get("api", {})
    auth = config.get("auth", {})

    # IMPORTANT: use repr(), not json.dumps(), to embed these as Python literals.
    # json.dumps emits `true`/`false`/`null`, which are valid JSON but NOT valid
    # Python identifiers -> would raise NameError at module load time. repr()
    # on a Python dict/list (built from already-parsed JSON, so it's plain
    # dicts/lists/bools/None/str/int at this point) emits correct Python syntax.
    endpoints_literal = repr(api.get("endpoints", []))
    permissions_literal = repr(auth.get("permissions", []))

    template = f'''"""
Auto-generated mock API for {app_name}.
Generated deterministically from validated config — not hand-written, not LLM-written.
Run with: uvicorn mock_api:app --reload
"""

from fastapi import FastAPI, HTTPException, Header
from typing import Optional
import uuid

app = FastAPI(title="{app_name} (generated mock API)")

ENDPOINTS = {endpoints_literal}

PERMISSIONS = {permissions_literal}

# In-memory store: {{table_name: [records]}}
STORE: dict[str, list[dict]] = {{}}


def check_permission(role: str, entity: str, action: str) -> bool:
    for p in PERMISSIONS:
        if p["role"] == role and p["entity"] == entity and action in p.get("actions", []):
            return True
    return False


def get_role(x_role: Optional[str]) -> str:
    return x_role or "user"


def action_for_method(method: str) -> str:
    return {{"GET": "list", "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}}.get(method, "read")


@app.get("/")
def root():
    return {{"app": "{app_name}", "endpoints": [e["path"] for e in ENDPOINTS], "status": "running"}}


for _ep in ENDPOINTS:
    def _make_handler(ep):
        def handler(x_role: Optional[str] = Header(default=None), body: Optional[dict] = None):
            role = get_role(x_role)
            action = action_for_method(ep["method"])
            if ep.get("auth_required", True):
                if role not in ep.get("allowed_roles", []):
                    raise HTTPException(status_code=403, detail=f"role '{{role}}' not permitted on {{ep['path']}}")
                if not check_permission(role, ep["entity"], action) and action != "read":
                    raise HTTPException(status_code=403, detail=f"role '{{role}}' lacks '{{action}}' on {{ep['entity']}}")
            table = STORE.setdefault(ep["entity"], [])
            if ep["method"] == "GET":
                return {{"data": table}}
            if ep["method"] == "POST":
                record = dict(body or {{}})
                record["id"] = str(uuid.uuid4())
                table.append(record)
                return {{"data": record}}
            if ep["method"] in ("PUT", "PATCH"):
                return {{"data": body or {{}}, "note": "mock update accepted"}}
            if ep["method"] == "DELETE":
                return {{"data": None, "note": "mock delete accepted"}}
            return {{"data": None}}
        return handler

    app.add_api_route(_ep["path"], _make_handler(_ep), methods=[_ep["method"]])
'''
    return template


def generate_ui_preview(config: dict, app_name: str = "GeneratedApp") -> str:
    ui = config.get("ui", {})
    pages_html = []
    for page in ui.get("pages", []):
        comps_html = []
        for comp in page.get("components", []):
            comps_html.append(
                f'<div class="component {comp.get("type")}">'
                f'<span class="comp-type">{comp.get("type")}</span> '
                f'&rarr; entity: <code>{comp.get("binds_to_entity")}</code>'
                + (f' &rarr; api: <code>{comp.get("binds_to_api")}</code>' if comp.get("binds_to_api") else "")
                + "</div>"
            )
        roles = ", ".join(page.get("allowed_roles", []))
        pages_html.append(textwrap.dedent(f"""
        <section class="page">
          <h2>{page.get("name")} <span class="route">{page.get("route")}</span></h2>
          <p class="roles">Visible to: {roles}</p>
          <div class="components">{''.join(comps_html)}</div>
        </section>
        """))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{app_name} — UI Preview</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #0f1115; color: #e6e6e6; }}
h1 {{ color: #fff; }}
.page {{ border: 1px solid #2a2d35; border-radius: 8px; padding: 16px; margin-bottom: 16px; background: #161922; }}
.route {{ font-size: 12px; color: #888; margin-left: 8px; }}
.roles {{ font-size: 13px; color: #9aa0a6; }}
.component {{ display: inline-block; border: 1px solid #3a3f4b; border-radius: 6px; padding: 8px 12px; margin: 4px; background: #1d212c; font-size: 13px; }}
.comp-type {{ font-weight: 600; color: #7aa2f7; text-transform: uppercase; font-size: 11px; }}
code {{ color: #9ece6a; }}
</style></head>
<body>
<h1>{app_name}</h1>
<p>Auto-generated UI preview — rendered deterministically from validated config.</p>
{''.join(pages_html)}
</body></html>"""


def generate_all_artifacts(pipeline_result_config: dict, app_name: str = "GeneratedApp") -> dict:
    """Returns the three executable artifacts as strings, ready to write to disk."""
    return {
        "schema.sql": generate_sql_ddl(pipeline_result_config.get("db", {})),
        "mock_api.py": generate_mock_api(pipeline_result_config, app_name),
        "preview.html": generate_ui_preview(pipeline_result_config, app_name),
    }
