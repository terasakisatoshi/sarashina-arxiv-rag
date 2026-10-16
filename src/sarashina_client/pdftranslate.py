import argparse
import html
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf
from dotenv import load_dotenv

API_URL = "https://api.sarashina.ai/v1/translate"
MAX_CHARS = 4800
MIN_INTERVAL = 10.0
MAX_RETRIES = 5


class Translator:
    def __init__(self, api_key: str, source: str, target: str) -> None:
        self.api_key = api_key
        self.source = source
        self.target = target
        self._last = 0.0

    def _throttle(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def translate(self, text: str) -> str:
        payload = {
            "text": text,
            "source_lang": self.source,
            "target_lang": self.target,
        }
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                with urllib.request.urlopen(req) as resp:
                    self._last = time.monotonic()
                    return json.load(resp)["translated_text"]
            except urllib.error.HTTPError as err:
                self._last = time.monotonic()
                if err.code == 429 and attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** (attempt + 1) * 5)
                    continue
                try:
                    message = json.loads(err.read().decode("utf-8")).get(
                        "message", err.reason
                    )
                except Exception:
                    message = err.reason
                raise RuntimeError(f"HTTP {err.code}: {message}") from err
            except urllib.error.URLError as err:
                raise RuntimeError(err.reason) from err
        raise RuntimeError("translation failed after retries")


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            head, line = line[:limit], line[limit:]
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.append(head.strip())
        if len(current) + len(line) > limit:
            chunks.append(current.strip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.strip())
    return chunks


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        path = parsed.path
        if path.startswith("/abs/"):
            path = "/pdf/" + path[len("/abs/") :]
            parsed = parsed._replace(path=path)
            url = urllib.parse.urlunparse(parsed)
    return url


def download_pdf(url: str) -> tuple[Path, str]:
    url = normalize_url(url)
    name = Path(urllib.parse.urlparse(url).path).name or "download.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    dest = Path(tempfile.mkdtemp(prefix="pdftranslate-")) / name
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    print(f"downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest, name


def parse_pages(value: str) -> tuple[int, int]:
    if "-" in value:
        start, end = value.split("-", 1)
        return int(start), int(end)
    return int(value), int(value)


def extract_blocks(path: Path, pages_range: tuple[int, int] | None = None) -> list[list[str]]:
    doc = pymupdf.open(path)
    pages = []
    for page_no, page in enumerate(doc, start=1):
        if pages_range and not (pages_range[0] <= page_no <= pages_range[1]):
            continue
        blocks = [b[4].strip() for b in page.get_text("blocks")]
        pages.append([b for b in blocks if b])
    doc.close()
    return pages


def to_markdown(pages, translations, source: str, target: str, title: str) -> str:
    lines = [f"# {title}", "", f"_{source} → {target}_", ""]
    for page_no, (blocks, translated) in enumerate(zip(pages, translations), start=1):
        lines += [f"## Page {page_no}", ""]
        for index, (original, parts) in enumerate(zip(blocks, translated), start=1):
            lines += [
                f"### {index}",
                f"*原文 ({source})*",
                "",
            ]
            lines += [f"> {ln}" if ln else ">" for ln in original.splitlines()]
            lines += ["", f"*翻訳 ({target})*", ""]
            for part in parts:
                lines += [f"> {ln}" if ln else ">" for ln in part.splitlines()]
            lines += [""]
    return "\n".join(lines)


def to_html(markdown_text: str, title: str) -> str:
    import markdown

    body = markdown.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n"
        "<style>body{max-width:820px;margin:2rem auto;padding:0 1rem;"
        "font-family:system-ui,'Hiragino Sans',sans-serif;line-height:1.7}"
        "blockquote{border-left:3px solid #ccc;margin:.4rem 0;padding:.1rem 1rem;"
        "color:#333}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )


def to_translated_html(pages, translations, target: str, title: str) -> str:
    parts = [f"<h1>{html.escape(title)}</h1>"]
    for page_no, page in enumerate(translations, start=1):
        parts.append(f"<h2>Page {page_no}</h2>")
        for block in page:
            for part in block:
                body = html.escape(part).replace("\n", "<br>")
                parts.append(f"<p>{body}</p>")
    return (
        '<html><head><meta charset="utf-8"></head><body>'
        + "".join(parts)
        + "</body></html>"
    )


def html_to_pdf(html_text: str, path: Path) -> None:
    story = pymupdf.Story(html=html_text)
    writer = pymupdf.DocumentWriter(str(path))
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (50, 50, -50, -50)
    while True:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
        if not more:
            break
    writer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdftranslate",
        description="Translate a PDF's text into Japanese and emit bilingual Markdown/HTML.",
    )
    parser.add_argument(
        "input", help="input PDF path or http(s) URL (e.g. an arXiv PDF link)"
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="output path (default: <input>.md)"
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="only process a page range, e.g. 1-3 or 2 (default: all)",
    )
    parser.add_argument(
        "--from",
        dest="source",
        choices=["ja", "en"],
        default="en",
        help="source language (default: en)",
    )
    parser.add_argument(
        "--to",
        dest="target",
        choices=["ja", "en"],
        default="ja",
        help="target language (default: ja)",
    )
    parser.add_argument("--html", action="store_true", help="also emit an HTML file")
    parser.add_argument(
        "--pdf",
        dest="emit_pdf",
        action="store_true",
        help="also emit a translated-only PDF",
    )
    parser.add_argument(
        "--api-key", default=None, help="API key (overrides SARASHINA_API_KEY)"
    )
    return parser


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
    if is_url(args.input):
        try:
            local, title = download_pdf(args.input)
        except (urllib.error.URLError, urllib.error.HTTPError) as err:
            print(f"error: failed to download: {err}", file=sys.stderr)
            raise SystemExit(1)
        default_output = Path(title).with_suffix(".md")
    else:
        local = Path(args.input)
        if not local.is_file():
            print(f"error: file not found: {local}", file=sys.stderr)
            raise SystemExit(1)
        title = local.name
        default_output = local.with_suffix(".md")

    pages_range = parse_pages(args.pages) if args.pages else None
    translator = Translator(api_key, args.source, args.target)
    pages = extract_blocks(local, pages_range)

    translations: list[list[list[str]]] = []
    total = sum(len(p) for p in pages)
    if total == 0:
        print(
            "error: no extractable text found (scanned PDFs need OCR first)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    done = 0
    for page in pages:
        page_translations = []
        for block in page:
            chunks = split_text(block)
            try:
                page_translations.append(
                    [translator.translate(chunk) for chunk in chunks]
                )
            except RuntimeError as err:
                print(f"error: {err}", file=sys.stderr)
                raise SystemExit(1)
            done += 1
            print(f"[{done}/{total}] translated a block", file=sys.stderr)
        translations.append(page_translations)

    markdown_text = to_markdown(
        pages, translations, args.source, args.target, title
    )
    output = args.output or default_output
    output.write_text(markdown_text, encoding="utf-8")
    print(f"saved {output}")

    if args.html:
        html_path = output.with_suffix(".html")
        html_path.write_text(to_html(markdown_text, title), encoding="utf-8")
        print(f"saved {html_path}")

    if args.emit_pdf:
        pdf_path = output.with_suffix(".pdf")
        html_to_pdf(
            to_translated_html(pages, translations, args.target, title), pdf_path
        )
        print(f"saved {pdf_path}")


if __name__ == "__main__":
    main()
