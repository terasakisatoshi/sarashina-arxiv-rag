import pytest

from sarashina_client import rag


def test_cosine():
    assert rag.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert rag.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert rag.cosine([0, 0], [1, 1]) == 0.0
    assert rag.cosine([1, 1], [1, 1]) == pytest.approx(1.0)


def test_rate_limiter_blocks_over_limit(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(rag.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(rag.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    limiter = rag.RateLimiter(2, period=60.0)
    limiter.acquire()
    limiter.acquire()
    assert clock["t"] == 100.0
    limiter.acquire()
    assert clock["t"] >= 160.0


def test_embed_many_preserves_order(monkeypatch):
    embedder = rag.Embedder("key", max_workers=4)
    monkeypatch.setattr(
        embedder, "embed", lambda text, task_type: [float(len(text))]
    )
    seen = []
    out = embedder.embed_many(["a", "bb", "ccc"], "RETRIEVAL_DOCUMENT", lambda: seen.append(1))
    assert out == [[1.0], [2.0], [3.0]]
    assert len(seen) == 3


def test_rerank_maps_document_ids(monkeypatch):
    def fake_post(url, payload, api_key):
        assert url.endswith("/rerank")
        assert payload["model"] == rag.RERANK_MODEL
        return {
            "data": [
                {"result": [{"document_id": 2, "score": 0.9}, {"document_id": 0, "score": 0.4}]}
            ]
        }

    monkeypatch.setattr(rag, "_post", fake_post)
    assert rag.rerank("q", ["x", "y", "z"], "key") == [(2, 0.9), (0, 0.4)]


def test_retrieve_two_stage(monkeypatch):
    chunks = ["a", "b", "c"]
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]]
    monkeypatch.setattr(
        rag.Embedder, "embed", lambda self, text, task_type: [1.0, 0.0]
    )
    monkeypatch.setattr(rag, "rerank", lambda *a, **k: [(1, 0.9), (0, 0.8)])
    # cosine order: a(1.0), c(~0.994), b(0.0) -> candidate_ids [0, 2, 1]
    # rerank returns doc ids 1 then 0 -> [candidate_ids[1], candidate_ids[0]] = [2, 0]
    assert rag.retrieve("q", chunks, vectors, "key") == [2, 0]


def test_retrieve_empty_chunks():
    assert rag.retrieve("q", [], [], "key") == []
