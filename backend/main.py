"""FastAPI application for the sovereign medical RAG chatbot."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import observability as obs
from config import get_settings
from document_loader import (
    bootstrap_collection,
    delete_document,
    list_documents,
    process_and_index_document,
)
from rag_pipeline import (
    close_http_client,
    get_embedder,
    get_qdrant_client,
    is_dependency_unavailable,
    query_rag,
    stream_rag,
)
from rate_limit import SlidingWindowRateLimiter

settings = get_settings()
obs.configure_logging(level=settings.log_level, json_output=settings.log_json)
logger = logging.getLogger("medibot")

LLM_UNAVAILABLE_DETAIL = "The language model service is unavailable. Please retry in a moment."
INTERNAL_ERROR_DETAIL = "Internal server error."
STORAGE_UNAVAILABLE_DETAIL = "The document store is unavailable. Please retry in a moment."
QUIET_PATHS = frozenset({"/health", "/ready", "/metrics"})

rate_limiter = SlidingWindowRateLimiter(
    max_requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_s,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    # Load the embedding model at startup so the first request does not pay for it.
    await run_in_threadpool(get_embedder)

    # Create the collection if needed: without it, the first question asked
    # before any ingestion would surface a Qdrant error instead of "I don't know".
    try:
        await run_in_threadpool(bootstrap_collection)
    except Exception as exc:
        # Qdrant may start after the API: /ready stays red and the orchestrator
        # will not route traffic until the dependency is up.
        logger.warning("Qdrant collection not initialised at startup: %s", exc)

    logger.info(
        "API ready.",
        extra={"environment": settings.environment, "model": settings.ollama_model},
    )
    try:
        yield
    finally:
        await close_http_client()


app = FastAPI(
    title="Medical RAG API",
    description="API for the sovereign, hospital-oriented medical chatbot",
    version="2.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)


# --------------------------------------------------------------------------- #
# Middlewares
# --------------------------------------------------------------------------- #

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # No cookie authentication: the API relies on the X-API-Key header.
    # Refusing credentials avoids opening the door to browser-authenticated
    # cross-site requests.
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """Correlate logs by request id and feed the HTTP metrics."""
    request_id = request.headers.get("X-Request-ID") or obs.new_request_id()
    token = obs.request_id_var.set(request_id)
    start = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        obs.request_id_var.reset(token)
        logger.exception("Unhandled request: %s %s", request.method, request.url.path)
        raise

    elapsed = time.perf_counter() - start
    # The route template (not the URL) bounds the cardinality of the series.
    route = request.scope.get("route")
    path = getattr(route, "path", None) or "unmatched"

    obs.HTTP_REQUESTS.inc(request.method, path, str(response.status_code))
    obs.HTTP_DURATION.observe(elapsed, request.method, path)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"

    logger.log(
        logging.DEBUG if path in QUIET_PATHS else logging.INFO,
        "%s %s -> %d",
        request.method,
        path,
        response.status_code,
        extra={"duration_ms": round(elapsed * 1000), "status": response.status_code},
    )
    obs.request_id_var.reset(token)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never return the traceback to the client: it can contain excerpts of
    # medical documents or infrastructure details.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": INTERNAL_ERROR_DETAIL})


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class Source(BaseModel):
    source: str
    page: int | None = None
    chunk_id: str | None = None
    score: float | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    metrics: dict = {}


class UploadResponse(BaseModel):
    message: str
    chunks_added: int


class DocumentSummary(BaseModel):
    source: str
    chunks: int
    pages: int
    indexed_at: str | None = None


class DeleteResponse(BaseModel):
    message: str
    chunks_removed: int


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    if not settings.api_key:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _client_identity(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        # Truncated fingerprint: the key never lands in a log or a dict key.
        return "key:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return "ip:" + forwarded.split(",")[0].strip()
    return "ip:" + (request.client.host if request.client else "unknown")


def enforce_rate_limit(request: Request):
    if not settings.rate_limit_enabled:
        return
    allowed, remaining, retry_after = rate_limiter.check(_client_identity(request))
    if not allowed:
        route = request.scope.get("route")
        obs.RATE_LIMITED.inc(getattr(route, "path", None) or "unmatched")
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please retry in a moment.",
            headers={"Retry-After": str(retry_after)},
        )
    request.state.rate_limit_remaining = remaining


PROTECTED = [Depends(require_api_key), Depends(enforce_rate_limit)]


def _storage_error(exc: Exception, context: str) -> HTTPException:
    """503 when the vector store is unreachable, 500 when the fault is ours."""
    if is_dependency_unavailable(exc):
        logger.warning("Qdrant unreachable (%s): %s", context, exc)
        return HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE_DETAIL)
    logger.exception("%s", context)
    return HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


def _safe_pdf_path(filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    return settings.upload_dir / f"{uuid4().hex}.pdf"


async def _write_upload(file: UploadFile, destination: Path) -> int:
    total = 0
    with destination.open("wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"PDF too large. Limit: {settings.max_upload_mb} MB.",
                )
            buffer.write(chunk)
    return total


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


@app.get("/health", tags=["operations"])
async def health_check():
    """Liveness: the process responds. Does not test any external dependency."""
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.get("/ready", tags=["operations"])
async def readiness_check():
    """Readiness: the dependencies the RAG needs are reachable."""
    try:
        collections_response = await run_in_threadpool(get_qdrant_client().get_collections)
        collections = [collection.name for collection in collections_response.collections]

        async with httpx.AsyncClient(timeout=5.0) as http:
            ollama_response = await http.get(f"{settings.ollama_host}/api/tags")
            ollama_response.raise_for_status()
            available_models = [
                model.get("name", "") for model in ollama_response.json().get("models", [])
            ]

        # A missing model would otherwise only surface on the first question,
        # minutes after rollout: better to block going live.
        model_ready = any(
            name == settings.ollama_model or name.startswith(f"{settings.ollama_model}:")
            for name in available_models
        )
        if not model_ready:
            raise HTTPException(
                status_code=503,
                detail=f"Model '{settings.ollama_model}' is not present in Ollama. Pull it first.",
            )

        return {
            "status": "ok",
            "qdrant_connected": True,
            "ollama_connected": True,
            "collection": settings.collection_name,
            "collection_exists": settings.collection_name in collections,
            "model": settings.ollama_model,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Readiness check failed: %s", e)
        raise HTTPException(
            status_code=503, detail="Dependencies unavailable (Qdrant or Ollama)."
        ) from e


@app.get("/metrics", include_in_schema=False)
async def metrics(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Prometheus exposition endpoint."""
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled.")
    if settings.metrics_require_api_key:
        require_api_key(x_api_key)
    return PlainTextResponse(obs.render_metrics(), media_type=obs.CONTENT_TYPE_PROMETHEUS)


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


