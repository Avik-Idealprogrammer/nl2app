"""
schemas.py — Strict contracts for every stage of the pipeline.

These are not "nice to have" docstrings — they are the enforcement mechanism.
Every stage output is validated against these JSON Schemas before it is allowed
to flow to the next stage. If validation fails, the Validation+Repair engine
(repair.py) intervenes instead of the pipeline silently continuing with bad data.

Design choice: we use JSON Schema (via `jsonschema` lib) rather than ad-hoc
isinstance checks because:
  1. It gives us machine-checkable "required fields present" + "type safety"
     for free, satisfying requirement #2 (Strict Schema Enforcement).
  2. It produces structured error paths (e.g. "entities[2].fields[0].type")
     that the repair engine can target for partial re-generation instead of
     a blind full retry (requirement #3).
"""

INTENT_SCHEMA = {
    "type": "object",
    "required": ["app_name", "summary", "entities", "roles", "features", "monetization"],
    "properties": {
        "app_name": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
            "minItems": 1,
        },
        "features": {"type": "array", "items": {"type": "string"}},
        "monetization": {
            "type": "object",
            "required": ["has_payments", "model"],
            "properties": {
                "has_payments": {"type": "boolean"},
                "model": {"type": "string", "enum": ["none", "subscription", "one_time", "freemium"]},
            },
        },
        "ambiguities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "assumption", "reason"],
                "properties": {
                    "field": {"type": "string"},
                    "assumption": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

ARCHITECTURE_SCHEMA = {
    "type": "object",
    "required": ["entities", "roles", "permissions", "flows"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "fields"],
                "properties": {
                    "name": {"type": "string"},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "type"],
                            "properties": {
                                "name": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["string", "number", "boolean", "datetime", "enum", "relation", "text", "uuid"],
                                },
                                "required": {"type": "boolean"},
                                "enum_values": {"type": "array", "items": {"type": "string"}},
                                "relation_target": {"type": "string"},
                            },
                        },
                        "minItems": 1,
                    },
                },
            },
            "minItems": 1,
        },
        "roles": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "permissions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["role", "entity", "actions"],
                "properties": {
                    "role": {"type": "string"},
                    "entity": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["create", "read", "update", "delete", "list"]},
                    },
                },
            },
        },
        "flows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "steps"],
                "properties": {
                    "name": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

FULL_CONFIG_SCHEMA = {
    "type": "object",
    "required": ["ui", "api", "db", "auth", "business_logic"],
    "properties": {
        "ui": {
            "type": "object",
            "required": ["pages"],
            "properties": {
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "route", "components", "allowed_roles"],
                        "properties": {
                            "name": {"type": "string"},
                            "route": {"type": "string"},
                            "allowed_roles": {"type": "array", "items": {"type": "string"}},
                            "components": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["type", "binds_to_entity"],
                                    "properties": {
                                        "type": {
                                            "type": "string",
                                            "enum": ["table", "form", "card", "chart", "nav", "detail", "button"],
                                        },
                                        "binds_to_entity": {"type": ["string", "null"]},
                                        "binds_to_api": {"type": ["string", "null"]},
                                    },
                                },
                            },
                        },
                    },
                    "minItems": 1,
                }
            },
        },
        "api": {
            "type": "object",
            "required": ["endpoints"],
            "properties": {
                "endpoints": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["path", "method", "entity", "auth_required", "allowed_roles"],
                        "properties": {
                            "path": {"type": "string"},
                            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                            "entity": {"type": "string"},
                            "auth_required": {"type": "boolean"},
                            "allowed_roles": {"type": "array", "items": {"type": "string"}},
                            "request_fields": {"type": "array", "items": {"type": "string"}},
                            "response_fields": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "minItems": 1,
                }
            },
        },
        "db": {
            "type": "object",
            "required": ["tables"],
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "columns"],
                        "properties": {
                            "name": {"type": "string"},
                            "columns": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name", "type"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "type": {"type": "string"},
                                        "primary_key": {"type": "boolean"},
                                        "foreign_key": {"type": ["string", "null"]},
                                        "nullable": {"type": "boolean"},
                                    },
                                },
                            },
                        },
                    },
                    "minItems": 1,
                }
            },
        },
        "auth": {
            "type": "object",
            "required": ["roles", "permissions"],
            "properties": {
                "roles": {"type": "array", "items": {"type": "string"}},
                "permissions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["role", "entity", "actions"],
                        "properties": {
                            "role": {"type": "string"},
                            "entity": {"type": "string"},
                            "actions": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
        "business_logic": {
            "type": "object",
            "required": ["rules"],
            "properties": {
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "condition", "effect"],
                        "properties": {
                            "name": {"type": "string"},
                            "condition": {"type": "string"},
                            "effect": {"type": "string"},
                        },
                    },
                }
            },
        },
    },
}
