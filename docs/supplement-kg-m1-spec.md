# Supplement Knowledge Graph — Milestone 1 Build Spec

*A KG of claimed/evidenced biological interactions for a small list of supplements (taurine, etc.). Analogous in spirit to BenchSci, scaled down to a personal project. Priorities: keep it simple, keep it legible, and use a graph DB so multi-hop querying is available later.*

---

## 1. Guiding principles

These are the rules every design decision below was checked against. If a future change violates one, stop and reconsider.

1. **KISS over completeness.** Correct-for-this-scale beats universally-correct. The target is ~20 supplements and a project one person can hold in their head, not BenchSci's volume.
2. **Quarantine the biology.** The only step that requires domain knowledge is LLM extraction. Everything else is ordinary data engineering.
3. **Put each job where the information already exists.** Resolve compound names when *you* type them; resolve target/effect names where the LLM already holds the knowledge. No mechanism duplicates information another step already has.
4. **The source quote is the QA mechanism.** With no bio background, you can't validate an extracted edge against biology — but you can always check whether the verbatim quote actually says what the structured claim says. That's reading comprehension, not biology. So the quote is mandatory, not optional.
5. **Stay honestly a graph.** The one structure justifying a graph DB over a flat table is the `compound → target → effect` chain. Don't optimise it away.

---

## 2. Architecture at a glance

Five steps. Only step 2 touches biology, and the LLM does it.

| Step | Name | What happens | Bio knowledge needed? |
|------|------|--------------|----------------------|
| 1 | Fetch | PubMed E-utilities: query each supplement, pull abstracts + PMIDs | No |
| 2 | Extract | Abstract in → JSON claims out (LLM) | LLM only |
| 3 | Normalise | Map names to canonical form so one entity = one node. Deterministic string-normalise (No bio) + an LLM canonicalisation pass for synonym clustering (§5.4) | String pass: No / Synonym clustering: LLM only |
| 4 | Load | MERGE/CREATE into Kùzu via Cypher | No |
| 5 | Query | (Milestone 2) Ask the graph questions | No |

---

## 3. Storage: Kùzu (embedded, Cypher)

**Decision: Kùzu.** Embedded (in-process, `pip install kuzu`, data in a local file — no server, no container, SQLite-style deployment), MIT-licensed, and you write real Cypher so the skill transfers to Neo4j later. Results return directly as pandas DataFrames via `.getAsDF()`.

**Differences from Neo4j to be aware of:**
- Kùzu uses **typed tables** (`CREATE NODE TABLE`, `CREATE REL TABLE`) with declared columns and primary keys — more SQL-like than Neo4j's schema-optional model. Given a SQL background this is a *plus* for legibility, but the schema must be declared up front.
- Cypher is **openCypher**, not 100% Neo4j dialect. Core (`MATCH`, `MERGE`, `CREATE`, `WHERE`, variable-length paths `-[*1..3]->`) is identical and is all that's needed. Neo4j proprietary extensions (APOC) won't be present — so some Stack Overflow answers won't paste in cleanly. For a *learning* goal this is arguably better: you learn the standard.
- Relationships connect **exactly two endpoints** — this drives the claim-as-node decision below.

**Build against the current docs at `docs.kuzudb.com`, not old Medium tutorials.** The API has moved since early versions; check the version number on anything copied.

---

## 4. Schema

### 4.1 The key decision: claim is a NODE, not an edge

A claim is inherently **ternary** — "taurine → GABA-A → anxiety" touches a compound, a target, and an effect. Kùzu relationships are binary, so a claim cannot be a single relationship without dropping the target and losing the mechanistic chain (which is the whole reason to use a graph DB). Therefore the claim is its own node, linked out to the three entities.

This also delivers, for free, the things the reified-claim model was chosen for:
- **Contradictions are data, not bugs.** Two claim nodes with opposite `direction` on the same compound→effect is a finding to surface, not a duplicate to merge.
- **Dose-dependent reversals** (hormesis) represent naturally — different claims, different dose, different direction.
- **Provenance and evidence grading** hang off each individual assertion.

