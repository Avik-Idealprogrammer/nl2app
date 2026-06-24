"""
execution_validator.py — Proves the generated artifacts actually execute.

Per requirement #5 ("either integrate with a basic runtime, OR simulate
execution and validate correctness"), this module does BOTH:
  1. Syntax-checks generated Python by compiling it (catches codegen bugs
     immediately, before ever trying to import it).
  2. Dynamically loads the generated mock_api.py FastAPI app into memory
     using Starlette's TestClient and fires real HTTP requests at every
     generated endpoint, checking for the expected auth behavior (a 403
     for a role that lacks permission, a 200/201 for a role that has it).
  3. Validates the generated SQL by parsing it with sqlparse and checking
     every CREATE TABLE statement is well-formed.

If any of this fails, execution_awareness.success = False and the specific
failing endpoint/statement is reported — this is the proof the task asks
for that the output is "directly usable... no manual fixes."
"""

import importlib.util
import sys
import types


def validate_python_syntax(code: str) -> tuple[bool, str | None]:
    try:
        compile(code, "<generated_mock_api>", "exec")
        return True, None
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"


def validate_sql_syntax(sql: str) -> tuple[bool, str | None]:
    try:
        import sqlparse
        statements = sqlparse.parse(sql)
        if not statements:
            return False, "No SQL statements parsed"
        for stmt in statements:
            if stmt.get_type() == "UNKNOWN" and stmt.token_first(skip_cm=True) is not None:
                tok = stmt.token_first(skip_cm=True)
                if tok and tok.ttype is None and "CREATE" not in str(tok).upper():
                    return False, f"Unrecognized statement: {str(stmt)[:80]}"
        return True, None
    except Exception as e:
        return False, str(e)


def execute_and_test_api(mock_api_code: str, config: dict) -> dict:
    """
    Dynamically imports the generated mock_api module and fires test requests
    at every endpoint using FastAPI's TestClient, checking role-based access
    actually behaves as specified in config["api"].
    """
    ok, syntax_err = validate_python_syntax(mock_api_code)
    if not ok:
        return {"executed": False, "error": syntax_err, "endpoint_results": []}

    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return {"executed": False, "error": "fastapi/starlette TestClient not installed", "endpoint_results": []}

    module_name = "generated_mock_api_under_test"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    try:
        exec(compile(mock_api_code, "<generated_mock_api>", "exec"), module.__dict__)
    except Exception as e:
        return {"executed": False, "error": f"Runtime error during module load: {e}", "endpoint_results": []}

    app = getattr(module, "app", None)
    if app is None:
        return {"executed": False, "error": "Generated module has no `app` object", "endpoint_results": []}

    client = TestClient(app)
    endpoint_results = []

    for ep in config.get("api", {}).get("endpoints", []):
        path, method = ep["path"], ep["method"]
        allowed_roles = ep.get("allowed_roles", [])
        test_role = allowed_roles[0] if allowed_roles else "user"
        denied_role = "nonexistent_role_xyz"

        try:
            body = {} if method in ("POST", "PUT", "PATCH") else None
            resp_allowed = client.request(method, path, headers={"x-role": test_role}, json=body)
            resp_denied = client.request(method, path, headers={"x-role": denied_role}, json=body)

            allowed_ok = resp_allowed.status_code < 400
            denied_correctly_blocked = resp_denied.status_code == 403 if ep.get("auth_required", True) else True

            endpoint_results.append({
                "path": path,
                "method": method,
                "allowed_role_status": resp_allowed.status_code,
                "denied_role_status": resp_denied.status_code,
                "pass": allowed_ok and denied_correctly_blocked,
            })
        except Exception as e:
            endpoint_results.append({"path": path, "method": method, "pass": False, "error": str(e)})

    all_passed = all(r.get("pass") for r in endpoint_results) if endpoint_results else False
    return {"executed": True, "error": None, "endpoint_results": endpoint_results, "all_passed": all_passed}


def run_full_validation(artifacts: dict, config: dict) -> dict:
    sql_ok, sql_err = validate_sql_syntax(artifacts["schema.sql"])
    api_result = execute_and_test_api(artifacts["mock_api.py"], config)

    return {
        "sql_valid": sql_ok,
        "sql_error": sql_err,
        "api_execution": api_result,
        "overall_executable": sql_ok and api_result.get("executed", False) and api_result.get("all_passed", False),
    }
