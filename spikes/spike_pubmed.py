"""Spike: PubMed E-utilities esearch + efetch (spec §8.3)."""

import xml.etree.ElementTree as ET

import httpx

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

esearch = httpx.get(
    f"{BASE}/esearch.fcgi",
    params={"db": "pubmed", "term": "taurine", "sort": "date", "retmax": 3, "retmode": "json"},
    timeout=30,
)
pmids = esearch.json()["esearchresult"]["idlist"]
print("PMIDs:", pmids)

efetch = httpx.get(
    f"{BASE}/efetch.fcgi",
    params={"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"},
    timeout=30,
)
root = ET.fromstring(efetch.text)
for art in root.findall(".//PubmedArticle"):
    pmid = art.findtext(".//PMID")
    title = art.findtext(".//ArticleTitle")
    abstract = " ".join(t.text or "" for t in art.findall(".//AbstractText"))
    print(f"\n--- PMID {pmid} ---")
    print("title:", title)
    print("abstract chars:", len(abstract))
    print("abstract head:", abstract[:200])
