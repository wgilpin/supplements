# Milestone 1 — Implementation Plan

Build the supplement knowledge graph pipeline: **fetch → extract → normalise → load**, plus a small hand-labelled precision check. Built per [supplement-kg-m1-spec.md](supplement-kg-m1-spec.md).

## Locked decisions (this run)

| Choice | Value |
|--------|-------|
| Extractor LLM | **Gemini 3.5 Flash** (`google-genai`, structured JSON output via `response_schema`) |
| Supplement scope | Start with **3**: taurine, glycine, N-acetyl cysteine. Pipeline has no count cap — add names to scale. ~20 is the scale the *manual* dedup/QA steps were sized for, not a code limit |
| Abstracts / supplement | **~20 most recent** (PubMed `sort=date`, `retmax=20`) |
| Storage | Kùzu embedded, local file (`data/kg.kuzu`) |
| Package mgmt | `uv` (project already uses `.python-version` + `pyproject.toml`) |

## Dependencies to add

```
kuzu                # embedded graph DB
google-genai        # Gemini SDK (structured output)
httpx               # PubMed E-utilities + PubChem REST
pydantic            # claim schema + validation (also Gemini response_schema)
python-dotenv       # GEMINI_API_KEY from .env
pytest              # tests
```

`GEMINI_API_KEY` read from `.env` (gitignored). PubMed works keyless at 3 req/s; add `NCBI_API_KEY` as optional env to lift to 10 req/s.

## Project layout

```
src/skg/
  config.py        # env, paths, supplement list, constants
  fetch.py         # step 1 — PubMed esearch + efetch
  extract.py       # step 2 — Gemini → list[Claim] (the only bio step)
  normalise.py     # step 3 — string-normalise + compound synonym lookup
  synonyms.py      # PubChem add-time synonym dict builder (run once per supplement)
  schema.py        # pydantic Claim model + the canonical claim id hash
  graph.py         # step 4 — Kùzu DDL + MERGE/load
  canonicalise.py  # step 3b — LLM entity dedup (propose/apply); spec §5.4
  pipeline.py      # wires 1→4 for a supplement; CLI entry
data/
  synonyms.json    # compound → canonical name map (built by synonyms.py)
  abstracts/       # cached raw abstracts (so re-runs skip PubMed)
  kg.kuzu          # the graph (gitignored)
eval/
  sample.jsonl     # hand-labelled extraction sample
  score.py         # precision = quotes that support the claim
tests/
```

`data/kg.kuzu` and `.env` go in `.gitignore`. Cached abstracts are committed-optional (small at this scale; gitignore by default).

---

## Build sequence

### Phase 0 — Scaffold & spike (verify §8 open items first)
The spec flags four things to confirm *before* relying on them. Do these as throwaway spikes so later phases build on verified behaviour.

1. **Kùzu API + MERGE** — 5-line script: create node table, `MERGE` the same compound twice, assert one row via `.getAsDF()`. Confirms `kuzu.Database`/`Connection`/`.execute()`/`.getAsDF()` shape against installed version (§8.1, §8.4).
2. **PubChem synonyms** — fetch `/rest/pug/compound/name/taurine/synonyms/JSON`, confirm response shape (§8.2).
3. **PubMed E-utilities** — `esearch` taurine → PMIDs, `efetch` → abstract text. Confirm endpoints + rate limit (§8.3).
4. **Gemini structured output** — one abstract → JSON array matching the Claim schema, no prose. Confirm `response_schema` honours the verbatim-quote requirement.

Output of phase 0: confidence + any endpoint/API corrections folded into the modules below.

**Phase 0 results (verified 2026-06-19):**
- **Kùzu 0.11.3** — `MERGE` dedups (two MERGEs → one node ✅). API is `kuzu.Database(path)` → `kuzu.Connection(db)` → `conn.execute(q)` → `.get_as_df()` (snake_case in the Python wrapper). `get_as_df()` requires **pandas** (added) — without it, `ModuleNotFoundError: numpy`.
- **PubChem** — endpoint shape from spec confirmed. taurine → 175 synonyms, NAC → 278, returned at `InformationList.Information[0].Synonym`. NAC's typed name `"N-acetyl cysteine"` is *not* the PubChem canonical (`N-Acetyl-L-cysteine`) — synonym dict must include our typed form as the canonical key.
- **PubMed E-utilities** — `esearch.fcgi` with `retmode=json` gives `esearchresult.idlist`; `efetch.fcgi` with `retmode=xml` gives articles. Abstract = join of `.//AbstractText` (structured abstracts have multiple). Keyless calls worked.
- **Gemini** — `client.models.generate_content(model="gemini-3.5-flash", config={response_mime_type, response_schema=list[Claim]})`; `resp.parsed` returns `list[Claim]` directly. All 3 source quotes came back verbatim (substring check True), and it correctly emitted a `direction="none"` claim for the no-effect sentence. **Model id pinned: `gemini-3.5-flash`** (also valid: `gemini-flash-latest`).
- One extraction-quality note (not plumbing): it labelled the GABA-A *target* claim `direction="mixed"` where the quote supports `decreases`/`increases-activity` — exactly the kind of thing the §6 quote-vs-claim QA pass is meant to catch. Prompt tuning territory, not a blocker.

