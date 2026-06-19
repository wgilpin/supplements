"""Step 2 — the one bio-bearing step: abstract -> list[Claim] via Gemini."""

from __future__ import annotations

from google import genai

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


def extract_claims(abstract: str) -> list[Claim]:
    """Extract claims from one abstract. Drops claims whose source_quote is not
    verbatim (the §6 QA backstop) and claims that say nothing."""
    resp = _get_client().models.generate_content(
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
