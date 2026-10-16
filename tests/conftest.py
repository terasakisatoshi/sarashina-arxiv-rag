import json

import pytest


class FakeResponse:
    """Minimal stand-in for the object returned by urllib.request.urlopen."""

    def __init__(self, body=None, lines=None):
        self._body = body
        self._lines = lines or []

    def read(self):
        return json.dumps(self._body).encode("utf-8")

    def __iter__(self):
        for line in self._lines:
            yield line.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Point the disk cache at a temp dir and never read the real .env."""
    monkeypatch.setenv("SARASHINA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("SARASHINA_API_KEY", raising=False)

    import sarashina_client.cli as cli
    import sarashina_client.pdftranslate as pdftranslate
    import sarashina_client.translate as translate
    import sarashina_client.webapp as webapp

    for module in (cli, pdftranslate, translate, webapp):
        monkeypatch.setattr(module, "load_dotenv", lambda *a, **k: None)
    return tmp_path
