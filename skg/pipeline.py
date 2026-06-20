"""Wire steps 1-4 for a supplement and run the M1 pipeline end-to-end."""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import kuzu

from . import config, fetch, graph, synonyms
from .extract import extract_claims_batch
from .normalise import load_synonyms, add_ingested_compound, canonical_compound
from .schema import Claim

logger = logging.getLogger(__name__)


def run(supplement_names: list[str]) -> None:
    # Step 3a: ensure synonym dict covers every supplement (off the hot path).
    synonyms.build_all(supplement_names)
    syns = load_synonyms()

    conn = graph.connect()
    graph.init_schema(conn)

    # 1. Gather all records from PubMed for all supplements
    all_records = []
    for name in supplement_names:
        logger.info("=== Fetching abstracts for %s ===", name)
        records = fetch.fetch_supplement(name)
        all_records.extend(records)

    # 2. Dedup records by PMID to avoid duplicate work (e.g. if a PMID appears in multiple searches)
    seen_pmids = set()
    deduped_records = []
    for rec in all_records:
        if rec["pmid"] not in seen_pmids:
            seen_pmids.add(rec["pmid"])
            deduped_records.append(rec)

    # 3. Check which PMIDs are already cached and load them; identify uncached ones
    config.CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
    cached_results = {}
    uncached_records = []

    for rec in deduped_records:
        cpath = config.CLAIMS_DIR / f"{rec['pmid']}.json"
        if cpath.exists():
            try:
                cached_results[rec["pmid"]] = [Claim(**d) for d in json.loads(cpath.read_text())]
            except Exception as e:
                logger.warning("Failed to read cache for PMID %s (%s). Will re-extract.", rec["pmid"], e)
                uncached_records.append(rec)
        else:
            uncached_records.append(rec)

    # 4. Extract claims for uncached records in parallel with 1s pacing
    if uncached_records:
        logger.info("Extracting claims for %d uncached PMIDs asynchronously...", len(uncached_records))
        extracted_map = asyncio.run(extract_claims_batch(uncached_records))

        # Cache the results
        for pmid, claims in extracted_map.items():
            cpath = config.CLAIMS_DIR / f"{pmid}.json"
            cpath.write_text(json.dumps([c.model_dump() for c in claims], indent=2))
            cached_results[pmid] = claims

    # 5. Load all claims sequentially into Kùzu database
    logger.info("Loading claims into the database...")
    total_claims = 0
    for rec in deduped_records:
        claims = cached_results.get(rec["pmid"], [])
        loaded = 0
        for claim in claims:
            if graph.load_claim(conn, claim, rec["pmid"], syns) is not None:
                loaded += 1
        total_claims += loaded
        logger.info("PMID %s: %d claims loaded", rec["pmid"], loaded)

    logger.info("Loaded %d claims.", total_claims)
    logger.info("Graph node counts: %s", graph.counts(conn))

    for name in supplement_names:
        canonical_name = canonical_compound(name, syns)
        add_ingested_compound(canonical_name)


async def ingest_supplement_async(conn: kuzu.Connection, name: str) -> int:
    """Ingest a single supplement on demand.

    1. Look up PubChem synonyms and update synonyms.json.
    2. Search and fetch abstracts from PubMed.
    3. Extract claims via LLM batch asynchronously.
    4. Save claims to data/claims/ cached.
    5. Load claims into the Kuzu database.
    """
    logger.info("=== Ingesting %s on-demand ===", name)

    # 1. PubChem synonyms lookup
    synonyms.build_all([name])
    syns = load_synonyms()

    # 2. Fetch abstracts
    records = fetch.fetch_supplement(name)
    if not records:
        logger.info("No abstracts found for %s.", name)
        return 0

    seen_pmids = set()
    deduped_records = []
    for rec in records:
        if rec["pmid"] not in seen_pmids:
            seen_pmids.add(rec["pmid"])
            deduped_records.append(rec)

    config.CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
    cached_results = {}
    uncached_records = []

    for rec in deduped_records:
        cpath = config.CLAIMS_DIR / f"{rec['pmid']}.json"
        if cpath.exists():
            try:
                cached_results[rec["pmid"]] = [Claim(**d) for d in json.loads(cpath.read_text())]
            except Exception as e:
                logger.warning("Failed to read cache for PMID %s (%s). Will re-extract.", rec["pmid"], e)
                uncached_records.append(rec)
        else:
            uncached_records.append(rec)

    # 3. Extract claims for uncached records
    if uncached_records:
        logger.info("Extracting claims for %d uncached PMIDs asynchronously...", len(uncached_records))
        extracted_map = await extract_claims_batch(uncached_records)

        for pmid, claims in extracted_map.items():
            cpath = config.CLAIMS_DIR / f"{pmid}.json"
            cpath.write_text(json.dumps([c.model_dump() for c in claims], indent=2))
            cached_results[pmid] = claims

    # 4. Load claims into the database
    logger.info("Loading claims for %s into database...", name)
    loaded_claims = 0
    for rec in deduped_records:
        claims = cached_results.get(rec["pmid"], [])
        for claim in claims:
            if graph.load_claim(conn, claim, rec["pmid"], syns) is not None:
                loaded_claims += 1

    logger.info("Ingested %s: loaded %d claims.", name, loaded_claims)
    canonical_name = canonical_compound(name, syns)
    add_ingested_compound(canonical_name)
    return loaded_claims


if __name__ == "__main__":
    from .logging_config import setup_logging

    setup_logging()
    names = sys.argv[1:] or config.SUPPLEMENTS
    run(names)
