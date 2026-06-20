"""Milestone 2 — the query layer (spec §9).

Fixed, parameterised, hand-written Cypher over the M1 graph. No LLM here: the
chat router (``skg.router``) only chooses which of these functions to call and
with what parameters. Every function takes a read connection first (functional
style), filters on ``evidence_score`` where noted, and returns typed pydantic
rows — DataFrames never leak out of this module.
"""

from __future__ import annotations

import logging
from typing import Literal

import kuzu
import pandas as pd
from pydantic import BaseModel

from . import graph
from .normalise import normalise_str
from .schema import Direction

logger = logging.getLogger(__name__)


# --- result models -------------------------------------------------------------

class ClaimRow(BaseModel):
    compound: str
    target: str | None
    effect: str | None
    direction: Direction
    evidence_score: int
    model: str
    dose_text: str          # shown raw (spec §4.3)
    cohort_text: str        # shown raw
    source_pmid: str
    source_quote: str       # the QA quote — always displayed


class ClaimGroup(BaseModel):
    """One or more ClaimRows that are the SAME evidence (same quote/pmid/effect/
    direction/…) and differ only by compound — merged for display."""

    compounds: list[str]
    target: str | None
    effect: str | None
    direction: Direction
    evidence_score: int
    model: str
    dose_text: str
    cohort_text: str
    source_pmid: str
    source_quote: str


class BridgeRow(BaseModel):
    shared_target: str
    other_compound: str
    effect: str | None
    direction: Direction
    evidence_score: int
    source_pmid: str
    source_quote: str


class ContradictionRow(BaseModel):
    compound: str
    effect: str
    direction_a: Direction
    direction_b: Direction
    pmid_a: str
    pmid_b: str


QueryName = Literal["compound", "effect", "target", "bridge",
                    "contradictions", "search", "intersection",
                    "list_supplements", "unknown"]


class SupplementRow(BaseModel):
    """One supplement the graph has data on (a fully ingested compound)."""

    name: str


class QueryRequest(BaseModel):
    """What the router resolves a natural-language question into."""

    query: QueryName
    entity: str | None = None
    entities: list[str] = []
    min_evidence: int = 1


QueryResult = (list[ClaimRow] | list[BridgeRow] | list[ContradictionRow]
               | list[SupplementRow])


# --- helpers -------------------------------------------------------------------

def _opt(value: object) -> str | None:
    """Coerce a possibly-null cell (Cypher null surfaces as pandas NaN) to
    ``str | None`` — see the Phase-0 OPTIONAL MATCH finding."""
    if value is None or pd.isna(value):
        return None
    return str(value)


def _claim_rows(df: pd.DataFrame) -> list[ClaimRow]:
    return [
        ClaimRow(
            compound=str(r["compound"]),
            target=_opt(r["target"]),
            effect=_opt(r["effect"]),
            direction=r["direction"],
            evidence_score=int(r["evidence_score"]),
            model=str(r["model"]),
            dose_text=str(r["dose_text"]),
            cohort_text=str(r["cohort_text"]),
            source_pmid=str(r["source_pmid"]),
            source_quote=str(r["source_quote"]),
        )
        for r in df.to_dict("records")
    ]


def group_claims(rows: list[ClaimRow]) -> list[ClaimGroup]:
    """Merge claims that share identical evidence (everything except the compound)
    into one group listing the compounds. Order is preserved (so the query's
    evidence-DESC ordering carries through)."""
    groups: dict[tuple[object, ...], ClaimGroup] = {}
    for r in rows:
        key = (r.target, r.effect, r.direction, r.evidence_score, r.model,
               r.dose_text, r.cohort_text, r.source_pmid, r.source_quote)
        existing = groups.get(key)
        if existing is None:
            groups[key] = ClaimGroup(compounds=[r.compound], target=r.target,
                                     effect=r.effect, direction=r.direction,
                                     evidence_score=r.evidence_score, model=r.model,
                                     dose_text=r.dose_text, cohort_text=r.cohort_text,
                                     source_pmid=r.source_pmid,
                                     source_quote=r.source_quote)
        elif r.compound not in existing.compounds:
            existing.compounds.append(r.compound)
    return list(groups.values())


