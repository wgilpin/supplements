from skg.schema import Claim, claim_id, is_meaningful


def _claim(**kw):
    base = dict(compound="taurine", target=None, effect="anxiety",
               direction="decreases", dose_text="", cohort_text="",
               model="human RCT", source_quote="q")
    base.update(kw)
    return Claim(**base)


def test_claim_id_is_stable():
    a = claim_id("taurine", None, "anxiety", "decreases", "123")
    b = claim_id("taurine", None, "anxiety", "decreases", "123")
    assert a == b


def test_claim_id_distinguishes_direction():
    inc = claim_id("taurine", None, "anxiety", "increases", "123")
    dec = claim_id("taurine", None, "anxiety", "decreases", "123")
    assert inc != dec


def test_is_meaningful_requires_target_or_effect():
    assert is_meaningful(_claim(target="gaba", effect=None))
    assert is_meaningful(_claim(target=None, effect="anxiety"))
    assert not is_meaningful(_claim(target=None, effect=None))
    assert not is_meaningful(_claim(target="  ", effect=""))
