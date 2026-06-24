"""
test_repair_engine.py — Unit tests for the deterministic parts of the system
(repair engine, codegen, execution validator, refinement). These require NO
API key, since they test the non-LLM logic directly with hand-crafted inputs.

Run with: python -m pytest tests/ -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.repair import try_parse_json, validate_against_schema, strip_unknown_fields
from pipeline.schemas import INTENT_SCHEMA, FULL_CONFIG_SCHEMA
from pipeline.stage4_refinement import check_consistency, _auto_fix
from runtime.codegen import generate_all_artifacts, generate_sql_ddl
from runtime.execution_validator import run_full_validation, validate_python_syntax


def test_trailing_comma_repair():
    broken = '{"a": 1, "b": [1, 2,],}'
    obj, err = try_parse_json(broken)
    assert obj is not None, f"Should repair trailing commas, got error: {err}"
    assert obj == {"a": 1, "b": [1, 2]}


def test_smart_quote_repair():
    broken = '{\u201ca\u201d: 1}'
    obj, err = try_parse_json(broken)
    assert obj is not None, f"Should repair smart quotes, got error: {err}"


def test_missing_required_field_detected():
    incomplete = {"app_name": "Test", "summary": "x"}
    errors = validate_against_schema(incomplete, INTENT_SCHEMA)
    assert len(errors) > 0
    assert any(e.validator == "required" for e in errors)


def test_hallucinated_field_stripped():
    obj = {
        "app_name": "Test", "summary": "x", "entities": [],
        "roles": [{"name": "user", "description": "d"}],
        "features": [], "monetization": {"has_payments": False, "model": "none"},
        "fake_field_the_model_invented": "nonsense",
    }
    cleaned, stripped = strip_unknown_fields(obj, INTENT_SCHEMA)
    assert "fake_field_the_model_invented" not in cleaned
    assert len(stripped) == 1


def test_valid_intent_passes_clean():
    obj = {
        "app_name": "Test", "summary": "x", "entities": [{"name": "Thing", "description": "d"}],
        "roles": [{"name": "user", "description": "d"}],
        "features": ["login"], "monetization": {"has_payments": False, "model": "none"},
    }
    errors = validate_against_schema(obj, INTENT_SCHEMA)
    assert len(errors) == 0


def _sample_architecture():
    return {
        "entities": [
            {"name": "Contact", "fields": [{"name": "id", "type": "uuid", "required": True}]},
            {"name": "Deal", "fields": [{"name": "id", "type": "uuid", "required": True}]},
        ],
        "roles": ["admin", "user"],
        "permissions": [
            {"role": "admin", "entity": "Contact", "actions": ["create", "read", "update", "delete", "list"]},
            {"role": "admin", "entity": "Deal", "actions": ["create", "read", "update", "delete", "list"]},
        ],
        "flows": [],
    }


def _broken_config():
    return {
        "ui": {"pages": [{"name": "Dash", "route": "/d", "allowed_roles": ["admin"],
                "components": [{"type": "table", "binds_to_entity": "Contact", "binds_to_api": "/api/contacts"}]}]},
        "api": {"endpoints": [{"path": "/api/contacts", "method": "GET", "entity": "Contact",
                "auth_required": True, "allowed_roles": ["admin"], "request_fields": [], "response_fields": ["id"]}]},
        "db": {"tables": [{"name": "contacts", "columns": [{"name": "id", "type": "uuid", "primary_key": True, "nullable": False}]}]},
        "auth": {"roles": ["admin"], "permissions": []},
        "business_logic": {"rules": []},
    }


def test_cross_layer_consistency_catches_missing_table():
    arch = _sample_architecture()
    config = _broken_config()
    issues = check_consistency(arch, config)
    assert any("Deal" in i.issue and "db table" in i.issue for i in issues)


def test_cross_layer_consistency_catches_auth_drift():
    arch = _sample_architecture()
    config = _broken_config()
    issues = check_consistency(arch, config)
    assert any(i.layer == "auth" for i in issues)


def test_auto_fix_synthesizes_missing_table():
    arch = _sample_architecture()
    config = _broken_config()
    issues = check_consistency(arch, config)
    table_issue = next(i for i in issues if "db table" in i.issue)
    fixed = _auto_fix(arch, config, table_issue)
    assert fixed is True
    assert any(t["name"] == "deals" for t in config["db"]["tables"])


def test_auto_fix_repairs_auth_roles():
    arch = _sample_architecture()
    config = _broken_config()
    issues = check_consistency(arch, config)
    auth_issue = next(i for i in issues if i.layer == "auth")
    fixed = _auto_fix(arch, config, auth_issue)
    assert fixed is True
    assert set(config["auth"]["roles"]) == {"admin", "user"}


def _valid_config():
    return {
        "ui": {"pages": [{"name": "Dashboard", "route": "/dashboard", "allowed_roles": ["admin", "user"],
                "components": [{"type": "table", "binds_to_entity": "Contact", "binds_to_api": "/api/contacts"}]}]},
        "api": {"endpoints": [
            {"path": "/api/contacts", "method": "GET", "entity": "Contact", "auth_required": True,
             "allowed_roles": ["admin", "user"], "request_fields": [], "response_fields": ["id", "name"]},
            {"path": "/api/contacts", "method": "POST", "entity": "Contact", "auth_required": True,
             "allowed_roles": ["admin"], "request_fields": ["name"], "response_fields": ["id", "name"]},
        ]},
        "db": {"tables": [{"name": "contacts", "columns": [
            {"name": "id", "type": "uuid", "primary_key": True, "nullable": False},
            {"name": "name", "type": "varchar", "nullable": False},
        ]}]},
        "auth": {"roles": ["admin", "user"], "permissions": [
            {"role": "admin", "entity": "Contact", "actions": ["create", "read", "update", "delete", "list"]},
            {"role": "user", "entity": "Contact", "actions": ["read", "list"]},
        ]},
        "business_logic": {"rules": [{"name": "none", "condition": "n/a", "effect": "n/a"}]},
    }


def test_generated_sql_is_valid():
    config = _valid_config()
    sql = generate_sql_ddl(config["db"])
    assert "CREATE TABLE" in sql
    assert "contacts" in sql


def test_generated_mock_api_has_valid_python_syntax():
    config = _valid_config()
    artifacts = generate_all_artifacts(config, app_name="Test")
    ok, err = validate_python_syntax(artifacts["mock_api.py"])
    assert ok, f"Generated code has syntax error: {err}"


def test_generated_mock_api_actually_executes_and_enforces_roles():
    config = _valid_config()
    artifacts = generate_all_artifacts(config, app_name="Test")
    validation = run_full_validation(artifacts, config)
    assert validation["sql_valid"] is True
    assert validation["api_execution"]["executed"] is True
    assert validation["api_execution"]["all_passed"] is True
    assert validation["overall_executable"] is True

    for r in validation["api_execution"]["endpoint_results"]:
        assert r["denied_role_status"] == 403, "Denied role should get 403"
        assert r["allowed_role_status"] < 400, "Allowed role should succeed"


def test_full_config_schema_rejects_missing_section():
    incomplete = {"ui": {"pages": []}, "api": {"endpoints": []}}
    errors = validate_against_schema(incomplete, FULL_CONFIG_SCHEMA)
    assert len(errors) > 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
