"""FastAPI + HTMX chat UI (spec §9).

A chat box posts a question to ``/ask``; the LLM router resolves it to a
``QueryRequest``, ``query.dispatch`` runs the fixed Cypher, and a rendered
fragment is appended to the transcript. Read-only: one ``read_only`` Kùzu
Database is opened at startup and a fresh Connection is made per request
(verified safe in Phase 0).
"""

from __future__ import annotations

import logging
import gc
from collections.abc import AsyncIterator, Generator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, cast

import re
import json
from functools import lru_cache

import kuzu
import markdown
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import config, query, router, summarize
from ..logging_config import setup_logging
from ..normalise import is_compound_ingested, load_synonyms, normalise_str
from ..pipeline import ingest_supplement_async
from ..canonicalise import propose, apply_map

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))


def render_markdown(text: str) -> str:
    # Ensure a blank line before list blocks for proper markdown rendering.
    lines = text.splitlines()
    if not lines:
        return ""

    result = [lines[0]]
    list_marker_pattern = re.compile(r"^\s*([*+-]|\d+\.)\s")

    for i in range(1, len(lines)):
        prev_line = lines[i - 1].strip()
        curr_line = lines[i]

        # If current line starts a list, but previous line was not empty and did not start a list
        if (
            list_marker_pattern.match(curr_line)
            and prev_line
            and not list_marker_pattern.match(prev_line)
        ):
            result.append("")
        result.append(curr_line)

    prepared_text = "\n".join(result)
    return markdown.markdown(prepared_text)


templates.env.filters["markdown"] = render_markdown


@lru_cache(maxsize=1024)
def get_pmid_display(pmid: str) -> str:
    if not pmid:
        return ""
    path = config.ABSTRACTS_DIR / f"{pmid}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            journal = data.get("journal")
            if journal:
                return str(journal).strip()
            authors = data.get("authors")
            if authors:
                return str(authors).strip()
        except Exception:
            pass
    return f"PMID {pmid}"


@lru_cache(maxsize=1024)
def get_pmid_title(pmid: str) -> str:
    if not pmid:
        return ""
    path = config.ABSTRACTS_DIR / f"{pmid}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            title = data.get("title")
            if title:
                return str(title).strip()
        except Exception:
            pass
    return f"PMID {pmid}"


templates.env.filters["pmid_display"] = get_pmid_display
templates.env.filters["pmid_title"] = get_pmid_title


def _static_version() -> str:
    """Cache-busting token for /static assets — newest mtime among them, so a
    changed CSS/JS file invalidates the browser cache on next page load."""
    static_dir = _BASE / "static"
    try:
        return str(int(max(f.stat().st_mtime for f in static_dir.glob("*"))))
    except ValueError:
        return "0"


templates.env.globals["static_v"] = _static_version()


def get_db_conn(request: Request) -> Generator[kuzu.Connection, None, None]:
    # Check if a test database has been injected via app.state.db, use it.
    test_db = getattr(request.app.state, "db", None)
    if test_db is not None:
        conn = kuzu.Connection(test_db)
        try:
            yield conn
        finally:
            del conn
    else:
        db = kuzu.Database(str(config.GRAPH_PATH))
        conn = kuzu.Connection(db)
        try:
            yield conn
        finally:
            del conn
            del db
            gc.collect()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    app.state.db = None
    logger.info("app started; database will be opened on demand: %s", config.GRAPH_PATH)
    yield


