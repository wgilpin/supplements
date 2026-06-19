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

import kuzu
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import config, query, router, summarize
from ..logging_config import setup_logging

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    app.state.db = kuzu.Database(str(config.GRAPH_PATH), read_only=True)
    logger.info("opened graph read-only: %s", config.GRAPH_PATH)
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

    # Claim results merge same-evidence rows (differ only by compound) for display.
    display: Sequence[BaseModel]
    if req.query in ("compound", "effect", "target", "search"):
        display = query.group_claims(cast("list[query.ClaimRow]", results))
    else:
        display = results

    summary = summarize.summarize(q, display)
    logger.info("ask %r -> query=%s entity=%r (%d rows, %d cards)",
                q, req.query, req.entity, len(results), len(display))
    return templates.TemplateResponse(
        request=request,
        name="_answer.html",
        context={"q": q, "req": req, "results": display, "summary": summary},
    )
