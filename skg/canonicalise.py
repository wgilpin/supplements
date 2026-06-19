"""Step 3b — LLM entity canonicalisation / dedup (spec §5.4).

The residual duplicates that survive string-normalisation need *biological*
judgment to merge (is "autophagy" == "autophagic flux"?). The operator is not a
biologist, so that judgment goes to the LLM; the human reviews the proposed
clusters by reading. Two phases:

  python -m skg.canonicalise propose   # LLM clusters -> data/canonical_map.json
  # ...human reads/edits the map...
  python -m skg.canonicalise apply     # rewrite the graph per the map

`apply_map` is separated from the LLM call so the merge logic is testable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import kuzu
from google import genai
from pydantic import BaseModel

from . import config, graph
from .normalise import normalise_str

CANONICAL_MAP_PATH = config.DATA_DIR / "canonical_map.json"

# How each entity type connects to claims (label -> the rel and its direction).
LABELS = {
    "Compound": ("HAS_CLAIM", "from"),   # (Compound)-[:HAS_CLAIM]->(Claim)
    "Target": ("ON_TARGET", "to"),       # (Claim)-[:ON_TARGET]->(Target)
    "Effect": ("HAS_EFFECT", "to"),      # (Claim)-[:HAS_EFFECT]->(Effect)
}


# --- LLM output schema ---------------------------------------------------------
class Cluster(BaseModel):
    canonical: str          # the name all members should collapse to
    members: list[str]      # existing node names that are the same entity


class Flag(BaseModel):
    name: str               # an existing node name that looks invalid/mistyped
    reason: str             # short human-readable reason (read, don't trust blindly)


class LabelProposal(BaseModel):
    clusters: list[Cluster]
    flags: list[Flag]


PROMPT = """You are de-duplicating a list of {label} names from a biomedical \
knowledge graph. The names are already lowercased with punctuation stripped, so \
some are mangled (e.g. concatenated words).

Return JSON with two keys:
- "clusters": groups of names in the list that refer to the SAME {label}. For each \
group give a "canonical" (the best single name for the entity, prefer a clean \
standard full name) and "members" (every name from the list in that group, \
including the canonical if it appears). Only include a cluster if it MERGES \
two or more names OR renames one name to a cleaner canonical. Do not cluster \
entities that are merely related but distinct (e.g. a neurotransmitter and its \
receptor are different).
- "flags": names that are not valid {label} entities at all (wrong type, junk, \
a process listed as a molecular target, etc.), each with a short "reason".

NAMES:
{names}
"""

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def distinct_names(conn: kuzu.Connection, label: str) -> list[str]:
    df = conn.execute(f"MATCH (n:{label}) RETURN n.name AS name ORDER BY name").get_as_df()
    return df["name"].tolist()


def propose_label(conn: kuzu.Connection, label: str) -> LabelProposal:
    names = distinct_names(conn, label)
    if not names:
        return LabelProposal(clusters=[], flags=[])
    resp = _get_client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=PROMPT.format(label=label, names="\n".join(names)),
        config={"response_mime_type": "application/json",
                "response_schema": LabelProposal},
    )
    return resp.parsed or LabelProposal(clusters=[], flags=[])


def propose(conn: kuzu.Connection | None = None) -> dict:
    conn = conn or graph.connect()
    out = {}
    for label in LABELS:
        prop = propose_label(conn, label)
        out[label] = prop.model_dump()
        print(f"{label}: {len(prop.clusters)} clusters, {len(prop.flags)} flags")
    CANONICAL_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANONICAL_MAP_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nWrote proposal -> {CANONICAL_MAP_PATH}")
    print("Review it (read the clusters), then run: apply")
    return out


def _node_exists(conn: kuzu.Connection, label: str, name: str) -> bool:
    df = conn.execute(
        f"MATCH (n:{label} {{name: $n}}) RETURN count(n) AS n", {"n": name}
    ).get_as_df()
    return int(df["n"][0]) > 0


def merge_into(conn: kuzu.Connection, label: str, canonical: str,
               members: list[str]) -> int:
    """Collapse the cluster's existing nodes into a single canonical node.

    Dedup only — never beautifies. Names are normalised to match the stored PK
    convention, and the surviving node is always one that already exists (so a
    pretty-but-different canonical never spawns a new node). Single-node clusters
    are no-ops. Returns the number of nodes merged away."""
    rel, direction = LABELS[label]
    norm_canon = normalise_str(canonical)

    # Distinct existing nodes this cluster refers to (members + canonical itself).
    candidates: list[str] = []
    for raw in [*members, canonical]:
        nm = normalise_str(raw)
        if nm not in candidates and _node_exists(conn, label, nm):
            candidates.append(nm)
    if len(candidates) < 2:
        return 0  # nothing to merge (pure rename or lone node)

    canon = norm_canon if norm_canon in candidates else candidates[0]
    merged = 0
    for m in candidates:
        if m == canon:
            continue
        if direction == "to":  # (Claim)-[rel]->(entity)
            conn.execute(
                f"""MATCH (cl:Claim)-[:{rel}]->(b:{label} {{name: $m}}),
                          (a:{label} {{name: $c}})
                    MERGE (cl)-[:{rel}]->(a)""",
                {"m": m, "c": canon},
            )
        else:  # (entity)-[rel]->(Claim)
            conn.execute(
                f"""MATCH (b:{label} {{name: $m}})-[:{rel}]->(cl:Claim),
                          (a:{label} {{name: $c}})
                    MERGE (a)-[:{rel}]->(cl)""",
                {"m": m, "c": canon},
            )
        conn.execute(f"MATCH (b:{label} {{name: $m}}) DETACH DELETE b", {"m": m})
        merged += 1
    return merged


def apply_map(conn: kuzu.Connection, mapping: dict) -> dict[str, int]:
    """Apply a reviewed proposal. `mapping` is {label: {"clusters": [...], ...}}.
    Returns merged-node counts per label. Flags are advisory — not acted on."""
    results = {}
    for label, payload in mapping.items():
        if label not in LABELS:
            continue
        total = 0
        for cluster in payload.get("clusters", []):
            total += merge_into(conn, label, cluster["canonical"], cluster["members"])
        results[label] = total
    return results


def apply(conn: kuzu.Connection | None = None,
          map_path: Path | None = None) -> dict[str, int]:
    conn = conn or graph.connect()
    map_path = map_path or CANONICAL_MAP_PATH
    if not map_path.exists():
        print(f"No proposal at {map_path}. Run 'propose' first.")
        return {}
    mapping = json.loads(map_path.read_text())
    results = apply_map(conn, mapping)
    print("Merged nodes:", results)
    print("Graph node counts:", graph.counts(conn))
    return results


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "propose"
    if cmd == "propose":
        propose()
    elif cmd == "apply":
        apply()
    else:
        print("usage: python -m skg.canonicalise [propose|apply]")
