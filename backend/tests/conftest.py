"""Shared fixtures.

Environment variables are set BEFORE the application modules are imported:
`get_settings()` is memoised, so a later change would have no effect.
"""

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

API_KEY = "test-api-key-0123456789"

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "API_KEY": API_KEY,
        "ALLOWED_ORIGINS": "http://localhost:3000",
        "RATE_LIMIT_ENABLED": "true",
        "RATE_LIMIT_REQUESTS": "1000",
        "RATE_LIMIT_WINDOW_S": "60",
        "DEMO_DOCS_DIR": str(BACKEND_DIR / "tests" / "fixtures" / "demo_docs"),
        "MAX_UPLOAD_MB": "1",
        "LOG_LEVEL": "WARNING",
    }
)


class _Vector(list):
    """Mimics a sentence-transformers output (ndarray.tolist())."""

    def tolist(self):
        return list(self)


class FakeEmbedder:
    """Stand-in for SentenceTransformer: keeps torch out of the test run."""

    dimension = 4

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode(self, texts, **_kwargs):
        if isinstance(texts, str):
            return _Vector([float(len(texts) % 7), 0.1, 0.2, 0.3])
        return _Vector([[float(len(text) % 7), 0.1, 0.2, 0.3] for text in texts])


@pytest.fixture(autouse=True)
def _reset_state():
    import main
    import rag_pipeline

    main.rate_limiter.reset()
    rag_pipeline._embed_query_cached.cache_clear()
    yield
    main.rate_limiter.reset()


@pytest.fixture
def fake_embedder(monkeypatch):
    embedder = FakeEmbedder()
    import document_loader
    import main
    import rag_pipeline

    for module in (rag_pipeline, document_loader, main):
        monkeypatch.setattr(module, "get_embedder", lambda: embedder, raising=False)
    return embedder


@pytest.fixture
def client(monkeypatch, fake_embedder):
    """TestClient running the full lifespan, with no real external dependency."""
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main, "bootstrap_collection", lambda: None)

    with TestClient(main.app, headers={"X-API-Key": API_KEY}) as test_client:
        yield test_client


@pytest.fixture
def anonymous_client(monkeypatch, fake_embedder):
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main, "bootstrap_collection", lambda: None)

    with TestClient(main.app) as test_client:
        yield test_client
