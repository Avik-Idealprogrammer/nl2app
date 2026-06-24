# nl2app — a compiler for software generation

Natural language → structured, validated config → executable application
(via a runtime). Built for the AI Engineer demo task. This is a system,
not a prompt — see `pipeline/` for why.

## Architecture (the multi-stage pipeline)

```
User prompt
   │
   ▼
┌─────────────────────┐
│ Stage 1: Intent      │  pipeline/stage1_intent.py
│ Extraction           │  NL -> structured intent (entities, roles, features,
│                       │  monetization). Flags vague requests for clarification
│                       │  instead of guessing silently.
└─────────┬─────────────┘
          ▼
┌─────────────────────┐
│ Stage 2: System      │  pipeline/stage2_architecture.py
│ Design               │  Intent -> concrete architecture: entity fields,
│                       │  roles, permission matrix, user flows.
└─────────┬─────────────┘
          ▼
┌─────────────────────┐
│ Stage 3: Schema      │  pipeline/stage3_schemas.py
│ Generation           │  Architecture -> UI schema + API schema + DB schema
│                       │  + Auth rules + business logic, generated together
│                       │  in one pass so names stay in sync across layers.
└─────────┬─────────────┘
          ▼
┌─────────────────────┐
│ Stage 4: Refinement  │  pipeline/stage4_refinement.py
│                       │  Mechanically (code, not LLM) checks cross-layer
│                       │  consistency: does every entity have a table? does
│                       │  every table have an endpoint? do UI bindings point
│                       │  at real endpoints? Auto-fixes what it can locally,
│                       │  escalates the rest to a scoped repair call.
└─────────┬─────────────┘
          ▼
   Validated config
          │
          ▼
┌─────────────────────┐
│ Execution layer      │  runtime/codegen.py + runtime/execution_validator.py
│ (codegen + proof)    │  Deterministically (NO LLM) generates real SQL DDL,
│                       │  a runnable FastAPI mock backend, and an HTML UI
│                       │  preview — then ACTUALLY RUNS the generated API
│                       │  in-process with FastAPI's TestClient and checks
│                       │  every endpoint behaves per the auth rules. This is
│                       │  what "execution awareness" means here: not a claim,
│                       │  a test that either passes or fails.
└─────────────────────┘
```

Every stage validates its own output against a strict JSON Schema
(`pipeline/schemas.py`) before passing it downstream. Cheap fast-fail beats
expensive garbage-in-garbage-out.

## The validation + repair engine (`pipeline/repair.py`)

This is the core deliverable. On any schema violation, the engine:

1. Classifies the error (syntax / missing field / type mismatch /
   hallucinated field / cross-layer mismatch) using the JSON Schema
   validator's structured error paths.
2. Applies the cheapest fix first: syntax cleanup and hallucinated-field
   stripping are pure local string/dict operations — zero LLM calls.
3. Only for fields that truly need re-generation does it call the LLM —
   and only with that single field's context, not the whole document.
4. Caps total repair rounds (`MAX_REPAIR_ROUNDS = 3`) so a pathological
   input can't loop forever; past the cap it returns a structured failure.

This is deliberately NOT "retry the whole prompt and hope." A full retry
of stage 3 costs ~13x more than a single targeted field repair (see
`eval/COST_QUALITY_TRADEOFFS.md`).

## Determinism

- `temperature=0` on every call (`pipeline/llm_provider.py`).
- An exact-prompt-hash cache, so identical inputs are genuinely free and
  instant on repeat — used by the eval harness to verify "same input, same
  output" rather than just asserting it.
- All codegen (`runtime/codegen.py`) is pure templating off already-validated
  data — no LLM involvement, so it's deterministic by construction.

## Failure handling

If Stage 1 detects no usable entities or features, the pipeline does not
guess — it returns `needs_clarification: true` with specific questions. If
a request mixes contradictory requirements (e.g. "no login" + "role-based
access on every page"), the architecture stage's permission rules will
surface as a refinement-stage inconsistency the system reports rather than
silently picking a side. Every assumption the system DOES make (e.g.
defaulting to a "user" role when none is specified) is recorded explicitly
in `ambiguities` in the intent object — visible in the UI, not hidden.

## Evaluation framework

`eval/dataset.py` — 10 real product prompts + 10 edge cases (vague /
conflicting / incomplete), as required.

`eval/run_eval.py` — runs the full dataset through the pipeline AND the
execution validator, and reports success rate, repair rounds, latency
(p50/p90/max), cost, and a failure-type breakdown, broken down by category.
Run it yourself:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m eval.run_eval
```

Results land in `eval/results/summary.json`, `raw_results.json`, and a
human-readable `report.md`. These are real measured numbers from an actual
run, not estimates — re-run it any time to regenerate them.

`eval/COST_QUALITY_TRADEOFFS.md` — the cost/latency/quality tradeoff
analysis (task requirement #8).

## Running locally

```bash
git clone <your-repo-url>
cd nl2app
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-your-key
uvicorn server:app --reload
# open http://localhost:8000
```

## Project layout

```
pipeline/
  schemas.py            strict JSON Schema contracts for every stage
  llm_provider.py        LLM call abstraction, determinism settings, cache
  repair.py               validation + repair engine (the core deliverable)
  stage1_intent.py        Intent Extraction
  stage2_architecture.py  System Design
  stage3_schemas.py       Schema Generation (UI/API/DB/Auth/business logic)
  stage4_refinement.py    cross-layer consistency checker + repairer
  orchestrator.py         wires the 4 stages together
runtime/
  codegen.py               generates real SQL/FastAPI/HTML from config (no LLM)
  execution_validator.py   actually runs the generated API and checks it works
eval/
  dataset.py               10 real prompts + 10 edge cases
  run_eval.py              eval harness, produces real metrics
  COST_QUALITY_TRADEOFFS.md
static/
  index.html               compiler-style UI: live pipeline view, repair log,
                            generated schemas, execution proof
server.py                  FastAPI entrypoint wiring it all together
render.yaml                 one-click Render deployment blueprint
```

## What this is intentionally NOT

- Not a single mega-prompt. Each stage is independently testable and has
  its own contract.
- Not "trust the LLM." Stage 4 and the repair engine exist because we
  assume the LLM will sometimes get cross-layer consistency wrong, and we
  catch it mechanically rather than hoping.
- Not unbounded retry. Every repair path has a cost ceiling.