_CLAIM_PROJECTION = """
       c.name AS compound, t.name AS target, e.name AS effect,
       cl.direction AS direction, cl.evidence_score AS evidence_score,
       cl.model AS model, cl.dose_text AS dose_text,
       cl.cohort_text AS cohort_text, cl.source_pmid AS source_pmid,
       cl.source_quote AS source_quote
"""

_LABEL_BY_KIND = {"compound": "Compound", "target": "Target", "effect": "Effect"}


# --- list helpers (also feed the router its valid-entity set) ------------------

def _distinct(conn: kuzu.Connection, label: str) -> list[str]:
    df = graph.query_df(
        conn, f"MATCH (n:{label}) RETURN n.name AS name ORDER BY name"
    )
    return [str(n) for n in df["name"].tolist()]


def list_compounds(conn: kuzu.Connection) -> list[str]:
    return _distinct(conn, "Compound")


def list_targets(conn: kuzu.Connection) -> list[str]:
    return _distinct(conn, "Target")


def list_effects(conn: kuzu.Connection) -> list[str]:
    return _distinct(conn, "Effect")


def list_supplements(conn: kuzu.Connection,
                     ingested: set[str] | None = None) -> list[SupplementRow]:
    """The supplements we actually have data on: graph Compound nodes that have
    been fully ingested (not merely *mentioned* in another compound's evidence).
    Names are returned in their graph display form, alphabetically."""
    if ingested is None:
        from .normalise import get_ingested_compounds
        ingested = get_ingested_compounds()
    return [
        SupplementRow(name=n)
        for n in list_compounds(conn)
        if normalise_str(n) in ingested
    ]


# --- the four essential queries ------------------------------------------------

def claims_for_compound(conn: kuzu.Connection, compound: str,
                        min_evidence: int = 1) -> list[ClaimRow]:
    """What does this compound do? (spec §9)"""
    logger.info("claims_for_compound(%r, min_evidence=%d)", compound, min_evidence)
    df = graph.query_df(
        conn,
        f"""MATCH (c:Compound {{name: $name}})-[:HAS_CLAIM]->(cl:Claim)
            WHERE cl.evidence_score >= $min_ev
            OPTIONAL MATCH (cl)-[:ON_TARGET]->(t:Target)
            OPTIONAL MATCH (cl)-[:HAS_EFFECT]->(e:Effect)
            RETURN {_CLAIM_PROJECTION}
            ORDER BY evidence_score DESC""",
        {"name": compound, "min_ev": min_evidence},
    )
    return _claim_rows(df)


def claims_for_effect(conn: kuzu.Connection, effect: str,
                      min_evidence: int = 1) -> list[ClaimRow]:
    """What affects this effect, and via what target? (spec §9)"""
    logger.info("claims_for_effect(%r, min_evidence=%d)", effect, min_evidence)
    df = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:HAS_EFFECT]->(e:Effect {{name: $name}})
            WHERE cl.evidence_score >= $min_ev
            OPTIONAL MATCH (cl)-[:ON_TARGET]->(t:Target)
            RETURN {_CLAIM_PROJECTION}
            ORDER BY evidence_score DESC""",
        {"name": effect, "min_ev": min_evidence},
    )
    return _claim_rows(df)


def claims_for_target(conn: kuzu.Connection, target: str,
                      min_evidence: int = 1) -> list[ClaimRow]:
    """What acts on this target? (spec §9)"""
    logger.info("claims_for_target(%r, min_evidence=%d)", target, min_evidence)
    df = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:ON_TARGET]->(t:Target {{name: $name}})
            WHERE cl.evidence_score >= $min_ev
            OPTIONAL MATCH (cl)-[:HAS_EFFECT]->(e:Effect)
            RETURN {_CLAIM_PROJECTION}
            ORDER BY evidence_score DESC""",
        {"name": target, "min_ev": min_evidence},
    )
    return _claim_rows(df)