### 4.2 Entity nodes (three types)

`Compound`, `Target`, `Effect`. Each is just a typed node with a normalised name as primary key. You don't need to understand the biology of what's inside them — they're typed strings.

> **Target vs Effect must stay separate node types.** Some papers assert mechanism ("taurine → GABA-A", ends at a target); others assert phenotype ("taurine → cognition", skips to effect). Keeping them distinct is what lets a Milestone-2 query stitch the first kind into the second across compounds. Don't collapse them into one "outcome" type to save a table.

### 4.3 Qualifiers: properties on the claim, NOT separate nodes

At this scale, dose/direction/cohort/evidence are **properties on the claim node**, not their own nodes. (The node-per-cohort version buys an elegant "what's missing" query but costs conceptual complexity not worth paying at 20 supplements — a `GROUP BY` or eyeballing the list answers the same question. Revisit only if it actually bites.)

- `dose_text` and `cohort_text` are **raw strings**, not parsed structures. Store the sentence the model found ("500mg twice daily"); let the *human* read it in Milestone 2. Parsing dose into comparable numbers (units, salts, bioavailability) is genuinely hard and not needed until you compute across regimes — a later, optional milestone. Keep the raw text; nothing is lost that can't be added back.

### 4.4 Kùzu DDL (verify against current docs before relying on it)

```cypher
// --- Entity tables ---
CREATE NODE TABLE Compound (
    name STRING,            // normalised, e.g. "taurine"
    PRIMARY KEY (name)
);

CREATE NODE TABLE Target (
    name STRING,            // canonical, e.g. "gaba-a receptor"
    PRIMARY KEY (name)
);

CREATE NODE TABLE Effect (
    name STRING,            // canonical, e.g. "alzheimer disease"
    PRIMARY KEY (name)
);

// --- Claim node (the reified assertion) ---
CREATE NODE TABLE Claim (
    id STRING,              // hash of (pmid + compound + target + effect + direction)
    direction STRING,       // 'increases' | 'decreases' | 'none' | 'mixed'
    dose_text STRING,       // raw, human-readable; "" if none stated
    cohort_text STRING,     // raw; e.g. "adults 60+, healthy"; "" if none
    model STRING,           // 'human RCT' | 'mouse' | 'in vitro' | 'review' | ...
    evidence_score INT64,   // 1-5, simple rubric (see §7)
    source_pmid STRING,
    source_quote STRING,    // verbatim sentence — MANDATORY (this is your QA)
    PRIMARY KEY (id)
);

// --- Relationship tables wiring claim to entities ---
CREATE REL TABLE HAS_CLAIM  (FROM Compound TO Claim);
CREATE REL TABLE ON_TARGET  (FROM Claim TO Target);   // optional per claim
CREATE REL TABLE HAS_EFFECT (FROM Claim TO Effect);   // optional per claim
```

A claim links from one `Compound`, to zero-or-one `Target`, and to zero-or-one `Effect` (at least one of the two should be present, or the claim says nothing).

### 4.5 Dedup is enforced by the schema

The whole entity-dedup strategy rests on one entity = one node. The **primary key on the normalised name enforces this at the DB level** regardless of how `MERGE` behaves — you physically cannot create two `Compound` nodes named `"taurine"`. So even if Kùzu's `MERGE` semantics turn out weaker than Neo4j's, dedup is backstopped by the typed-table primary key. (Still verify `MERGE` — see §8.)

---

## 5. Entity resolution (the "one taurine node" problem)

No ontologies (ChEBI/MONDO/GO are over-engineering at this scale and require bio expertise to reason about). Instead, an **asymmetric** strategy, each half placed where the name enters the system:

### 5.1 Compounds — PubChem lookup at add-time
When you add a supplement (you typed the name, so you know what to look up), hit **PubChem's REST API** for its synonym list and write entries into a small synonym dict (a JSON file). Runs once per supplement (~20 lookups total), not in the abstract-ingestion hot path. PubChem returns synonyms as a structured list — no scraping, no LLM, no copyright concern.

