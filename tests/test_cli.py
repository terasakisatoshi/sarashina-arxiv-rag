import io
import urllib.error

import pytest

from sarashina_client import cli
from tests.conftest import FakeResponse


def test_build_payload_defaults():
    args = cli.build_parser().parse_args(["hello"])
    payload = cli.build_payload(args, "hello")
    assert payload["model"] == cli.DEFAULT_MODEL
    assert payload["stream"] is True
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert "temperature" not in payload


def test_build_payload_with_system_and_temperature():
    args = cli.build_parser().parse_args(["-s", "sys", "-t", "0.5", "--no-stream", "hi"])
    payload = cli.build_payload(args, "hi")
    assert payload["stream"] is False
    assert payload["temperature"] == 0.5
    assert payload["messages"][0] == {"role": "system", "content": "sys"}


def test_stream_response(capsys):
    resp = FakeResponse(
        lines=[
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            "event: ping",
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]
    )
    cli.stream_response(resp)
    assert capsys.readouterr().out == "Hello world\n"


def test_full_response(capsys):
    resp = FakeResponse(body={"choices": [{"message": {"content": "Hi"}}]})
    cli.full_response(resp)
    assert capsys.readouterr().out == "Hi\n"


def test_handle_error(capsys):
    err = urllib.error.HTTPError(
        "http://x", 400, "Bad Request", None, io.BytesIO(b'{"message":"boom"}')
    )
    cli.handle_error(err)
    captured = capsys.readouterr()
    assert "HTTP 400: boom" in captured.err


def test_main_missing_key(monkeypatch):
    monkeypatch.setattr("sys.argv", ["chat", "hi"])
    with pytest.raises(SystemExit):
        cli.main()


def test_main_streams_output(monkeypatch, capsys):
    monkeypatch.setenv("SARASHINA_API_KEY", "test-key")
    monkeypatch.setattr("sys.argv", ["chat", "hi"])
    monkeypatch.setattr(
        cli,
        "request",
        lambda payload, api_key: FakeResponse(
            lines=['data: {"choices":[{"delta":{"content":"Hello"}}]}', "data: [DONE]"]
        ),
    )
    cli.main()
    assert capsys.readouterr().out == "Hello\n"
