"""
server.py — Main entrypoint.

Exposes:
  POST /api/generate         -> runs the full 4-stage pipeline + codegen + execution validation
  GET  /api/health           -> liveness check
  GET  /                     -> serves the static chat-style UI (static/index.html)

This file is intentionally thin: it has no business logic of its own. It
only orchestrates calls into pipeline/orchestrator.py and runtime/codegen.py
and shapes the response — keeping the "pipeline separation" the task asks
for visible in the file layout itself, not just in prose.
"""

import dataclasses
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.orchestrator import run_pipeline
from runtime.codegen import generate_all_artifacts
from runtime.execution_validator import run_full_validation

app = FastAPI(title="NL-to-App Compiler")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class GenerateRequest(BaseModel):
    prompt: str


def _to_dict(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/generate")
def generate(req: GenerateRequest):
    try:
        result = run_pipeline(req.prompt)
    except Exception as e:
        return {
            "success": False,
            "failure_stage": "uncaught_exception",
            "failure_reason": f"{type(e).__name__}: {e}",
            "stage_metas": [],
        }

    response = _to_dict(result)

    if result.success and result.config:
        try:
            artifacts = generate_all_artifacts(result.config, app_name=result.intent.get("app_name", "GeneratedApp") if result.intent else "GeneratedApp")
            validation = run_full_validation(artifacts, result.config)
            response["artifacts"] = artifacts
            response["execution_validation"] = validation
        except Exception as e:
            response["artifacts"] = None
            response["execution_validation"] = {"overall_executable": False, "error": f"{type(e).__name__}: {e}"}

    return response


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