> Endpoint shape to confirm before building (APIs drift): roughly
> `GET https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/taurine/synonyms/JSON`

### 5.2 Targets & Effects — LLM-normalise at extraction
These come *out* of the LLM (discovered during extraction), so there's no "add it" moment to pre-populate. Instead, instruct the extractor to **emit canonical full names** ("use the full standard disease/protein name, not abbreviations"). The model already knows "AD" = "Alzheimer disease" — that's the bio knowledge being outsourced anyway. Free, no second synonym table.

### 5.3 String-normalise backstop (both halves)
LLM normalisation is probabilistic — it'll mostly emit "Alzheimer disease" but occasionally "Alzheimer's disease". A trivial deterministic pass (lowercase, strip punctuation, collapse whitespace) before lookup/insert catches that drift cheaply. The two mechanisms backstop each other: LLM gets ~95% canonical, string-normalise mops up casing/punctuation.

### 5.4 LLM canonicalisation pass — *not* a human-glance pass
> **Correction to an earlier version of this spec.** The original §5.3 ended "you can eyeball the final node list and hand-merge any stragglers — a human glance is a legitimate pipeline step." That was wrong *for this operator*, and the reason exposes a principle violation worth stating.

The residual duplicates that survive string-normalisation fall into two kinds:

1. **Reading-comprehension drift** — casing/punctuation/whitespace, and *extraction artifacts* like concatenated strings (`nuclear factor erythroid 2related factor 2heme…`, the same protein name glued to its neighbour). These are not biology; a non-biologist catches them by reading, and string-normalise already handles the easy ones. The artifacts are better fixed upstream (the "spell out full names" instruction in §5.2/§6 is part of what produces them).
2. **Biological synonymy** — `gamma-aminobutyric acid` vs a `GABA-A receptor` (neurotransmitter ≠ receptor — a *non*-merge), `autophagy` vs `autophagic flux`, `reactive oxygen species` vs `oxidative stress`, or an entry mistyped as the wrong node type (`muscle protein synthesis` filed as a Target). **Deciding these requires domain knowledge the operator does not have.**

Parking kind 2 on a human glance violates guiding principle 3 ("put each job where the knowledge already exists") and principle 2 ("quarantine the biology"). The fix is the same move used everywhere else: **push the biological judgment to the LLM, keep the human on reading-comprehension QA.**

So entity dedup gets a dedicated step (run after load, before M2 queries):
- Dump the distinct `Compound` / `Target` / `Effect` name lists to the LLM.
- The LLM **clusters synonyms**, proposes **one canonical name per cluster**, and **flags entries that are not valid entities of that type** (wrong type, junk, non-entity).
- Output is written to a **reviewable proposal file**. The human reads the proposed clusters — *"does grouping these make sense?"* is reading comprehension; *deciding* they were the same was biology — and confirms.
- On confirm, an **apply** step rewrites the graph: relationships pointing at a merged-away node are repointed to the cluster's canonical node, and the orphaned nodes are deleted. The normalised-name primary key (§4.5) keeps the canonical unique.

This mirrors the source-quote QA principle (§4 principle 4): the operator checks the LLM's work by reading, never originates the biology. Flags are advisory in M1 (reported, not auto-deleted) so nothing destructive happens without a human reading it first.

> Known M1 limitation: merging two entities can leave two `Claim` nodes that are now logically identical (same compound/effect/direction/pmid, differing only by the just-merged entity name). Rare at this scale and surfaceable by query; claim-level re-dedup after entity merge is deferred.

---

## 6. Extraction (step 2) — the one bio-bearing step

Feed one abstract at a time; get back a JSON array of claims. Key prompt requirements:

