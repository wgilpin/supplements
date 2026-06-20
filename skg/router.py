"""M2 chat router: free-text question -> QueryRequest (spec §9).

The LLM's ONLY job here is to classify the question into one of the fixed query
types and extract its parameters. It never writes Cypher — `skg.query` owns all
of that. The distinct entity names are passed in so the model can resolve
abbreviations to real node names (e.g. "GABA" -> the full receptor name the
extractor stored), which the deterministic `resolve_entity` backstop then
exact-matches.

Not unit-tested: it calls a remote LLM, and per the project policy a test that
depends on a remote API isn't worth writing.
"""

from __future__ import annotations

import logging

import kuzu
from google import genai

from . import config, query
from .query import QueryRequest

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


PROMPT = """You route a user's question to ONE query over a supplement knowledge graph.
Pick exactly one `query` type:
- "compound": what a single given compound does. entity = the compound.
- "effect": what affects a single given effect/outcome/disease. entity = the effect.
- "target": what acts on a single given molecular target (protein/receptor/pathway). entity = the target.
- "bridge": OTHER compounds that share a biological target with a given compound. entity = the compound.
- "contradictions": conflicting (increase vs decrease) claims for an effect. entity = the effect, or null for all effects.
- "search": a free-text keyword search over the evidence quotes — use when the user asks what mentions/studies a term, or when the term is unlikely to be a normalised compound/effect/target node (e.g. a disease named only by abbreviation). entity = the keyword.
- "intersection": when the user asks about the intersection of multiple entities (e.g. what is affected by BOTH compound A and compound B, what affects BOTH effect A and effect B, or what acts on BOTH target A and target B). entities = the list of these multiple entities.
- "list_supplements": the user wants the whole list of supplements/compounds the graph has data on, not anything about one specific compound (e.g. "what supplements do you know about", "list supplements", "show me which supplements you have data on", "which compounds do you cover"). entity = null.
- "unknown": the question does not map to any of the above.

Extract `entity` (for non-intersection queries):
- If a name in the relevant list below clearly matches what the user means, return that EXACT name (expand abbreviations to the matching full name, e.g. "GABA" -> the matching receptor name in the list).
- Do not match a specific chemical variant or derivative (e.g., "methyl folate", "N-acetyl cysteine") to a generic or different compound name in the list (e.g., "folate", "cysteine"). If the user specifies a variant not in the list, return their raw term.
- Otherwise return the user's own term.
- Use null for a contradictions question with no specific effect, or for intersection queries.

Extract `entities` (ONLY for intersection queries):
- If a name in the relevant lists below clearly matches what the user means, return that EXACT name in the list.
- Otherwise return the user's own terms.
- For non-intersection queries, set `entities` to an empty list.

Extract `min_evidence` (1-5): default 1. Use 4 if the user asks for strong/high-quality/human evidence, 5 if they ask specifically for RCT/clinical-trial evidence.

COMPOUNDS:
{compounds}

EFFECTS:
{effects}

TARGETS:
{targets}

QUESTION: {question}
"""


def route(conn: kuzu.Connection, question: str) -> QueryRequest:
    """Resolve a natural-language question to a QueryRequest via the LLM."""
    prompt = PROMPT.format(
        compounds=", ".join(query.list_compounds(conn)),
        effects=", ".join(query.list_effects(conn)),
        targets=", ".join(query.list_targets(conn)),
        question=question,
    )
    resp = _get_client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": QueryRequest,
        },
    )
    parsed = resp.parsed
    if isinstance(parsed, QueryRequest):
        logger.info("routed %r -> %s", question, parsed)
        return parsed
    logger.warning("router could not parse a QueryRequest for %r", question)
    return QueryRequest(query="unknown", entity=None, min_evidence=1)