app = FastAPI(title="Supplementary", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/ask", response_class=HTMLResponse)
def ask(
    request: Request,
    q: Annotated[str, Form()],
    conn: Annotated[kuzu.Connection, Depends(get_db_conn)],
) -> HTMLResponse:
    req = router.route(conn, q)
    results = query.dispatch(conn, req)

    # Check if this query is about a compound that is missing from the database
    candidate_ingestion = None
    loose_match_resolved = None
    loose_match_resolved_ingested = False
    if req.query in ("compound", "bridge") and req.entity:
        resolved = query.resolve_entity(conn, req.entity, "compound")
        if resolved:
            synonyms = load_synonyms()
            norm_entity = normalise_str(req.entity)
            norm_resolved = normalise_str(resolved)
            is_exact_or_syn = (norm_resolved == norm_entity) or (synonyms.get(norm_entity) == resolved)
            if not is_exact_or_syn:
                candidate_ingestion = req.entity
                loose_match_resolved = resolved
                loose_match_resolved_ingested = is_compound_ingested(resolved)
            elif not is_compound_ingested(resolved):
                candidate_ingestion = req.entity
        else:
            candidate_ingestion = req.entity
    elif req.query == "intersection" and req.entities:
        for ent in req.entities:
            resolved = query.resolve_entity(conn, ent, "compound")
            if (not resolved or not is_compound_ingested(resolved)) and \
               not query.resolve_entity(conn, ent, "effect") and \
               not query.resolve_entity(conn, ent, "target"):
                candidate_ingestion = ent
                break

    # Claim results merge same-evidence rows (differ only by compound) for display.
    display: Sequence[BaseModel]
    matrices: list[query.CompoundMatrix] = []
    if req.query in ("compound", "effect", "target", "search", "intersection"):
        grouped = query.group_claims(cast("list[query.ClaimRow]", results))
        # Only drop self-referential labels when the queried entity is itself a
        # compound (compound/intersection). For effect/target queries the entity
        # IS the label axis, so excluding it would delete the answer.
        if req.query == "compound" and req.entity:
            exclude = [req.entity]
        elif req.query == "intersection":
            exclude = req.entities
        else:
            exclude = []
        matrices = query.build_matrices(grouped, exclude=exclude)
        display = grouped
    else:
        display = results

    # A plain supplement list has nothing to summarise — skip the LLM call.
    summary = "" if req.query == "list_supplements" else summarize.summarize(q, display)
    logger.info("ask %r -> query=%s entity=%r (%d rows, %d cards)",
                q, req.query, req.entity, len(results), len(display))
    return templates.TemplateResponse(
        request=request,
        name="_answer.html",
        context={
            "q": q,
            "req": req,
            "results": display,
            "matrices": matrices,
            "summary": summary,
            "candidate_ingestion": candidate_ingestion,
            "loose_match_resolved": loose_match_resolved,
            "loose_match_resolved_ingested": loose_match_resolved_ingested
        },
    )


@app.post("/ingest", response_class=HTMLResponse)
async def ingest(
    request: Request,
    supplement: Annotated[str, Form()],
    conn: Annotated[kuzu.Connection, Depends(get_db_conn)],
) -> HTMLResponse:
    resolved = query.resolve_entity(conn, supplement, "compound")
    
    if not resolved or not is_compound_ingested(resolved):
        logger.info("Starting live ingestion for %r", supplement)
        try:
            await ingest_supplement_async(conn, supplement)
            
            # Post-ingestion canonicalisation pass (principled deduplication)
            try:
                logger.info("Running automatic post-ingestion canonicalisation pass")
                proposal = propose(conn)
                apply_map(conn, proposal)
            except Exception as e:
                logger.warning("Post-ingestion canonicalisation failed: %s", e)
                
            resolved = query.resolve_entity(conn, supplement, "compound")
        except Exception as e:
            logger.exception("Failed to ingest %r", supplement)
            return HTMLResponse(
                content=f"<div class='alert alert-danger'>Error ingesting '<strong>{supplement}</strong>': {e}</div>"
            )

    if resolved:
        # Retrieve claims for the newly ingested compound
        results = query.claims_for_compound(conn, resolved, min_evidence=1)
        display = query.group_claims(results)
        summary = summarize.summarize(f"what does {resolved} do", display)

        return templates.TemplateResponse(
            request=request,
            name="_ingested_results.html",
            context={
                "req": query.QueryRequest(query="compound", entity=resolved),
                "results": display,
                "matrices": query.build_matrices(display, exclude=[resolved]),
                "summary": summary
            }
        )
    else:
        return HTMLResponse(
            content=f"<div class='alert alert-warning'>Ingestion completed, but no claims could be extracted for '<strong>{supplement}</strong>' (no abstracts found or no claims matched the criteria).</div>"
        )


@app.post("/canonicalise", response_class=HTMLResponse)
async def canonicalise_endpoint(
    request: Request,
    conn: Annotated[kuzu.Connection, Depends(get_db_conn)],
) -> HTMLResponse:
    logger.info("Starting manual canonicalisation/deduplication pass")
    try:
        proposal = propose(conn)
        merged = apply_map(conn, proposal)
        logger.info("Manual canonicalisation complete: %s", merged)
        summary = ", ".join(f"{k}: {v} merged" for k, v in merged.items())
        return HTMLResponse(
            content=f"<div class='alert alert-success'>Graph deduplicated successfully ({summary})</div>"
        )
    except Exception as e:
        logger.exception("Manual canonicalisation failed")
        return HTMLResponse(
            content=f"<div class='alert alert-danger'>Deduplication failed: {e}</div>"
        )