def shared_target_bridge(conn: kuzu.Connection, compound: str,
                         min_evidence: int = 1) -> list[BridgeRow]:
    """The multi-hop query that earns the word 'graph' (spec §9): other
    compounds that share a target with this one, and the effects each reaches."""
    logger.info("shared_target_bridge(%r, min_evidence=%d)", compound, min_evidence)
    df = graph.query_df(
        conn,
        """MATCH (c1:Compound {name: $name})-[:HAS_CLAIM]->(:Claim)-[:ON_TARGET]->(t:Target)
                 <-[:ON_TARGET]-(cl2:Claim)<-[:HAS_CLAIM]-(c2:Compound)
           WHERE c2.name <> $name AND cl2.evidence_score >= $min_ev
           OPTIONAL MATCH (cl2)-[:HAS_EFFECT]->(e:Effect)
           RETURN t.name AS shared_target, c2.name AS other_compound,
                  e.name AS effect, cl2.direction AS direction,
                  cl2.evidence_score AS evidence_score,
                  cl2.source_pmid AS source_pmid, cl2.source_quote AS source_quote
           ORDER BY evidence_score DESC""",
        {"name": compound, "min_ev": min_evidence},
    )
    return [
        BridgeRow(
            shared_target=str(r["shared_target"]),
            other_compound=str(r["other_compound"]),
            effect=_opt(r["effect"]),
            direction=r["direction"],
            evidence_score=int(r["evidence_score"]),
            source_pmid=str(r["source_pmid"]),
            source_quote=str(r["source_quote"]),
        )
        for r in df.to_dict("records")
    ]


# --- full-text quote search ----------------------------------------------------

def search_claims(conn: kuzu.Connection, term: str,
                  min_evidence: int = 1, limit: int = 50) -> list[ClaimRow]:
    """Case-insensitive substring search over claim source quotes (and compound
    names). Catches things mentioned in the evidence text that aren't captured as
    a queryable entity (e.g. a disease named only as an abbreviation in a quote)."""
    t = term.strip().lower()
    logger.info("search_claims(%r, min_evidence=%d)", term, min_evidence)
    if not t:
        return []
    df = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)
            WHERE cl.evidence_score >= $min_ev
              AND (lower(cl.source_quote) CONTAINS $t OR lower(c.name) CONTAINS $t)
            OPTIONAL MATCH (cl)-[:ON_TARGET]->(t2:Target)
            OPTIONAL MATCH (cl)-[:HAS_EFFECT]->(e:Effect)
            RETURN {_CLAIM_PROJECTION.replace("t.name AS target", "t2.name AS target")}
            ORDER BY evidence_score DESC
            LIMIT $lim""",
        {"t": t, "min_ev": min_evidence, "lim": limit},
    )
    return _claim_rows(df)


# --- contradictions (extra; spec §4.1) -----------------------------------------

def contradictions(conn: kuzu.Connection,
                   effect: str | None = None) -> list[ContradictionRow]:
    """Same compound→effect asserted both 'increases' and 'decreases' — a finding
    to surface, not a bug (spec §4.1)."""
    logger.info("contradictions(effect=%r)", effect)
    clause = "AND e.name = $effect" if effect is not None else ""
    df = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl_a:Claim)-[:HAS_EFFECT]->(e:Effect),
                  (c)-[:HAS_CLAIM]->(cl_b:Claim)-[:HAS_EFFECT]->(e)
            WHERE cl_a.direction = 'increases' AND cl_b.direction = 'decreases' {clause}
            RETURN c.name AS compound, e.name AS effect,
                   cl_a.direction AS direction_a, cl_b.direction AS direction_b,
                   cl_a.source_pmid AS pmid_a, cl_b.source_pmid AS pmid_b""",
        {"effect": effect} if effect is not None else {},
    )
    return [
        ContradictionRow(
            compound=str(r["compound"]),
            effect=str(r["effect"]),
            direction_a=r["direction_a"],
            direction_b=r["direction_b"],
            pmid_a=str(r["pmid_a"]),
            pmid_b=str(r["pmid_b"]),
        )
        for r in df.to_dict("records")
    ]


