"""
The two model tiers the interview runs on, and why there are two rather than one or four.

CONVERSATION — gpt-5.2-chat
    Every turn the analyst waits through: classification, fact extraction, deciding what to
    ask next, reading their verdict on the draft. This tier is chosen for latency and
    judgement together. gpt-4o-mini is cheaper and was tried in the predecessor of this
    module; the failure is documented there — asked to judge how much an opening brief had
    already settled, it returned the same answer for a one-line brief and a full
    specification alike, and the interview length it produced was effectively random. The
    classification this graph opens with is a harder version of that same judgement, and it
    decides which of five completely different interviews the analyst then gets. A reasoning
    deployment would judge it better still, but it thinks for several seconds before its
    first token, and that wait sits between every message the analyst sends and the reply.

WRITER — gpt-5, reasoning_effort low
    The activity description, and the rewrites made to it during review. It runs once at the
    end of the interview rather than on every turn, and everything downstream — cost
    components, quantities, the estimate itself — is built from its output and never sees
    the conversation it came from. Slower is affordable here and better is not optional.

There is deliberately no embeddings tier. The reference data this interview needs is the
category tree and the Cost Driver Library, together about 12,000 tokens, and the interview
needs exactly one family out of thirty-four — chosen deterministically in ``knowledge.py``
from a classification the model has already committed to. Retrieval would answer that
question less reliably than a dictionary lookup, put an embedding call on the critical path
of every turn, and need rebuilding each time the library is revised.

CREDENTIALS
-----------
Both clients are built by ``agents/env_config.py``, which is the only place in this project
where a credential or an endpoint enters the process. This module used to carry its own keys
as literals with an environment override; they are now ``AZURE_OPENAI_CONVERSATION_*`` and
``AZURE_OPENAI_WRITER_*`` in the project-root ``.env``, and a missing one fails naming the
variable rather than as a 401 three questions into an interview.

Both are built on first use and then cached, rather than at import. With the keys hardcoded
a module-level client cost nothing; read from the environment it means importing ``graph``
raises ``MissingCredential`` on a machine with no ``.env`` — which would take ``selftest``
down with it, and the whole point of that file is that it runs anywhere for free.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import AzureChatOpenAI, ChatOpenAI

import env_config

#: Both tiers accept ``json_schema``, but tool calling is what every deployment in this
#: estate does reliably, and it is what the rest of the codebase already uses. Named once
#: here so switching is a one-line change rather than a search for every call site.
STRUCTURED_METHOD = "function_calling"


@lru_cache(maxsize=1)
def conversation_llm() -> ChatOpenAI:
    """The tier the analyst waits through."""
    return env_config.conversation_llm()


@lru_cache(maxsize=1)
def writer_llm() -> AzureChatOpenAI:
    """The tier that writes the description."""
    return env_config.writer_llm(reasoning_effort="low", max_completion_tokens=8000)
