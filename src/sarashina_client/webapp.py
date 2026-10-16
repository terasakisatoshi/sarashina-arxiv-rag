import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import cache
from .pdftranslate import (
    Translator,
    download_pdf,
    extract_blocks,
    normalize_url,
    parse_pages,
    split_text,
)
from .rag import Embedder, retrieve

CHAT_URL = "https://api.sarashina.ai/v1/chat/completions"
CHAT_MODEL = "sarashina3-mini"
SUGGEST_MODEL = "sarashina3-nano"

app = FastAPI(title="Sarashina arXiv RAG")

INSTRUCTIONS = (
    "あなたは論文の解説アシスタントです。ユーザーの質問ごとに、英語論文の該当箇所が"
    "抜粋として与えられます。その抜粋だけに基づいて、日本語で分かりやすく解説・回答して"
    "ください。各抜粋には [p.ページ番号] の出典が付いています。回答中で根拠を示す場合は"
    "その出典を引用してください。抜粋に無いことは推測せず、分からない場合はその旨を"
    "伝えてください。回答は Markdown で記述してください。"
)


def build_context(chunks: list[str], pages: list[int], indices: list[int]) -> str:
    lines = ["--- 関連する論文の抜粋 ---"]
    seen: set[int] = set()
    for i in indices:
        if i in seen:
            continue
        seen.add(i)
        lines.append(f"[p.{pages[i]}] {chunks[i]}")
    return "\n\n".join(lines)


@dataclass
class Job:
    status: str = "running"
    phase: str = "extracting"
    indexed: bool = False
    cached: bool = False
    reused: int = 0
    done: int = 0
    total: int = 0
    embed_done: int = 0
    embed_total: int = 0
    title: str = ""
    dockey: str = ""
    markdown: str = ""
    chunks: list = field(default_factory=list)
    pages: list = field(default_factory=list)
    vectors: list = field(default_factory=list)
    error: str = ""
    messages: list = field(default_factory=list)


JOBS: dict[str, Job] = {}


