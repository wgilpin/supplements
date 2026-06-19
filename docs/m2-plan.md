# Milestone 2 — Implementation Plan

Build the **query application** over the M1 graph: ask the graph what affects X, via what target, with what evidence, in what cohort — including the multi-hop `compound → shared target → effect` query that "earns the word graph" (spec [§9](supplement-kg-m1-spec.md)). A FastAPI + HTMX web app with a chat box, driven by a thin LLM router over a fixed set of hand-written, parameterised Cypher queries.

M2 is **read-only** against `data/kg.kuzu`. It adds no pipeline behaviour and does not modify the graph.

## Locked decisions (this run)

| Choice | Value |
|--------|-------|
| Interface | **FastAPI + HTMX + Jinja2**, server-rendered. Minimal JS (any JS in a static file, never inline — global rule). |
| Query construction | **Fixed parameterised Cypher.** A hand-written query registry. The LLM never emits Cypher. |
| Chat | **Chat box → thin LLM router.** Gemini 3-series resolves free text → `(query name, params)` against the fixed menu, then dispatch runs the canned Cypher. |
| Router LLM | **`gemini-3.5-flash`** (reuse `config.GEMINI_MODEL`; structured output via `response_schema`, same pattern as `extract.py`). |
| Storage | **Kùzu embedded, unchanged.** The constitution's Postgres clause does **not** apply — the graph DB is this project's reason for being (spec §3). Dev runs locally via `uv run`. |
| Deploy | **Docker via OrbStack.** The app is containerised for deployment (Kùzu file baked into the image or mounted as a volume). Dev stays local; the Dockerfile is a late, additive phase. |
| Query menu | 4 essentials + contradictions + list helpers (see below). |
| Typing | Strong typing throughout — pydantic models / `Literal` / `TypedDict`, **never bare dicts** for args/returns; avoid `Any`. Functional style. |
| Tooling | **ruff** + **mypy** in the dev group (added); all M2 code passes both before saving. |
| Logging | `logging` to **both a file and the terminal** (file + stream handler) throughout; no `print`, no silent exceptions without a log line. |

## Constitution → how it applies here

- **TDD** the query layer (`skg/query.py`) against a temp Kùzu DB seeded with a tiny known fixture graph (`tmp_path`, never `data/kg.kuzu`).
- **No unit tests** for the router (it calls the LLM — no point per the rule), the FastAPI routes, or the Jinja templates.
- **Never call remote APIs in tests** — there are none in the tested layer; the router is excluded by the rule above.
- Kùzu analog of "never use the live DB as the test DB": tests use `tmp_path`; the live `data/kg.kuzu` is off-limits to tests (same as M1).

## Dependencies

Already added (this run): `ruff`, `mypy` (dev group; config in `pyproject.toml`).

To add when M2 build starts:
```
fastapi            # web framework
uvicorn            # ASGI server (uv run uvicorn ...)
jinja2             # server-side templates
python-multipart   # form posts from the HTMX chat box
```

`google-genai`, `kuzu`, `pydantic`, `python-dotenv` already present (reused). No new runtime LLM/HTTP deps beyond the web stack.

**Logging setup:** a small `skg/logging_config.py` (or `config.py` helper) wiring a root logger with a `StreamHandler` (terminal) **and** a `FileHandler` (e.g. `data/logs/skg.log`, gitignored). Used by M2 modules; M1 `print`s left as-is unless you ask to migrate them.

## Project layout (additive — nothing in M1 moves)

```
skg/
  query.py         # the query registry — fixed parameterised Cypher (TDD'd core)
  router.py        # LLM intent router: free text -> QueryRequest (no unit test)
  web/
    app.py         # FastAPI app: GET / (shell) + POST /ask (chat turn -> fragment)
    templates/
      index.html   # chat shell
      _answer.html  # one rendered answer (claim cards / bridge / contradictions)
    static/
      app.js       # only if strictly needed (e.g. scroll-to-bottom); else omitted
data/
  kg.kuzu          # read-only here                                  [gitignored]
tests/
  test_query.py    # query layer against a seeded temp graph
```

`query.py` opens its read connection via the existing `graph.connect(path)`; result rows are typed pydantic models, not DataFrames leaking out of the module.

---

## Query menu

