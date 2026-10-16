import pytest
from fastapi.testclient import TestClient

from sarashina_client import cache, webapp


@pytest.fixture(autouse=True)
def clean_jobs():
    webapp.JOBS.clear()
    yield
    webapp.JOBS.clear()


@pytest.fixture
def client():
    return TestClient(webapp.app)


def _job(chunks, pages, indexed=False, status="done", dockey="arxiv-test-0000"):
    job = webapp.Job()
    job.chunks = chunks
    job.pages = pages
    job.dockey = dockey
    job.indexed = indexed
    job.status = status
    webapp.JOBS["j1"] = job
    return job


def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "divider" in res.text


def test_index_rejects_bad_url(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    res = client.post("/api/index", json={"url": "ftp://example.com/a.pdf"})
    assert res.status_code == 400


def test_index_unknown_job(client):
    assert client.get("/api/index/nope").status_code == 404


def test_paper_unknown_job(client):
    assert client.get("/api/paper/nope").status_code == 404


def test_paper_not_finished(client):
    _job(["a"], [1], status="running")
    assert client.get("/api/paper/j1").status_code == 409


def test_paper_returns_chunks_and_cached_translations(client):
    _job(["hello", "world"], [1, 2], indexed=True, dockey="arxiv-x")
    cache.save_translations("arxiv-x", {cache.text_hash("hello"): "こんにちは"})
    data = client.get("/api/paper/j1").json()
    assert [c["text"] for c in data["chunks"]] == ["hello", "world"]
    assert data["chunks"][0]["page"] == 1
    assert data["translations"] == {"0": "こんにちは"}


def test_translate_unknown_job(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    res = client.post("/api/translate", json={"job_id": "nope", "index": 0})
    assert res.status_code == 404


def test_translate_out_of_range(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    _job(["hello"], [1], indexed=True)
    res = client.post("/api/translate", json={"job_id": "j1", "index": 5})
    assert res.status_code == 400


def test_translate_cache_hit_skips_api(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    _job(["hello"], [1], indexed=True, dockey="arxiv-x")
    cache.save_translations("arxiv-x", {cache.text_hash("hello"): "こんにちは"})

    def fail(*args, **kwargs):
        raise AssertionError("Translator must not be constructed on cache hit")

    monkeypatch.setattr(webapp, "Translator", fail)
    res = client.post("/api/translate", json={"job_id": "j1", "index": 0})
    assert res.status_code == 200
    assert res.json() == {"index": 0, "text": "こんにちは"}


def test_translate_fresh_calls_api_and_caches(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    _job(["hello"], [1], indexed=True, dockey="arxiv-x")

    class FakeTranslator:
        def __init__(self, api_key, source, target):
            self.source, self.target = source, target

        def translate(self, text):
            return f"訳:{text}"

    monkeypatch.setattr(webapp, "Translator", FakeTranslator)
    res = client.post("/api/translate", json={"job_id": "j1", "index": 0})
    assert res.json() == {"index": 0, "text": "訳:hello"}
    assert cache.load_translations("arxiv-x")[cache.text_hash("hello")] == "訳:hello"


def test_suggest_without_history(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    _job(["hello"], [1])
    res = client.post("/api/suggest", json={"job_id": "j1"})
    assert res.status_code == 200
    assert res.json() == {"suggestions": []}


def test_chat_unknown_job(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    res = client.post("/api/chat", json={"job_id": "nope", "message": "hi"})
    assert res.status_code == 404


def test_chat_before_index(client, monkeypatch):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    _job(["hello"], [1], indexed=False)
    res = client.post("/api/chat", json={"job_id": "j1", "message": "hi"})
    assert res.status_code == 409
