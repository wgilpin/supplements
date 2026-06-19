import pytest

from skg import canonicalise, graph
from skg.schema import Claim


@pytest.fixture
def conn(tmp_path):
    c = graph.connect(tmp_path / "test.kuzu")
    graph.init_schema(c)
    return c


def _claim(**kw):
    base = dict(compound="taurine", target=None, effect="anxiety",
               direction="decreases", dose_text="", cohort_text="",
               model="human RCT", source_quote="q")
    base.update(kw)
    return Claim(**base)


def test_merge_target_repoints_and_deletes(conn):
    # Two claims on two target names that are really the same entity.
    graph.load_claim(conn, _claim(target="nrf2", effect=None), "1", {})
    graph.load_claim(conn, _claim(target="nuclear factor erythroid 2", effect=None,
                                  direction="increases"), "2", {})
    assert graph.counts(conn)["Target"] == 2

    merged = canonicalise.merge_into(
        conn, "Target",
        canonical="nuclear factor erythroid 2",
        members=["nrf2", "nuclear factor erythroid 2"],
    )
    assert merged == 1
    counts = graph.counts(conn)
    assert counts["Target"] == 1
    assert counts["Claim"] == 2  # both claims survive, repointed

    df = conn.execute(
        "MATCH (cl:Claim)-[:ON_TARGET]->(t:Target) RETURN t.name AS name"
    ).get_as_df()
    assert set(df["name"]) == {"nuclear factor erythroid 2"}


def test_merge_compound_repoints_from_side(conn):
    graph.load_claim(conn, _claim(compound="nac"), "1", {})
    graph.load_claim(conn, _claim(compound="n-acetylcysteine", direction="increases"),
                     "2", {})
    assert graph.counts(conn)["Compound"] == 2

    # Canonical resolves to an existing member (dedup never invents a node).
    canonicalise.merge_into(conn, "Compound", canonical="n-acetylcysteine",
                            members=["nac", "n-acetylcysteine"])
    counts = graph.counts(conn)
    assert counts["Compound"] == 1
    df = conn.execute(
        "MATCH (c:Compound)-[:HAS_CLAIM]->(:Claim) RETURN DISTINCT c.name AS name"
    ).get_as_df()
    assert list(df["name"]) == ["n acetylcysteine"]  # an existing normalised member


def test_merge_skips_nonexistent_member(conn):
    graph.load_claim(conn, _claim(target="autophagy", effect=None), "1", {})
    merged = canonicalise.merge_into(
        conn, "Target", canonical="autophagy",
        members=["autophagy", "ghost entity not in graph"],
    )
    assert merged == 0
    assert graph.counts(conn)["Target"] == 1


def test_single_member_rename_is_noop(conn):
    # A pretty canonical with restored punctuation must NOT spawn a new node.
    graph.load_claim(conn, _claim(target="bcell lymphoma 2", effect=None), "1", {})
    merged = canonicalise.merge_into(
        conn, "Target", canonical="B-cell lymphoma 2",
        members=["bcell lymphoma 2"],
    )
    assert merged == 0
    df = conn.execute("MATCH (t:Target) RETURN t.name AS name").get_as_df()
    assert list(df["name"]) == ["bcell lymphoma 2"]  # unchanged


def test_apply_map_runs_all_labels(conn):
    graph.load_claim(conn, _claim(target="ros", effect=None), "1", {})
    graph.load_claim(conn, _claim(target="reactive oxygen species", effect=None,
                                  direction="increases"), "2", {})
    mapping = {
        "Target": {
            "clusters": [
                {"canonical": "reactive oxygen species",
                 "members": ["ros", "reactive oxygen species"]}
            ],
            "flags": [],
        }
    }
    results = canonicalise.apply_map(conn, mapping)
    assert results["Target"] == 1
    assert graph.counts(conn)["Target"] == 1
