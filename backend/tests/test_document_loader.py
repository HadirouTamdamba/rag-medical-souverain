import pytest
from qdrant_client import models

import document_loader
import rag_pipeline
from document_loader import (
    _chunk_id,
    delete_document,
    ensure_collection_exists,
    list_documents,
    process_and_index_document,
)


class FakeQdrant:
    """QdrantClient double covering what ingestion actually calls."""

    def __init__(self, collections=(), vector_size=4):
        self._collections = list(collections)
        self._vector_size = vector_size
        self.points: dict[str, models.PointStruct] = {}
        self.created_indexes: list[str] = []
        self.upsert_calls: list[bool] = []
        self.deleted_filters: list[models.Filter] = []

    def get_collections(self):
        return type(
            "R", (), {"collections": [type("C", (), {"name": name})() for name in self._collections]}
        )()

    def create_collection(self, collection_name, vectors_config, **_kwargs):
        self._collections.append(collection_name)
        self._vector_size = vectors_config.size

    def get_collection(self, _collection_name):
        vectors = type("V", (), {"size": self._vector_size})()
        params = type("P", (), {"vectors": vectors})()
        return type("C", (), {"config": type("Cfg", (), {"params": params})()})()

    def create_payload_index(self, collection_name, field_name, field_schema, **_kwargs):
        if field_name in self.created_indexes:
            raise RuntimeError("index already exists")
        self.created_indexes.append(field_name)

    def upsert(self, collection_name, points, wait=True):
        self.upsert_calls.append(wait)
        for point in points:
            self.points[point.id] = point

    def _matching(self, source_name):
        return [
            point_id
            for point_id, point in self.points.items()
            if point.payload["metadata"]["source_name"] == source_name
        ]

    def count(self, collection_name, count_filter, exact=True):
        source = count_filter.must[0].match.value
        return type("R", (), {"count": len(self._matching(source))})()

    def delete(self, collection_name, points_selector, wait=True):
        self.deleted_filters.append(points_selector.filter)
        source = points_selector.filter.must[0].match.value
        for point_id in self._matching(source):
            del self.points[point_id]

    def scroll(self, collection_name, limit, offset, with_payload, with_vectors):
        ordered = list(self.points.values())
        start = offset or 0
        window = ordered[start : start + limit]
        next_offset = start + limit if start + limit < len(ordered) else None
        return window, next_offset


@pytest.fixture
def qdrant(monkeypatch):
    fake = FakeQdrant()
    monkeypatch.setattr(rag_pipeline, "get_qdrant_client", lambda: fake)
    monkeypatch.setattr(document_loader, "get_qdrant_client", lambda: fake)
    return fake


@pytest.fixture
def pdf_pages(monkeypatch):
    pages = [(0, "Amoxicilline 50 mg/kg/j pendant 14 jours."), (1, "Doxycycline 200 mg/j.")]
    monkeypatch.setattr(document_loader, "_load_pdf_pages", lambda _path: pages)
    return pages


def test_chunk_ids_are_deterministic_and_content_addressed():
    first = _chunk_id("proto.pdf", 0, "texte")
    assert first == _chunk_id("proto.pdf", 0, "texte")
    assert first != _chunk_id("proto.pdf", 1, "texte")
    assert first != _chunk_id("autre.pdf", 0, "texte")


def test_collection_is_created_with_a_source_index(qdrant, fake_embedder):
    ensure_collection_exists(qdrant, "medical_docs", vector_size=4)

    assert "medical_docs" in qdrant._collections
    assert qdrant.created_indexes == ["metadata.source_name"]


def test_incompatible_vector_dimension_is_refused(fake_embedder):
    existing = FakeQdrant(collections=["medical_docs"], vector_size=768)

    with pytest.raises(ValueError, match="dimension 768"):
        ensure_collection_exists(existing, "medical_docs", vector_size=384)


def test_indexing_writes_one_point_per_chunk(qdrant, pdf_pages, fake_embedder):
    chunks = process_and_index_document("/tmp/proto.pdf", "Protocole_Lyme.pdf")

    assert chunks == len(qdrant.points) == 2
    payloads = [point.payload for point in qdrant.points.values()]
    assert {payload["metadata"]["source_name"] for payload in payloads} == {"Protocole_Lyme.pdf"}
    assert all(payload["metadata"]["indexed_at"] for payload in payloads)
    assert sorted(payload["metadata"]["page"] for payload in payloads) == [0, 1]


def test_only_the_last_batch_waits_for_confirmation(qdrant, pdf_pages, fake_embedder):
    process_and_index_document("/tmp/proto.pdf", "Protocole_Lyme.pdf")
    assert qdrant.upsert_calls[-1] is True


def test_reindexing_a_revised_document_removes_the_previous_version(
    qdrant, monkeypatch, fake_embedder
):
    """Without the purge, a stale dosage would stay citable by the RAG."""
    monkeypatch.setattr(document_loader, "_load_pdf_pages", lambda _p: [(0, "Version 1: 500 mg.")])
    process_and_index_document("/tmp/proto.pdf", "Protocole.pdf")
    first_ids = set(qdrant.points)

    monkeypatch.setattr(document_loader, "_load_pdf_pages", lambda _p: [(0, "Version 2: 750 mg.")])
    process_and_index_document("/tmp/proto.pdf", "Protocole.pdf")

    assert set(qdrant.points).isdisjoint(first_ids)
    contents = [point.payload["page_content"] for point in qdrant.points.values()]
    assert contents == ["Version 2: 750 mg."]


def test_replace_can_be_disabled(qdrant, monkeypatch, fake_embedder):
    monkeypatch.setattr(document_loader, "_load_pdf_pages", lambda _p: [(0, "A.")])
    process_and_index_document("/tmp/a.pdf", "Doc.pdf")
    monkeypatch.setattr(document_loader, "_load_pdf_pages", lambda _p: [(0, "B.")])
    process_and_index_document("/tmp/a.pdf", "Doc.pdf", replace=False)

    assert len(qdrant.points) == 2


def test_a_pdf_without_extractable_text_is_rejected(qdrant, monkeypatch, fake_embedder):
    monkeypatch.setattr(document_loader, "_load_pdf_pages", lambda _p: [])

    with pytest.raises(ValueError, match="OCR"):
        process_and_index_document("/tmp/scan.pdf", "Scan.pdf")


def test_delete_document_returns_the_number_of_removed_chunks(qdrant, pdf_pages, fake_embedder):
    process_and_index_document("/tmp/proto.pdf", "Protocole.pdf")

    assert delete_document("Protocole.pdf") == 2
    assert qdrant.points == {}
    assert delete_document("Protocole.pdf") == 0


def test_document_inventory_aggregates_chunks_and_pages(qdrant, pdf_pages, fake_embedder):
    process_and_index_document("/tmp/a.pdf", "A.pdf")

    inventory = list_documents()

    assert len(inventory) == 1
    assert inventory[0]["source"] == "A.pdf"
    assert inventory[0]["chunks"] == 2
    assert inventory[0]["pages"] == 2
    assert inventory[0]["indexed_at"]


def test_inventory_is_empty_when_nothing_was_ever_indexed(monkeypatch):
    class NoCollection:
        def scroll(self, **_kwargs):
            raise type("NotFound", (Exception,), {"status_code": 404})("Collection not found")

    monkeypatch.setattr(document_loader, "get_qdrant_client", lambda: NoCollection())
    assert list_documents() == []
