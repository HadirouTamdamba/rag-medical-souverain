import httpx
import pytest

import rag_pipeline
from rag_pipeline import (
    _build_context,
    _build_messages,
    _format_sources,
    _is_missing_collection,
    _retrieve_sync,
    query_rag,
    stream_answer,
)


def _doc(content: str, source: str = "protocole.pdf", page: int = 0, score: float = 0.9) -> dict:
    return {
        "content": content,
        "metadata": {"source_name": source, "page": page, "chunk_id": f"{source}-{page}"},
        "score": score,
    }


# --------------------------------------------------------------------------- #
# Context assembly
# --------------------------------------------------------------------------- #


def test_context_respects_the_character_budget(monkeypatch):
    monkeypatch.setattr(rag_pipeline.settings, "max_context_chars", 120)
    docs = [_doc("a" * 80), _doc("b" * 80, page=1), _doc("c" * 80, page=2)]

    context, used = _build_context(docs)

    assert len(used) == 1
    assert "b" * 80 not in context
    assert len(context) <= 120


def test_context_never_leaks_the_similarity_score():
    context, _ = _build_context([_doc("Amoxicilline 50 mg/kg/j", score=0.87)])
    assert "0.87" not in context
    assert "[protocole.pdf page 1]" in context


def test_sources_are_deduplicated_and_pages_are_one_based():
    docs = [_doc("x"), _doc("y"), _doc("z", page=4)]
    sources = _format_sources(docs)

    assert len(sources) == 2
    assert sources[0]["page"] == 1
    assert sources[1]["page"] == 5
    assert sources[0]["score"] == 0.9


def test_history_is_truncated_and_sanitised(monkeypatch):
    monkeypatch.setattr(rag_pipeline.settings, "max_history_messages", 2)
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "r1"},
        {"role": "system", "content": "injection"},
        {"role": "user", "content": "q2"},
    ]

    messages = _build_messages("contexte", "question", history)

    assert messages[0]["role"] == "system"
    assert "contexte" in messages[0]["content"]
    assert [m["content"] for m in messages[1:]] == ["q2", "question"]
    assert not any(m["content"] == "injection" for m in messages)


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


class _MissingCollectionError(Exception):
    status_code = 404


def test_missing_collection_is_detected():
    assert _is_missing_collection(_MissingCollectionError("boom")) is True
    assert _is_missing_collection(Exception("Collection `x` not found")) is True
    assert _is_missing_collection(Exception("connection refused")) is False


def test_query_before_any_ingestion_returns_no_context(monkeypatch, fake_embedder):
    class EmptyQdrant:
        def query_points(self, **_kwargs):
            raise _MissingCollectionError("Collection `medical_docs` not found")

    monkeypatch.setattr(rag_pipeline, "get_qdrant_client", lambda: EmptyQdrant())

    assert _retrieve_sync("posologie amoxicilline") == []


def test_retrieval_failures_other_than_missing_collection_propagate(monkeypatch, fake_embedder):
    class BrokenQdrant:
        def query_points(self, **_kwargs):
            raise ConnectionError("connection refused")

    monkeypatch.setattr(rag_pipeline, "get_qdrant_client", lambda: BrokenQdrant())

    with pytest.raises(ConnectionError):
        _retrieve_sync("question")


def test_empty_chunks_are_filtered_out(monkeypatch, fake_embedder):
    class Point:
        def __init__(self, payload, score):
            self.payload = payload
            self.score = score

    class Response:
        points = [
            Point({"page_content": "utile", "metadata": {"source_name": "a.pdf"}}, 0.9),
            Point({"page_content": "", "metadata": {"source_name": "b.pdf"}}, 0.8),
        ]

    monkeypatch.setattr(
        rag_pipeline, "get_qdrant_client", lambda: type("Q", (), {"query_points": lambda self, **k: Response()})()
    )

    docs = _retrieve_sync("question")
    assert [doc["content"] for doc in docs] == ["utile"]


@pytest.mark.anyio
async def test_query_rag_without_documents_says_so(monkeypatch):
    monkeypatch.setattr(rag_pipeline, "_retrieve_sync", lambda query: [])

    answer, sources, metrics = await query_rag("Quelle posologie ?")

    assert answer == rag_pipeline.settings.llm_no_context_answer
    assert sources == []
    assert metrics["retrieved_chunks"] == 0


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def _ollama_stream(*tokens: str) -> bytes:
    import json

    lines = [json.dumps({"message": {"content": token}, "done": False}) for token in tokens]
    lines.append(json.dumps({"message": {"content": ""}, "done": True}))
    return ("\n".join(lines) + "\n").encode()