def get_api_key() -> str:
    load_dotenv(".env")
    key = os.environ.get("SARASHINA_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="SARASHINA_API_KEY is not set")
    return key


class IndexRequest(BaseModel):
    url: str
    pages: str | None = None


class ChatRequest(BaseModel):
    job_id: str
    message: str


class SuggestRequest(BaseModel):
    job_id: str


class TranslateRequest(BaseModel):
    job_id: str
    index: int


def complete(messages: list[dict], model: str, api_key: str) -> str:
    payload = {"model": model, "messages": messages, "stream": False}
    req = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def seed_embeddings(dockey: str, data: dict) -> None:
    try:
        mapping = cache.load_embeddings(dockey)
        changed = False
        for chunk, vector in zip(data.get("chunks", []), data.get("vectors", [])):
            key = cache.text_hash(chunk)
            if key not in mapping:
                mapping[key] = vector
                changed = True
        if changed:
            cache.save_embeddings(dockey, mapping)
    except Exception:  # noqa: BLE001
        pass


def apply_cached(job: Job, data: dict) -> None:
    job.title = data["title"]
    job.chunks = data["chunks"]
    job.pages = data["pages"]
    job.vectors = data["vectors"]
    job.markdown = data.get("markdown", "")
    job.total = data.get("total", len(job.chunks))
    job.done = job.total
    job.embed_total = len(job.chunks)
    job.embed_done = job.embed_total
    job.cached = True
    job.indexed = True
    job.phase = "done"
    job.status = "done"


def run_index(job: Job, req: IndexRequest, api_key: str) -> None:
    try:
        url = normalize_url(req.url)
        key = cache.make_key(url, req.pages)
        dockey = cache.doc_key(url)
        job.dockey = dockey
        cached = cache.load(key)
        if cached is not None:
            apply_cached(job, cached)
            seed_embeddings(dockey, cached)
            return

        job.phase = "extracting"
        local, title = download_pdf(req.url)
        job.title = title
        pages_range = parse_pages(req.pages) if req.pages else None
        raw_pages = extract_blocks(local, pages_range)
        job.total = sum(len(p) for p in raw_pages)
        if job.total == 0:
            raise RuntimeError("no extractable text found (scanned PDFs need OCR first)")

        start_page = pages_range[0] if pages_range else 1
        lines = [f"# {title}", ""]
        for page_index, page in enumerate(raw_pages):
            page_no = start_page + page_index
            lines += [f"## Page {page_no}", ""]
            for block in page:
                for chunk in split_text(block):
                    chunk = chunk.strip()
                    if chunk:
                        job.chunks.append(chunk)
                        job.pages.append(page_no)
                        lines += [chunk, ""]
                job.done += 1
        job.markdown = "\n".join(lines)

        job.embed_total = len(job.chunks)
        if job.embed_total == 0:
            raise RuntimeError("no text to index")

        embeddings = cache.load_embeddings(dockey)
        hashes = [cache.text_hash(chunk) for chunk in job.chunks]
        vectors: list[list[float] | None] = [embeddings.get(h) for h in hashes]
        job.reused = sum(1 for vec in vectors if vec is not None)
        job.embed_done = job.reused

        pending = [i for i, vec in enumerate(vectors) if vec is None]
        job.phase = "embedding"
        if pending:
            embedder = Embedder(api_key)
            progress_lock = threading.Lock()

            def on_progress() -> None:
                with progress_lock:
                    job.embed_done += 1

            new_vectors = embedder.embed_many(
                [job.chunks[i] for i in pending], "RETRIEVAL_DOCUMENT", on_progress
            )
            for index, vector in zip(pending, new_vectors):
                vectors[index] = vector
                embeddings[hashes[index]] = vector

        job.vectors = [vec for vec in vectors if vec is not None]
        job.indexed = True
        job.phase = "done"
        job.status = "done"
        try:
            cache.save_embeddings(dockey, embeddings)
            cache.save(
                key,
                {
                    "title": job.title,
                    "chunks": job.chunks,
                    "pages": job.pages,
                    "vectors": job.vectors,
                    "markdown": job.markdown,
                    "total": job.total,
                },
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as err:  # noqa: BLE001
        job.status = "error"
        job.error = str(err)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.post("/api/index")
def start_index(req: IndexRequest) -> dict:
    api_key = get_api_key()
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http(s)://")
    job_id = uuid.uuid4().hex
    job = Job()
    JOBS[job_id] = job
    threading.Thread(
        target=run_index, args=(job, req, api_key), daemon=True
    ).start()
    return {"job_id": job_id}


@app.get("/api/index/{job_id}")
def index_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {
        "status": job.status,
        "phase": job.phase,
        "indexed": job.indexed,
        "cached": job.cached,
        "done": job.done,
        "total": job.total,
        "embed_done": job.embed_done,
        "embed_total": job.embed_total,
        "reused": job.reused,
        "title": job.title,
        "error": job.error,
    }


@app.get("/api/paper/{job_id}")
def paper(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if job.status != "done":
        raise HTTPException(status_code=409, detail="indexing is not finished")
    translations = cache.load_translations(job.dockey) if job.dockey else {}
    chunks = []
    translated = {}
    for index, (text, page) in enumerate(zip(job.chunks, job.pages)):
        chunks.append({"i": index, "page": page, "text": text})
        cached = translations.get(cache.text_hash(text))
        if cached:
            translated[str(index)] = cached
    return {"title": job.title, "chunks": chunks, "translations": translated}


@app.post("/api/translate")
def translate(req: TranslateRequest) -> dict:
    api_key = get_api_key()
    job = JOBS.get(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if not (0 <= req.index < len(job.chunks)):
        raise HTTPException(status_code=400, detail="chunk index out of range")

    text = job.chunks[req.index]
    digest = cache.text_hash(text)
    translations = cache.load_translations(job.dockey) if job.dockey else {}
    if digest in translations:
        return {"index": req.index, "text": translations[digest]}

    try:
        translated = Translator(api_key, "en", "ja").translate(text)
    except RuntimeError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err

    translations[digest] = translated
    try:
        if job.dockey:
            cache.save_translations(job.dockey, translations)
    except Exception:  # noqa: BLE001
        pass
    return {"index": req.index, "text": translated}


def stream_chat(messages: list[dict], api_key: str):
    payload = {"model": CHAT_MODEL, "messages": messages, "stream": True}
    req = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[len("data:") :].strip()
            if chunk == "[DONE]":
                break
            try:
                data = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices") or []
            if choices:
                delta = choices[0].get("delta", {}).get("content")
                if delta:
                    yield delta


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    api_key = get_api_key()
    job = JOBS.get(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if not job.indexed:
        raise HTTPException(status_code=409, detail="RAG index is not built yet")

    query_en = Translator(api_key, "ja", "en").translate(req.message)
    indices = retrieve(query_en, job.chunks, job.vectors, api_key)
    sources = sorted({job.pages[i] for i in indices})
    context = build_context(job.chunks, job.pages, indices)

    if not job.messages:
        job.messages.append({"role": "system", "content": INSTRUCTIONS})
    job.messages.append({"role": "system", "content": context})
    job.messages.append({"role": "user", "content": req.message})

    def generate():
        collected = ""
        try:
            for delta in stream_chat(job.messages, api_key):
                collected += delta
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            job.messages.append({"role": "assistant", "content": collected})
            if sources:
                links = ", ".join(
                    f'<a href="#page-{p}">p.{p}</a>' for p in sources
                )
                yield f"data: {json.dumps({'note': '参考ページ: ' + links}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except urllib.error.HTTPError as err:
            yield f"data: {json.dumps({'error': f'HTTP {err.code}'})}\n\n"
        except Exception as err:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(err)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/suggest")
def suggest(req: SuggestRequest) -> dict:
    api_key = get_api_key()
    job = JOBS.get(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")

    history = []
    for message in job.messages:
        if message["role"] == "user":
            history.append("ユーザー: " + message["content"])
        elif message["role"] == "assistant":
            history.append("アシスタント: " + message["content"])
    if not history:
        return {"suggestions": []}

    prompt = (
        "以下は論文についての直近の会話です。\n\n"
        + "\n".join(history[-6:])
        + "\n\nこの論文について、ユーザーが次に尋ねると有用な質問を3つ、"
        "日本語で簡潔に提案してください。質問文だけを1行に1つ、番号や記号を付けずに"
        "出力してください。"
    )
    try:
        content = complete([{"role": "user", "content": prompt}], SUGGEST_MODEL, api_key)
    except Exception:  # noqa: BLE001
        return {"suggestions": []}
    suggestions = [
        line.strip().lstrip("-・*0123456789. 　\t")
        for line in content.splitlines()
        if line.strip()
    ]
    return {"suggestions": [s for s in suggestions if s][:3]}


PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sarashina arXiv RAG</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked-katex-extension@5/lib/index.umd.js"></script>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, "Hiragino Sans", sans-serif; height: 100vh;
         display: flex; flex-direction: column; background: #f5f6f8; }
  header { padding: .6rem 1rem; background: #1f2937; color: #fff; display: flex; gap: .5rem;
           align-items: center; flex-wrap: wrap; }
  header h1 { font-size: 1rem; margin: 0 1rem 0 0; }
  header input { padding: .4rem; border-radius: 4px; border: 1px solid #4b5563; }
  #url { flex: 1; min-width: 220px; }
  #pages { width: 90px; }
  header button { padding: .45rem 1rem; border: none; border-radius: 4px; background: #2563eb;
                  color: #fff; cursor: pointer; }
  header button:disabled { opacity: .5; cursor: default; }
  main { flex: 1; display: flex; min-height: 0; }
  #left { flex: 1.3; overflow: auto; padding: 1.5rem 2rem; background: #fff; }
  #right { flex: 1; display: flex; flex-direction: column; min-width: 320px; }
  #divider { flex: 0 0 6px; cursor: col-resize; background: #e5e7eb; transition: background .15s;
             touch-action: none; }
  #divider:hover, #divider.dragging { background: #2563eb; }
  #chat-log { flex: 1; overflow: auto; padding: 1rem; display: flex; flex-direction: column;
              gap: .75rem; }
  .msg { padding: .6rem .8rem; border-radius: 8px; line-height: 1.6; }
  .user { background: #2563eb; color: #fff; align-self: flex-end; max-width: 90%;
          white-space: pre-wrap; }
  .bot { background: #e5e7eb; color: #111827; align-self: flex-start; max-width: 95%; }
  .bot :first-child { margin-top: 0; } .bot :last-child { margin-bottom: 0; }
  .source { font-size: .8rem; color: #6b7280; margin-top: .3rem; }
  .source a { color: #2563eb; text-decoration: underline; cursor: pointer; }
  #suggestions { display: flex; flex-wrap: wrap; gap: .4rem; padding: .5rem .75rem 0; }
  .chip { background: #eef2ff; color: #3730a3; border: 1px solid #c7d2fe; border-radius: 14px;
          padding: .3rem .7rem; font-size: .82rem; cursor: pointer; font: inherit; }
  .chip:hover { background: #e0e7ff; }
  #chat-form { display: flex; gap: .5rem; padding: .75rem; border-top: 1px solid #e5e7eb; }
  #chat-input { flex: 1; padding: .55rem; border: 1px solid #d1d5db; border-radius: 6px;
                resize: none; font: inherit; }
  #chat-form button { padding: 0 1rem; border: none; border-radius: 6px; background: #059669;
                      color: #fff; cursor: pointer; }
  #status { font-size: .85rem; color: #d1d5db; }
  #left h1 { font-size: 1.4rem; } #left h2 { border-bottom: 1px solid #eee; padding-bottom: .3rem; }
  #left p { line-height: 1.7; }
  .empty { color: #9ca3af; }
  .chunk { position: relative; padding: .5rem .75rem; margin: .3rem 0; border-radius: 6px;
           cursor: pointer; transition: background .15s; }
  .chunk:hover { background: #eef2ff; }
  .chunk.show-ja { background: #f0fdf4; }
  .chunk.loading .chunk-body { color: #9ca3af; }
  .chunk.translated::before { content: "訳"; position: absolute; top: .35rem; left: -.7rem;
           font-size: .65rem; line-height: 1.4; color: #059669; background: #ecfdf5;
           border: 1px solid #a7f3d0; border-radius: 3px; padding: 0 .2rem; }
  .chunk-body { line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
</style>
</head>
<body>
<header>
  <h1>arXiv RAG</h1>
  <input id="url" placeholder="https://arxiv.org/pdf/2607.19653" value="https://arxiv.org/pdf/2607.19653">
  <input id="pages" placeholder="例: 1-3">
  <button id="go">RAG構築</button>
  <span id="status"></span>
</header>
<main>
  <section id="left"><p class="empty">arXiv の PDF URL を入力して「RAG構築」を押してください。構築後、本文の段落をクリックすると日本語訳を表示します。</p></section>
  <div id="divider" title="ドラッグで幅を変更 / ダブルクリックでリセット"></div>
  <section id="right">
    <div id="chat-log"></div>
    <div id="suggestions"></div>
    <form id="chat-form">
      <textarea id="chat-input" rows="2" placeholder="論文について質問する（例: この論文の貢献は？）"></textarea>
      <button type="submit">送信</button>
    </form>
  </section>
</main>
<script>
const log = document.getElementById("chat-log");
let jobId = null;
let indexed = false;

if (typeof marked !== "undefined" && typeof markedKatex !== "undefined") {
  marked.use(markedKatex({ throwOnError: false, nonStandard: true }));
}

const leftPane = document.getElementById("left");
const divider = document.getElementById("divider");
const mainEl = document.querySelector("main");
let dragging = false;

divider.addEventListener("pointerdown", (e) => {
  dragging = true;
  divider.classList.add("dragging");
  divider.setPointerCapture(e.pointerId);
  document.body.style.userSelect = "none";
  e.preventDefault();
});

divider.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const rect = mainEl.getBoundingClientRect();
  const minLeft = 200;
  const maxLeft = rect.width - 320;
  const width = Math.max(minLeft, Math.min(e.clientX - rect.left, maxLeft));
  leftPane.style.flex = `0 0 ${width}px`;
});

function endDrag(e) {
  if (!dragging) return;
  dragging = false;
  divider.classList.remove("dragging");
  document.body.style.userSelect = "";
  if (divider.hasPointerCapture(e.pointerId)) {
    divider.releasePointerCapture(e.pointerId);
  }
}
divider.addEventListener("pointerup", endDrag);
divider.addEventListener("pointercancel", endDrag);

divider.addEventListener("dblclick", () => { leftPane.style.flex = ""; });

function renderMarkdown(text) {
  if (typeof marked !== "undefined") { return marked.parse(text); }
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function addMsg(cls, text) {
  const el = document.createElement("div");
  el.className = "msg " + cls;
  if (cls === "bot") { el.innerHTML = renderMarkdown(text); }
  else { el.textContent = text; }
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function loadSuggestions() {
  const box = document.getElementById("suggestions");
  box.innerHTML = "";
  if (!jobId) return;
  try {
    const res = await fetch("/api/suggest", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job_id: jobId}),
    });
    if (!res.ok) return;
    const data = await res.json();
    for (const text of (data.suggestions || [])) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = text;
      chip.addEventListener("click", () => {
        const input = document.getElementById("chat-input");
        input.value = text;
        input.focus();
      });
      box.appendChild(chip);
    }
  } catch (e) { /* ignore */ }
}

function addSource(html) {
  const el = document.createElement("div");
  el.className = "msg bot source";
  el.innerHTML = html;
  el.querySelectorAll("a").forEach((a) => {
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      const target = document.querySelector(a.getAttribute("href"));
      if (target) target.scrollIntoView({behavior: "smooth", block: "start"});
    });
  });
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

function makeChunk(chunk, translation) {
  const el = document.createElement("div");
  el.className = "chunk";
  el.dataset.i = String(chunk.i);
  el.dataset.en = chunk.text;
  const body = document.createElement("div");
  body.className = "chunk-body";
  body.textContent = chunk.text;
  el.appendChild(body);
  if (translation) {
    el.dataset.ja = translation;
    el.classList.add("translated");
  }
  el.addEventListener("click", () => toggleChunk(el));
  return el;
}

function renderPaper(paper) {
  const left = document.getElementById("left");
  left.innerHTML = "";
  const title = document.createElement("h1");
  title.textContent = paper.title || "";
  left.appendChild(title);
  let currentPage = null;
  for (const chunk of paper.chunks) {
    if (chunk.page !== currentPage) {
      currentPage = chunk.page;
      const h2 = document.createElement("h2");
      h2.id = "page-" + chunk.page;
      h2.textContent = "Page " + chunk.page;
      left.appendChild(h2);
    }
    left.appendChild(makeChunk(chunk, paper.translations[String(chunk.i)]));
  }
}

async function toggleChunk(el) {
  const body = el.querySelector(".chunk-body");
  if (el.dataset.ja) {
    if (el.dataset.lang === "ja") {
      body.textContent = el.dataset.en;
      el.dataset.lang = "en";
      el.classList.remove("show-ja");
    } else {
      body.textContent = el.dataset.ja;
      el.dataset.lang = "ja";
      el.classList.add("show-ja");
    }
    return;
  }
  if (el.dataset.loading) return;
  el.dataset.loading = "1";
  el.classList.add("loading");
  const previous = body.textContent;
  body.textContent = "翻訳中…";
  try {
    const res = await fetch("/api/translate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job_id: jobId, index: Number(el.dataset.i)}),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    el.dataset.ja = data.text;
    el.dataset.lang = "ja";
    el.classList.add("translated", "show-ja");
    body.textContent = data.text;
  } catch (e) {
    body.textContent = previous;
  } finally {
    delete el.dataset.loading;
    el.classList.remove("loading");
  }
}

document.getElementById("go").addEventListener("click", async () => {
  const url = document.getElementById("url").value.trim();
  const pages = document.getElementById("pages").value.trim();
  if (!url) return;
  const status = document.getElementById("status");
  const btn = document.getElementById("go");
  btn.disabled = true;
  status.textContent = "開始...";
  document.getElementById("left").innerHTML = '<p class="empty">PDF を取得中...</p>';
  log.innerHTML = "";
  document.getElementById("suggestions").innerHTML = "";
  jobId = null;
  indexed = false;
  try {
    const res = await fetch("/api/index", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url, pages: pages || null}),
    });
    if (!res.ok) throw new Error(await res.text());
    jobId = (await res.json()).job_id;
    await pollIndex();
  } catch (e) {
    status.textContent = "エラー: " + e.message;
    btn.disabled = false;
  }
});

async function pollIndex() {
  const status = document.getElementById("status");
  const res = await fetch("/api/index/" + jobId);
  const info = await res.json();
  if (info.status === "running") {
    if (info.phase === "extracting") {
      status.textContent = `テキスト抽出中... ${info.done}/${info.total || "?"} ブロック`;
    } else {
      const reused = info.reused ? `（再利用 ${info.reused}）` : "";
      status.textContent = `埋め込み中... ${info.embed_done}/${info.embed_total || "?"} チャンク${reused}`;
    }
    setTimeout(pollIndex, 1000);
  } else if (info.status === "done") {
    indexed = true;
    const suffix = info.reused ? `（再利用 ${info.reused}/${info.embed_total}）` : "";
    status.textContent = info.cached ? "キャッシュから復元（RAG構築済み）" : "RAG構築完了" + suffix;
    document.getElementById("go").disabled = false;
    const paper = await (await fetch("/api/paper/" + jobId)).json();
    renderPaper(paper);
    addMsg("bot", "RAG の準備ができました。論文について質問できます。各段落をクリックすると日本語訳を表示/切り替えできます。");
  } else {
    status.textContent = "エラー: " + info.error;
    document.getElementById("go").disabled = false;
  }
}

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  if (!jobId || !indexed) { addMsg("bot", "先に「RAG構築」を実行してください。"); return; }
  input.value = "";
  document.getElementById("suggestions").innerHTML = "";
  addMsg("user", message);
  const bot = addMsg("bot", "");
  let raw = "";
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({job_id: jobId, message}),
    });
    if (!res.ok) throw new Error(await res.text());
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const parts = buffer.split("\\n\\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") break;
        const obj = JSON.parse(data);
        if (obj.error) { raw += "\\n\\n[エラー] " + obj.error; bot.innerHTML = renderMarkdown(raw); }
        else if (obj.delta) { raw += obj.delta; bot.innerHTML = renderMarkdown(raw); }
        else if (obj.note) { addSource(obj.note); }
        log.scrollTop = log.scrollHeight;
      }
    }
    await loadSuggestions();
  } catch (e) {
    raw += "\\n\\n[エラー] " + e.message;
    bot.innerHTML = renderMarkdown(raw);
  }
});
</script>
</body>
</html>
"""


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
