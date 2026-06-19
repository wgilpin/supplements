"""Deterministic name normalisation + compound synonym lookup (spec §5)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import config


def normalise_str(s: str) -> str:
    """Lowercase, drop apostrophes, turn other punctuation into spaces, collapse
    whitespace (spec §5.3).

    Hyphens/slashes become spaces rather than vanishing, so "amine-modified" ->
    "amine modified" (not "aminemodified") — word boundaries are preserved for
    matching and legibility. Apostrophes are deleted so "Alzheimer's" ->
    "alzheimers" (not "alzheimer s")."""
    s = s.lower()
    s = re.sub(r"['’‘`]", "", s)      # apostrophes vanish
    s = re.sub(r"[^a-z0-9]+", " ", s)            # all other punctuation -> space
    return s.strip()


def load_synonyms(path: Path | None = None) -> dict[str, str]:
    """Map of normalised synonym -> canonical compound name."""
    path = path or config.SYNONYMS_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def canonical_compound(name: str, synonyms: dict[str, str]) -> str:
    """Resolve a compound name to its canonical form via the synonym dict,
    falling back to the normalised string if unknown."""
    norm = normalise_str(name)
    return synonyms.get(norm, norm)


def canonical_entity(name: str | None) -> str | None:
    """Targets/effects: rely on LLM canonical names + string-normalise backstop."""
    if name is None or not name.strip():
        return None
    return normalise_str(name)
