"""Step 2 — the one bio-bearing step: abstract -> list[Claim] via Gemini."""

from __future__ import annotations

import asyncio
from google import genai
from google.genai.errors import APIError

from . import config
from .schema import Claim, is_meaningful

PROMPT = """You extract biological interaction claims from a research abstract.

Return a JSON array of claim objects. Each claim links a compound to a molecular \
target and/or a downstream effect. Rules:
- compound: the supplement/compound the claim is about.
- target: the molecular/cellular target (protein, receptor, pathway), or null.
- effect: the phenotype/outcome (disease, symptom, biomarker), or null.
  At least one of target/effect must be present.
- Use CANONICAL FULL NAMES for target and effect — expand all abbreviations
  (e.g. "AD" -> "Alzheimer disease", "GABA-A" -> "gamma-aminobutyric acid type A receptor").
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


async def extract_claims_async(abstract: str) -> list[Claim]:
    """Extract claims from one abstract asynchronously, with retry logic on rate limits."""
    client = _get_client()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = await client.aio.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=PROMPT.format(abstract=abstract),
                config={
                    "response_mime_type": "application/json",
                    "response_schema": list[Claim],
                },
            )
            claims: list[Claim] = resp.parsed or []
            kept = []
            for c in claims:
                if not is_meaningful(c):
                    continue
                if c.source_quote.strip() and c.source_quote.strip() not in abstract:
                    print(f"  dropped non-verbatim quote: {c.source_quote[:60]!r}...")
                    continue
                kept.append(c)
            return kept
        except APIError as e:
            is_transient = e.code in (429, 500, 503, 504) or "limit" in str(e).lower() or "exhausted" in str(e).lower()
            if is_transient and attempt < max_retries - 1:
                sleep_time = (2 ** attempt) + 1
                print(f"  Transient API error ({e}). Retrying in {sleep_time}s...")
                await asyncio.sleep(sleep_time)
                continue
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                sleep_time = (2 ** attempt) + 1
                print(f"  Unexpected error ({e}). Retrying in {sleep_time}s...")
                await asyncio.sleep(sleep_time)
                continue
            raise


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

        print(f"  Starting extraction for PMID {pmid} (stagger delay: {delay:.1f}s)...")
        try:
            claims = await extract_claims_async(abstract)
            results_map[pmid] = claims
            print(f"  Finished extraction for PMID {pmid}: found {len(claims)} claims")
        except Exception as e:
            print(f"  Error extracting PMID {pmid}: {e}")
            results_map[pmid] = []

    tasks = [worker(i, rec) for i, rec in enumerate(records)]
    await asyncio.gather(*tasks)
    return results_map
