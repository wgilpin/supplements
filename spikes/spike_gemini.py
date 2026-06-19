"""Spike: Gemini 3.5 Flash structured JSON extraction (spec §8 / §6)."""

import os

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel

load_dotenv()


class Claim(BaseModel):
    compound: str
    target: str | None
    effect: str | None
    direction: str  # increases | decreases | none | mixed
    dose_text: str
    cohort_text: str
    model: str
    source_quote: str


ABSTRACT = (
    "Taurine supplementation (1500 mg/day for 8 weeks) significantly reduced "
    "anxiety scores in a randomized controlled trial of 60 healthy adults, an "
    "effect attributed to modulation of GABA-A receptor activity. No change in "
    "blood pressure was observed."
)

PROMPT = f"""Extract biological claims from this abstract as JSON.
Rules:
- Use canonical full names for target/effect; no abbreviations.
- source_quote MUST be a verbatim sentence from the abstract.
- direction is one of: increases | decreases | none | mixed.
- 'none' (no significant effect) is a valid claim — keep it.
- If no dose or cohort is stated, use "".

ABSTRACT:
{ABSTRACT}
"""

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
resp = client.models.generate_content(
    model="gemini-flash-latest",
    contents=PROMPT,
    config={
        "response_mime_type": "application/json",
        "response_schema": list[Claim],
    },
)
print("raw text:\n", resp.text)
claims: list[Claim] = resp.parsed
print("\nparsed claim count:", len(claims))
for c in claims:
    in_abstract = c.source_quote in ABSTRACT
    print(f"\n- {c.compound} -> target={c.target} effect={c.effect} dir={c.direction}")
    print(f"  quote verbatim? {in_abstract}: {c.source_quote!r}")
