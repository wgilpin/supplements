"""FastAPI + HTMX chat UI (spec §9).

A chat box posts a question to ``/ask``; the LLM router resolves it to a
``QueryRequest``, ``query.dispatch`` runs the fixed Cypher, and a rendered
fragment is appended to the transcript. Read-only: one ``read_only`` Kùzu
Database is opened at startup and a fresh Connection is made per request
(verified safe in Phase 0).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, cast

import re

import kuzu
import markdown
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import config, query, router, summarize
from ..logging_config import setup_logging
from ..normalise import is_compound_ingested

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    app.state.db = kuzu.Database(str(config.GRAPH_PATH))
    logger.info("opened graph read-write: %s", config.GRAPH_PATH)
    yield


app = FastAPI(title="Supplement Knowledge Graph", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/ask", response_class=HTMLResponse)
def ask(request: Request, q: Annotated[str, Form()]) -> HTMLResponse:
    conn = kuzu.Connection(request.app.state.db)
    req = router.route(conn, q)
    results = query.dispatch(conn, req)

    # Check if this query is about a compound that is missing from the database
    candidate_ingestion = None
    if req.query in ("compound", "bridge") and req.entity:
        resolved = query.resolve_entity(conn, req.entity, "compound")
        if not resolved or not is_compound_ingested(resolved):
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
    if req.query in ("compound", "effect", "target", "search", "intersection"):
        display = query.group_claims(cast("list[query.ClaimRow]", results))
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
            "summary": summary,
            "candidate_ingestion": candidate_ingestion
        },
    )


@app.post("/ingest", response_class=HTMLResponse)
async def ingest(request: Request, supplement: Annotated[str, Form()]) -> HTMLResponse:
    conn = kuzu.Connection(request.app.state.db)
    resolved = query.resolve_entity(conn, supplement, "compound")
    if not resolved or not is_compound_ingested(resolved):
        logger.info("Starting live ingestion for %r", supplement)
        try:
            from ..pipeline import ingest_supplement_async
            await ingest_supplement_async(conn, supplement)
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
                "summary": summary
            }
        )
    else:
        return HTMLResponse(
            content=f"<div class='alert alert-warning'>Ingestion completed, but no claims could be extracted for '<strong>{supplement}</strong>' (no abstracts found or no claims matched the criteria).</div>"
        )
