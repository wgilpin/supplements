"""Step 1 — PubMed E-utilities: query a supplement, pull abstracts + PMIDs."""

from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from . import config

logger = logging.getLogger(__name__)


def _params(extra: dict) -> dict:
    p = dict(extra)
    if config.NCBI_API_KEY:
        p["api_key"] = config.NCBI_API_KEY
    return p


def esearch(term: str, retmax: int) -> list[str]:
    """Most-recent PMIDs for a search term."""
    r = httpx.get(
        f"{config.PUBMED_BASE}/esearch.fcgi",
        params=_params({"db": "pubmed", "term": term, "sort": "date",
                        "retmax": retmax, "retmode": "json"}),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["esearchresult"]["idlist"]


def efetch(pmids: list[str]) -> list[dict]:
    """Fetch title + abstract for each PMID."""
    if not pmids:
        return []
    r = httpx.get(
        f"{config.PUBMED_BASE}/efetch.fcgi",
        params=_params({"db": "pubmed", "id": ",".join(pmids),
                        "rettype": "abstract", "retmode": "xml"}),
        timeout=60,
    )
    r.raise_for_status()
    root = ET.fromstring(r.text)
    out = []
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID")
        title = art.findtext(".//ArticleTitle") or ""
        abstract = " ".join(t.text or "" for t in art.findall(".//AbstractText")).strip()
        if pmid and abstract:
            out.append({"pmid": pmid, "title": title, "abstract": abstract})
    return out


def fetch_supplement(term: str, retmax: int | None = None,
                     cache_dir: Path | None = None) -> list[dict]:
    """Fetch abstracts for a supplement, caching each to disk so re-runs skip
    PubMed. Returns the list of {pmid, title, abstract} records."""
    retmax = retmax or config.ABSTRACTS_PER_SUPPLEMENT
    cache_dir = cache_dir or config.ABSTRACTS_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    pmids = esearch(term, retmax)
    cached, to_fetch = [], []
    for pmid in pmids:
        cpath = cache_dir / f"{pmid}.json"
        if cpath.exists():
            cached.append(json.loads(cpath.read_text()))
        else:
            to_fetch.append(pmid)

    fetched = []
    if to_fetch:
        time.sleep(0.34)  # ~3 req/s keyless courtesy
        fetched = efetch(to_fetch)
        for rec in fetched:
            (cache_dir / f"{rec['pmid']}.json").write_text(json.dumps(rec, indent=2))

    logger.info("%s: %d PMIDs (%d cached, %d fetched)", term, len(pmids), len(cached), len(fetched))
    return cached + fetched
