"""Extraction-precision check (spec §9 acceptance).

Two modes:
  python -m eval.score sample [N]   # dump N random claims for hand-labelling
  python -m eval.score score        # report precision over labelled sample

Labelling: open eval/sample.jsonl and set "supported" to true/false for each row
by reading whether source_quote actually supports the structured claim. This is
reading comprehension, not biology (spec §4 principle 4).
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

from skg import graph

SAMPLE_PATH = Path(__file__).resolve().parent / "sample.jsonl"


def dump_sample(n: int) -> None:
    conn = graph.connect()
    df = conn.execute(
        """MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)
           OPTIONAL MATCH (cl)-[:ON_TARGET]->(t:Target)
           OPTIONAL MATCH (cl)-[:HAS_EFFECT]->(e:Effect)
           RETURN c.name AS compound, t.name AS target, e.name AS effect,
                  cl.direction AS direction, cl.source_pmid AS pmid,
                  cl.source_quote AS source_quote"""
    ).get_as_df()
    def clean(v):
        if isinstance(v, float) and math.isnan(v):
            return None
        return v

    rows = [{k: clean(v) for k, v in r.items()} for r in df.to_dict("records")]
    random.shuffle(rows)
    rows = rows[:n]
    with SAMPLE_PATH.open("w") as f:
        for r in rows:
            r["supported"] = None  # <- fill in by hand: true / false
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(rows)} claims -> {SAMPLE_PATH}")
    print('Hand-label each row\'s "supported" as true/false, then run: score')


def score() -> None:
    rows = [json.loads(l) for l in SAMPLE_PATH.read_text().splitlines() if l.strip()]
    labelled = [r for r in rows if r.get("supported") in (True, False)]
    if not labelled:
        print("No labelled rows. Set \"supported\": true/false in sample.jsonl first.")
        return
    supported = sum(1 for r in labelled if r["supported"] is True)
    print(f"Labelled: {len(labelled)}/{len(rows)}")
    print(f"Precision: {supported}/{len(labelled)} = {supported / len(labelled):.0%}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    if cmd == "sample":
        dump_sample(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        score()
