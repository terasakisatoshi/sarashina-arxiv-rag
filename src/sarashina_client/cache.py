import gzip
import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path

_ARXIV_PATH = re.compile(r"^/(?:abs|pdf)/(.+?)(?:\.pdf)?$")


def cache_dir() -> Path:
    env = os.environ.get("SARASHINA_CACHE_DIR")
    base = Path(env) if env else Path.home() / ".cache" / "sarashina-client"
    base.mkdir(parents=True, exist_ok=True)
    return base


def arxiv_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if not (parsed.netloc == "arxiv.org" or parsed.netloc.endswith(".arxiv.org")):
        return None
    match = _ARXIV_PATH.match(parsed.path)
    return match.group(1) if match else None


def _sanitize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "-", value)


def doc_key(url: str) -> str:
    identifier = arxiv_id(url)
    if identifier:
        return "arxiv-" + _sanitize(identifier)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return "url-" + digest


def make_key(url: str, pages: str | None) -> str:
    page_part = _sanitize(pages.strip()) if pages and pages.strip() else "all"
    return f"{doc_key(url)}__pages-{page_part}"


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _path(key: str) -> Path:
    return cache_dir() / f"{key}.json.gz"


def _embed_path(doc: str) -> Path:
    return cache_dir() / f"{doc}__embeddings.json.gz"


def _translation_path(doc: str) -> Path:
    return cache_dir() / f"{doc}__translations.json.gz"


def _read_gzip(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001
        return None


def _write_gzip(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    tmp.replace(path)


def load_embeddings(doc: str) -> dict:
    return _read_gzip(_embed_path(doc)) or {}


def save_embeddings(doc: str, mapping: dict) -> None:
    _write_gzip(_embed_path(doc), mapping)


def load_translations(doc: str) -> dict:
    return _read_gzip(_translation_path(doc)) or {}


def save_translations(doc: str, mapping: dict) -> None:
    _write_gzip(_translation_path(doc), mapping)


def load(key: str) -> dict | None:
    return _read_gzip(_path(key))


def save(key: str, data: dict) -> None:
    _write_gzip(_path(key), data)
