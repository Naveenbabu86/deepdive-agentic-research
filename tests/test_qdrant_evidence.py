from types import SimpleNamespace, ModuleType
import sys
import pytest

from app.services.qdrant_evidence import chunk_text, QdrantEvidenceStore
from app.schemas import SourceRecord


def test_chunk_text_empty_and_overlap():
    assert chunk_text("   ") == []
    chunks = chunk_text("abcdefghij", chunk_size=6, overlap=2)
    assert chunks == ["abcdef", "efghij"]


def test_chunk_text_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size=5, overlap=5)


class FakeModels:
    class PointStruct:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class VectorParams:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Distance:
        COSINE = "cosine"
    class Filter:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class FieldCondition:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class MatchValue:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)


class FakeEmbedder:
    def embed(self, texts):
        for text in texts:
            yield [float(len(text) % 7), 1.0, 0.5]


class FakeClient:
    def __init__(self):
        self.points = []
        self.created = False
    def collection_exists(self, name): return self.created
    def create_collection(self, **kwargs): self.created = True
    def upsert(self, collection_name, points, wait): self.points.extend(points)
    def search(self, collection_name, query_vector, query_filter, limit, with_payload):
        run_id = query_filter.must[0].match.value
        return [SimpleNamespace(payload=p.payload) for p in self.points if p.payload["run_id"] == run_id][:limit]


def test_indexing_and_ranking_are_scoped_to_run(monkeypatch):
    qdrant_module = ModuleType("qdrant_client")
    http_module = ModuleType("qdrant_client.http")
    models_module = ModuleType("qdrant_client.http.models")
    for name in ("PointStruct", "VectorParams", "Distance", "Filter", "FieldCondition", "MatchValue"):
        setattr(models_module, name, getattr(FakeModels, name))
    http_module.models = models_module
    qdrant_module.http = http_module
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_module)
    monkeypatch.setitem(sys.modules, "qdrant_client.http", http_module)
    monkeypatch.setitem(sys.modules, "qdrant_client.http.models", models_module)
    client = FakeClient()
    store = QdrantEvidenceStore.__new__(QdrantEvidenceStore)
    store.client, store.embedder, store.collection, store.top_k = client, FakeEmbedder(), "test", 4
    source = SourceRecord(source_id="S-1", title="Battery", url="https://example.com", content="A battery source passage.")
    assert store.index_sources("run-a", [source]) == 1
    assert store.rank_source_ids("run-a", ["battery question"]) == ["S-1"]
    assert store.rank_source_ids("run-b", ["battery question"]) == []
