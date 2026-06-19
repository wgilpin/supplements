# Supplement Knowledge Graph

A small knowledge graph of claimed/evidenced biological interactions for a handful
of supplements (taurine, glycine, N-acetyl cysteine). Built per
[docs/supplement-kg-m1-spec.md](docs/supplement-kg-m1-spec.md); plan and progress
in [docs/m1-plan.md](docs/m1-plan.md).

## Pipeline (Milestone 1)

`fetch → extract → normalise → load`, only step 2 touches biology (the LLM does it).

| Module | Step |
|--------|------|
| `skg/fetch.py` | PubMed E-utilities → abstracts + PMIDs (cached to `data/abstracts/`) |
| `skg/extract.py` | Abstract → `list[Claim]` via Gemini 3.5 Flash (structured JSON) |
| `skg/normalise.py` + `skg/synonyms.py` | Canonical names; PubChem synonyms for compounds |
| `skg/canonicalise.py` | LLM entity dedup (synonym clustering); human reviews, then applies |
| `skg/graph.py` | MERGE claims into Kùzu (claim is a node, not an edge) |
| `skg/pipeline.py` | Wires it together |

## Setup

```bash
uv sync
echo "GEMINI_API_KEY=..." > .env   # required; NCBI_API_KEY optional
```

## Run

```bash
# Full pipeline over the configured supplements (skg/config.py SUPPLEMENTS)
uv run python -m skg.pipeline

# Or specific ones
uv run python -m skg.pipeline taurine glycine

# Rebuild just the compound synonym dict
uv run python -m skg.synonyms
```

The graph lands in `data/kg.kuzu` (gitignored). Abstracts are cached, so re-runs
skip PubMed and only re-extract.

## Deduplicate entities (spec §5.4)

Biological synonym merges need domain knowledge, so the LLM proposes them and you
review by reading — never originate the biology yourself.

```bash
uv run python -m skg.canonicalise propose   # writes data/canonical_map.json
# read the proposed clusters; edit/remove any you disagree with
uv run python -m skg.canonicalise apply      # repoints + merges per the map
```

Dedup only — it collapses duplicate nodes into one existing node; it does not
rename to prettier forms (storage stays normalised). Flags are advisory.

## Evaluate extraction precision

```bash
uv run python -m eval.score sample 30   # dump 30 random claims to eval/sample.jsonl
# hand-label each row's "supported" field true/false
uv run python -m eval.score score       # report precision
```

Precision = fraction of claims whose verbatim `source_quote` actually supports the
structured claim. This is the only QA check available without domain knowledge
(spec §4).

## Tests

```bash
uv run pytest
```

Tests use an isolated temp Kùzu DB — never the live `data/kg.kuzu`.