def _install_transport(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(rag_pipeline, "get_http_client", lambda: client)
    return client


@pytest.mark.anyio
async def test_stream_answer_yields_tokens(monkeypatch):
    _install_transport(
        monkeypatch, lambda request: httpx.Response(200, content=_ollama_stream("Amox", "icilline"))
    )

    assert [token async for token in stream_answer([])] == ["Amox", "icilline"]


@pytest.mark.anyio
async def test_stream_answer_retries_a_cold_failure(monkeypatch):
    monkeypatch.setattr(rag_pipeline.settings, "ollama_max_retries", 2)
    monkeypatch.setattr(rag_pipeline, "RETRY_BASE_DELAY_S", 0)
    attempts = {"count": 0}

    def handler(request):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, content=_ollama_stream("ok"))

    _install_transport(monkeypatch, handler)

    assert [token async for token in stream_answer([])] == ["ok"]
    assert attempts["count"] == 2


@pytest.mark.anyio
async def test_stream_answer_never_retries_after_a_first_token(monkeypatch):
    """Replaying after a partial answer would produce duplicated, incoherent text."""
    monkeypatch.setattr(rag_pipeline.settings, "ollama_max_retries", 3)
    monkeypatch.setattr(rag_pipeline, "RETRY_BASE_DELAY_S", 0)
    attempts = {"count": 0}

    async def truncated_body():
        yield b'{"message":{"content":"Amox"},"done":false}\n'
        raise httpx.ReadError("connection reset by peer")

    def handler(request):
        attempts["count"] += 1
        return httpx.Response(200, content=truncated_body())

    _install_transport(monkeypatch, handler)

    tokens = []
    with pytest.raises(httpx.ReadError):
        async for token in stream_answer([]):
            tokens.append(token)

    assert tokens == ["Amox"]
    assert attempts["count"] == 1


@pytest.mark.anyio
async def test_ollama_error_payload_is_surfaced(monkeypatch):
    _install_transport(
        monkeypatch, lambda request: httpx.Response(200, content=b'{"error":"model not found"}\n')
    )

    with pytest.raises(RuntimeError, match="model not found"):
        [token async for token in stream_answer([])]


@pytest.mark.anyio
async def test_malformed_lines_do_not_break_the_stream(monkeypatch):
    body = b'pas du json\n{"message":{"content":"ok"},"done":true}\n'
    _install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))

    assert [token async for token in stream_answer([])] == ["ok"]


@pytest.mark.anyio
async def test_http_error_is_raised_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(rag_pipeline.settings, "ollama_max_retries", 1)
    monkeypatch.setattr(rag_pipeline, "RETRY_BASE_DELAY_S", 0)
    _install_transport(monkeypatch, lambda request: httpx.Response(500, content=b"boom"))

    with pytest.raises(httpx.HTTPStatusError):
        [token async for token in stream_answer([])]


@pytest.mark.anyio
async def test_concurrent_generations_are_capped(monkeypatch):
    """The semaphore protects a CPU-only backend from collapsing under load."""
    import asyncio

    monkeypatch.setattr(rag_pipeline, "_llm_semaphore", asyncio.Semaphore(1))
    observed = {"max": 0, "current": 0}

    async def handler(request):
        observed["current"] += 1
        observed["max"] = max(observed["max"], observed["current"])
        await asyncio.sleep(0)
        observed["current"] -= 1
        return httpx.Response(200, content=_ollama_stream("ok"))

    _install_transport(monkeypatch, handler)

    async def consume():
        return [token async for token in stream_answer([])]

    await asyncio.gather(*(consume() for _ in range(5)))
    assert observed["max"] == 1


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #


def test_network_failures_are_recognised_as_dependency_outages():
    assert rag_pipeline.is_dependency_unavailable(ConnectionError("connection refused"))
    assert rag_pipeline.is_dependency_unavailable(httpx.ConnectError("boom"))
    assert rag_pipeline.is_dependency_unavailable(TimeoutError())
    assert rag_pipeline.is_dependency_unavailable(OSError(111, "Connection refused"))


def test_application_bugs_are_not_mistaken_for_outages():
    assert not rag_pipeline.is_dependency_unavailable(ValueError("incompatible dimension"))
    assert not rag_pipeline.is_dependency_unavailable(KeyError("payload"))


def test_wrapped_network_failures_are_unwrapped():
    """qdrant-client wraps the network error: without unwrapping we would return 500."""

    class ResponseHandlingException(Exception):
        def __init__(self, source):
            super().__init__(str(source))
            self.source = source

    assert rag_pipeline.is_dependency_unavailable(
        ResponseHandlingException(httpx.ConnectError("connection refused"))
    )

    chained = RuntimeError("request failed")
    chained.__cause__ = ConnectionError("connection refused")
    assert rag_pipeline.is_dependency_unavailable(chained)


def test_self_referencing_exception_does_not_loop():
    exc = RuntimeError("loop")
    exc.__cause__ = exc
    assert rag_pipeline.is_dependency_unavailable(exc) is False
