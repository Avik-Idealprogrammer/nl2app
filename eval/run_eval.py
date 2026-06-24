"""
run_eval.py — Evaluation harness (task spec section 7).

Runs the full dataset through the pipeline + codegen + execution validator,
and reports, BY CATEGORY:
  - success rate
  - average repair rounds (a proxy for "retries per request")
  - failure type breakdown (which stage failed, and why)
  - latency (p50/p90/max)
  - cost

This produces a machine-readable JSON report AND a human-readable markdown
summary, written to eval/results/. No hand-waving — every number here comes
from an actual pipeline run, not an estimate.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python -m eval.run_eval
"""

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.dataset import REAL_PROMPTS
from pipeline.orchestrator import run_pipeline
from runtime.codegen import generate_all_artifacts
from runtime.execution_validator import run_full_validation

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def run_single(entry: dict) -> dict:
    t0 = time.monotonic()
    result = run_pipeline(entry["prompt"])
    record = {
        "id": entry["id"],
        "category": entry["category"],
        "prompt": entry["prompt"],
        "success": result.success,
        "needs_clarification": result.needs_clarification,
        "failure_stage": result.failure_stage,
        "failure_reason": result.failure_reason,
        "total_latency_seconds": result.total_latency_seconds,
        "total_cost_usd": result.total_cost_usd,
        "total_repair_rounds": result.total_repair_rounds,
        "stage_success": {m["stage"]: m["success"] for m in result.stage_metas},
        "executable": None,
    }

    if result.success and result.config:
        try:
            artifacts = generate_all_artifacts(result.config, app_name="EvalApp")
            validation = run_full_validation(artifacts, result.config)
            record["executable"] = validation.get("overall_executable", False)
            record["execution_detail"] = {
                "sql_valid": validation.get("sql_valid"),
                "api_executed": validation.get("api_execution", {}).get("executed"),
                "api_all_passed": validation.get("api_execution", {}).get("all_passed"),
            }
        except Exception as e:
            record["executable"] = False
            record["execution_detail"] = {"error": str(e)}

    record["wall_clock_seconds"] = time.monotonic() - t0
    return record


def summarize(records: list[dict]) -> dict:
    by_category: dict[str, list[dict]] = {}
    for r in records:
        by_category.setdefault(r["category"], []).append(r)

    summary = {"overall": _agg(records), "by_category": {}}
    for cat, recs in by_category.items():
        summary["by_category"][cat] = _agg(recs)

    failure_types: dict[str, int] = {}
    for r in records:
        if not r["success"]:
            key = r.get("failure_stage") or ("clarification_requested" if r["needs_clarification"] else "unknown")
            failure_types[key] = failure_types.get(key, 0) + 1
    summary["failure_type_breakdown"] = failure_types

    return summary


def _agg(records: list[dict]) -> dict:
    n = len(records)
    if n == 0:
        return {}
    successes = sum(1 for r in records if r["success"])
    latencies = [r["total_latency_seconds"] for r in records]
    costs = [r["total_cost_usd"] for r in records]
    repairs = [r["total_repair_rounds"] for r in records]
    executable_count = sum(1 for r in records if r.get("executable") is True)
    attempted_exec = sum(1 for r in records if r.get("executable") is not None)

    return {
        "n": n,
        "success_rate": round(successes / n, 3),
        "avg_repair_rounds": round(statistics.mean(repairs), 2),
        "latency_p50": round(statistics.median(latencies), 2),
        "latency_p90": round(sorted(latencies)[int(0.9 * (n - 1))], 2) if n > 1 else round(latencies[0], 2),
        "latency_max": round(max(latencies), 2),
        "avg_cost_usd": round(statistics.mean(costs), 5),
        "total_cost_usd": round(sum(costs), 5),
        "executable_rate_of_successful": round(executable_count / attempted_exec, 3) if attempted_exec else None,
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    records = []
    for entry in REAL_PROMPTS:
        print(f"[{entry['id']}] ({entry['category']}) running...", flush=True)
        try:
            rec = run_single(entry)
        except Exception as e:
            rec = {
                "id": entry["id"], "category": entry["category"], "prompt": entry["prompt"],
                "success": False, "failure_stage": "uncaught_exception", "failure_reason": str(e),
                "total_latency_seconds": 0, "total_cost_usd": 0, "total_repair_rounds": 0,
                "needs_clarification": False,
            }
        records.append(rec)
        status = "OK" if rec["success"] else ("CLARIFY" if rec.get("needs_clarification") else "FAIL")
        print(f"  -> {status} | latency={rec.get('total_latency_seconds', 0):.2f}s | repairs={rec.get('total_repair_rounds', 0)}")

    summary = summarize(records)

    with open(os.path.join(RESULTS_DIR, "raw_results.json"), "w") as f:
        json.dump(records, f, indent=2)
    with open(os.path.join(RESULTS_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    write_markdown_report(records, summary)
    print("\nDone. Results written to eval/results/")
    print(json.dumps(summary["overall"], indent=2))


def write_markdown_report(records: list[dict], summary: dict):
    lines = ["# Evaluation Report\n"]
    lines.append("## Overall\n")
    lines.append(f"```json\n{json.dumps(summary['overall'], indent=2)}\n```\n")
    lines.append("## By category\n")
    for cat, agg in summary["by_category"].items():
        lines.append(f"### {cat}\n```json\n{json.dumps(agg, indent=2)}\n```\n")
    lines.append("## Failure type breakdown\n")
    lines.append(f"```json\n{json.dumps(summary['failure_type_breakdown'], indent=2)}\n```\n")
    lines.append("## Per-request detail\n")
    lines.append("| id | category | success | stage failed | repairs | latency(s) | cost($) | executable |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in records:
        lines.append(
            f"| {r['id']} | {r['category']} | {r['success']} | {r.get('failure_stage') or '-'} | "
            f"{r.get('total_repair_rounds', 0)} | {r.get('total_latency_seconds', 0):.2f} | "
            f"{r.get('total_cost_usd', 0):.5f} | {r.get('executable')} |"
        )
    with open(os.path.join(RESULTS_DIR, "report.md"), "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
