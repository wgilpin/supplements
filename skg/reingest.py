"""Re-ingest one or more already-ingested supplements from scratch.

Forces a fresh extraction by clearing each supplement's cached claim files, then runs
the normal pipeline. Use this to pick up extractor fixes (e.g. quote verification /
quote cleaning) for supplements whose claims were cached under the old behaviour.

The extractor caches its output per PMID in data/claims/<pmid>.json and skips
re-extraction when a cache file exists, so without clearing the cache a re-run just
replays the stale results. The graph write is an upsert on claim_id, so re-ingestion
overwrites changed claims in place, adds any newly-recovered claims, and never
duplicates.

Cost / safety
-------------
  * One PubMed esearch per supplement to resolve its PMID list — abstracts come from
    the local cache when present.
  * One Gemini extraction pass over each supplement's abstract set.
  * Writes the live Kùzu graph. Kùzu is single-writer: make sure no other pipeline or
    Claude session is writing the database before running this.

Usage
-----
    uv run reingest astaxanthin
    uv run reingest astaxanthin curcumin --yes
"""

from __future__ import annotations

import argparse

from . import config, fetch, pipeline


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="reingest",
        description="Clear cached claims and re-ingest supplements from scratch.",
    )
    parser.add_argument("supplements", nargs="+", help="Supplement name(s) to re-ingest.")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    args = parser.parse_args()

    # Resolve PMID sets first so we can report the blast radius before touching anything.
    pmids_by_supplement: dict[str, list[str]] = {}
    for name in args.supplements:
        records = fetch.fetch_supplement(name)
        pmids_by_supplement[name] = [r["pmid"] for r in records]

    to_clear = [
        config.CLAIMS_DIR / f"{pmid}.json"
        for pmids in pmids_by_supplement.values()
        for pmid in pmids
    ]
    to_clear = [p for p in to_clear if p.exists()]

    total_pmids = sum(len(p) for p in pmids_by_supplement.values())
    if total_pmids == 0:
        print("No abstracts found for the requested supplement(s); nothing to do.")
        return 1

    print(
        f"Re-ingesting {', '.join(args.supplements)}: "
        f"{total_pmids} PMIDs, {len(to_clear)} cached claim files to clear."
    )
    print("This will re-extract via Gemini and upsert into the live Kùzu graph.")

    if not args.yes:
        if input("Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Aborted; no changes made.")
            return 1

    for path in to_clear:
        path.unlink()
    print(f"Cleared {len(to_clear)} cached claim files. Re-running pipeline...")

    pipeline.run(args.supplements)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
