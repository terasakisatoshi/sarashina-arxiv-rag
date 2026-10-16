import pymupdf
import pytest

from sarashina_client import pdftranslate


def _make_pdf(path):
    doc = pymupdf.open()
    for text in ("Page one text", "Page two text"):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


class _RawResponse:
    def __init__(self, raw):
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_is_url():
    assert pdftranslate.is_url("http://x/a.pdf")
    assert pdftranslate.is_url("https://x/a.pdf")
    assert not pdftranslate.is_url("/tmp/a.pdf")


def test_normalize_url_arxiv():
    assert (
        pdftranslate.normalize_url("https://arxiv.org/abs/1706.03762")
        == "https://arxiv.org/pdf/1706.03762"
    )
    assert (
        pdftranslate.normalize_url("https://www.arxiv.org/abs/1706.03762v2")
        == "https://www.arxiv.org/pdf/1706.03762v2"
    )
    url = "https://arxiv.org/pdf/1706.03762v2"
    assert pdftranslate.normalize_url(url) == url
    other = "https://example.com/abs/1"
    assert pdftranslate.normalize_url(other) == other


def test_parse_pages():
    assert pdftranslate.parse_pages("1-3") == (1, 3)
    assert pdftranslate.parse_pages("2") == (2, 2)


def test_split_text():
    assert pdftranslate.split_text("hello") == ["hello"]
    assert pdftranslate.split_text("") == []
    assert pdftranslate.split_text("  spaced  ") == ["spaced"]
    assert pdftranslate.split_text("abcdefghij", limit=5) == ["abcde", "fghij"]
    assert pdftranslate.split_text("a\nb\nc", limit=10) == ["a\nb\nc"]


def test_download_pdf_normalizes_arxiv(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _RawResponse(b"%PDF-1.7 fake")

    monkeypatch.setattr(pdftranslate.urllib.request, "urlopen", fake_urlopen)
    path, title = pdftranslate.download_pdf("https://arxiv.org/abs/1706.03762")
    assert captured["url"] == "https://arxiv.org/pdf/1706.03762"
    assert title == "1706.03762.pdf"
    assert path.read_bytes() == b"%PDF-1.7 fake"


def test_download_pdf_non_pdf_name(monkeypatch):
    monkeypatch.setattr(
        pdftranslate.urllib.request, "urlopen", lambda req, timeout=None: _RawResponse(b"x")
    )
    _, title = pdftranslate.download_pdf("https://example.com/paper")
    assert title == "paper.pdf"


def test_extract_blocks(tmp_path):
    path = _make_pdf(tmp_path / "sample.pdf")
    pages = pdftranslate.extract_blocks(path)
    assert len(pages) == 2
    assert any("Page one" in block for block in pages[0])

    limited = pdftranslate.extract_blocks(path, (2, 2))
    assert len(limited) == 1
    assert any("Page two" in block for block in limited[0])


def test_to_markdown():
    pages = [["original text"]]
    translations = [[["訳文"]]]
    out = pdftranslate.to_markdown(pages, translations, "en", "ja", "Title")
    assert "# Title" in out
    assert "原文 (en)" in out
    assert "翻訳 (ja)" in out
    assert "> original text" in out
    assert "> 訳文" in out


def test_to_html_escapes_title():
    out = pdftranslate.to_html("# hi\n\nbody", "<Title>")
    assert "<!DOCTYPE html>" in out
    assert "<title>&lt;Title&gt;</title>" in out
    assert "body" in out


def test_to_translated_html():
    translations = [[["訳1"]]]
    out = pdftranslate.to_translated_html([[{}]], translations, "ja", "Title")
    assert "<h1>Title</h1>" in out
    assert "<p>訳1</p>" in out


def test_translator_missing_key_exits(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pdftranslate", "missing.pdf"])
    with pytest.raises(SystemExit):
        pdftranslate.main()
