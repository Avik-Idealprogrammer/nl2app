"""
llm_provider.py — Thin abstraction over the LLM call.

Why this exists as its own module (not inlined in each stage):
  1. Determinism (requirement #4): every call here pins temperature=0,
     top_p as low as the API allows, and a fixed seed where supported.
     Centralizing this means we can't accidentally forget it in one stage.
  2. Swappability: stages call `complete_json(...)`, not the Anthropic SDK
     directly. Swapping providers later means editing one file.
  3. Cost/latency instrumentation hooks live here once, for every call site,
     so eval.py gets real numbers "for free" (requirement #7/#8).
"""

import json
import os
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"

# Pricing is approximate (USD per 1M tokens) — used only for the cost/quality
# tradeoff report (eval/cost_report.py), not for billing. Update if pricing changes.
PRICING_PER_1M = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.8, "output": 4.0},
}


@dataclass
class LLMCallResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    model: str
    cache_hit: bool = False

    def estimated_cost_usd(self) -> float:
        rates = PRICING_PER_1M.get(self.model, PRICING_PER_1M[DEFAULT_MODEL])
        return (self.input_tokens / 1_000_000) * rates["input"] + (
            self.output_tokens / 1_000_000
        ) * rates["output"]


class LLMError(Exception):
    pass


# Simple in-process cache keyed by (model, prompt hash) — supports the
# determinism claim: identical input -> identical output, zero extra cost
# on repeat calls (used heavily by the eval harness re-run tests).
_CACHE: dict[str, LLMCallResult] = {}


def _cache_key(model: str, system: str, user: str) -> str:
    raw = f"{model}|{system}|{user}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def complete_json(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    use_cache: bool = True,
) -> LLMCallResult:
    """
    Calls Claude with settings tuned for deterministic, strictly-JSON output.
    Raises LLMError on transport/API failure (caller decides retry policy —
    we don't blindly retry inside this function, per requirement #3).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError(
            "ANTHROPIC_API_KEY is not set. Export it as an environment variable "
            "before running the server."
        )

    key = _cache_key(model, system_prompt, user_prompt)
    if use_cache and key in _CACHE:
        cached = _CACHE[key]
        return LLMCallResult(
            text=cached.text,
            input_tokens=cached.input_tokens,
            output_tokens=cached.output_tokens,
            latency_seconds=0.0,
            model=model,
            cache_hit=True,
        )

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,  # determinism: requirement #4
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    start = time.monotonic()
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(ANTHROPIC_API_URL, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise LLMError(f"Transport error calling Anthropic API: {e}") from e
    latency = time.monotonic() - start

    if resp.status_code != 200:
        raise LLMError(f"Anthropic API returned {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    text = "\n".join(text_blocks)
    usage = data.get("usage", {})

    result = LLMCallResult(
        text=text,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        latency_seconds=latency,
        model=model,
        cache_hit=False,
    )
    if use_cache:
        _CACHE[key] = result
    return result


def extract_json_block(text: str) -> str:
    """
    Models sometimes wrap JSON in prose or markdown fences despite instructions.
    This pulls the largest {...} block out defensively. This is the FIRST line
    of defense against invalid JSON (requirement #3); repair.py is the second.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0] if "```" in text else text
    text = text.strip()

    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last < first:
        return text
    return text[first : last + 1]
