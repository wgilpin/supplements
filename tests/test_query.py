"""Query layer tests (spec §9) against a small, known, seeded graph.

Uses a temp Kùzu DB (`tmp_path`) — never the live `data/kg.kuzu`. The fixture is
loaded through the real `graph.load_claim` path so the query Cypher is exercised
against the true schema and relationship wiring.
"""

from __future__ import annotations

import pytest

from skg import graph, query
from skg.schema import Claim


def _claim(compound, target, effect, direction, model, pmid_quote):
    return Claim(
        compound=compound,
        target=target,
        effect=effect,
        direction=direction,
        dose_text="500 mg twice daily" if target else "",
        cohort_text="healthy adults" if effect else "",
        model=model,
        source_quote=pmid_quote,
    )


# (claim, pmid). evidence_score is derived from `model` by the §7 rubric:
# human RCT->5, human observational->4, mouse->3, in vitro->2, review->1.
_FIXTURE = [
    (_claim("taurine", "GABA-A receptor", "anxiety", "decreases", "human RCT",
            "Taurine reduced anxiety."), "p1"),
    (_claim("glycine", "GABA-A receptor", "sleep quality", "increases",
            "human observational", "Glycine improved sleep quality."), "p2"),
    (_claim("magnesium", "NMDA receptor", "muscle cramps", "decreases",
            "in vitro", "Magnesium decreased cramping."), "p3"),
    (_claim("taurine", None, "anxiety", "increases", "mouse",
            "Taurine increased anxiety-like behaviour in mice."), "p4"),
    (_claim("taurine", "NMDA receptor", None, "modulates", "review",
            "Taurine modulates NMDA receptor signalling."), "p5"),
]


@pytest.fixture
def conn(tmp_path):
    c = graph.connect(tmp_path / "test.kuzu")
    graph.init_schema(c)
    for claim, pmid in _FIXTURE:
        graph.load_claim(c, claim, pmid, synonyms={})
    return c


# --- list helpers --------------------------------------------------------------

def test_list_compounds(conn):
    assert query.list_compounds(conn) == ["glycine", "magnesium", "taurine"]


def test_list_targets(conn):
    assert query.list_targets(conn) == ["gaba a receptor", "nmda receptor"]


def test_list_effects(conn):
    assert query.list_effects(conn) == ["anxiety", "muscle cramps", "sleep quality"]


# --- claims_for_compound -------------------------------------------------------

def test_claims_for_compound(conn):
    rows = query.claims_for_compound(conn, "taurine")
    assert len(rows) == 3
    assert all(r.compound == "taurine" for r in rows)


def test_claims_for_compound_min_evidence(conn):
    rows = query.claims_for_compound(conn, "taurine", min_evidence=4)
    assert len(rows) == 1
    assert rows[0].evidence_score == 5
    assert rows[0].direction == "decreases"


def test_claims_for_compound_null_effect_is_none(conn):
    # The 'review' claim (p5) has a target but no effect — must map to None, not NaN.
    rows = query.claims_for_compound(conn, "taurine")
    review = [r for r in rows if r.source_pmid == "p5"][0]
    assert review.effect is None
    assert review.target == "nmda receptor"
    # And the mouse claim (p4) has no target.
    mouse = [r for r in rows if r.source_pmid == "p4"][0]
    assert mouse.target is None


# --- claims_for_effect ---------------------------------------------------------

def test_claims_for_effect(conn):
    rows = query.claims_for_effect(conn, "anxiety")
    assert len(rows) == 2
    assert {r.direction for r in rows} == {"increases", "decreases"}


# --- claims_for_target ---------------------------------------------------------

def test_claims_for_target(conn):
    rows = query.claims_for_target(conn, "gaba a receptor")
    assert {r.compound for r in rows} == {"taurine", "glycine"}


# --- shared_target_bridge (the multi-hop query) --------------------------------

def test_shared_target_bridge(conn):
    rows = query.shared_target_bridge(conn, "taurine")
    # taurine shares GABA-A with glycine and NMDA with magnesium.
    assert {r.other_compound for r in rows} == {"glycine", "magnesium"}
    gly = [r for r in rows if r.other_compound == "glycine"][0]
    assert gly.shared_target == "gaba a receptor"
    assert gly.effect == "sleep quality"


def test_shared_target_bridge_min_evidence(conn):
    # min_evidence=3 drops magnesium's in-vitro (2) claim, keeps glycine's (4).
    rows = query.shared_target_bridge(conn, "taurine", min_evidence=3)
    assert {r.other_compound for r in rows} == {"glycine"}


# --- contradictions ------------------------------------------------------------

