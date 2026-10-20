"""Optional Qdrant Cloud evidence index with local, free FastEmbed vectors.

Qdrant is a retrieval aid, not a source of truth. Only current-run evidence is
retrieved into a run, preventing stale cross-run evidence from silently entering
claim verification. If Qdrant is not configured, the workflow continues using
freshly collected source records.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from app.config import get_settings
from app.schemas import SourceRecord

logger = logging.getLogger(__name__)
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_VECTOR_SIZE = 384


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    """Create bounded overlapping chunks without embedding empty strings."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Require chunk_size > overlap >= 0")
    chunks = []
    start = 0
    while start < len(normalized):
        chunk = normalized[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(normalized):
            break
        start += chunk_size - overlap
    return chunks


class QdrantEvidenceStore:
    """Small adapter for Qdrant Cloud; dependencies are loaded only when enabled."""

    def __init__(self, client: Any | None = None, embedder: Any | None = None):
        cfg = get_settings()
        if not cfg.qdrant_url or not cfg.qdrant_api_key:
            raise RuntimeError("Qdrant is not configured")
        try:
            if client is None:
                from qdrant_client import QdrantClient
                client = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key, timeout=10.0)
            if embedder is None:
                from fastembed import TextEmbedding
                embedder = TextEmbedding(model_name=DEFAULT_EMBEDDING_MODEL)
        except ImportError as exc:
            raise RuntimeError("Install qdrant-client and fastembed to enable Qdrant retrieval") from exc
        self.client = client
        self.embedder = embedder
        self.collection = cfg.qdrant_collection
        self.top_k = cfg.qdrant_top_k
        self._ensure_collection()

    @staticmethod
    def enabled() -> bool:
        cfg = get_settings()
        return bool(cfg.qdrant_url.strip() and cfg.qdrant_api_key.strip())

    def _ensure_collection(self) -> None:
        from qdrant_client.http import models
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=DEFAULT_VECTOR_SIZE, distance=models.Distance.COSINE),
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        # FastEmbed runs locally; no external embedding API call is made.
        return [vector.tolist() if hasattr(vector, "tolist") else list(vector) for vector in self.embedder.embed(texts)]

    def index_sources(self, run_id: str, sources: Iterable[SourceRecord]) -> int:
        from qdrant_client.http import models
        points = []
        for source in sources:
            text = source.content or source.snippet
            chunks = chunk_text(text)
            if not chunks:
                continue
            vectors = self._embed(chunks)
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                stable_id = str(uuid5(NAMESPACE_URL, f"deepdive:{run_id}:{source.source_id}:{index}"))
                points.append(models.PointStruct(
                    id=stable_id,
                    vector=vector,
                    payload={
                        "run_id": run_id,
                        "source_id": source.source_id,
                        "title": source.title,
                        "url": source.url,
                        "snippet": source.snippet[:1500],
                        "content_chunk": chunk,
                        "quality_tier": source.quality_tier,
                        "chunk_index": index,
                    },
                ))
        if points:
            self.client.upsert(collection_name=self.collection, points=points, wait=True)
        return len(points)

    def rank_source_ids(self, run_id: str, queries: Iterable[str], limit: int | None = None) -> list[str]:
        from qdrant_client.http import models
        seen: set[str] = set()
        ordered: list[str] = []
        query_list = [q.strip() for q in queries if q and q.strip()]
        if not query_list:
            return ordered
        for query in query_list:
            vector = self._embed([query])[0]
            hits = self.client.search(
                collection_name=self.collection,
                query_vector=vector,
                query_filter=models.Filter(must=[models.FieldCondition(key="run_id", match=models.MatchValue(value=run_id))]),
                limit=limit or self.top_k,
                with_payload=True,
            )
            for hit in hits:
                payload = hit.payload or {}
                source_id = payload.get("source_id")
                if source_id and source_id not in seen:
                    seen.add(source_id)
                    ordered.append(source_id)
        return ordered

    def order_sources(self, run_id: str, queries: Iterable[str], sources: list[SourceRecord]) -> list[SourceRecord]:
        """Put semantically retrieved current-run sources first; preserve all others."""
        ids = self.rank_source_ids(run_id, queries)
        rank = {source_id: index for index, source_id in enumerate(ids)}
        return sorted(sources, key=lambda source: (rank.get(source.source_id, len(rank)), source.source_id))
