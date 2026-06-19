"""Spike: PubChem synonyms endpoint shape (spec §8.2)."""

import httpx

URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/synonyms/JSON"

for name in ["taurine", "N-acetyl cysteine"]:
    r = httpx.get(URL.format(name), timeout=30)
    print(f"\n=== {name} -> {r.status_code} ===")
    data = r.json()
    syns = data["InformationList"]["Information"][0]["Synonym"]
    print("synonym count:", len(syns))
    print("first 8:", syns[:8])
