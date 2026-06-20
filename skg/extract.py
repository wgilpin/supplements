"""Step 2 — the one bio-bearing step: abstract -> list[Claim] via Gemini."""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import unicodedata

from google import genai
from google.genai.errors import APIError

from . import config
from .schema import Claim, is_meaningful

logger = logging.getLogger(__name__)

PROMPT = """You extract biological interaction claims from a research abstract.

Return a JSON array of claim objects. Each claim links a compound to a molecular \
target and/or a downstream effect. Rules:
- compound: the supplement/compound the claim is about.
- target: the molecular/cellular target (protein, receptor, pathway), or null.
- effect: the phenotype/outcome (disease, symptom, biomarker), or null.
  At least one of target/effect must be present.
- Use CANONICAL FULL NAMES for target and effect — expand all abbreviations
  (e.g. "AD" -> "Alzheimer disease", "GABA-A" -> "gamma-aminobutyric acid type A receptor").
- DISEASE/CONDITION CAPTURE: if the abstract studies a disease or condition that the
  compound is presented as affecting, emit that disease as an `effect` with its
  canonical full name — even when it is named only by an abbreviation (e.g. "AD" ->
  "Alzheimer disease") or only implied by a disease model (e.g. "5XFAD" or "APP/PS1"
  mice -> "Alzheimer disease"; "MPTP model" -> "Parkinson disease"). Do not drop the
  disease just because a sentence focuses on a downstream mechanism — emit a separate
  claim linking the compound to the disease effect (same source_quote, metadata).
- Each target and each effect must name a SINGLE biological entity. NEVER
  concatenate several entity names into one value (no "A / B / C", no
  "A-B pathway" built from distinct proteins, no "A and B complex" unless that
  complex is itself the one named entity).
- If a sentence implicates a multi-component pathway, axis, or signalling
  cascade (e.g. "PI3K/AKT/mTOR pathway", "Nrf2/HO-1 axis"), do NOT emit it as
  one glued target/effect. Instead emit one separate claim per distinct
  component (one for "phosphoinositide 3-kinase", one for "protein kinase B",
  one for "mammalian target of rapamycin"), each with the same source_quote,
  direction, and metadata. If only one component is truly the point of the
  sentence, emit just that single most-specific entity.
- direction: exactly one of "increases", "decreases", "none", "mixed", "modulates".
  - "increases" / "decreases": the compound raises / lowers the target or effect.
  - "none" = explicitly no significant effect. This is a VALID, valuable claim — keep it.
  - "mixed" = genuinely bidirectional or dose-dependent (e.g. hormesis).
  - "modulates" = the compound affects/influences the target or effect but the
    sentence states NO clear direction (e.g. "taurine influences NF-κB"). Use this
    instead of guessing a direction.
- CAUSATION: only assert "increases"/"decreases" when the sentence presents the
  COMPOUND as the agent causing the change. If the sentence is merely associative
  or observational (e.g. "mice exhibited elevated IL-6", "pathway X displayed
  activation, indicating a response to disease Y"), do NOT infer a causal
  direction or its sign — use "modulates", or skip the claim if no relationship
  is actually asserted. Do not reverse cause and effect.
- dose_text: the dose/regimen sentence verbatim if stated, else "".
- cohort_text: the population/model description if stated, else "".
- model: study type, e.g. "human RCT", "human observational", "mouse", "rat",
  "in vitro", "review".
- source_quote: a VERBATIM sentence copied exactly from the abstract that supports
  this claim. This is mandatory and must appear word-for-word in the abstract.
- Do not invent dose or cohort. Emit "" when not stated.

ABSTRACT:
{abstract}
"""

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def normalize_text(text: str) -> str:
    """Normalize text by converting to lowercase, replacing Greek letters with names,
    stripping combining marks, replacing punctuation with spaces, and collapsing spaces.

    Gemini sometimes corrupts Greek letters in source quotes, echoing e.g. ``TNF-α`` back
    as ``TNF-`` + a stray combining mark (U+0311) instead of U+03B1. Stripping combining
    marks (Unicode category Mn) neutralises that corruption so a quote that is otherwise
    verbatim still matches the abstract.
    """
    t = text.lower()
    t = t.replace("γ", "gamma").replace("α", "alpha").replace("β", "beta")
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def fuzzy_substring_match(sub: str, text: str, threshold: float = 0.85) -> bool:
    """Check if the normalized sub is a fuzzy substring of the normalized text."""
    if not sub or not text:
        return False
    if sub in text:
        return True

    # Ensure all words of length >= 3 in the sub are present in the text to prevent word swaps (like increases/decreases)
    sub_words = [w for w in sub.split() if len(w) >= 3]
    for w in sub_words:
        if w not in text:
            return False

    # autojunk=False is essential here: on a long abstract its heuristic treats common
    # characters (spaces, frequent letters) as junk and skips them, which misaligns the
    # longest-match anchor and collapses the ratio for quotes that are genuinely present.
    matcher = difflib.SequenceMatcher(None, sub, text, autojunk=False)
    match = matcher.find_longest_match(0, len(sub), 0, len(text))
    if match.size == 0:
        return False

    # Extract the window in text that aligns with the sub
    start = max(0, match.b - match.a)
    end = min(len(text), match.b + len(sub) - match.a)
    candidate = text[start:end]

    ratio = difflib.SequenceMatcher(None, sub, candidate, autojunk=False).ratio()
    return ratio >= threshold


