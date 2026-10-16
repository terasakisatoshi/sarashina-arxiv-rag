import io
import urllib.error

import pytest

from sarashina_client import translate
from tests.conftest import FakeResponse


def _langs(*argv):
    return translate.resolve_langs(translate.build_parser().parse_args(list(argv)))


def test_resolve_langs():
    assert _langs("text") == ("ja", "en")
    assert _langs("text", "--from", "en") == ("en", "ja")
    assert _langs("text", "--to", "en") == ("ja", "en")
    assert _langs("text", "--to", "ja") == ("en", "ja")
    assert _langs("text", "--from", "en", "--to", "ja") == ("en", "ja")


def test_main_prints_translation(monkeypatch, capsys):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    monkeypatch.setattr("sys.argv", ["translate", "hello", "--to", "ja"])
    monkeypatch.setattr(
        translate.urllib.request,
        "urlopen",
        lambda req, timeout=None: FakeResponse(body={"translated_text": "こんにちは"}),
    )
    translate.main()
    assert capsys.readouterr().out == "こんにちは\n"


def test_main_http_error(monkeypatch, capsys):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    monkeypatch.setattr("sys.argv", ["translate", "hello"])

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            "http://x", 429, "Too Many", None, io.BytesIO(b'{"message":"slow down"}')
        )

    monkeypatch.setattr(translate.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit):
        translate.main()
    assert "HTTP 429: slow down" in capsys.readouterr().err
