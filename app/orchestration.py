"""
The Orchestration Engine / Model Router described in
docs/03-system-architecture.md. This now makes REAL calls to Anthropic's
API when an API key is configured — this is not a stub pretending to be
real, it's an actual model call.

Three honesty constraints, deliberate:

1. Citations and confidence scores are only ever populated when there's
   a real basis for them. In general chat mode, there's no search tool
   attached, so citations/confidence stay null there — fabricating
   either would be worse than returning nothing. In research mode
   (mode="research"), a real Anthropic web search tool is attached, so
   citations there are real search results Claude actually retrieved and
   cited, not invented. Confidence scores remain null in both modes —
   citations existing doesn't give us a legitimate way to score
   confidence, and we're not going to fabricate one just because we can
   now show sources.

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

from .schemas import Citation

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
    "You are a research assistant with a real, live web search tool. Use "
    "it whenever the question benefits from current, verifiable, or "
    "citable information — don't answer from memory alone when a search "
    "would give a better-grounded, sourced answer. Cite what you actually "
    "find. If search doesn't turn up a clear answer, say so plainly "
    "rather than guessing or fabricating a source."
)
GENERAL_SYSTEM_PROMPT = (
    "You are Aurora, a helpful AI assistant. Be direct and concise unless the "
    "user's question calls for more depth."
)

# Real web search, only attached in Research mode. This is the Anthropic
# Messages API's server-side web search tool — Claude decides for itself
# whether/what to search, retrieves real results, and the API response
# comes back with real citations attached to the text (see
# _extract_citations below). This is billed per-search by Anthropic on
# top of normal token costs, which is why it's capped with max_uses and
# only ever attached for mode="research", not every chat message.
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"
RESEARCH_MAX_SEARCHES = 5
RESEARCH_MAX_TOKENS = 2048  # a sourced, grounded answer needs more room than a quick chat reply


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


def _extract_text(content_blocks) -> str:
    return "".join(getattr(b, "text", "") for b in content_blocks if getattr(b, "type", None) == "text")


def _extract_citations(content_blocks) -> list[Citation] | None:
    """Pulls real web_search_result_location citations off text blocks.

    Only text blocks carry citations (per the Messages API shape); other
    block types (server_tool_use, web_search_tool_result) are the search
    mechanics themselves, not citable sources. Deduplicates by URL since
    the same source is often cited more than once across a long answer.
    Returns None (not an empty list) when there's nothing to show —
    "no citations" and "empty citations list" should look the same to
    callers.
    """
    citations: list[Citation] = []
    seen_urls: set[str] = set()

    for block in content_blocks:
        if getattr(block, "type", None) != "text":
            continue
        for c in getattr(block, "citations", None) or []:
            if getattr(c, "type", None) != "web_search_result_location":
                continue
            url = getattr(c, "url", None)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = getattr(c, "title", None)
            citations.append(Citation(source=f"{title} — {url}" if title else url))

    return citations or None


def generate_reply(user_content: str, mode: str | None = None, model_choice: str | None = None) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _stub_reply("no ANTHROPIC_API_KEY configured", user_content)

    if model_choice == "auto":
        model = _auto_route(user_content)
    else:
        model = _resolve_model(model_choice)

    system_prompt = RESEARCH_SYSTEM_PROMPT if mode == "research" else GENERAL_SYSTEM_PROMPT

    # Only research mode gets the search tool — it's billed per-search on
    # top of normal tokens, and a general chat reply has no business
    # searching the web on every message.
    tools = (
        [{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": RESEARCH_MAX_SEARCHES}]
        if mode == "research"
        else None
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        create_kwargs = dict(
            model=model,
            max_tokens=RESEARCH_MAX_TOKENS if mode == "research" else MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        if tools:
            create_kwargs["tools"] = tools

        response = client.messages.create(**create_kwargs)
        text = _extract_text(response.content)
        citations = _extract_citations(response.content) if mode == "research" else None
        real_tokens = response.usage.input_tokens + response.usage.output_tokens
        return {
            "content": text,
            "model_used": model,
            "citations": citations,
            # Confidence stays null even with real citations now available
            # in research mode — having sources doesn't give us a
            # legitimate way to score confidence, so we don't fabricate one.
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