def _strip_combining_marks(text: str) -> str:
    """Drop Unicode combining marks while preserving case, spacing, and punctuation."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize(
        "NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    )


def recover_quote(quote: str, abstract: str) -> str:
    """Return the abstract's own text for an already-verified quote.

    Gemini occasionally corrupts characters in ``source_quote`` — most often Greek
    letters echoed back as stray combining marks (``TNF-α`` -> ``TNF-`` + U+0311) — so
    the model's quote can render as garbage in the UI even when it verifies. Since the
    quote has passed verbatim verification, prefer the matching sentence from the
    abstract, which is guaranteed clean. Falls back to a mark-stripped copy of the
    model quote when no sentence matches well enough.
    """
    norm_quote = normalize_text(quote)
    if not norm_quote:
        return quote
    best, best_ratio = "", 0.0
    for sentence in re.split(r"(?<=[.!?])\s+", abstract.strip()):
        ratio = difflib.SequenceMatcher(
            None, norm_quote, normalize_text(sentence), autojunk=False
        ).ratio()
        if ratio > best_ratio:
            best, best_ratio = sentence.strip(), ratio
    if best_ratio >= 0.85:
        return best
    return _strip_combining_marks(quote)


async def extract_claims_async(abstract: str) -> list[Claim]:
    """Extract claims from one abstract asynchronously, with retry logic on rate limits."""
    client = _get_client()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=PROMPT.format(abstract=abstract),
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": list[Claim],
                    },
                ),
                timeout=config.GEMINI_REQUEST_TIMEOUT,
            )
            parsed = resp.parsed
            claims: list[Claim] = parsed if isinstance(parsed, list) else []
            kept = []
            norm_abstract = normalize_text(abstract)
            for c in claims:
                if not is_meaningful(c):
                    continue
                quote = c.source_quote.strip()
                if quote and not fuzzy_substring_match(normalize_text(quote), norm_abstract):
                    logger.info("dropped non-verbatim quote: %r...", c.source_quote[:60])
                    continue
                if quote:
                    # Store the abstract's own text rather than the model's echo, which
                    # can carry corrupted characters (e.g. Greek letters as combining marks).
                    c.source_quote = recover_quote(quote, abstract)
                kept.append(c)
            return kept
        except (asyncio.TimeoutError, TimeoutError):
            if attempt < max_retries - 1:
                sleep_time = (2 ** attempt) + 1
                logger.warning(
                    "Request exceeded %.0fs timeout. Retrying in %ds...",
                    config.GEMINI_REQUEST_TIMEOUT,
                    sleep_time,
                )
                await asyncio.sleep(sleep_time)
                continue
            raise
        except APIError as e:
            is_transient = e.code in (429, 500, 503, 504) or "limit" in str(e).lower() or "exhausted" in str(e).lower()
            if is_transient and attempt < max_retries - 1:
                sleep_time = (2 ** attempt) + 1
                logger.warning("Transient API error (%s). Retrying in %ds...", e, sleep_time)
                await asyncio.sleep(sleep_time)
                continue
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                sleep_time = (2 ** attempt) + 1
                logger.warning("Unexpected error (%s). Retrying in %ds...", e, sleep_time)
                await asyncio.sleep(sleep_time)
                continue
            raise
    raise RuntimeError("unreachable: extraction retry loop exhausted without return")


async def extract_claims_batch(records: list[dict]) -> dict[str, list[Claim]]:
    """Extract claims for a list of abstracts in parallel, pacing requests with a 1.0s stagger delay."""
    results_map: dict[str, list[Claim]] = {}

    async def worker(index: int, rec: dict):
        pmid = rec["pmid"]
        abstract = rec["abstract"]

        # Stagger the start time of each request by 1.0s * index
        delay = index * 1.0
        if delay > 0:
            await asyncio.sleep(delay)

        logger.info("Starting extraction for PMID %s (stagger delay: %.1fs)...", pmid, delay)
        try:
            claims = await extract_claims_async(abstract)
            results_map[pmid] = claims
            logger.info("Finished extraction for PMID %s: found %d claims", pmid, len(claims))
        except Exception as e:
            logger.error("Error extracting PMID %s: %s", pmid, e)
            results_map[pmid] = []

    tasks = [worker(i, rec) for i, rec in enumerate(records)]
    await asyncio.gather(*tasks)
    return results_map
