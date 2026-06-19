"""Wire steps 1-4 for a supplement and run the M1 pipeline end-to-end."""

from __future__ import annotations

import sys

from . import config, fetch, graph, synonyms
from .extract import extract_claims
from .normalise import load_synonyms


def run(supplement_names: list[str]) -> None:
    # Step 3a: ensure synonym dict covers every supplement (off the hot path).
    synonyms.build_all(supplement_names)
    syns = load_synonyms()

    conn = graph.connect()
    graph.init_schema(conn)

    total_claims = 0
    for name in supplement_names:
        print(f"\n=== {name} ===")
        records = fetch.fetch_supplement(name)
        for rec in records:
            claims = extract_claims(rec["abstract"])
            loaded = 0
            for claim in claims:
                if graph.load_claim(conn, claim, rec["pmid"], syns) is not None:
                    loaded += 1
            total_claims += loaded
            print(f"  PMID {rec['pmid']}: {loaded} claims")

    print(f"\nLoaded {total_claims} claims.")
    print("Graph node counts:", graph.counts(conn))


if __name__ == "__main__":
    names = sys.argv[1:] or config.SUPPLEMENTS
    run(names)