def test_contradictions(conn):
    rows = query.contradictions(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r.compound == "taurine"
    assert r.effect == "anxiety"
    assert {r.direction_a, r.direction_b} == {"increases", "decreases"}


def test_contradictions_filtered_by_effect(conn):
    assert query.contradictions(conn, effect="anxiety")  # present
    assert query.contradictions(conn, effect="sleep quality") == []  # no conflict


# --- search_claims (full-text quote search) ------------------------------------

def test_search_claims_matches_quote(conn):
    rows = query.search_claims(conn, "cramping")
    assert len(rows) == 1
    assert rows[0].compound == "magnesium"


def test_search_claims_case_insensitive(conn):
    assert len(query.search_claims(conn, "CRAMPING")) == 1


def test_search_claims_no_match(conn):
    assert query.search_claims(conn, "zzzznothing") == []


def test_dispatch_search(conn):
    req = query.QueryRequest(query="search", entity="cramping", min_evidence=1)
    assert len(query.dispatch(conn, req)) == 1


def test_dispatch_falls_back_to_search_when_entity_unresolved(conn):
    # "cramping" is not an effect node name, but appears in a quote -> fallback.
    req = query.QueryRequest(query="effect", entity="cramping", min_evidence=1)
    rows = query.dispatch(conn, req)
    assert len(rows) == 1
    assert rows[0].compound == "magnesium"


# --- resolve_entity ------------------------------------------------------------

def test_resolve_entity_exact(conn):
    assert query.resolve_entity(conn, "Taurine", "compound") == "taurine"


def test_resolve_entity_substring(conn):
    assert query.resolve_entity(conn, "GABA", "target") == "gaba a receptor"


def test_resolve_entity_missing(conn):
    assert query.resolve_entity(conn, "nonexistent thing", "effect") is None


def test_resolve_entity_unnormalised_node_name(conn):
    # M1 stores compound names un-normalised (caps/hyphens). Resolving the typed
    # form must match the stored node, not a shorter substring node like "cysteine".
    conn.execute("MERGE (c:Compound {name: 'N-Acetyl Cysteine'})")
    conn.execute("MERGE (c:Compound {name: 'cysteine'})")
    assert query.resolve_entity(conn, "n acetyl cysteine", "compound") == "N-Acetyl Cysteine"


def test_resolve_entity_synonym(conn):
    conn.execute("MERGE (c:Compound {name: 'curcumin'})")
    assert query.resolve_entity(conn, "curcurmin", "compound") == "curcumin"


# --- group_claims (display merge of same-evidence rows) ------------------------

def _row(compound, effect, quote, pmid, direction="modulates", evidence=4):
    return query.ClaimRow(
        compound=compound, target=None, effect=effect, direction=direction,
        evidence_score=evidence, model="human observational", dose_text="",
        cohort_text="", source_pmid=pmid, source_quote=quote,
    )


def test_group_claims_merges_same_evidence_different_compound():
    rows = [
        _row("taurine", "oxidative stress", "one sentence", "p9"),
        _row("cysteine", "oxidative stress", "one sentence", "p9"),
        _row("methionine", "oxidative stress", "one sentence", "p9"),
    ]
    groups = query.group_claims(rows)
    assert len(groups) == 1
    assert groups[0].compounds == ["taurine", "cysteine", "methionine"]
    assert groups[0].source_quote == "one sentence"


def test_group_claims_keeps_distinct_evidence_separate():
    rows = [
        _row("taurine", "oxidative stress", "quote A", "p1"),
        _row("taurine", "anxiety", "quote B", "p2"),       # different effect+quote
        _row("glycine", "oxidative stress", "quote A", "p1"),  # merges with first
    ]
    groups = query.group_claims(rows)
    assert len(groups) == 2
    first = [g for g in groups if g.effect == "oxidative stress"][0]
    assert set(first.compounds) == {"taurine", "glycine"}


# --- dispatch ------------------------------------------------------------------

def test_dispatch_compound(conn):
    req = query.QueryRequest(query="compound", entity="taurine", min_evidence=1)
    result = query.dispatch(conn, req)
    assert len(result) == 3


def test_dispatch_unknown_returns_empty(conn):
    req = query.QueryRequest(query="unknown", entity=None, min_evidence=1)
    assert query.dispatch(conn, req) == []


def test_claims_for_compound_intersection(conn):
    rows = query.claims_for_compound_intersection(conn, ["taurine", "glycine"])
    assert len(rows) == 2
    assert {r.source_pmid for r in rows} == {"p1", "p2"}
    assert all(r.target == "gaba a receptor" for r in rows)


def test_claims_for_effect_intersection(tmp_path):
    c = graph.connect(tmp_path / "test_effect.kuzu")
    graph.init_schema(c)
    graph.load_claim(c, _claim("compA", None, "anxiety", "decreases", "human RCT", "A reduced anxiety."), "p10", synonyms={})
    graph.load_claim(c, _claim("compA", None, "depression", "decreases", "human RCT", "A reduced depression."), "p11", synonyms={})
    graph.load_claim(c, _claim("compB", None, "anxiety", "decreases", "human RCT", "B reduced anxiety."), "p12", synonyms={})

    rows = query.claims_for_effect_intersection(c, ["anxiety", "depression"])
    assert len(rows) == 2
    assert all(r.compound == "compa" for r in rows)
    assert {r.effect for r in rows} == {"anxiety", "depression"}


def test_claims_for_target_intersection(tmp_path):
    c = graph.connect(tmp_path / "test_target.kuzu")
    graph.init_schema(c)
    graph.load_claim(c, _claim("compA", "rec1", None, "decreases", "human RCT", "A acts on rec1."), "p20", synonyms={})
    graph.load_claim(c, _claim("compA", "rec2", None, "decreases", "human RCT", "A acts on rec2."), "p21", synonyms={})
    graph.load_claim(c, _claim("compB", "rec1", None, "decreases", "human RCT", "B acts on rec1."), "p22", synonyms={})

    rows = query.claims_for_target_intersection(c, ["rec1", "rec2"])
    assert len(rows) == 2
    assert all(r.compound == "compa" for r in rows)
    assert {r.target for r in rows} == {"rec1", "rec2"}


def test_dispatch_intersection(conn):
    req = query.QueryRequest(query="intersection", entities=["taurine", "glycine"], min_evidence=1)
    rows = query.dispatch(conn, req)
    assert len(rows) == 2
    assert {r.source_pmid for r in rows} == {"p1", "p2"}
