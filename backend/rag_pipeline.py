"""RAG pipeline: Qdrant retrieval + Ollama generation (async, streaming)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import TYPE_CHECKING

import httpx
from qdrant_client import QdrantClient, models
from starlette.concurrency import run_in_threadpool

import observability as obs
from config import get_settings

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)
settings = get_settings()
COLLECTION_NAME = settings.collection_name

# `{language}` is filled from LLM_RESPONSE_LANGUAGE so the same prompt serves a
# French hospital and an English-speaking evaluator without a code change.
SYSTEM_PROMPT_TEMPLATE = """You are a sovereign medical assistant specialised in analysing clinical protocols and health documents.
Answer questions from healthcare professionals precisely, based EXCLUSIVELY on the context provided below.
If you cannot find the answer in the context, say so plainly and do not speculate.
Do not give general medical advice outside the context.
Reproduce numeric values (dosages, durations, thresholds) EXACTLY as written in the context; never complete them from memory.
Cite the sources you used as [source page X] when they are available.
Answer in {language}, concisely (10 lines maximum), usefully and professionally.

Context:
{context}"""

# Base delay for exponential backoff between LLM attempts.
RETRY_BASE_DELAY_S = 0.5

# Caps concurrent generations: beyond this they compete for the CPU and all of
# them slow down. Excess requests wait their turn.
_llm_semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
_http_client: httpx.AsyncClient | None = None


# --------------------------------------------------------------------------- #
# Shared clients
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(**settings.qdrant_kwargs)


def get_http_client() -> httpx.AsyncClient:
    """Single HTTP client: connection pooling avoids a TCP handshake per request."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ollama_timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    # Deferred import: `sentence_transformers` pulls in torch (several seconds
    # and hundreds of megabytes). Keeping it out of module import speeds up
    # worker startup and lets the RAG layer be tested without torch installed.
    import torch
    from sentence_transformers import SentenceTransformer

    # Without this cap each uvicorn worker claims every core and the processes
    # steal CPU from one another (throughput drops as workers are added).
    torch.set_num_threads(max(1, settings.embedding_threads))

    logger.info("Loading embedding model: %s", settings.embedding_model)
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embedder().encode(texts, batch_size=32, show_progress_bar=False).tolist()


@lru_cache(maxsize=settings.embedding_cache_size)
def _embed_query_cached(query: str) -> tuple[float, ...]:
    return tuple(get_embedder().encode(query).tolist())


def embed_query(query: str) -> list[float]:
    """Vectorise a question. Repeated questions (demos, dashboards) are cached."""
    return list(_embed_query_cached(query))


def _reset_caches() -> None:
    """Test hook: clears the memoised singletons."""
    get_qdrant_client.cache_clear()
    get_embedder.cache_clear()
    _embed_query_cached.cache_clear()


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


def _extract_point(point) -> dict:
    payload = point.payload or {}
    metadata = payload.get("metadata") or payload
    content = payload.get("page_content") or payload.get("content") or ""
    return {"content": content, "metadata": metadata, "score": float(point.score)}


def _retrieve_sync(query: str) -> list[dict]:
    """Blocking vector search — call through run_in_threadpool."""
    try:
        response = get_qdrant_client().query_points(
            collection_name=COLLECTION_NAME,
            query=embed_query(query),
            limit=settings.rag_top_k,
            score_threshold=settings.min_relevance_score,
            with_payload=True,
            search_params=models.SearchParams(hnsw_ef=settings.qdrant_hnsw_ef),
        )
    except Exception as exc:
        # Empty knowledge base (nothing ingested yet) is not an outage, it is an
        # absence of knowledge. Answer "I don't know" rather than returning 500.
        if _is_missing_collection(exc):
            logger.warning("Collection '%s' missing: no document indexed yet.", COLLECTION_NAME)
            return []
        raise

    docs = [_extract_point(point) for point in response.points]
    return [doc for doc in docs if doc["content"]]


