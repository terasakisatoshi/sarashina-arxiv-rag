import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

API_URL = "https://api.sarashina.ai/v1/chat/completions"
DEFAULT_MODEL = "sarashina3-mini"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chat",
        description="Send a prompt to the Sarashina API and print the reply.",
    )
    parser.add_argument("prompt", nargs="+", help="the user prompt")
    parser.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help=f"model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument("-s", "--system", help="optional system prompt")
    parser.add_argument(
        "-t",
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="disable streaming and wait for the full response",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (overrides the SARASHINA_API_KEY environment variable)",
    )
    return parser


def build_payload(args: argparse.Namespace, text: str) -> dict:
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": text})

    payload = {"model": args.model, "messages": messages, "stream": not args.no_stream}
    if args.temperature is not None:
        payload["temperature"] = args.temperature
    return payload


def request(payload: dict, api_key: str):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream"
            if payload.get("stream")
            else "application/json",
        },
    )
    return urllib.request.urlopen(req)


def handle_error(err: urllib.error.HTTPError) -> None:
    try:
        body = err.read().decode("utf-8")
        message = json.loads(body).get("message", body)
    except Exception:
        message = err.reason
    print(f"error: HTTP {err.code}: {message}", file=sys.stderr)


def stream_response(resp) -> None:
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if not line or not line.startswith("data:"):
            continue
        chunk = line[len("data:") :].strip()
        if chunk == "[DONE]":
            break
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        choices = payload.get("choices") or []
        if not choices:
            continue
        content = choices[0].get("delta", {}).get("content")
        if content:
            print(content, end="", flush=True)
    print()


def full_response(resp) -> None:
    payload = json.loads(resp.read().decode("utf-8"))
    print(payload["choices"][0]["message"]["content"])


def main() -> None:
    args = build_parser().parse_args()
    load_dotenv(".env")
    api_key = args.api_key or os.environ.get("SARASHINA_API_KEY")
    if not api_key:
        print(
            "error: SARASHINA_API_KEY is not set "
            "(export SARASHINA_API_KEY=... or pass --api-key)",
            file=sys.stderr,
        )
        raise SystemExit(1)

    text = " ".join(args.prompt)
    payload = build_payload(args, text)

    try:
        with request(payload, api_key) as resp:
            if payload["stream"]:
                stream_response(resp)
            else:
                full_response(resp)
    except urllib.error.HTTPError as err:
        handle_error(err)
        raise SystemExit(1)
    except urllib.error.URLError as err:
        print(f"error: {err.reason}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
