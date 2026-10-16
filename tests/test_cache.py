from sarashina_client import cache


def test_arxiv_id_variants():
    assert cache.arxiv_id("https://arxiv.org/abs/1706.03762") == "1706.03762"
    assert cache.arxiv_id("https://arxiv.org/pdf/1706.03762v2") == "1706.03762v2"
    assert cache.arxiv_id("https://arxiv.org/pdf/cs/0601001") == "cs/0601001"
    assert cache.arxiv_id("https://www.arxiv.org/abs/2401.00001") == "2401.00001"
    assert cache.arxiv_id("https://example.com/paper.pdf") is None


def test_doc_key_arxiv_and_url():
    assert cache.doc_key("https://arxiv.org/abs/1706.03762") == "arxiv-1706.03762"
    assert cache.doc_key("https://arxiv.org/pdf/cs/0601001") == "arxiv-cs-0601001"
    other = cache.doc_key("https://example.com/a.pdf")
    assert other.startswith("url-")
    assert other == cache.doc_key("https://example.com/a.pdf")


def test_make_key_pages():
    url = "https://arxiv.org/abs/1706.03762"
    assert cache.make_key(url, None) == "arxiv-1706.03762__pages-all"
    assert cache.make_key(url, "") == "arxiv-1706.03762__pages-all"
    assert cache.make_key(url, " 1-3 ") == "arxiv-1706.03762__pages-1-3"


def test_text_hash_deterministic():
    assert cache.text_hash("hello") == cache.text_hash("hello")
    assert cache.text_hash("hello") != cache.text_hash("world")


def test_index_cache_round_trip():
    data = {"title": "t", "chunks": ["a"], "pages": [1], "vectors": [[0.1]], "total": 1}
    assert cache.load("missing") is None
    cache.save("k", data)
    assert cache.load("k") == data


def test_embeddings_round_trip():
    assert cache.load_embeddings("doc") == {}
    cache.save_embeddings("doc", {"hash": [0.5, 0.5]})
    assert cache.load_embeddings("doc") == {"hash": [0.5, 0.5]}


def test_translations_round_trip():
    assert cache.load_translations("doc") == {}
    cache.save_translations("doc", {"hash": "こんにちは"})
    assert cache.load_translations("doc") == {"hash": "こんにちは"}


def test_corrupt_cache_returns_none():
    from sarashina_client import cache as c

    path = c.cache_dir() / "broken.json.gz"
    path.write_bytes(b"not gzip")
    assert c.load("broken") is None
