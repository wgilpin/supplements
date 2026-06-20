"""Quote verification (normalize_text / fuzzy_substring_match).

Regression coverage for claims being dropped during astaxanthin ingestion: Gemini
echoed Greek letters (TNF-α, IL-1β) back as stray combining marks instead of the
proper codepoints, so otherwise-verbatim quotes failed the verbatim check.
"""

from skg.extract import fuzzy_substring_match, normalize_text, recover_quote

# A real astaxanthin abstract sentence, rich in cytokine names with Greek letters.
ABSTRACT = (
    "In this study AST treatment promoted cell proliferation, inhibited apoptosis, "
    "and markedly reduced the levels of 8-OHdG, 4-HNE, MDA, ROS, TNF-α, IL-1β, IL-6, "
    "and MCP-1. Furthermore, the OGD/R model confirmed these protective effects."
)


def _matches(quote: str) -> bool:
    return fuzzy_substring_match(normalize_text(quote), normalize_text(ABSTRACT))


def test_combining_mark_corruption_still_matches():
    # Gemini renders TNF-α / IL-1β with combining marks (U+0311, U+0322) instead of
    # the proper Greek codepoints; the quote is otherwise verbatim and must verify.
    corrupted = (
        "AST treatment promoted cell proliferation, inhibited apoptosis, and markedly "
        "reduced the levels of 8-OHdG, 4-HNE, MDA, ROS, TNF-̑, IL-1̢, IL-6, and MCP-1."
    )
    assert _matches(corrupted)


def test_verbatim_quote_with_greek_matches():
    verbatim = (
        "AST treatment promoted cell proliferation, inhibited apoptosis, and markedly "
        "reduced the levels of 8-OHdG, 4-HNE, MDA, ROS, TNF-α, IL-1β, IL-6, and MCP-1."
    )
    assert _matches(verbatim)


def test_spelled_out_greek_matches():
    # Abstract uses the α/β symbols; a quote that is otherwise verbatim but spells the
    # Greek letters out ("alpha"/"beta") should still verify.
    spelled = (
        "AST treatment promoted cell proliferation, inhibited apoptosis, and markedly "
        "reduced the levels of 8-OHdG, 4-HNE, MDA, ROS, TNF-alpha, IL-1beta, IL-6, and MCP-1."
    )
    assert _matches(spelled)


def test_fabricated_quote_is_rejected():
    fabricated = (
        "AST had no effect on tumor growth in patients with diabetes mellitus and "
        "chronic hypertension across the cohort."
    )
    assert not _matches(fabricated)


def test_meaning_inverting_word_swap_is_rejected():
    # "increased"/"elevated" replace the abstract's "reduced"/"inhibited"; the
    # word-existence guard must drop this even though the topic matches.
    swapped = (
        "Inflammatory responses were increased through elevated COX expression and "
        "higher cytokine levels overall."
    )
    assert not _matches(swapped)


def test_recover_quote_restores_clean_abstract_text():
    # A corrupted quote that verifies should be replaced by the abstract's own clean
    # sentence, so the stored/displayed quote shows proper Greek rather than garbage.
    corrupted = (
        "AST treatment promoted cell proliferation, inhibited apoptosis, and markedly "
        "reduced the levels of 8-OHdG, 4-HNE, MDA, ROS, TNF-̑, IL-1̢, IL-6, and MCP-1."
    )
    recovered = recover_quote(corrupted, ABSTRACT)
    assert "TNF-α" in recovered
    assert "IL-1β" in recovered
    assert "̑" not in recovered and "̢" not in recovered


def test_recover_quote_falls_back_to_mark_stripped_quote():
    # When the quote does not match any abstract sentence (e.g. abstract unavailable),
    # the fallback at least strips combining marks rather than storing garbage.
    orphan = "Some TNF-̑ finding not present in the abstract at all whatsoever here."
    recovered = recover_quote(orphan, "Unrelated abstract about something else entirely.")
    assert "̑" not in recovered


def test_normalize_strips_corrupted_greek_to_match_proper_greek():
    # The corrupted-mark form and the proper-Greek form normalize compatibly enough
    # that the abstract still contains the quote's substantive words.
    assert normalize_text("TNF-α") == "tnf alpha"
    # Stray combining mark is dropped rather than left as garbage.
    assert normalize_text("TNF-̑") == "tnf"
