import pytest

from skg import graph
from skg.schema import Claim


@pytest.fixture
def conn(tmp_path):
    # Isolated temp graph — never the live data/kg.kuzu (per global DB rule).
    c = graph.connect(tmp_path / "test.kuzu")
    graph.init_schema(c)
    return c


def _claim(**kw):
    base = dict(compound="taurine", target="gamma-aminobutyric acid type A receptor",
               effect="anxiety", direction="decreases", dose_text="500mg",
               cohort_text="adults", model="human RCT", source_quote="q")
    base.update(kw)
    return Claim(**base)


def test_evidence_score_rubric():
    assert graph.evidence_score("human RCT") == 5
    assert graph.evidence_score("mouse model") == 3
    assert graph.evidence_score("in vitro") == 2
    assert graph.evidence_score("review") == 1
    assert graph.evidence_score("something odd") == 1


def test_load_claim_creates_chain(conn):
    graph.load_claim(conn, _claim(), "111", {})
    counts = graph.counts(conn)
    assert counts == {"Compound": 1, "Target": 1, "Effect": 1, "Claim": 1}


def test_load_is_idempotent_and_dedups_compound(conn):
    # Same claim twice -> one of everything.
    graph.load_claim(conn, _claim(), "111", {})
    graph.load_claim(conn, _claim(), "111", {})
    assert graph.counts(conn)["Claim"] == 1
    assert graph.counts(conn)["Compound"] == 1

    # Second claim on same compound, different effect -> compound still deduped.
    graph.load_claim(conn, _claim(effect="depression"), "111", {})
    counts = graph.counts(conn)
    assert counts["Compound"] == 1
    assert counts["Claim"] == 2
    assert counts["Effect"] == 2


def test_load_claim_skips_empty(conn):
    assert graph.load_claim(conn, _claim(target=None, effect=None), "111", {}) is None
    assert graph.counts(conn)["Claim"] == 0


def test_multihop_query_chain(conn):
    graph.load_claim(conn, _claim(), "111", {})
    df = conn.execute(
        """MATCH (c:Compound)-[:HAS_CLAIM]->(cl:Claim)-[:ON_TARGET]->(t:Target)
           RETURN c.name AS compound, t.name AS target"""
    ).get_as_df()
    assert df["compound"][0] == "taurine"
    assert df["target"][0] == "gamma aminobutyric acid type a receptor"