All take a `kuzu.Connection` first (functional), return typed pydantic models, and apply an `evidence_score >= min_evidence` filter where noted. Entity-name params are resolved (§ Entity resolution) before the query runs.

**The 4 essentials** (what spec §9 requires):

| Function | Question | Returns |
|----------|----------|---------|
| `claims_for_compound(conn, compound, min_evidence=1)` | "What does taurine do?" | `list[ClaimRow]` |
| `claims_for_effect(conn, effect, min_evidence=1)` | "What affects anxiety?" | `list[ClaimRow]` |
| `claims_for_target(conn, target, min_evidence=1)` | "What acts on GABA-A?" | `list[ClaimRow]` |
| `shared_target_bridge(conn, compound, min_evidence=1)` | **multi-hop** — other compounds sharing a target with this one, and the effects each reaches | `list[BridgeRow]` |

**Extras:**

| Function | Question | Returns |
|----------|----------|---------|
| `contradictions(conn, effect=None)` | same compound→effect with opposing `direction` (spec §4.1) | `list[ContradictionRow]` |
| `list_compounds(conn)` / `list_effects(conn)` / `list_targets(conn)` | distinct names; feed the router's valid-entity set | `list[str]` |

### Typed result models (pydantic; no bare dicts)

```python
class ClaimRow(BaseModel):
    compound: str
    target: str | None
    effect: str | None
    direction: Direction            # reuse schema.Direction Literal
    evidence_score: int
    model: str
    dose_text: str                  # shown raw (spec §4.3)
    cohort_text: str                # shown raw
    source_pmid: str
    source_quote: str               # the QA quote, always displayed

class BridgeRow(BaseModel):
    shared_target: str
    other_compound: str
    effect: str | None
    direction: Direction
    evidence_score: int
    source_pmid: str
    source_quote: str

class ContradictionRow(BaseModel):
    compound: str
    effect: str
    direction_a: Direction
    direction_b: Direction
    pmid_a: str
    pmid_b: str
```

### The multi-hop query (the one that earns "graph")

```cypher
MATCH (c1:Compound {name: $compound})-[:HAS_CLAIM]->(:Claim)-[:ON_TARGET]->(t:Target)
      <-[:ON_TARGET]-(cl2:Claim)<-[:HAS_CLAIM]-(c2:Compound)
WHERE c2.name <> $compound AND cl2.evidence_score >= $min_evidence
OPTIONAL MATCH (cl2)-[:HAS_EFFECT]->(e:Effect)
RETURN t.name AS shared_target, c2.name AS other_compound,
       e.name AS effect, cl2.direction AS direction,
       cl2.evidence_score AS evidence_score, cl2.source_pmid AS source_pmid,
       cl2.source_quote AS source_quote
```
*(Exact Cypher verified during Phase 1 against the live graph; rel names `HAS_CLAIM`/`ON_TARGET`/`HAS_EFFECT` per `graph.py`.)*

### Dispatch (typed, no Any-registry)

A single `QueryRequest` model + a `dispatch(conn, req) -> QueryResult` that `match`es on `req.query` and calls the right typed function. This keeps strong typing instead of a `dict[str, Callable[..., Any]]`.

```python
QueryName = Literal["compound", "effect", "target", "bridge",
                    "contradictions", "unknown"]

class QueryRequest(BaseModel):
    query: QueryName
    entity: str | None = None       # raw compound/effect/target name from the user
    min_evidence: int = 1
```

`QueryResult` is a typed union (`list[ClaimRow] | list[BridgeRow] | list[ContradictionRow]`) tagged for the template.

### Entity resolution (router params → real node names)

`resolve_entity(conn, raw, kind) -> str | None`: `normalise_str(raw)`, then (1) exact match against `list_<kind>()`, (2) substring/contains fallback so "GABA" → "gamma-aminobutyric acid type A receptor". No fuzzy-match library (KISS). Returns `None` → the UI says "I don't have anything on *X*."

---

## Build sequence