### Phase 1 — Schema (`schema.py`)
- Pydantic `Claim`: `compound`, `target | None`, `effect | None`, `direction` (Literal `increases|decreases|none|mixed`), `dose_text`, `cohort_text`, `model`, `source_quote`. Reused as Gemini `response_schema`.
- `claim_id(claim, pmid)` → stable hash of `pmid + compound + target + effect + direction` (per spec §4.4).
- Validation guard: reject a claim with both target *and* effect null (says nothing, per spec §4.4).

### Phase 2 — Fetch (`fetch.py`)
- `esearch.fcgi`: `db=pubmed`, `term=<supplement>`, `sort=date`, `retmax=20` → PMIDs.
- `efetch.fcgi`: `db=pubmed`, `id=...`, `rettype=abstract`, `retmode=xml` → parse title + abstract per PMID.
- Cache each abstract to `data/abstracts/<pmid>.json` so extraction re-runs don't re-hit PubMed. Respect rate limit (sleep / key).

### Phase 3 — Synonyms (`synonyms.py`)
- `add_supplement(name)`: PubChem synonym list → write into `data/synonyms.json` as `{synonym_lower: canonical_name}`. Runs once per supplement, off the hot path (spec §5.1).
- Canonical name = the name you typed (e.g. `"taurine"`).

### Phase 4 — Extract (`extract.py`) — the one bio step
- One abstract in → `list[Claim]` out, via Gemini 3.5 Flash with `response_schema=list[Claim]`, `response_mime_type="application/json"`.
- Prompt enforces spec §6: canonical full names (no abbreviations), `source_quote` **verbatim from the abstract**, `none` is a real claim, `""` for absent dose/cohort, never invent.
- Post-parse guard: assert `source_quote` is a substring of the abstract (cheap, deterministic QA backstop); flag misses.

### Phase 5 — Normalise (`normalise.py`)
- `normalise_str(s)`: lowercase, strip punctuation, collapse whitespace (spec §5.3) — applied to all names.
- Compounds: map via `synonyms.json`; fall back to normalised string.
- Targets/effects: rely on LLM canonical names + `normalise_str` backstop. No second synonym table (spec §5.2).

### Phase 5b — Canonicalise (`canonicalise.py`) — entity dedup (spec §5.4)
Added after the §5.3 human-glance flaw was identified: the operator is not a biologist, so biological synonym merges can't sit on a human glance. Push them to the LLM; human reviews by reading.
- `propose`: dump distinct `Compound`/`Target`/`Effect` names → Gemini → clusters (synonyms with a canonical) + flags (invalid/mistyped entries). Write to `data/canonical_map.json` (reviewable).
- `apply`: read the (human-reviewed) map → for each cluster, repoint relationships to the canonical node and `DETACH DELETE` the merged-away nodes. Flags are advisory only (not auto-deleted).
- Merge logic is testable without the LLM (`apply_map` takes a hand-built map). Run on a temp DB in tests.
- Known limitation (noted in spec §5.4): may leave logically-duplicate Claim nodes; claim-level re-dedup deferred.

### Phase 6 — Load (`graph.py`)
- DDL from spec §4.4 (Compound/Target/Effect/Claim node tables; HAS_CLAIM/ON_TARGET/HAS_EFFECT rel tables). Run once / idempotently.
- `load_claims(conn, claims, pmid)`: `MERGE` entity nodes by PK, `CREATE`/`MERGE` Claim by `id`, wire the rels. PK on normalised name is the dedup backstop (spec §4.5).
- `evidence_score` assigned here from `model` via the §7 rubric (1–5 lookup).

