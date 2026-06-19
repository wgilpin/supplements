"""Step 4 — load normalised claims into Kùzu via Cypher (spec §4.4)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import kuzu

if TYPE_CHECKING:
    import pandas as pd

from . import config
from .normalise import canonical_compound, canonical_entity
from .schema import Claim, claim_id

DDL = [
    "CREATE NODE TABLE IF NOT EXISTS Compound (name STRING, PRIMARY KEY (name))",
    "CREATE NODE TABLE IF NOT EXISTS Target (name STRING, PRIMARY KEY (name))",
    "CREATE NODE TABLE IF NOT EXISTS Effect (name STRING, PRIMARY KEY (name))",
    """CREATE NODE TABLE IF NOT EXISTS Claim (
        id STRING,
        direction STRING,
        dose_text STRING,
        cohort_text STRING,
        model STRING,
        evidence_score INT64,
        source_pmid STRING,
        source_quote STRING,
        PRIMARY KEY (id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS HAS_CLAIM (FROM Compound TO Claim)",
    "CREATE REL TABLE IF NOT EXISTS ON_TARGET (FROM Claim TO Target)",
    "CREATE REL TABLE IF NOT EXISTS HAS_EFFECT (FROM Claim TO Effect)",
]


def connect(path: Path | None = None) -> kuzu.Connection:
    db = kuzu.Database(str(path or config.GRAPH_PATH))
    return kuzu.Connection(db)


def query_df(conn: kuzu.Connection, statement: str,
             params: dict[str, object] | None = None) -> "pd.DataFrame":
    """Run a read query and return its result as a DataFrame.

    Wraps Kùzu's `execute` (typed as QueryResult | list[QueryResult]) so callers
    get a single DataFrame without each site repeating the union narrowing.
    """
    result = conn.execute(statement, params or {})
    if isinstance(result, list):
        result = result[0]
    return result.get_as_df()


def init_schema(conn: kuzu.Connection) -> None:
    for stmt in DDL:
        conn.execute(stmt)


def evidence_score(model: str) -> int:
    """Map a study-type string to a 1-5 score (spec §7)."""
    m = (model or "").lower()
    for key, score in config.EVIDENCE_RUBRIC.items():
        if key in m:
            return score
    return config.DEFAULT_EVIDENCE_SCORE


def load_claim(conn: kuzu.Connection, claim: Claim, pmid: str,
               synonyms: dict[str, str]) -> str | None:
    """Normalise + MERGE one claim and wire it to its entities. Returns the
    claim id, or None if the claim has no target/effect after normalisation."""
    compound = canonical_compound(claim.compound, synonyms)
    target = canonical_entity(claim.target)
    effect = canonical_entity(claim.effect)
    if not target and not effect:
        return None

    cid = claim_id(compound, target, effect, claim.direction, pmid)

    conn.execute("MERGE (c:Compound {name: $name})", {"name": compound})
    conn.execute(
        """MERGE (cl:Claim {id: $id})
           SET cl.direction = $direction, cl.dose_text = $dose,
               cl.cohort_text = $cohort, cl.model = $model,
               cl.evidence_score = $score, cl.source_pmid = $pmid,
               cl.source_quote = $quote""",
        {"id": cid, "direction": claim.direction, "dose": claim.dose_text,
         "cohort": claim.cohort_text, "model": claim.model,
         "score": evidence_score(claim.model), "pmid": pmid,
         "quote": claim.source_quote},
    )
    conn.execute(
        """MATCH (c:Compound {name: $cn}), (cl:Claim {id: $id})
           MERGE (c)-[:HAS_CLAIM]->(cl)""",
        {"cn": compound, "id": cid},
    )
    if target:
        conn.execute("MERGE (t:Target {name: $name})", {"name": target})
        conn.execute(
            """MATCH (cl:Claim {id: $id}), (t:Target {name: $tn})
               MERGE (cl)-[:ON_TARGET]->(t)""",
            {"id": cid, "tn": target},
        )
    if effect:
        conn.execute("MERGE (e:Effect {name: $name})", {"name": effect})
        conn.execute(
            """MATCH (cl:Claim {id: $id}), (e:Effect {name: $en})
               MERGE (cl)-[:HAS_EFFECT]->(e)""",
            {"id": cid, "en": effect},
        )
    return cid


def counts(conn: kuzu.Connection) -> dict[str, int]:
    out = {}
    for label in ("Compound", "Target", "Effect", "Claim"):
        df = query_df(conn, f"MATCH (n:{label}) RETURN count(n) AS n")
        out[label] = int(df["n"][0])
    return out
