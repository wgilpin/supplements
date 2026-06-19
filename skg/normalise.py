"""Deterministic name normalisation + compound synonym lookup (spec §5)."""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

from . import config

_PUNCT = str.maketrans("", "", string.punctuation)


def normalise_str(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace (spec §5.3)."""
    s = s.lower().translate(_PUNCT)
    return re.sub(r"\s+", " ", s).strip()


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