# --- entity resolution (router params -> real node names) ----------------------

def resolve_entity(conn: kuzu.Connection, raw: str,
                   kind: Literal["compound", "target", "effect"]) -> str | None:
    """Map a free-text name to a real node name: normalise, then exact match,
    then synonym lookup, then substring fallback (so "GABA" -> "gaba a receptor").
    KISS — no fuzzy library. Returns None if nothing plausible matches."""
    norm = normalise_str(raw)
    if not norm:
        return None
    # Compare on normalised forms: M1 stores compound names un-normalised
    # (e.g. "N-acetyl cysteine"), so we must normalise both sides to match.
    by_norm = {normalise_str(n): n for n in _distinct(conn, _LABEL_BY_KIND[kind])}
    if norm in by_norm:
        return by_norm[norm]

    # Try synonym resolution for compounds
    if kind == "compound":
        from .normalise import load_synonyms
        synonyms = load_synonyms()
        resolved_name = synonyms.get(norm)
        if resolved_name:
            resolved_norm = normalise_str(resolved_name)
            if resolved_norm in by_norm:
                return by_norm[resolved_norm]

    hits = [orig for nn, orig in by_norm.items() if norm in nn or nn in norm]
    if hits:
        # Prefer the shortest match (closest to the typed term).
        return min(hits, key=len)
    logger.info("resolve_entity: no match for %r (%s)", raw, kind)
    return None


def claims_for_compound_intersection(conn: kuzu.Connection, compounds: list[str],
                                    min_evidence: int = 1) -> list[ClaimRow]:
    """Find claims where multiple compounds share the same effects or targets."""
    logger.info("claims_for_compound_intersection(%r, min_evidence=%d)", compounds, min_evidence)
    if not compounds:
        return []

    # 1. Shared effects
    df_effects = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:HAS_EFFECT]->(e:Effect)
            WHERE c.name IN $compounds AND cl.evidence_score >= $min_ev
            WITH e, count(distinct c) AS num_compounds
            WHERE num_compounds = $expected_count
            MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:HAS_EFFECT]->(e)
            WHERE c.name IN $compounds AND cl.evidence_score >= $min_ev
            OPTIONAL MATCH (cl)-[:ON_TARGET]->(t:Target)
            RETURN {_CLAIM_PROJECTION}
            ORDER BY e.name, evidence_score DESC""",
        {"compounds": compounds, "min_ev": min_evidence, "expected_count": len(compounds)}
    )

    # 2. Shared targets
    df_targets = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:ON_TARGET]->(t:Target)
            WHERE c.name IN $compounds AND cl.evidence_score >= $min_ev
            WITH t, count(distinct c) AS num_compounds
            WHERE num_compounds = $expected_count
            MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:ON_TARGET]->(t)
            WHERE c.name IN $compounds AND cl.evidence_score >= $min_ev
            OPTIONAL MATCH (cl)-[:HAS_EFFECT]->(e:Effect)
            RETURN {_CLAIM_PROJECTION}
            ORDER BY t.name, evidence_score DESC""",
        {"compounds": compounds, "min_ev": min_evidence, "expected_count": len(compounds)}
    )

    # Combine and de-duplicate claims if any overlap
    seen_keys = set()
    combined_rows = []

    for r in _claim_rows(df_effects) + _claim_rows(df_targets):
        key = (r.compound, r.target, r.effect, r.source_pmid, r.source_quote)
        if key not in seen_keys:
            seen_keys.add(key)
            combined_rows.append(r)

    return combined_rows


