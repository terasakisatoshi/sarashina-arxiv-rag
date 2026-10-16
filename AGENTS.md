# AGENTS.md

CLI + FastAPI client for the Sarashina API (SB Intuitions Japanese LLMs). Single package `sarashina_client` under `src/`. Python 3.14, `uv` + `uv_build`.

## Commands

Everything runs through `uv run` (never bare `python`):

```sh
uv sync
uv run chat "..."                 # sarashina_client.cli:main
uv run translate "..." --to en    # sarashina_client.translate:main
uv run pdftranslate <path|url> -o out.md --pages 1-3 --html --pdf
uv run sarashina-web              # = uvicorn on 127.0.0.1:8000
uv run uvicorn sarashina_client.webapp:app --reload   # dev server
```

- **Tests: `uv run pytest`** (`tests/`, pytest + httpx dev deps). They are fully offline: HTTP is monkeypatched, no API key is needed, and the cache is redirected to a tmp dir. CI (`.github/workflows/test.yml`) runs this on push to `main` and on PRs. There is still **no linter and no typechecker** — do not invent `ruff`/`mypy` commands.
- `.env` lives at the **project root** and is loaded cwd-relative (`load_dotenv(".env")` in `cli`/`translate`/`pdftranslate` `main`, and in `webapp.get_api_key`). It holds a real `SARASHINA_API_KEY`, is gitignored, and must **never be overwritten/truncated** (a previous `> .env` destroyed the user's key).

## Conventions / gotchas

- HTTP is stdlib `urllib` only — no `requests`/`httpx`. Deps are just `pymupdf`, `markdown`, `python-dotenv`, `fastapi`, `uvicorn`.
- **Rate limits are the main constraint** (no `Retry-After` header → exponential backoff on 429): `sarashina3-mini|guard|translate` = 6 RPM; `sarashina3-nano|embedding|rerank` = 60 RPM. Prefer `nano` for side tasks so they don't starve `mini`. Route sequential translate calls through `pdftranslate.Translator` (throttles to 6 RPM).
- Embedding API takes **one text per request** and requires `task_type` (`RETRIEVAL_DOCUMENT` for the index, `RETRIEVAL_QUERY` for queries). Batching/parallelism lives in `rag.Embedder` (`RateLimiter` 55/min + 4 worker threads).
- `pdftranslate.normalize_url` rewrites arXiv `abs` → `pdf`; it is used by `download_pdf` **and** by cache keys (import it, don't re-roll arXiv parsing).
- RAG flow (`webapp.py` + `rag.py`): user asks in Japanese → JA→EN translate → `RETRIEVAL_QUERY` embed → cosine top-20 → `sarashina3-rerank` top-5 → `sarashina3-mini` answers in Japanese over SSE. Chunks are the **original English** text; only the query is translated.
- Caching (`cache.py`, dir `~/.cache/sarashina-client` or `$SARASHINA_CACHE_DIR`) has three layers: a per-range index cache (`<doc>__pages-<range>.json.gz`, keyed by normalized URL + page range, `all` when unset), a per-document chunk→vector store (`<doc>__embeddings.json.gz`, keyed by chunk-text SHA1), and a per-document chunk→translation store (`<doc>__translations.json.gz`, same SHA1 key). Rebuilding a wider page range reuses cached chunk embeddings and only embeds the missing ones; translations are reused across page ranges too.
- `webapp.JOBS` is an in-process dict: index jobs are lost on restart (disk cache survives). Only one in-memory job per request; no persistence.
- The entire web UI is an inline HTML/JS string (`PAGE` at the bottom of `webapp.py`). Changing endpoints usually means editing that embedded JS too.

## Layout

```
src/sarashina_client/
  cli.py          chat
  translate.py    translate
  pdftranslate.py PDF/arXiv extraction, translation, Markdown/HTML/PDF
  rag.py          embed / rerank / cosine retrieval
  cache.py        disk cache (index + embeddings + translations)
  webapp.py       FastAPI app + embedded UI
tests/            pytest suite (offline, HTTP monkeypatched)
```

`origin` is `git@github.com:terasakisatoshi/sarashina-arxiv-rag.git`; `main` tracks it.
