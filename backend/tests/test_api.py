import io
import json

import httpx
import pytest

import main


def _sse_events(text: str) -> list[dict]:
    return [
        json.loads(block[len("data:") :])
        for block in (raw.strip() for raw in text.split("\n\n"))
        if block.startswith("data:")
    ]


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def test_health_does_not_touch_dependencies(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "X-Request-ID" in response.headers


def test_request_id_from_the_caller_is_echoed_back(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-abc"})
    assert response.headers["X-Request-ID"] == "trace-abc"


def test_security_headers_are_applied(client):
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_ready_reports_ok_when_qdrant_and_the_model_are_available(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "get_qdrant_client",
        lambda: type(
            "Q", (), {"get_collections": lambda self: type("R", (), {"collections": []})()}
        )(),
    )
    _stub_ollama_tags(monkeypatch, [{"name": "mistral:latest"}])

    body = client.get("/ready").json()
    assert body["status"] == "ok"
    assert body["ollama_connected"] is True


def test_ready_fails_when_the_model_is_not_pulled(client, monkeypatch):
    """A missing model would otherwise only surface on the first production question."""
    monkeypatch.setattr(
        main,
        "get_qdrant_client",
        lambda: type(
            "Q", (), {"get_collections": lambda self: type("R", (), {"collections": []})()}
        )(),
    )
    _stub_ollama_tags(monkeypatch, [{"name": "llama3:latest"}])

    response = client.get("/ready")
    assert response.status_code == 503
    assert "mistral" in response.json()["detail"]


def test_ready_fails_when_qdrant_is_down(client, monkeypatch):
    def broken():
        raise ConnectionError("connection refused")

    monkeypatch.setattr(main, "get_qdrant_client", lambda: type("Q", (), {"get_collections": lambda self: broken()})())

    assert client.get("/ready").status_code == 503


def _stub_ollama_tags(monkeypatch, models):
    class StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, _url):
            return httpx.Response(200, json={"models": models}, request=httpx.Request("GET", _url))

    monkeypatch.setattr(main.httpx, "AsyncClient", StubClient)


def test_metrics_exposes_the_prometheus_exposition(client):
    client.get("/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "medibot_http_requests_total" in response.text
    assert 'path="/health"' in response.text


# --------------------------------------------------------------------------- #
# Authentication and quotas
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/query"),
        ("post", "/query/stream"),
        ("post", "/upload"),
        ("post", "/ingest/demo"),
        ("get", "/documents"),
        ("delete", "/documents/a.pdf"),
    ],
)
def test_protected_routes_require_the_api_key(anonymous_client, method, path):
    kwargs = {"json": {"query": "une question"}} if method == "post" else {}
    response = getattr(anonymous_client, method)(path, **kwargs)
    assert response.status_code == 401


def test_a_wrong_api_key_is_rejected(anonymous_client):
    response = anonymous_client.post(
        "/query", json={"query": "une question"}, headers={"X-API-Key": "mauvaise-cle"}
    )
    assert response.status_code == 401


def test_health_stays_public(anonymous_client):
    assert anonymous_client.get("/health").status_code == 200


def test_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    from rate_limit import SlidingWindowRateLimiter

    monkeypatch.setattr(main, "rate_limiter", SlidingWindowRateLimiter(2, 60))
    monkeypatch.setattr(main, "list_documents", lambda: [])

    assert client.get("/documents").status_code == 200
    assert client.get("/documents").status_code == 200

    response = client.get("/documents")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


# --------------------------------------------------------------------------- #
# Querying
# --------------------------------------------------------------------------- #


