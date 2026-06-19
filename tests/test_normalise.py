from skg.normalise import canonical_compound, canonical_entity, normalise_str


def test_normalise_lowercases_strips_punct_collapses_ws():
    assert normalise_str("  Alzheimer's   Disease! ") == "alzheimers disease"


def test_normalise_hyphens_and_slashes_become_spaces():
    # Hyphens must not glue words (the "aminemodified" bug).
    assert normalise_str("amine-modified") == "amine modified"
    assert normalise_str("taurine-based") == "taurine based"
    assert normalise_str("GABA-A receptor") == "gaba a receptor"
    assert normalise_str("PI3K/Akt") == "pi3k akt"


def test_canonical_compound_uses_synonym_map():
    syns = {"nac": "N-acetyl cysteine", "acetylcysteine": "N-acetyl cysteine"}
    assert canonical_compound("NAC", syns) == "N-acetyl cysteine"
    assert canonical_compound("acetylcysteine", syns) == "N-acetyl cysteine"


def test_canonical_compound_falls_back_to_normalised():
    assert canonical_compound("Taurine", {}) == "taurine"


def test_canonical_entity_handles_none_and_blank():
    assert canonical_entity(None) is None
    assert canonical_entity("   ") is None
    assert canonical_entity("Alzheimer Disease") == "alzheimer disease"
