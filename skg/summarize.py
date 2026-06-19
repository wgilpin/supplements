"""Grounded natural-language answer over retrieved results (spec §9 UX).

Turns the structured query results into a short prose answer to the user's
question. Grounded ONLY in the provided rows — no external knowledge, no Cypher.
If the LLM call fails the cards still render (returns "").

Not unit-tested: it calls a remote LLM (per the project test policy).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from google import genai
from pydantic import BaseModel

from . import config

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


PROMPT = """Answer the user's question in 1-3 sentences using ONLY the structured
claims below, retrieved from a supplement knowledge graph. Ground every statement
in the claims — do NOT add outside knowledge. Note evidence strength (1-5, higher
is stronger) where useful, and flag any disagreement between claims. Be concise
and factual; do not repeat the source quotes verbatim.

QUESTION: {question}

CLAIMS (JSON):
{claims}
"""

_MAX_ROWS = 40


def summarize(question: str, results: Sequence[BaseModel]) -> str:
    """Return a short grounded answer, or "" if there's nothing to summarise or
    the LLM call fails."""
    if not results:
        return ""
    payload = [r.model_dump() for r in list(results)[:_MAX_ROWS]]
    try:
        resp = _get_client().models.generate_content(
            model=config.GEMINI_MODEL,
            contents=PROMPT.format(
                question=question, claims=json.dumps(payload, default=str)
            ),
        )
        text = (resp.text or "").strip()
        logger.info("summarised %d rows for %r (%d chars)",
                    len(payload), question, len(text))
        return text
    except Exception as e:  # summary is best-effort; never break the response
        logger.warning("summary failed for %r: %s", question, e)
        return ""
