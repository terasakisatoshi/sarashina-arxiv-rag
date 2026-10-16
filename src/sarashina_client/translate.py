import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

API_URL = "https://api.sarashina.ai/v1/translate"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translate",
        description="Translate text between Japanese and English via the Sarashina API.",
    )
    parser.add_argument("text", nargs="+", help="the text to translate")
    parser.add_argument(
        "--to",
        choices=["ja", "en"],
        default=None,
        help="target language (default: inferred from --from)",
    )
    parser.add_argument(
        "--from",
        dest="source",
        choices=["ja", "en"],
        default=None,
        help="source language (default: opposite of --to, or ja)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (overrides the SARASHINA_API_KEY environment variable)",
    )
    return parser


def resolve_langs(args: argparse.Namespace) -> tuple[str, str]:
    source, target = args.source, args.to
    if source and target:
        return source, target
    if source:
        return source, "en" if source == "ja" else "ja"
    if target:
        return "ja" if target == "en" else "en", target
    return "ja", "en"


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

    source, target = resolve_langs(args)
    payload = {
        "text": " ".join(args.text),
        "source_lang": source,
        "target_lang": target,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(json.load(resp)["translated_text"])
    except urllib.error.HTTPError as err:
        try:
            message = json.loads(err.read().decode("utf-8")).get("message", err.reason)
        except Exception:
            message = err.reason
        print(f"error: HTTP {err.code}: {message}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as err:
        print(f"error: {err.reason}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