def claims_for_effect_intersection(conn: kuzu.Connection, effects: list[str],
                                  min_evidence: int = 1) -> list[ClaimRow]:
    """Find claims for compounds that affect all the specified effects."""
    logger.info("claims_for_effect_intersection(%r, min_evidence=%d)", effects, min_evidence)
    if not effects:
        return []

    df = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:HAS_EFFECT]->(e:Effect)
            WHERE e.name IN $effects AND cl.evidence_score >= $min_ev
            WITH c, count(distinct e) AS num_effects
            WHERE num_effects = $expected_count
            MATCH (c)-[:HAS_CLAIM]->(cl:Claim)-[:HAS_EFFECT]->(e:Effect)
            WHERE e.name IN $effects AND cl.evidence_score >= $min_ev
            OPTIONAL MATCH (cl)-[:ON_TARGET]->(t:Target)
            RETURN {_CLAIM_PROJECTION}
            ORDER BY c.name, evidence_score DESC""",
        {"effects": effects, "min_ev": min_evidence, "expected_count": len(effects)}
    )
    return _claim_rows(df)


def claims_for_target_intersection(conn: kuzu.Connection, targets: list[str],
                                  min_evidence: int = 1) -> list[ClaimRow]:
    """Find claims for compounds that act on all the specified targets."""
    logger.info("claims_for_target_intersection(%r, min_evidence=%d)", targets, min_evidence)
    if not targets:
        return []

    df = graph.query_df(
        conn,
        f"""MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:ON_TARGET]->(t:Target)
            WHERE t.name IN $targets AND cl.evidence_score >= $min_ev
            WITH c, count(distinct t) AS num_targets
            WHERE num_targets = $expected_count
            MATCH (c)-[:HAS_CLAIM]->(cl:Claim)-[:ON_TARGET]->(t:Target)
            WHERE t.name IN $targets AND cl.evidence_score >= $min_ev
            OPTIONAL MATCH (cl)-[:HAS_EFFECT]->(e:Effect)
            RETURN {_CLAIM_PROJECTION}
            ORDER BY c.name, evidence_score DESC""",
        {"targets": targets, "min_ev": min_evidence, "expected_count": len(targets)}
    )
    return _claim_rows(df)


# --- dispatch (typed; no Any-registry) -----------------------------------------

def dispatch(conn: kuzu.Connection, req: QueryRequest) -> QueryResult:
    """Run the query a router decision points at, resolving its entity first."""
    match req.query:
        case "compound":
            name = _require(conn, req.entity, "compound")
            if name:
                return claims_for_compound(conn, name, req.min_evidence)
            return _search_fallback(conn, req)
        case "effect":
            name = _require(conn, req.entity, "effect")
            if name:
                return claims_for_effect(conn, name, req.min_evidence)
            return _search_fallback(conn, req)
        case "target":
            name = _require(conn, req.entity, "target")
            if name:
                return claims_for_target(conn, name, req.min_evidence)
            return _search_fallback(conn, req)
        case "bridge":
            name = _require(conn, req.entity, "compound")
            return shared_target_bridge(conn, name, req.min_evidence) if name else []
        case "contradictions":
            eff = _require(conn, req.entity, "effect") if req.entity else None
            return contradictions(conn, eff)
        case "search":
            return _search_fallback(conn, req)
        case "intersection":
            if not req.entities:
                return []
            resolved_compounds = [resolve_entity(conn, ent, "compound") for ent in req.entities]
            resolved_effects = [resolve_entity(conn, ent, "effect") for ent in req.entities]
            resolved_targets = [resolve_entity(conn, ent, "target") for ent in req.entities]

            if all(resolved_compounds):
                return claims_for_compound_intersection(
                    conn, [c for c in resolved_compounds if c is not None], req.min_evidence
                )
            elif all(resolved_effects):
                return claims_for_effect_intersection(
                    conn, [e for e in resolved_effects if e is not None], req.min_evidence
                )
            elif all(resolved_targets):
                return claims_for_target_intersection(
                    conn, [t for t in resolved_targets if t is not None], req.min_evidence
                )
            return []
        case "list_supplements":
            return list_supplements(conn)
        case "unknown":
            return []


def _search_fallback(conn: kuzu.Connection, req: QueryRequest) -> list[ClaimRow]:
    """When a named entity doesn't resolve, search the quote text for the term."""
    return search_claims(conn, req.entity, req.min_evidence) if req.entity else []


def _require(conn: kuzu.Connection, raw: str | None,
             kind: Literal["compound", "target", "effect"]) -> str | None:
    return resolve_entity(conn, raw, kind) if raw else None
