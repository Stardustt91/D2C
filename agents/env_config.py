"""
The single point where credentials and endpoints enter the process.

Every value here comes from the environment, loaded from the project-root ``.env``
by python-dotenv the first time this module is imported. Because every module that
builds a client imports this one, that load happens however the code is entered:
``uvicorn d2c_app.app:app``, ``python agents/workflow_test.py``, or running an agent
module on its own.

Secrets are *required*. A missing key raises ``MissingCredential`` naming the
variable at import time, rather than letting the process start and fail later inside
an LLM call with an opaque 401. Non-secret configuration — endpoints, deployment
names, API versions — keeps the literal it had before as its default, so an existing
checkout only needs the keys supplied to keep working exactly as it did.

Four Azure OpenAI deployments are in play, and they are not interchangeable:

    chat        gpt-4o-mini             cheap structure generation: drivers,
                                        components, inputs, deduplication
    reasoning   gpt-5                   steps that must deliberate: resource planner,
                                        cost parameters, cost estimation, pricing engine
    intake      gpt-chat-latest         the activity-intake conversation — the one place
                                        a person is on the other end of the call
    embeddings  text-embedding-3-large  the FAISS retrievers used for RAG

Each tier carries its own API version variable rather than sharing one, because the
versions genuinely differ: gpt-5 needs a newer API version than gpt-4o-mini, and
collapsing them silently downgrades one of the two.

See ``.env.example`` for the full list of variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

#: Repository root. This file lives in agents/, so the .env sits one level up.
ROOT = Path(__file__).resolve().parent.parent

# override=False so a variable exported in the real environment (container, CI, a
# systemd unit) wins over the checked-out .env file — the behaviour you want anywhere
# secrets are injected properly rather than kept in a file.
load_dotenv(ROOT / ".env", override=False)


class MissingCredential(RuntimeError):
    """A required credential is absent from both the environment and .env."""


def require(name: str, *aliases: str) -> str:
    """The value of ``name``, or of the first alias that is set.

    Aliases exist because parts of this codebase reached the same secret under
    ``D2C_``-prefixed names; both spellings keep working.
    """
    value = optional(name, *aliases)
    if value:
        return value
    accepted = ", ".join((name, *aliases))
    raise MissingCredential(
        f"{name} is not set. Copy .env.example to .env and fill it in, or export the "
        f"variable before starting the app. Accepted names: {accepted}. "
        f"Credentials are no longer hardcoded in this repository."
    )


def optional(name: str, *aliases: str, default: str = "") -> str:
    """Same lookup as :func:`require`, but falls back to ``default`` instead of raising."""
    for candidate in (name, *aliases):
        value = os.environ.get(candidate, "").strip()
        if value:
            return value
    return default


# ---------------------------------------------------------------------------
# Azure OpenAI — chat tier (gpt-4o-mini)
# ---------------------------------------------------------------------------

CHAT_ENDPOINT = optional(
    "AZURE_OPENAI_CHAT_ENDPOINT", default="https://ds-openai-4omini-v01.openai.azure.com"
)
CHAT_DEPLOYMENT = optional("AZURE_OPENAI_CHAT_DEPLOYMENT", default="gpt-4o-mini")
CHAT_API_VERSION = optional(
    "AZURE_OPENAI_CHAT_API_VERSION", "AZURE_OPENAI_API_VERSION", default="2024-08-01-preview"
)


def chat_llm(*, temperature: float = 0, max_tokens: int = 5000, **kwargs) -> AzureChatOpenAI:
    """Client for the cheap chat deployment used by the structure-generation steps."""
    return AzureChatOpenAI(
        deployment_name=CHAT_DEPLOYMENT,
        openai_api_version=CHAT_API_VERSION,
        azure_endpoint=CHAT_ENDPOINT,
        api_key=require("AZURE_OPENAI_CHAT_API_KEY"),
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Azure OpenAI — reasoning tier (gpt-5)
# ---------------------------------------------------------------------------

REASONING_ENDPOINT = optional(
    "AZURE_OPENAI_REASONING_ENDPOINT",
    "D2C_AZURE_OPENAI_ENDPOINT",
    default="https://ds-openai-v01.openai.azure.com/",
)
REASONING_DEPLOYMENT = optional(
    "AZURE_OPENAI_REASONING_DEPLOYMENT", "D2C_AZURE_DEPLOYMENT", default="gpt-5"
)
# Deliberately does NOT fall back to AZURE_OPENAI_API_VERSION: in existing .env files
# that variable holds the *chat* tier's version, and applying it here would quietly
# point gpt-5 at an API version that predates the parameters it needs.
REASONING_API_VERSION = optional(
    "AZURE_OPENAI_REASONING_API_VERSION", "D2C_AZURE_API_VERSION", default="2024-12-01-preview"
)


def reasoning_key() -> str:
    return require("AZURE_OPENAI_REASONING_API_KEY", "D2C_AZURE_OPENAI_KEY")


def reasoning_llm(
    *,
    reasoning_effort: str = "low",
    max_completion_tokens: int = 12000,
    deployment: Optional[str] = None,
    endpoint: Optional[str] = None,
    api_version: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs,
) -> AzureChatOpenAI:
    """Client for the reasoning deployment.

    The keyword overrides exist for the resource planner, which may be pointed at a
    different (stronger) deployment than the rest of the reasoning steps. Passing
    ``None`` for any of them means "use the shared reasoning tier".

    ``max_completion_tokens`` rather than ``max_tokens``: the reasoning deployment
    expects the former, and reasoning tokens are drawn from the same budget.
    """
    return AzureChatOpenAI(
        deployment_name=deployment or REASONING_DEPLOYMENT,
        openai_api_version=api_version or REASONING_API_VERSION,
        azure_endpoint=endpoint or REASONING_ENDPOINT,
        api_key=api_key or reasoning_key(),
        reasoning_effort=reasoning_effort,
        max_completion_tokens=max_completion_tokens,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Azure OpenAI — intake tier (gpt-chat-latest)
# ---------------------------------------------------------------------------

INTAKE_ENDPOINT = optional(
    "AZURE_OPENAI_INTAKE_ENDPOINT", default="https://lifestyleenhanced.openai.azure.com/"
)
INTAKE_DEPLOYMENT = optional("AZURE_OPENAI_INTAKE_DEPLOYMENT", default="gpt-chat-latest")
# Its own version for the same reason the reasoning tier has one: this deployment is a
# newer model than gpt-4o-mini and does not run on the chat tier's API version.
INTAKE_API_VERSION = optional(
    "AZURE_OPENAI_INTAKE_API_VERSION", default="2024-12-01-preview"
)


def intake_llm(**kwargs) -> AzureChatOpenAI:
    """Client for the activity-intake conversation.

    Deliberately passes no ``temperature`` or token cap. This deployment sits between an
    analyst pressing send and the next question appearing, and it is the only model in the
    system talking to a person rather than to another prompt — its own defaults are what
    the interview was written and tuned against.
    """
    return AzureChatOpenAI(
        deployment_name=INTAKE_DEPLOYMENT,
        openai_api_version=INTAKE_API_VERSION,
        azure_endpoint=INTAKE_ENDPOINT,
        api_key=require("AZURE_OPENAI_INTAKE_API_KEY"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Azure OpenAI — embeddings tier (text-embedding-3-large)
# ---------------------------------------------------------------------------

EMBEDDINGS_ENDPOINT = optional(
    "AZURE_OPENAI_EMBEDDINGS_ENDPOINT", default="https://ds-embed-v01.openai.azure.com"
)
EMBEDDINGS_DEPLOYMENT = optional(
    "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT", default="text-embedding-3-large"
)
EMBEDDINGS_API_VERSION = optional(
    "AZURE_OPENAI_EMBEDDINGS_API_VERSION", "AZURE_OPENAI_API_VERSION", default="2024-08-01-preview"
)


def embeddings_client() -> AzureOpenAIEmbeddings:
    """Embeddings client for the FAISS stores in new_vector_dbs/.

    The deployment name doubles as the model name, as it did when these arguments
    were written out at each call site; the vector stores were built with this model
    and will not match anything else.
    """
    return AzureOpenAIEmbeddings(
        deployment=EMBEDDINGS_DEPLOYMENT,
        model=EMBEDDINGS_DEPLOYMENT,
        openai_api_key=require("AZURE_OPENAI_EMBEDDINGS_API_KEY"),
        azure_endpoint=EMBEDDINGS_ENDPOINT,
        openai_api_version=EMBEDDINGS_API_VERSION,
    )


# ---------------------------------------------------------------------------
# Non-Azure services
# ---------------------------------------------------------------------------


def tavily_key() -> str:
    """Web search. Required: the search-backed pricing paths cannot run without it."""
    return require("TAVILY_API_KEY", "D2C_TAVILY_API_KEY")


def exchangerate_key() -> str:
    """Live FX rates.

    Optional by design — an empty string means pricing_fx skips the live lookup and
    uses its embedded fallback table, which is more honest than calling the API with
    a key that is absent or dead and waiting for the timeout.
    """
    return optional("EXCHANGERATE_API_KEY", "D2C_EXCHANGERATE_API_KEY")