- Output **only** valid JSON (no prose, no markdown fences) — parse it directly.
- Each claim object: `compound`, `target` (or null), `effect` (or null), `direction`, `dose_text`, `cohort_text`, `model`, `source_quote`.
- **Canonical full names** for target/effect; no abbreviations (per §5.2).
- **`source_quote` must be a verbatim sentence from the abstract** — this is non-negotiable; it's the only available QA check.
- `direction` from the fixed set: `increases | decreases | none | mixed`. **`none` (no significant effect) is a real, valuable claim** — capture it, don't discard it. It's how Milestone 2 answers "is there evidence *against*".
- If the abstract states no dose or cohort, emit `""` — don't invent.

> Validation loop you *can* run without bio knowledge: for a sample of extracted claims, check the structured `direction`/`compound`/`effect` is consistent with `source_quote`. Mismatches = extraction errors you can catch by reading.

---

## 7. Evidence scoring (simple rubric)

`evidence_score` is a 1–5 integer you assign from `model` and any stated N — no biology required, just study-design tiering:

| Score | Rough tier |
|-------|-----------|
| 5 | Human RCT, reasonable N |
| 4 | Human observational / cohort |
| 3 | Animal in vivo (e.g. mouse) |
| 2 | In vitro / cell study |
| 1 | Review, opinion, or web source |

Keep it this crude for M1. It exists so M2 can filter ("strong evidence only") and so on-demand claims (M3) can be flagged lower-trust until reviewed.

---

## 8. Open items to verify before/while building

1. **Kùzu `MERGE` semantics.** Write a 5-line test: `MERGE` the same compound twice, confirm exactly one node. Dedup is backstopped by the primary key regardless (§4.5), but confirm the ingestion pattern you intend to use behaves as expected.
2. **PubChem synonyms endpoint** — confirm current URL shape and response format (§5.1).
3. **PubMed E-utilities** — confirm query/fetch endpoints and any rate-limit / API-key requirements for step 1.
4. **Kùzu version** — build against current `docs.kuzudb.com`; verify the Python API (`kuzu.Database` / `kuzu.Connection` / `.execute()` / `.getAsDF()`) matches the installed version.

---

## 9. Milestone map (context for M1 choices)

- **M1 — this spec.** Build the KG: fetch → extract → normalise → load. Acceptance: a populated Kùzu graph plus a small hand-labelled sample measuring extraction precision (does the quote support the claim?).
- **M2 — Query application.** Ask the graph: what affects X, via what target, with what evidence, in what cohort. Includes at least one **multi-hop** query (`compound → shared target → effect` across compounds) — the query that earns the word "graph". Dose/cohort shown as raw text for the human to read.
- **M3 — On-demand ingestion.** Name a supplement not yet in the graph → run the M1 pipeline live. Mostly M1 plumbing behind a trigger. New claims flagged unreviewed/lower-trust (runtime extraction errors land in front of the user with no review step — matters in a supplements context).
- **M4 — Gap-driven retrieval.** The graph detects what it *doesn't* know and goes looking to fill it. The genuinely novel milestone. **Note:** its full version needs the graph to represent *typed absence* — distinguishing "no claims in this cohort" from "nobody looked" from "not yet ingested". At 20 supplements this is answerable by inspection; if M4 gets ambitious, that's the point to reconsider whether cohort should become a node (§4.3) and whether pathway-level structure (deliberately dropped with ontologies) is needed.

---

## 10. Accepted trade-offs (so they don't surprise you — or a reviewer — later)

- **No ontologies** → no hierarchy to traverse, so no *pathway-level* mechanistic inference ("is GABA-A in a pathway that also includes target X?"). Direct shared-target inference still works and is the more legible result anyway. Knowingly traded pathway reasoning for comprehensibility.
- **Raw-text dose/cohort** → can't compute across regimes yet. Added back later if wanted; costs nothing now.
- **Qualifiers as properties, not nodes** → the elegant graph-native "what's missing" query is deferred; a `GROUP BY` covers it at this scale.
- **Human-in-the-loop dedup** → fine at 20 supplements, would not survive BenchSci volume. The design is correct *for this size*, chosen deliberately.
- **LLM extraction, unvalidatable by domain knowledge** → mitigated by the mandatory source quote as a reading-comprehension QA check, not eliminated.
