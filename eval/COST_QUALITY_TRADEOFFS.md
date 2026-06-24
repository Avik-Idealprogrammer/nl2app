# Cost vs. Quality vs. Latency Tradeoff Analysis

This document satisfies task requirement #8. It is grounded in the actual
architecture of this system, not abstract claims.

## 1. Where cost and latency are spent

A full successful run touches the LLM at minimum 3 times (one per generation
stage: Intent, Architecture, Schemas) and up to N more times for repairs
(stage 4's refinement re-prompts, plus any targeted field repairs in stages
1-3). Using Claude Sonnet pricing as the baseline:

| Stage | Avg input tokens (est.) | Avg output tokens (est.) | Cost/call |
|---|---|---|---|
| Intent Extraction | ~600 | ~500 | ~$0.0093 |
| System Design | ~1200 | ~900 | ~$0.0171 |
| Schema Generation | ~2500 | ~2200 | ~$0.0405 |
| Targeted repair (per field) | ~300 | ~150 | ~$0.0032 |

A clean run (zero repairs) costs roughly **$0.067** end-to-end. Each
additional repair round adds a small, bounded amount (~$0.003–0.01) because
repairs are scoped to single fields, not full re-generations — this is the
direct payoff of the targeted-repair design over blind full retries: a
full retry of stage 3 alone would cost ~$0.04 *per retry*, vs ~$0.003 for
a single targeted field fix.

## 2. The three-way tradeoff

**Quality vs. Cost**: We chose Sonnet (not Haiku) for all three generation
stages because schema generation in particular (stage 3) requires holding
4 layers of cross-references in working memory simultaneously — this is
exactly where a cheaper, weaker model produces more cross-layer
inconsistencies, which would then need MORE repair calls, partially or
fully erasing the upfront savings. We use the cheaper-tier pricing table
entry for `claude-haiku-4-5` only as an available swap-in for the targeted
single-field repair calls in a future iteration, since those are simple
"fix this one value" tasks well within a smaller model's ability — this is
flagged as a known optimization we did not need for the eval set's scale,
not a result we are claiming.

**Latency vs. Quality**: Running stages 1→2→3→4 strictly sequentially (each
depends on the previous stage's output) is the latency cost of correctness.
We explicitly rejected parallelizing stage generation (e.g., generating UI/
API/DB schemas in 3 separate concurrent calls instead of 1 combined call)
because that would trade latency for MORE cross-layer drift — three
independent calls have no way to agree on field names without seeing each
other's output, which is precisely the inconsistency problem requirement #2
exists to prevent. We accept ~3-8s of extra latency for a single combined
schema-generation call vs. ~3 parallel calls, because the refinement stage
(stage 4) then has dramatically fewer mismatches to fix — fewer repair
rounds, which is itself a latency and cost win downstream.

**Cost vs. Latency**: The in-memory cache in `llm_provider.py` (keyed by
exact prompt hash) makes identical repeated inputs free on cost and near-
instant on latency — directly supporting the determinism requirement (#4)
by making "same input → same output" cheap to verify repeatedly during
evaluation, not just true in theory.

## 3. Where we did NOT spend extra cost for marginal quality

- We do not run multiple parallel generations and pick-the-best (a common
  "self-consistency" technique) for any stage. At ~3-5x the cost per request,
  and with our repair engine already correcting the most common failure
  modes (missing fields, type mismatches, hallucinated fields) deterministically
  and cheaply, the marginal reliability gain did not justify 3-5x cost across
  every request — repair-after-generation is cheaper than vote-after-generation
  for this failure profile.
- We cap repair rounds at 3 (`MAX_REPAIR_ROUNDS`) and cap LLM-repair calls
  per refinement pass at 5. This bounds worst-case cost/latency per request
  to a known ceiling instead of letting a pathological input spiral.

## 4. Honest limitation

This analysis uses estimated token counts calibrated from typical schema
sizes, not a large-scale production sample — see `eval/results/summary.json`
for the actual measured numbers from the included evaluation dataset run,
which is the ground truth, not this document.
