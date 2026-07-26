"""
The Orchestration Engine / Model Router described in
docs/03-system-architecture.md. This now makes REAL calls to Anthropic's
API when an API key is configured — this is not a stub pretending to be
real, it's an actual model call.

Three honesty constraints, deliberate:

1. Citations and confidence scores are only ever populated when there's
   a real basis for them. A plain LLM call with no search tool attached
   cannot honestly cite sources or score its own confidence — fabricating
   either would be worse than returning nothing. Real citations/confidence
   require wiring up an actual search tool (Research Engine), which is a
   separate, not-yet-built piece — see README "Known limitations."

2. Failure is graceful, not fatal. If the API key is missing, invalid, or
   the request fails for any reason, this falls back to a clearly-labeled
   placeholder response rather than taking the whole endpoint down. A
   real product doesn't 500 every chat message because one upstream call
   had a bad day.

3. Token usage is real when a real call is made — Anthropic's response
   includes actual input/output token counts, and that's what gets
   returned here, not an estimate dressed up as a real number. The stub
   path (no API key) uses a clearly-labeled rough estimate instead, so
   the usage meter still moves meaningfully in local development without
   ever presenting a guess as measured fact.
"""

import os

import anthropic

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024
CHARS_PER_ESTIMATED_TOKEN = 4  # rough English-text heuristic, stub path only

# Real multi-model routing. Each key here is what the frontend's model
# picker actually sends — not a marketing label with nothing behind it.
# "ultra"/"fast"/"balanced" map to real, distinct Claude models with real
# cost/latency/capability tradeoffs. We deliberately do NOT list GPT or
# Gemini as selectable anywhere in the product yet — this file only ever
# calls Anthropic, and a picker option with no model behind it is worse
# than not having the option at all.
MODEL_MAP = {
    "ultra": "claude-opus-4-8",  # deepest reasoning, slowest, most expensive
    "balanced": "claude-sonnet-5",  # default — good reasoning, real-time speed
    "fast": "claude-haiku-4-5-20251001",  # cheapest, fastest, simple tasks
}

# Auto Mode's routing thresholds. This is a deliberately simple, legible
# heuristic (message length as a proxy for task complexity) rather than
# a second model call to "decide" the model — that would burn tokens on
# every single message just to route it. Simple and honest beats clever
# and expensive here; these thresholds can be tuned later against real
# usage data without changing the interface.
AUTO_FAST_CHAR_CEILING = 200  # short, simple asks -> fast model
AUTO_ULTRA_CHAR_FLOOR = 2000  # long/complex asks -> deepest model


def _resolve_model(model_choice: str | None) -> str:
    """Turns a frontend model choice into a real Anthropic model ID.

    Unknown or missing values fall back to the env-configured default
    (ANTHROPIC_MODEL, or DEFAULT_MODEL) rather than erroring or silently
    auto-routing — a stale frontend build, a direct API call with no
    model field, or a bad value here should degrade to one predictable
    model, not a heuristic the caller didn't ask for.
    """
    if model_choice and model_choice in MODEL_MAP:
        return MODEL_MAP[model_choice]
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)


def _auto_route(user_content: str) -> str:
    length = len(user_content)
    if length <= AUTO_FAST_CHAR_CEILING:
        return MODEL_MAP["fast"]
    if length >= AUTO_ULTRA_CHAR_FLOOR:
        return MODEL_MAP["ultra"]
    return MODEL_MAP["balanced"]

RESEARCH_SYSTEM_PROMPT = (
    "You are a research assistant. Answer clearly and directly. You do not have "
    "live web search in this deployment, so do not invent citations or sources — "
    "if the user needs verified, sourced information, say so plainly rather than "
    "fabricating a reference."
)
GENERAL_SYSTEM_PROMPT = (
    "You are Aurora, a helpful AI assistant. Be direct and concise unless the "
    "user's question calls for more depth."
)


def _estimate_tokens(*texts: str) -> int:
    total_chars = sum(len(t) for t in texts)
    return max(1, total_chars // CHARS_PER_ESTIMATED_TOKEN)


def _stub_reply(reason: str, user_content: str) -> dict:
    content = (
        f"This is a placeholder response from the orchestration stub ({reason}). "
        "No real model call was made. Set ANTHROPIC_API_KEY in the environment "
        "to connect a real model — see aurora-api/README.md."
    )
    return {
        "content": content,
        "model_used": "aurora-stub",
        "citations": None,
        "confidence": None,
        "tokens_used": _estimate_tokens(user_content, content),
        "tokens_are_estimated": True,
    }


def generate_reply(user_content: str, mode: str | None = None, model_choice: str | None = None) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _stub_reply("no ANTHROPIC_API_KEY configured", user_content)

    if model_choice == "auto":
        model = _auto_route(user_content)
    else:
        model = _resolve_model(model_choice)

    system_prompt = RESEARCH_SYSTEM_PROMPT if mode == "research" else GENERAL_SYSTEM_PROMPT

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        real_tokens = response.usage.input_tokens + response.usage.output_tokens
        return {
            "content": text,
            "model_used": model,
            # No search tool is wired up yet, so no real citations exist to
            # return. See the module docstring — this is deliberate, not
            # an oversight.
            "citations": None,
            "confidence": None,
            "tokens_used": real_tokens,
            "tokens_are_estimated": False,
        }
    except anthropic.APIStatusError as e:
        detail = "unknown reason"
        try:
            body = e.response.json()
            detail = body.get("error", {}).get("message", detail)
        except Exception:  # noqa: BLE001 — best-effort detail extraction, never let this itself crash
            pass
        return _stub_reply(
            f"model call failed: HTTP {e.status_code} — {detail}", user_content
        )
    except anthropic.APIConnectionError:
        return _stub_reply("could not reach the Anthropic API — check network connectivity", user_content)
    except Exception as e:  # noqa: BLE001 — this endpoint must never 500 because of an upstream model failure
        return _stub_reply(f"unexpected error calling the model: {type(e).__name__}", user_content)
