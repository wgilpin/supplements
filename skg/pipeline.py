"""Wire steps 1-4 for a supplement and run the M1 pipeline end-to-end."""

from __future__ import annotations

import asyncio
import json
import sys

from . import config, fetch, graph, synonyms
from .extract import extract_claims, extract_claims_batch
from .normalise import load_synonyms
from .schema import Claim


def cached_claims(pmid: str, abstract: str) -> list[Claim]:
    """Extract claims for an abstract, caching the raw LLM output per PMID so
    re-runs (e.g. after a normalisation change) replay without re-calling the LLM.
    Delete data/claims/<pmid>.json to force re-extraction."""
    config.CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
    cpath = config.CLAIMS_DIR / f"{pmid}.json"
    if cpath.exists():
        return [Claim(**d) for d in json.loads(cpath.read_text())]
    claims = extract_claims(abstract)
    cpath.write_text(json.dumps([c.model_dump() for c in claims], indent=2))
    return claims


def run(supplement_names: list[str]) -> None:
    # Step 3a: ensure synonym dict covers every supplement (off the hot path).
    synonyms.build_all(supplement_names)
    syns = load_synonyms()

    conn = graph.connect()
    graph.init_schema(conn)

    # 1. Gather all records from PubMed for all supplements
    all_records = []
    for name in supplement_names:
        print(f"\n=== Fetching abstracts for {name} ===")
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
                print(f"  Failed to read cache for PMID {rec['pmid']} ({e}). Will re-extract.")
                uncached_records.append(rec)
        else:
            uncached_records.append(rec)

    # 4. Extract claims for uncached records in parallel with 1s pacing
    if uncached_records:
        print(f"\nExtracting claims for {len(uncached_records)} uncached PMIDs asynchronously...")
        extracted_map = asyncio.run(extract_claims_batch(uncached_records))

        # Cache the results
        for pmid, claims in extracted_map.items():
            cpath = config.CLAIMS_DIR / f"{pmid}.json"
            cpath.write_text(json.dumps([c.model_dump() for c in claims], indent=2))
            cached_results[pmid] = claims

    # 5. Load all claims sequentially into Kùzu database
    print("\nLoading claims into the database...")
    total_claims = 0
    for rec in deduped_records:
        claims = cached_results.get(rec["pmid"], [])
        loaded = 0
        for claim in claims:
            if graph.load_claim(conn, claim, rec["pmid"], syns) is not None:
                loaded += 1
        total_claims += loaded
        print(f"  PMID {rec['pmid']}: {loaded} claims loaded")

    print(f"\nLoaded {total_claims} claims.")
    print("Graph node counts:", graph.counts(conn))


if __name__ == "__main__":
    names = sys.argv[1:] or config.SUPPLEMENTS
    run(names)
