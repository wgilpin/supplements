"""Claim schema (also used as Gemini response_schema) + canonical claim id."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel

Direction = Literal["increases", "decreases", "none", "mixed", "modulates"]


class Claim(BaseModel):
    """One reified assertion extracted from an abstract (spec §4.1)."""

    compound: str
    target: str | None
    effect: str | None
    direction: Direction
    dose_text: str
    cohort_text: str
    model: str
    source_quote: str


def claim_id(compound: str, target: str | None, effect: str | None,
             direction: str, pmid: str) -> str:
    """Stable hash of (pmid + compound + target + effect + direction) — spec §4.4.

    Inputs should already be normalised so the same assertion hashes identically.
    """
    parts = [pmid, compound, target or "", effect or "", direction]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def is_meaningful(claim: Claim) -> bool:
    """A claim with neither target nor effect says nothing (spec §4.4)."""
    return bool((claim.target and claim.target.strip())
                or (claim.effect and claim.effect.strip()))