### Phase 0 — Verify open items (spike, throwaway)
Mirrors the M1 §8 discipline — confirm before relying on it:
1. **Kùzu read concurrency** — confirm `kuzu.Database(path, read_only=True)` exists in 0.11.3 and that a Connection-per-request (or shared) pattern works under FastAPI's threadpool without races. Decide: one read-only `Database` at startup, new `Connection` per request.
2. **Single-writer interaction** — confirm a read-only web handle does not deadlock against the pipeline writer; document "don't run the pipeline while the web app holds the DB" (consistent with the existing single-writer constraint).
3. **Router structured output** — one NL question → `QueryRequest` via `response_schema=QueryRequest`, no prose. Confirm the `Literal` query name comes back clean.

### Phase 1 — Query layer (`skg/query.py`) — TDD
- Write `test_query.py` first: seed a tiny known graph in `tmp_path` (2 compounds sharing a target, a couple of effects, a contradiction pair), then assert each query returns the expected typed rows, including the `min_evidence` filter and the bridge.
- Implement the typed result models + the six query functions + `resolve_entity` to green.
- Verify the bridge Cypher against the live `data/kg.kuzu` manually (read-only) as a sanity check.

### Phase 2 — Router (`skg/router.py`) — no unit test
- `route(conn, question: str) -> QueryRequest` via Gemini structured output. Prompt: pick one query name from the fixed menu + extract the entity + optional `min_evidence`; return `query="unknown"` if it doesn't map. Pass the `list_*` names in as context so it picks real entities.
- Logging on every routing decision; transient-error retry reused from the `extract.py` pattern.

### Phase 3 — Web app (`skg/web/`) — no unit test
- `GET /` → chat shell. `POST /ask` (form field `q`) → `route` → `dispatch` → render `_answer.html` fragment, appended to the transcript (`hx-post`, `hx-swap="beforeend"`).
- Each answer renders claim cards: direction badge, evidence score, **raw** `dose_text`/`cohort_text`, and the `source_quote` prominently (the QA mechanism). Bridge and contradiction results get their own card layouts.
- **Claim cards merge same-evidence rows.** `query.group_claims` (TDD'd, pure) collapses claims that share everything but the compound (same pmid/quote/effect/direction) into one `ClaimGroup` listing the compounds — fixes the "one sentence mentions three compounds → three identical cards" case.
- **Grounded NL answer above the cards.** `skg/summarize.py` asks the LLM for a 1–3 sentence answer to the question, grounded ONLY in the retrieved rows (no Cypher, best-effort — failure still renders the cards). This is a second LLM call in the response path (route + summarize), added at the user's request.
- **Evidence collapsed by default.** Cards live in a native `<details>` ("N supporting claims"), rolled up so the prose answer leads; expand to read the claims. Zero JS (native element).
- **Progress indicator.** HTMX `hx-indicator` shows an animated "searching the graph…" while the two LLM round-trips (route + summarize) run; the Ask button disables via `hx-disabled-elt`.
- HTMX via CDN; JS kept to scroll-to-bottom + input-reset in `static/app.js`. Styling in `static/style.css`.

### Phase 4 — Containerise (deploy)
- Dockerfile for the FastAPI app (uv-based, slim Python base). Kùzu file baked in or mounted; runs under OrbStack. Additive and late — dev does not need it.

### Tooling (done this run)
- `ruff` + `mypy` added to the dev group; config in `pyproject.toml`. `ruff check` is **clean** repo-wide (3 trivial M1 lint issues fixed). mypy has **6 pre-existing M1 errors** — all external-stub friction (Kùzu `.execute()` union return; genai `resp.parsed`), not bugs. M2 code is written to pass mypy; M1 cleanup is a separate decision (see open question).

---

## Testing notes (per constitution)
- TDD only the query layer; **no** unit tests for router / FastAPI routes / templates.
- Seeded temp graph via `tmp_path`; never touch `data/kg.kuzu` in tests. No live PubMed/PubChem/Gemini calls.

## Verification / done criteria
1. `uv run uvicorn skg.web.app:app` serves the chat UI locally against `data/kg.kuzu`.
2. Each of the 4 essential queries returns correct results via the chat box on the live graph.
3. The **multi-hop bridge** returns a real cross-compound `compound → shared target → effect` result (the §9 acceptance query).
4. `contradictions()` surfaces at least one opposing-direction pair (if any exist in the graph).
5. Every answer card shows the verbatim `source_quote` and raw dose/cohort.
6. `pytest` green; `ruff` clean; `mypy` clean.