def _is_missing_collection(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    message = str(exc).lower()
    return "not found" in message and "collection" in message


def is_dependency_unavailable(exc: BaseException) -> bool:
    """Tell "Qdrant is unreachable" apart from a genuine application bug.

    The API must answer 503 in the former case: a 500 would send engineers
    hunting for a code regression while the failure is downstream, and would
    stop a load balancer from retrying against another instance.
    """
    if isinstance(exc, httpx.TransportError | ConnectionError | TimeoutError | OSError):
        return True
    # qdrant-client wraps network errors; the original exception is exposed via
    # `source` (ResponseHandlingException) or __cause__.
    for nested in (getattr(exc, "source", None), exc.__cause__):
        if nested is not None and nested is not exc and is_dependency_unavailable(nested):
            return True
    return False


def _source_label(doc: dict) -> str:
    metadata = doc["metadata"]
    source = metadata.get("source_name") or metadata.get("source") or "Unknown document"
    page = metadata.get("page")
    if page is None:
        return source
    return f"{source} page {int(page) + 1}"


def _build_context(scored_docs: list[dict]) -> tuple[str, list[dict]]:
    """Assemble the context within a character budget; also return the chunks actually used."""
    sections = []
    used_docs = []
    total_chars = 0

    for doc in scored_docs:
        # No score in the label: the model would copy it into its citations.
        section = f"[{_source_label(doc)}]\n{doc['content'].strip()}"
        if total_chars + len(section) > settings.max_context_chars:
            break
        sections.append(section)
        used_docs.append(doc)
        total_chars += len(section)

    return "\n\n---\n\n".join(sections), used_docs


def _format_sources(scored_docs: list[dict]) -> list[dict]:
    sources = []
    seen = set()
    for doc in scored_docs:
        metadata = doc["metadata"]
        payload = {
            "source": metadata.get("source_name") or metadata.get("source", "Unknown document"),
            "page": (int(metadata["page"]) + 1) if metadata.get("page") is not None else None,
            "chunk_id": metadata.get("chunk_id"),
            "score": round(float(doc["score"]), 4),
        }
        key = (payload["source"], payload["page"], payload["chunk_id"])
        if key not in seen:
            sources.append(payload)
            seen.add(key)
    return sources


def _build_messages(context: str, question: str, history: list[dict]) -> list[dict]:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        language=settings.llm_response_language, context=context
    )
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history[-settings.max_history_messages:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})
    return messages


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def _ollama_payload(messages: list[dict], stream: bool) -> dict:
    return {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": stream,
        "keep_alive": settings.ollama_keep_alive,
        "options": {
            "temperature": settings.llm_temperature,
            "num_predict": settings.llm_max_tokens,
            "num_ctx": settings.llm_num_ctx,
        },
    }


async def generate_answer(messages: list[dict]) -> str:
    # Consume the stream rather than issuing a blocking POST: if the call is
    # abandoned (client timeout, disconnect), Ollama cancels generation at the
    # next token instead of running on and blocking the request queue.
    parts = [token async for token in stream_answer(messages)]
    return "".join(parts)


