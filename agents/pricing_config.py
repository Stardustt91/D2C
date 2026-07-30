"""
Shared clients and tuning limits for the pricing engine.

Credentials come from agents/env_config.py, which loads them from the environment or
the project-root .env; the older D2C_AZURE_* / D2C_TAVILY_API_KEY names are still
accepted there as aliases. Nothing in this repository carries a working key any more.

The tuning knobs below stay D2C_-prefixed and keep their defaults — they are
behaviour, not secrets.
"""

from __future__ import annotations

import os
from typing import Optional

from langchain_openai import AzureChatOpenAI
from tavily import TavilyClient

from env_config import optional, reasoning_llm, tavily_key

# --- credentials ----------------------------------------------------------

#: GPT-5 reasoning effort. The pricing engine's work (search-query planning, price
#: extraction from page text, aggregation) is bounded and needs little deliberation,
#: so 'minimal' keeps reasoning tokens from consuming the whole completion budget —
#: the failure mode where a call spends all its tokens thinking and returns no output.
_REASONING_EFFORT = optional("D2C_REASONING_EFFORT", default="minimal")

# --- tuning ---------------------------------------------------------------

#: Upper bound on queries per parameter. The fan-out varies phrasing along several
#: axes, but each query costs a search call, so the planner is capped rather than
#: left to generate as many as it likes.
MAX_QUERIES = int(os.environ.get("D2C_MAX_QUERIES", "7"))

#: Results read per query. The previous engine requested ten and used five; here
#: every returned result is read, so the number requested is the number used.
RESULTS_PER_QUERY = int(os.environ.get("D2C_RESULTS_PER_QUERY", "6"))

#: Characters of page body passed to extraction. Prices live in tables partway down
#: a page, so snippets are not enough, but whole pages would blow the context
#: budget across a fan-out.
MAX_PAGE_CHARS = int(os.environ.get("D2C_MAX_PAGE_CHARS", "4000"))

#: Pages per extraction call. Batching keeps the number of LLM round trips down
#: without letting any single prompt grow unmanageable.
PAGES_PER_EXTRACTION_CALL = int(os.environ.get("D2C_PAGES_PER_EXTRACTION", "4"))

#: Set to "0" to bypass the evidence cache entirely (useful when testing).
USE_CACHE = os.environ.get("D2C_USE_PRICE_CACHE", "1") != "0"

#: Last rung of the fallback ladder: when the search found no price and internal
#: history has none either, ask the model for a benchmark figure from its own
#: knowledge. It is unsourced by construction, so it is published with
#: estimate_basis 'model_judgement' and flagged everywhere it appears. Set
#: D2C_ALLOW_MODEL_JUDGEMENT=0 to leave those parameters blank instead, which is
#: the right setting if a blank is more useful to your reviewers than a starting point.
ALLOW_MODEL_JUDGEMENT = os.environ.get("D2C_ALLOW_MODEL_JUDGEMENT", "1") != "0"


# --- clients --------------------------------------------------------------

_llm: Optional[AzureChatOpenAI] = None
_tavily: Optional[TavilyClient] = None


def get_llm(temperature: float = 0.0, max_tokens: int = 12000) -> AzureChatOpenAI:
    """Shared chat model.

    The GPT-5 reasoning deployment only supports the default temperature, so the
    ``temperature`` argument is accepted for backward compatibility with existing
    callers but is no longer forwarded to the model. ``max_tokens`` is passed as
    ``max_completion_tokens`` (the parameter GPT-5 expects)."""
    global _llm
    if _llm is None:
        _llm = reasoning_llm(
            reasoning_effort=_REASONING_EFFORT,
            max_completion_tokens=max_tokens,
        )
    return _llm


def get_tavily() -> TavilyClient:
    global _tavily
    if _tavily is None:
        _tavily = TavilyClient(api_key=tavily_key())
    return _tavily
