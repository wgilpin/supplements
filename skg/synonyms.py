"""Build the compound synonym dict from PubChem (spec §5.1).

Runs once per supplement, off the abstract-ingestion hot path.
"""

from __future__ import annotations

import json
import logging

import httpx

from . import config
from .normalise import normalise_str

logger = logging.getLogger(__name__)


def fetch_pubchem_synonyms(name: str) -> list[str]:
    """Return PubChem's synonym list for a compound name."""
    r = httpx.get(config.PUBCHEM_SYNONYMS_URL.format(name), timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["InformationList"]["Information"][0]["Synonym"]


def add_supplement(name: str, synonyms: dict[str, str] | None = None) -> dict[str, str]:
    """Add `name` (the canonical, as-typed form) and all its PubChem synonyms
    to the synonym map, each keyed by its normalised form -> canonical `name`."""
    synonyms = synonyms if synonyms is not None else _load()
    # The typed form is always canonical, even if PubChem prefers another.
    synonyms[normalise_str(name)] = name
    try:
        for syn in fetch_pubchem_synonyms(name):
            synonyms[normalise_str(syn)] = name
    except (httpx.HTTPError, KeyError, IndexError) as e:
        logger.warning("PubChem lookup failed for %r: %s", name, e)
    return synonyms


def _load() -> dict[str, str]:
    if config.SYNONYMS_PATH.exists():
        return json.loads(config.SYNONYMS_PATH.read_text())
    return {}


def _save(synonyms: dict[str, str]) -> None:
    config.SYNONYMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SYNONYMS_PATH.write_text(json.dumps(synonyms, indent=2, sort_keys=True))


def build_all(names: list[str]) -> None:
    """Build the synonym dict for a list of supplements and persist it."""
    syns = _load()
    for name in names:
        logger.info("PubChem synonyms: %s", name)
        add_supplement(name, syns)
    _save(syns)
    logger.info("Saved %d synonym entries -> %s", len(syns), config.SYNONYMS_PATH)


if __name__ == "__main__":
    from .logging_config import setup_logging

    setup_logging()
    build_all(config.SUPPLEMENTS)
