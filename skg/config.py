"""Central config: paths, supplement list, constants."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ABSTRACTS_DIR = DATA_DIR / "abstracts"
CLAIMS_DIR = DATA_DIR / "claims"
SYNONYMS_PATH = DATA_DIR / "synonyms.json"
GRAPH_PATH = DATA_DIR / "kg.kuzu"

# Starter supplements (M1). The pipeline has no count cap — add names here.
SUPPLEMENTS = ["taurine", "glycine", "N-acetyl cysteine"]

# Fetch
ABSTRACTS_PER_SUPPLEMENT = 20
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")  # optional; lifts rate limit

# PubChem
PUBCHEM_SYNONYMS_URL = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/synonyms/JSON"
)

# Gemini
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Evidence score rubric (spec §7): model keyword -> score 1-5.
EVIDENCE_RUBRIC = {
    "human rct": 5,
    "rct": 5,
    "human observational": 4,
    "cohort": 4,
    "observational": 4,
    "human": 4,
    "mouse": 3,
    "rat": 3,
    "animal": 3,
    "in vivo": 3,
    "in vitro": 2,
    "cell": 2,
    "review": 1,
    "opinion": 1,
}
DEFAULT_EVIDENCE_SCORE = 1
