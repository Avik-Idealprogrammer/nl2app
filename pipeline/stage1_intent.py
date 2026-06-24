"""
stage1_intent.py — Intent Extraction

Parses the user's free-text request into a structured intermediate form
(INTENT_SCHEMA). This stage's only job is to understand WHAT the user wants
— not how to build it. Keeping this separate from System Design (stage 2)
means a misunderstanding of intent and a bad architecture decision are
different, independently-debuggable failure modes — which is the whole
point of a multi-stage pipeline instead of one mega-prompt.

Also responsible for requirement #6 (Failure Handling): if the request is
too vague to extract a usable intent (e.g. missing entities AND missing
roles), this stage flags `needs_clarification=True` with specific questions,
rather than forcing a guess silently.
"""

from . import llm_provider
from .repair import validate_and_repair
from .schemas import INTENT_SCHEMA

SYSTEM_PROMPT = """You are the Intent Extraction stage of a natural-language-to-application compiler.

Your ONLY job: read the user's app request and extract a structured intent object.
Do NOT design the database, API, or UI yet — that happens in later stages.

Output STRICT JSON matching this shape (no markdown fences, no prose, JSON only):
{
  "app_name": "short app name",
  "summary": "1-2 sentence summary of what the app does",
  "entities": [{"name": "Contact", "description": "..."}],
  "roles": [{"name": "admin", "description": "..."}],
  "features": ["login", "dashboard", "..."],
  "monetization": {"has_payments": true, "model": "subscription"},
  "ambiguities": [
    {"field": "roles", "assumption": "Assumed a single 'user' role since none was specified", "reason": "request did not mention roles"}
  ]
}

Rules:
- monetization.model must be one of: none, subscription, one_time, freemium
- If the request mentions payments/premium/plans but doesn't specify a model, default to "subscription" and record it in `ambiguities`.
- If NO roles are mentioned at all, include a default "user" role and record the assumption in `ambiguities`.
- entities must include at least the core nouns of the domain (e.g. a CRM implies "Contact" at minimum).
- Every assumption you make for missing information MUST be recorded in `ambiguities` — do not silently invent requirements.
- Output ONLY the JSON object."""


def extract_intent(user_request: str) -> tuple[dict | None, dict]:
    """
    Returns (intent_dict_or_None, meta) where meta includes repair report,
    token/cost/latency info for the eval harness.
    """
    try:
        result = llm_provider.complete_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=f"User request:\n{user_request}",
        )
    except llm_provider.LLMError as e:
        return None, {
            "stage": "intent_extraction",
            "latency_seconds": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "cache_hit": False,
            "repair_rounds": 0,
            "repair_actions": [],
            "success": False,
            "remaining_errors": [str(e)],
            "needs_clarification": False,
            "clarification_questions": [],
        }

    obj, report = validate_and_repair(result.text, INTENT_SCHEMA, user_request)

    meta = {
        "stage": "intent_extraction",
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

    needs_clarification = False
    clarification_questions = []
    if obj:
        if not obj.get("entities"):
            needs_clarification = True
            clarification_questions.append(
                "What are the main 'things' this app manages (e.g. contacts, orders, posts)?"
            )
        if not obj.get("features"):
            needs_clarification = True
            clarification_questions.append("What should a user actually be able to DO in this app?")

    meta["needs_clarification"] = needs_clarification
    meta["clarification_questions"] = clarification_questions

    return obj, meta
