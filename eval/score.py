"""Extraction-precision check (spec §9 acceptance).

  python -m eval.score sample [N]   # dump N random claims (writes .jsonl + .csv)
  python -m eval.score csv          # regenerate sample.csv from sample.jsonl
  python -m eval.score score        # report precision over the labelled sample

Labelling workflow: open eval/sample.csv in Google Sheets (CSV imports cleanly;
JSONL does not), fill the `supported` column with TRUE/FALSE for each row by
reading whether source_quote actually supports the structured claim — reading
comprehension, not biology (spec §4 principle 4). Export back to CSV, then run
`score`.
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
from pathlib import Path

from skg import graph

HERE = Path(__file__).resolve().parent
JSONL_PATH = HERE / "sample.jsonl"
CSV_PATH = HERE / "sample.csv"

# Column order chosen for readability when labelling in a spreadsheet.
FIELDS = ["pmid", "compound", "target", "effect", "direction", "source_quote",
          "supported"]

_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


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
    with JSONL_PATH.open("w") as f:
        for r in rows:
            r["supported"] = None  # fill by hand: true / false
            f.write(json.dumps(r) + "\n")
    write_csv(rows)
    print(f"Wrote {len(rows)} claims -> {JSONL_PATH} and {CSV_PATH}")
    print("Label the `supported` column in sample.csv (TRUE/FALSE), then run: score")


def write_csv(rows: list[dict]) -> None:
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            row = dict(r)
            if row.get("supported") is None:
                row["supported"] = ""
            w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS})


def jsonl_to_csv() -> None:
    rows = [json.loads(l) for l in JSONL_PATH.read_text().splitlines() if l.strip()]
    write_csv(rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")


def _parse_supported(v) -> bool | None:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def _load_labelled() -> list[dict]:
    """Prefer the CSV (the spreadsheet round-trip); fall back to JSONL."""
    if CSV_PATH.exists():
        with CSV_PATH.open(newline="") as f:
            return list(csv.DictReader(f))
    return [json.loads(l) for l in JSONL_PATH.read_text().splitlines() if l.strip()]


def score() -> None:
    rows = _load_labelled()
    verdicts = [(r, _parse_supported(r.get("supported"))) for r in rows]
    labelled = [(r, v) for r, v in verdicts if v is not None]
    if not labelled:
        print("No labelled rows. Fill `supported` (TRUE/FALSE) in sample.csv first.")
        return
    supported = sum(1 for _, v in labelled if v)
    print(f"Labelled: {len(labelled)}/{len(rows)}")
    print(f"Precision: {supported}/{len(labelled)} = {supported / len(labelled):.0%}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    if cmd == "sample":
        dump_sample(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif cmd == "csv":
        jsonl_to_csv()
    else:
        score()