@app.post("/upload", response_model=UploadResponse, dependencies=PROTECTED, tags=["ingestion"])
async def upload_document(file: UploadFile = File(...)):
    """Ingest a PDF into the knowledge base (replaces any existing version)."""
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    file_path = _safe_pdf_path(file.filename or "document.pdf")
    try:
        await _write_upload(file, file_path)
        num_chunks = await run_in_threadpool(
            process_and_index_document, str(file_path), file.filename
        )
        return UploadResponse(
            message=f"Document {file.filename} indexed successfully.",
            chunks_added=num_chunks,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise _storage_error(e, f"Failed to index document '{file.filename}'") from e
    finally:
        file_path.unlink(missing_ok=True)


@app.post("/ingest/demo", response_model=UploadResponse, dependencies=PROTECTED, tags=["ingestion"])
async def ingest_demo_documents():
    """Index the bundled documents used for local demos and integration tests."""
    if not settings.demo_docs_dir.exists():
        raise HTTPException(
            status_code=404, detail="Demo documents directory not found."
        )

    pdfs = sorted(settings.demo_docs_dir.glob("*.pdf"))
    if not pdfs:
        raise HTTPException(status_code=404, detail="No demo PDF found.")

    total_chunks = 0
    try:
        for pdf in pdfs:
            total_chunks += await run_in_threadpool(process_and_index_document, str(pdf))
    except Exception as e:
        raise _storage_error(e, "Failed to ingest the demo documents") from e

    return UploadResponse(
        message=f"{len(pdfs)} demo documents indexed.",
        chunks_added=total_chunks,
    )


@app.get(
    "/documents",
    response_model=list[DocumentSummary],
    dependencies=PROTECTED,
    tags=["ingestion"],
)
async def get_documents():
    """Inventory of indexed documents: required to audit the knowledge base."""
    try:
        return await run_in_threadpool(list_documents)
    except Exception as e:
        raise _storage_error(e, "Failed to list documents") from e


@app.delete(
    "/documents/{source_name}",
    response_model=DeleteResponse,
    dependencies=PROTECTED,
    tags=["ingestion"],
)
async def remove_document(source_name: str):
    """Remove a document from the index (retiring a stale protocol, right to erasure)."""
    try:
        removed = await run_in_threadpool(delete_document, source_name)
    except Exception as e:
        raise _storage_error(e, f"Failed to delete document '{source_name}'") from e

    if removed == 0:
        raise HTTPException(status_code=404, detail=f"Document '{source_name}' not found.")

    return DeleteResponse(
        message=f"Document {source_name} removed from the index.",
        chunks_removed=removed,
    )


# --------------------------------------------------------------------------- #
# Querying
# --------------------------------------------------------------------------- #


@app.post("/query", response_model=QueryResponse, dependencies=PROTECTED, tags=["rag"])
async def query_document(request: QueryRequest):
    """Ask the RAG a question over the indexed medical documents (full answer)."""
    try:
        history = [message.model_dump() for message in request.history]
        answer, sources, metrics = await query_rag(request.query, history)
        return QueryResponse(answer=answer, sources=sources, metrics=metrics)
    except httpx.HTTPError as e:
        logger.error("LLM unreachable on /query: %s", e)
        raise HTTPException(status_code=503, detail=LLM_UNAVAILABLE_DETAIL) from e
    except Exception as e:
        raise _storage_error(e, "Unexpected error on /query") from e


@app.post("/query/stream", dependencies=PROTECTED, tags=["rag"])
async def query_document_stream(payload: QueryRequest, request: Request):
    """Ask the RAG a question and stream the answer token by token (SSE)."""
    history = [message.model_dump() for message in payload.history]

    async def event_source():
        try:
            async for event in stream_rag(payload.query, history):
                # If the tab is closed, stop generating rather than keep holding
                # an LLM slot for nobody.
                if await request.is_disconnected():
                    logger.info("Client disconnected: generation aborted.")
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except httpx.HTTPError as e:
            logger.error("LLM unreachable on /query/stream: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'detail': LLM_UNAVAILABLE_DETAIL})}\n\n"
        except Exception:
            logger.exception("Unexpected error on /query/stream")
            yield f"data: {json.dumps({'type': 'error', 'detail': INTERNAL_ERROR_DETAIL})}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
