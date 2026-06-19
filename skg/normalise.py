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


def get_ingested_compounds() -> set[str]:
    """Retrieve the set of fully ingested compounds from disk, initializing if necessary."""
    ingested_path = config.DATA_DIR / "ingested_compounds.json"
    if not ingested_path.exists():
        initial = {normalise_str(s) for s in config.SUPPLEMENTS + ["curcumin", "niacinamide"] if normalise_str(s)}
        save_ingested_compounds(initial)
        return initial
    try:
        data = json.loads(ingested_path.read_text())
        return set(data)
    except Exception:
        return {normalise_str(s) for s in config.SUPPLEMENTS + ["curcumin", "niacinamide"] if normalise_str(s)}


def save_ingested_compounds(compounds: set[str] | list[str]) -> None:
    """Save the set of fully ingested compounds to disk."""
    ingested_path = config.DATA_DIR / "ingested_compounds.json"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    ingested_path.write_text(json.dumps(sorted(list(compounds)), indent=2))


def add_ingested_compound(name: str) -> None:
    """Add a compound to the list of fully ingested compounds."""
    norm = normalise_str(name)
    if not norm:
        return
    compounds = get_ingested_compounds()
    if norm not in compounds:
        compounds.add(norm)
        save_ingested_compounds(compounds)


def is_compound_ingested(name: str) -> bool:
    """Check if a compound name has been fully ingested."""
    norm = normalise_str(name)
    if not norm:
        return False
    return norm in get_ingested_compounds()
