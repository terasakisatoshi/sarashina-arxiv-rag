import json
import math
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "https://api.sarashina.ai/v1"
EMBED_MODEL = "sarashina3-embedding"
RERANK_MODEL = "sarashina3-rerank"
MAX_RETRIES = 5

EMBED_RPM = 60
EMBED_MAX_CALLS = 55
EMBED_MAX_WORKERS = 4


class RateLimiter:
    def __init__(self, max_calls: int, period: float = 60.0) -> None:
        self.max_calls = max_calls
        self.period = period
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self.period:
                    self._times.popleft()
                if len(self._times) < self.max_calls:
                    self._times.append(now)
                    return
                wait = self.period - (now - self._times[0])
            time.sleep(max(wait, 0.01))


def _post(url: str, payload: dict, api_key: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
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
    raise RuntimeError("request failed after retries")


class Embedder:
    def __init__(
        self,
        api_key: str,
        max_calls: int = EMBED_MAX_CALLS,
        max_workers: int = EMBED_MAX_WORKERS,
    ) -> None:
        self.api_key = api_key
        self.limiter = RateLimiter(max_calls)
        self.max_workers = max_workers

    def embed(self, text: str, task_type: str) -> list[float]:
        self.limiter.acquire()
        data = _post(
            f"{BASE_URL}/embeddings",
            {"input": text, "task_type": task_type, "model": EMBED_MODEL},
            self.api_key,
        )
        return data["data"][0]["embedding"]

    def embed_many(
        self, texts: list[str], task_type: str, progress=None
    ) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)

        def work(index: int, text: str) -> None:
            results[index] = self.embed(text, task_type)
            if progress is not None:
                progress()

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for index, text in enumerate(texts):
                pool.submit(work, index, text)
        return [vec for vec in results if vec is not None]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rerank(
    query: str, documents: list[str], api_key: str, task_type: str = "GENERAL_QA"
) -> list[tuple[int, float]]:
    data = _post(
        f"{BASE_URL}/rerank",
        {
            "query": query,
            "documents": documents,
            "task_type": task_type,
            "model": RERANK_MODEL,
        },
        api_key,
    )
    return [
        (item["document_id"], item["score"]) for item in data["data"][0]["result"]
    ]


def retrieve(
    query: str,
    chunks: list[str],
    vectors: list[list[float]],
    api_key: str,
    candidate_k: int = 20,
    final_k: int = 5,
) -> list[int]:
    if not chunks:
        return []
    query_vector = Embedder(api_key).embed(query, "RETRIEVAL_QUERY")
    scored = sorted(
        ((cosine(query_vector, vec), i) for i, vec in enumerate(vectors)),
        reverse=True,
    )
    candidate_ids = [i for _, i in scored[:candidate_k]]
    if not candidate_ids:
        return []
    scores = rerank(query, [chunks[i] for i in candidate_ids], api_key)
    return [candidate_ids[doc_id] for doc_id, _ in scores[:final_k]]
