"""PDF ingestion: extraction, splitting, vectorisation and indexing into Qdrant."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from qdrant_client import QdrantClient, models

import observability as obs
from config import get_settings
from rag_pipeline import COLLECTION_NAME, embed_texts, get_embedder, get_qdrant_client

logger = logging.getLogger(__name__)
settings = get_settings()

EMBED_BATCH_SIZE = 64
SOURCE_FIELD = "metadata.source_name"
# Guard rail on the document inventory: past this point, scroll-based
# aggregation gets too expensive and a dedicated registry is needed
# (see README, scaling section).
DOCUMENT_SCAN_LIMIT = 50_000


def ensure_collection_exists(client: QdrantClient, collection_name: str, vector_size: int) -> None:
    collections = client.get_collections().collections
    if not any(col.name == collection_name for col in collections):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )
        _ensure_source_index(client, collection_name)
        logger.info("Qdrant collection '%s' created (dimension %d).", collection_name, vector_size)
        return

    collection = client.get_collection(collection_name)
    current_size = collection.config.params.vectors.size
    if current_size != vector_size:
        raise ValueError(
            f"Qdrant collection '{collection_name}' is incompatible: dimension {current_size}, "
            f"embedding model expects {vector_size}."
        )
    _ensure_source_index(client, collection_name)


def _ensure_source_index(client: QdrantClient, collection_name: str) -> None:
    """Index on the source name: without it, filtering or deleting a document scans the whole collection."""
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=SOURCE_FIELD,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    except Exception as exc:  # index already exists: the normal case on restart
        logger.debug("Payload index '%s' already present or not created: %s", SOURCE_FIELD, exc)


def bootstrap_collection() -> None:
    """Create the collection at startup so a query before any ingestion answers cleanly."""
    client = get_qdrant_client()
    ensure_collection_exists(
        client,
        COLLECTION_NAME,
        vector_size=get_embedder().get_sentence_embedding_dimension(),
    )


def _source_filter(source_name: str) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key=SOURCE_FIELD, match=models.MatchValue(value=source_name))]
    )


def _chunk_id(source_name: str, page: int | None, content: str) -> str:
    digest = hashlib.sha256(f"{source_name}:{page}:{content}".encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


def _load_pdf_pages(file_path: str) -> list[tuple[int, str]]:
    reader = PdfReader(file_path)
    pages = []
    for page_number, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((page_number, text))
    return pages


def delete_document(source_name: str) -> int:
    """Remove every chunk of a document. Returns the number of points deleted."""
    client = get_qdrant_client()
    before = count_document_chunks(source_name)
    if before == 0:
        return 0

    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.FilterSelector(filter=_source_filter(source_name)),
        wait=True,
    )
    logger.info("Document '%s' removed from the index (%d chunks).", source_name, before)
    return before


def count_document_chunks(source_name: str) -> int:
    client = get_qdrant_client()
    try:
        return client.count(
            collection_name=COLLECTION_NAME,
            count_filter=_source_filter(source_name),
            exact=True,
        ).count
    except Exception as exc:
        from rag_pipeline import _is_missing_collection

        if _is_missing_collection(exc):
            return 0
        raise


def list_documents() -> list[dict]:
    """Inventory of indexed documents (name, chunk count, pages, indexing date)."""
    client = get_qdrant_client()
    documents: dict[str, dict] = {}
    offset = None
    scanned = 0

    while scanned < DOCUMENT_SCAN_LIMIT:
        try:
            points, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                limit=1000,
                offset=offset,
                with_payload=[SOURCE_FIELD, "metadata.indexed_at", "metadata.page"],
                with_vectors=False,
            )
        except Exception as exc:
            from rag_pipeline import _is_missing_collection

            if _is_missing_collection(exc):
                return []
            raise

        for point in points:
            metadata = (point.payload or {}).get("metadata") or {}
            name = metadata.get("source_name")
            if not name:
                continue
            entry = documents.setdefault(
                name, {"source": name, "chunks": 0, "pages": 0, "indexed_at": metadata.get("indexed_at")}
            )
            entry["chunks"] += 1
            page = metadata.get("page")
            if page is not None:
                entry["pages"] = max(entry["pages"], int(page) + 1)

        scanned += len(points)
        if offset is None:
            break

    return sorted(documents.values(), key=lambda entry: entry["source"])


def process_and_index_document(
    file_path: str,
    source_name: str | None = None,
    replace: bool = True,
) -> int:
    """Load, split and index a PDF into Qdrant.

    Point IDs are derived from content, so re-indexing an identical document is
    idempotent. `replace=True` first purges existing chunks with the same name:
    without it, a revised protocol would coexist with the previous version and
    the RAG could cite a stale dosage.
    """
    source_name = Path(source_name or file_path).name
    pages = _load_pdf_pages(file_path)
    if not pages:
        raise ValueError(
            f"No extractable text in '{source_name}' (scanned document without OCR?)."
        )

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ".", "!", "?", ";", ":", ",", " "],
    )

    chunks: list[tuple[int, str]] = []
    for page_number, text in pages:
        for piece in text_splitter.split_text(text):
            chunks.append((page_number, piece))

    client = get_qdrant_client()
    ensure_collection_exists(
        client,
        COLLECTION_NAME,
        vector_size=get_embedder().get_sentence_embedding_dimension(),
    )

    if replace:
        removed = delete_document(source_name)
        if removed:
            logger.info("Re-indexing '%s': %d stale chunks removed.", source_name, removed)

    indexed_at = datetime.now(UTC).isoformat(timespec="seconds")

    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors = embed_texts([content for _, content in batch])
        points = []
        for offset, ((page_number, content), vector) in enumerate(zip(batch, vectors, strict=True)):
            chunk_id = _chunk_id(source_name, page_number, content)
            points.append(
                models.PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload={
                        "page_content": content,
                        "metadata": {
                            "source": source_name,
                            "source_name": source_name,
                            "page": page_number,
                            "chunk_index": start + offset,
                            "chunk_id": chunk_id,
                            "indexed_at": indexed_at,
                        },
                    },
                )
            )
        is_last_batch = start + EMBED_BATCH_SIZE >= len(chunks)
        # Only the final write waits for confirmation; intermediate batches are
        # acknowledged immediately, which speeds up large PDFs noticeably.
        client.upsert(collection_name=COLLECTION_NAME, points=points, wait=is_last_batch)

    obs.DOCUMENTS_INDEXED.inc()
    obs.CHUNKS_INDEXED.inc(amount=len(chunks))
    logger.info(
        "Document indexed.",
        extra={"document": source_name, "chunks": len(chunks), "pages": len(pages)},
    )
    return len(chunks)