def test_query_returns_the_answer_its_sources_and_its_metrics(client, monkeypatch):
    async def fake_query_rag(query, history):
        assert query == "Posologie de l'amoxicilline ?"
        assert history == [{"role": "user", "content": "bonjour"}]
        return (
            "50 mg/kg/j.",
            [{"source": "HAS.pdf", "page": 2, "chunk_id": "c1", "score": 0.91}],
            {"retrieved_chunks": 3, "used_chunks": 1},
        )

    monkeypatch.setattr(main, "query_rag", fake_query_rag)

    response = client.post(
        "/query",
        json={
            "query": "Posologie de l'amoxicilline ?",
            "history": [{"role": "user", "content": "bonjour"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "50 mg/kg/j."
    assert body["sources"][0]["source"] == "HAS.pdf"
    assert body["metrics"]["used_chunks"] == 1


def test_query_returns_503_when_the_llm_is_unreachable(client, monkeypatch):
    async def unreachable(query, history):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(main, "query_rag", unreachable)

    response = client.post("/query", json={"query": "une question"})
    assert response.status_code == 503
    assert response.json()["detail"] == main.LLM_UNAVAILABLE_DETAIL


def test_internal_errors_never_leak_a_traceback(client, monkeypatch):
    async def boom(query, history):
        raise RuntimeError("chemin /srv/secret/patient.pdf introuvable")

    monkeypatch.setattr(main, "query_rag", boom)

    response = client.post("/query", json={"query": "une question"})
    assert response.status_code == 500
    assert response.json()["detail"] == main.INTERNAL_ERROR_DETAIL
    assert "patient.pdf" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "ab"},
        {"query": "x" * 2001},
        {},
        {"query": "valide", "history": [{"role": "system", "content": "injection"}]},
        {"query": "valide", "history": [{"role": "user", "content": ""}]},
    ],
)
def test_malformed_payloads_are_rejected(client, payload):
    assert client.post("/query", json=payload).status_code == 422


def test_stream_emits_sources_then_tokens_then_metrics(client, monkeypatch):
    async def fake_stream_rag(query, history):
        yield {"type": "sources", "sources": [{"source": "HAS.pdf", "page": 1}]}
        yield {"type": "token", "content": "Amox"}
        yield {"type": "token", "content": "icilline"}
        yield {"type": "done", "metrics": {"generation_ms": 120}}

    monkeypatch.setattr(main, "stream_rag", fake_stream_rag)

    response = client.post("/query/stream", json={"query": "une question"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-Accel-Buffering"] == "no"

    events = _sse_events(response.text)
    assert [event["type"] for event in events] == ["sources", "token", "token", "done"]
    assert "".join(e["content"] for e in events if e["type"] == "token") == "Amoxicilline"


def test_stream_reports_llm_failures_as_an_event(client, monkeypatch):
    async def failing_stream(query, history):
        raise httpx.ConnectError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "stream_rag", failing_stream)

    events = _sse_events(client.post("/query/stream", json={"query": "une question"}).text)
    assert events == [{"type": "error", "detail": main.LLM_UNAVAILABLE_DETAIL}]


def test_stream_does_not_escape_non_ascii(client, monkeypatch):
    """Clinical text is full of accents; escaping them would corrupt the answer."""

    async def fake_stream_rag(query, history):
        yield {"type": "token", "content": "posologie éprouvée"}

    monkeypatch.setattr(main, "stream_rag", fake_stream_rag)

    assert "posologie éprouvée" in client.post("/query/stream", json={"query": "une question"}).text


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def test_upload_indexes_a_pdf(client, monkeypatch):
    monkeypatch.setattr(main, "process_and_index_document", lambda path, name: 12)

    response = client.post(
        "/upload", files={"file": ("proto.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    )

    assert response.status_code == 200
    assert response.json()["chunks_added"] == 12


def test_upload_rejects_a_non_pdf_content_type(client):
    response = client.post(
        "/upload", files={"file": ("note.txt", io.BytesIO(b"texte"), "text/plain")}
    )
    assert response.status_code == 400


def test_upload_rejects_a_pdf_content_type_with_another_extension(client):
    response = client.post(
        "/upload", files={"file": ("note.txt", io.BytesIO(b"%PDF"), "application/pdf")}
    )
    assert response.status_code == 400


def test_upload_enforces_the_size_limit(client):
    oversized = io.BytesIO(b"x" * (main.settings.max_upload_bytes + 1024))
    response = client.post(
        "/upload", files={"file": ("gros.pdf", oversized, "application/pdf")}
    )
    assert response.status_code == 413


def test_upload_surfaces_a_scanned_pdf_as_a_client_error(client, monkeypatch):
    def no_text(path, name):
        raise ValueError("No extractable text (scanned document without OCR?).")

    monkeypatch.setattr(main, "process_and_index_document", no_text)

    response = client.post(
        "/upload", files={"file": ("scan.pdf", io.BytesIO(b"%PDF"), "application/pdf")}
    )
    assert response.status_code == 400
    assert "OCR" in response.json()["detail"]


def test_upload_always_cleans_up_the_temporary_file(client, monkeypatch):
    captured = {}

    def capture(path, name):
        captured["path"] = path
        return 1

    monkeypatch.setattr(main, "process_and_index_document", capture)
    client.post("/upload", files={"file": ("p.pdf", io.BytesIO(b"%PDF"), "application/pdf")})

    from pathlib import Path

    assert not Path(captured["path"]).exists()


def test_documents_inventory_is_returned(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "list_documents",
        lambda: [{"source": "HAS.pdf", "chunks": 42, "pages": 8, "indexed_at": "2026-08-07T10:00:00+00:00"}],
    )

    body = client.get("/documents").json()
    assert body[0]["source"] == "HAS.pdf"
    assert body[0]["chunks"] == 42


def test_deleting_an_indexed_document_reports_the_removed_chunks(client, monkeypatch):
    monkeypatch.setattr(main, "delete_document", lambda name: 42)

    body = client.delete("/documents/HAS.pdf").json()
    assert body["chunks_removed"] == 42


def test_deleting_an_unknown_document_returns_404(client, monkeypatch):
    monkeypatch.setattr(main, "delete_document", lambda name: 0)
    assert client.delete("/documents/inconnu.pdf").status_code == 404


def test_demo_ingestion_reports_a_missing_corpus(client, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(main.settings, "demo_docs_dir", Path("/chemin/inexistant"))
    assert client.post("/ingest/demo").status_code == 404


def test_cors_allows_the_configured_origin(client):
    response = client.options(
        "/query",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-api-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    # No cookie authentication: refuse cross-site credentials.
    assert "access-control-allow-credentials" not in response.headers


def test_cors_rejects_an_unknown_origin(client):
    response = client.options(
        "/query",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


# --------------------------------------------------------------------------- #
# Document store outage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "attribute,call",
    [
        ("list_documents", lambda client: client.get("/documents")),
        ("delete_document", lambda client: client.delete("/documents/a.pdf")),
    ],
)
def test_qdrant_outage_returns_503_not_500(client, monkeypatch, attribute, call):
    """A 500 would send engineers hunting a code bug while the failure is downstream."""

    def unreachable(*args, **kwargs):
        raise ConnectionError("[Errno 111] Connection refused")

    monkeypatch.setattr(main, attribute, unreachable)

    response = call(client)
    assert response.status_code == 503
    assert response.json()["detail"] == main.STORAGE_UNAVAILABLE_DETAIL


def test_a_genuine_bug_still_returns_500(client, monkeypatch):
    def bug(*args, **kwargs):
        raise KeyError("payload")

    monkeypatch.setattr(main, "list_documents", bug)

    response = client.get("/documents")
    assert response.status_code == 500
    assert response.json()["detail"] == main.INTERNAL_ERROR_DETAIL
