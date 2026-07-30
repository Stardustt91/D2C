"""
Names a session from its activity description.

The title is what the analyst scans the session list by, so it has to say what the
work *is* — "40 outdoor 5G sites, Greater Cairo" — not restate that it is a cost
estimation. Everything in the list is a cost estimation.

Titling must never be able to block session creation: the description is already
saved by the time this runs, and a session with a plain title is fine while a failed
save is not. Every failure path here therefore falls back to
:func:`fallback_title`, which needs no network.
"""

import re
from typing import Optional

from pydantic import BaseModel, Field

from env_config import chat_llm

MAX_TITLE_CHARS = 60

_llm = chat_llm(max_tokens=60)


class SessionTitle(BaseModel):
    title: str = Field(
        ...,
        description="A short, specific title for this activity: what is being done, "
                    "how much of it, and where. Title Case, no trailing period.",
    )


_PROMPT = """Write a short title for this telecom activity, for a list of saved cost estimations.

Rules:
- 3 to 7 words, at most {max_chars} characters.
- Lead with the work itself, then any scale or place the description actually gives:
  <quantity> <work>, <location>.
- Use ONLY words and figures that appear in the description below. If it names no
  quantity, no location and no technology, leave them out and title it by the work
  alone. Never supply a number or a place that is not written in the description.
- Keep whatever numbers, site counts, locations and technologies ARE there — they are
  what distinguishes one session from another in the list.
- Do NOT include the words "cost", "estimation", "project" or "activity". Every entry in
  this list is a cost estimation, so those words carry no information.
- No quotes, no trailing punctuation.

Activity description:
{description}"""

#: Words allowed in a title without appearing in the description — joiners and the
#: generic nouns a reasonable paraphrase reaches for.
_TITLE_STOPWORDS = frozenset({
    "and", "or", "for", "the", "a", "an", "of", "in", "on", "at", "to", "with",
    "per", "across", "over", "new", "works", "work", "site", "sites",
})


def fallback_title(description: str) -> str:
    """A usable title without the network: the first clause of the description, trimmed.

    Used when the model is unreachable or returns nothing, and as the placeholder the
    UI shows while a title is being generated.
    """
    text = " ".join((description or "").split())
    if not text:
        return "Untitled session"
    # A description usually opens with the work itself and only then qualifies it, so
    # the first sentence or clause is the most title-like part of it.
    for sep in (". ", "; ", " including ", " covering ", ", including "):
        head, found, _ = text.partition(sep)
        if found and len(head) >= 15:
            text = head
            break
    if len(text) <= MAX_TITLE_CHARS:
        return text
    clipped = text[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (clipped or text[:MAX_TITLE_CHARS]) + "…"


def is_grounded(title: str, description: str) -> bool:
    """Is every specific claim in `title` actually present in `description`?

    A title is a label, not a finding, but this one sits at the head of a cost
    estimate — so a hallucinated quantity or place is worse than a dull title. The
    model is capable of pattern-completing scale it was never given (an earlier
    version of the prompt carried an example title, and descriptions with no numbers
    came back wearing the example's), and this is the check that catches it whatever
    the cause.

    Numbers must match exactly. Other words are allowed some latitude for ordinary
    paraphrase, but a title mostly made of words the description never used is not
    describing it.
    """
    if not title:
        return False
    haystack = description.lower()

    # Every figure in the title has to be one the description supplied.
    for number in re.findall(r"\d+(?:[.,]\d+)*", title):
        if number not in haystack:
            return False

    content = [
        word for word in re.findall(r"[a-z0-9]+", title.lower())
        if len(word) > 2 and word not in _TITLE_STOPWORDS
    ]
    if not content:
        return True
    grounded = sum(1 for word in content if word in haystack)
    return grounded * 2 >= len(content)  # at least half


def generate_title(description: str) -> str:
    """Title for `description`, falling back to :func:`fallback_title` on any failure."""
    text = " ".join((description or "").split())
    if not text:
        return "Untitled session"
    try:
        structured = _llm.with_structured_output(SessionTitle)
        # The description can run long; the opening is where the defining facts sit, and
        # sending less keeps this call cheap enough to be invisible next to the workflow.
        excerpt = text[:1200]
        response = structured.invoke(
            _PROMPT.format(description=excerpt, max_chars=MAX_TITLE_CHARS)
        )
        title = _clean(response.title if response else None)
        if title and not is_grounded(title, excerpt):
            print(f"[session_title] discarding ungrounded title {title!r}; using the description")
            return fallback_title(text)
        return title or fallback_title(text)
    except Exception as exc:  # network, auth, refusal, malformed output
        print(f"[session_title] falling back to a derived title: {exc}")
        return fallback_title(text)


def _clean(raw: Optional[str]) -> str:
    title = " ".join((raw or "").split()).strip().strip('"“”\'').rstrip(".")
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return title


if __name__ == "__main__":
    samples = [
        # Carries scale and place — the title should keep both.
        "Deploy 40 outdoor 5G sites across Greater Cairo over 6 months, including civil "
        "works, power, and transmission.",
        # Carries neither. The title must describe the work and invent nothing.
        "Civil works are the physical construction activities needed to build a site, such "
        "as clearing land, pouring concrete foundations, and erecting the steel tower. It "
        "also includes the supporting infrastructure like fences, power connections, and "
        "equipment rooms that keep the site secure and operational.",
        "Annual maintenance of diesel generators at remote base stations in Upper Egypt.",
    ]
    for sample in samples:
        print("\ndescription:", sample[:70] + "…")
        print("  fallback:", fallback_title(sample))
        print("  llm     :", generate_title(sample))