async def _stream_once(messages: list[dict]) -> AsyncIterator[str]:
    client = get_http_client()
    async with client.stream(
        "POST",
        f"{settings.ollama_host}/api/chat",
        json=_ollama_payload(messages, stream=True),
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping non-JSON line in the Ollama stream: %.120s", line)
                continue
            if error := data.get("error"):
                raise RuntimeError(f"Ollama returned an error: {error}")
            token = data.get("message", {}).get("content", "")
            if token:
                yield token
            if data.get("done"):
                return


async def stream_answer(messages: list[dict]) -> AsyncIterator[str]:
    """Stream the LLM answer, retrying only while no token has been emitted."""
    async with _llm_semaphore:
        obs.LLM_INFLIGHT.inc()
        try:
            for attempt in range(settings.ollama_max_retries + 1):
                emitted = False
                try:
                    async for token in _stream_once(messages):
                        emitted = True
                        yield token
                    return
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    kind = type(exc).__name__
                    # Replaying after a partial answer would produce duplicated,
                    # incoherent text: only retry from a cold start.
                    if emitted or attempt == settings.ollama_max_retries:
                        obs.LLM_ERRORS.inc(kind)
                        raise
                    delay = RETRY_BASE_DELAY_S * (2**attempt)
                    logger.warning(
                        "Ollama call failed (%s), retrying in %.1fs (%d/%d).",
                        kind,
                        delay,
                        attempt + 1,
                        settings.ollama_max_retries,
                    )
                    await asyncio.sleep(delay)
                except Exception:
                    obs.LLM_ERRORS.inc("unexpected")
                    raise
        finally:
            obs.LLM_INFLIGHT.dec()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _empty_metrics(retrieval_ms: int) -> dict:
    return {"retrieved_chunks": 0, "used_chunks": 0, "retrieval_ms": retrieval_ms}


async def _retrieve(query: str) -> tuple[list[dict], int]:
    start = time.perf_counter()
    docs = await run_in_threadpool(_retrieve_sync, query)
    elapsed = time.perf_counter() - start
    obs.RAG_RETRIEVAL_DURATION.observe(elapsed)
    obs.RAG_CHUNKS_RETRIEVED.observe(len(docs))
    return docs, round(elapsed * 1000)


async def query_rag(query: str, history: list[dict] | None = None) -> tuple[str, list[dict], dict]:
    docs, retrieval_ms = await _retrieve(query)

    if not docs:
        obs.RAG_QUERIES.inc("no_context")
        return settings.llm_no_context_answer, [], _empty_metrics(retrieval_ms)

    context, used_docs = _build_context(docs)
    messages = _build_messages(context, query, history or [])

    generation_start = time.perf_counter()
    answer = await generate_answer(messages)
    generation_elapsed = time.perf_counter() - generation_start
    obs.RAG_GENERATION_DURATION.observe(generation_elapsed)
    obs.RAG_QUERIES.inc("answered")

    metrics = {
        "retrieved_chunks": len(docs),
        "used_chunks": len(used_docs),
        "retrieval_ms": retrieval_ms,
        "generation_ms": round(generation_elapsed * 1000),
        "model": settings.ollama_model,
    }
    logger.info("RAG query handled.", extra={"rag_metrics": metrics})
    return answer.strip(), _format_sources(used_docs), metrics


async def stream_rag(query: str, history: list[dict] | None = None) -> AsyncIterator[dict]:
    """Emit SSE events: sources first, then tokens, then final metrics."""
    docs, retrieval_ms = await _retrieve(query)

    if not docs:
        obs.RAG_QUERIES.inc("no_context")
        yield {"type": "sources", "sources": []}
        yield {"type": "token", "content": settings.llm_no_context_answer}
        yield {"type": "done", "metrics": _empty_metrics(retrieval_ms)}
        return

    context, used_docs = _build_context(docs)
    yield {"type": "sources", "sources": _format_sources(used_docs)}

    messages = _build_messages(context, query, history or [])
    generation_start = time.perf_counter()
    async for token in stream_answer(messages):
        yield {"type": "token", "content": token}

    generation_elapsed = time.perf_counter() - generation_start
    obs.RAG_GENERATION_DURATION.observe(generation_elapsed)
    obs.RAG_QUERIES.inc("answered")

    metrics = {
        "retrieved_chunks": len(docs),
        "used_chunks": len(used_docs),
        "retrieval_ms": retrieval_ms,
        "generation_ms": round(generation_elapsed * 1000),
        "model": settings.ollama_model,
    }
    logger.info("RAG query handled (stream).", extra={"rag_metrics": metrics})
    yield {"type": "done", "metrics": metrics}