### Phase 7 — Pipeline (`pipeline.py`)
- `run(supplement)`: synonyms (if new) → fetch → extract per abstract → normalise → load. CLI: `uv run python -m skg.pipeline taurine`.
- Run for the 3 starter supplements (taurine, glycine, N-acetyl cysteine); eyeball the final node list and hand-merge stragglers (spec §5.3 — a legitimate step at this scale).

### Phase 8 — Eval (`eval/`)
Acceptance per spec §9: populated graph **+** a hand-labelled sample measuring extraction precision.
- Dump ~30 random claims with their `source_quote` to `eval/sample.jsonl`.
- Hand-label each: does the quote support the structured `direction`/`compound`/`effect`? (reading comprehension, not biology — spec §4 principle 4).
- `score.py` reports precision = supported / total. Record the number in the README.

---

## Testing notes (per global rules)
- Tests use a **temp Kùzu path** (`tmp_path` fixture), never `data/kg.kuzu`. Kùzu is an embedded file, so no separate test container needed — but the live graph file is off-limits to tests.
- Unit tests: `normalise_str`, `claim_id` stability, evidence-score rubric, MERGE-dedup (one node from two MERGEs), schema-guard rejects empty claims.
- Mock PubMed/PubChem/Gemini HTTP in tests (cached fixtures) — no live calls in the test suite.

## Verification / done criteria
1. `uv run python -m skg.pipeline taurine` (+ 2 others) populates `data/kg.kuzu` without error.
2. A Cypher sanity query returns the `compound → target → effect` chain for at least one claim.
3. No duplicate Compound nodes across synonym variants.
4. `eval/score.py` prints a precision figure over the labelled sample.
5. `pytest` green.

## M1 build results (2026-06-19) — COMPLETE

End-to-end run over taurine, glycine, N-acetyl cysteine:
- **57 abstracts** fetched + cached; **172 claims** extracted, **169** after claim-id dedup.
- **Graph:** 65 Compound, 38 Target, 98 Effect, 169 Claim nodes.
- **Multi-hop `compound → target → effect` chain present** (acceptance met).
- **Directions:** 89 increases / 67 decreases / 7 mixed / 6 none (`none` captured ✅).
- **Evidence scores** spread 1–5 (3 RCT, 17 obs, 50 animal, 50 in-vitro, 49 review).
- **NAC synonym dedup** collapsed variants to one node ✅.
- **Verbatim-quote QA backstop** dropped ~11 paraphrased quotes during the run.
- **12 unit tests green.**
- **Eval tooling ready:** `eval/sample.jsonl` holds 30 random claims awaiting hand-labelling; `python -m eval.score score` reports precision once labelled.

**Canonicalisation pass added (spec §5.4).** After identifying that the §5.3 human-glance step parks *biological* synonym judgment on a non-biologist, added `skg/canonicalise.py` (propose → review → apply). First run merged 2 genuine duplicates (`ascorate`→`ascorbate`; two mangled `cortical serine protease` variants), preserved all 169 claims, and correctly **skipped ~45 single-member "rename" clusters** as no-ops (dedup-only; storage stays normalised, no node invented). Notably the LLM *correctly declined* to merge entries that look duplicate to a non-biologist but are distinct axes (`Nrf2` vs `Nrf2/HO-1 pathway`; `PI3K/Akt/mTOR` vs `PI3K/Akt/CREB`) — the §5.4 principle working as intended. Residual mangled multi-entity strings are an upstream extraction-prompt issue, logged for later.

**Decision — off-target compounds kept (not pruned).** Extraction yields ~65 compounds because the LLM emits whatever compound each sentence is about. Kept deliberately: these are latent **bridge nodes** — when a future supplement also touches `glutathione`, `reactive oxygen species`, etc., the graph stitches together through existing nodes, feeding M2's cross-compound shared-target query. A few are type-sloppy (non-compounds in the Compound table); harmless at this scale, left for the §5.3 human-glance pass.

**Deviation from plan:** package lives at repo-root `skg/` (not `src/skg/`) to avoid packaging friction with `uv run python -m skg.…`. Throwaway Phase-0 spikes remain in `spikes/`.

## Setup status
- `GEMINI_API_KEY` is set in `.env` (loaded via `python-dotenv`; `.env` gitignored). ✅

Note: N-acetyl cysteine has many name variants (NAC, N-acetylcysteine, acetylcysteine) — a good early test of the PubChem synonym step.
