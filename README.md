# sarashina-client

[Sarashina API](https://docs.sarashina.ai/)（SB Intuitions の日本語 LLM）をコマンドライン / Web から使うためのクライアントです。

- `chat` — プロンプトを送って対話する
- `translate` — 日英・英日のテキスト翻訳
- `pdftranslate` — PDF（arXiv URL 可）を日本語に翻訳して対訳 Markdown / HTML / PDF を生成
- `sarashina-web` — arXiv 論文を RAG 化し、その内容を解説するチャットボットを並べた Web アプリ

## セットアップ

[uv](https://docs.astral.sh/uv/) を使います（Python 3.14 以上）。まずリポジトリを取得し、そのディレクトリで依存をインストールします。

```sh
git clone https://github.com/terasakisatoshi/sarashina-arxiv-rag.git
cd sarashina-arxiv-rag
uv sync
```

API キーを取得し、プロジェクト直下の `.env` に設定します。

```sh
# https://chat.sarashina.ai/dashboard/api-keys で発行
# 初回のみ。すでに .env がある場合は上書きせず、エディタで編集してください。
[ -f .env ] || echo 'SARASHINA_API_KEY=your_api_key_here' > .env
```

`.env` は `.gitignore` 済みです。`--api-key` を指定すれば環境変数より優先されます。

> **注意:** `.env` はプロジェクト直下に置き、CLI も同じディレクトリから実行してください（読み込みはカレントディレクトリ基準です）。既存の `.env` を `> .env` で上書きすると、設定済みの API キーが失われます。

## 使い方

### chat

```sh
uv run chat "Sarashina をテーマにした短い物語を書いてください"
uv run chat -m sarashina3-nano "この文を要約して: ..."
uv run chat -s "あなたは関西弁で話します" "今日の天気は？"
```

主なオプション: `-m/--model`（既定 `sarashina3-mini`）、`-s/--system`、`-t/--temperature`、`--no-stream`、`--api-key`。応答は既定でストリーミング表示され、返答本文が標準出力に出力されます。`-t/--temperature` を指定しない場合はリクエストに含めず API 既定値に任せます。

### translate

```sh
uv run translate "SB Intuitions について教えて下さい" --to en
uv run translate "I'll give it a shot, no cap." --to ja
uv run translate "今日はいい天気ですね" --from ja   # → en
```

`--from` / `--to` は `ja` か `en`（片方だけ指定すればもう片方は逆言語、省略時は `ja → en`）。両方指定した場合はその組み合わせをそのまま使います。翻訳は `sarashina3-translate` が使われ、`--api-key` で API キーを上書きできます。訳文は標準出力に表示されます。

### pdftranslate

```sh
uv run pdftranslate paper.pdf -o paper.md --html --pdf
uv run pdftranslate https://arxiv.org/abs/1706.03762 --pages 1-3 -o attention.md
```

- 入力はローカルパスまたは `http(s)` URL。arXiv の `abs` URL は内部で `pdf` URL に変換されます。
- 既定は `en → ja`。対訳（原文 / 訳文）の Markdown を出力します。
- 出力先は `-o/--output` で指定（省略時は入力のファイル名を `.md` にしたもの。例: `paper.pdf` → `paper.md`）。
- `--pages 1-3` でページ範囲を限定（超長編は必須級に遅いため推奨）。
- `--html` で HTML、`--pdf` で翻訳のみの PDF も生成します。ファイル名は出力 Markdown の拡張子を `.html` / `.pdf` に置き換えたもの（例: `-o paper.md` なら `paper.html` / `paper.pdf`）。

### sarashina-web（arXiv RAG 解説ボット）

```sh
uv run sarashina-web
# または: uv run uvicorn sarashina_client.webapp:app --reload
```

http://127.0.0.1:8000 を開きます。

1. arXiv URL（`abs` / `pdf` どちらも可）とページ範囲を入力 →「RAG構築」で PDF を取得・本文を抽出し、埋め込みを構築（左ペインに抽出した原文を表示、進捗はポーリング）。
2. 左ペインの本文を**段落（チャンク）単位でクリックすると日本語訳を表示**し、もう一度クリックで原文 ↔ 訳文をトグル（訳はチャンク単位でキャッシュされ再利用）。
3. 右ペインで論文について日本語で質問。
4. 内部で質問文を**英訳**し、その英語を `RETRIEVAL_QUERY` で埋め込み → コサイン top-k → `sarashina3-rerank` で再順位付け。
5. 上位チャンク（英語原文、ページ出典つき）を文脈に `sarashina3-mini` が日本語で回答（SSE ストリーミング、Markdown 表示、数式 `$...$` / `$$...$$` は KaTeX で描画）。回答末尾に参考ページを表示。

- インデックス: 抽出した原文チャンクを `RETRIEVAL_DOCUMENT` で埋め込み。
- チャンク翻訳: 段落クリック時に `sarashina3-translate`（en → ja）でそのチャンクだけ翻訳。既訳はキャッシュから即返し、API は呼びません。
- キャッシュ（`~/.cache/sarashina-client`、`SARASHINA_CACHE_DIR` で変更可）:
  - **索引キャッシュ**: arXiv ID（＋ページ範囲）または URL 単位で索引全体を保存。同じ条件の再構築は全処理をスキップ。
  - **埋め込みキャッシュ**: 文書単位で「チャンク本文のハッシュ → ベクトル」を保存。ページ範囲が広がっても（例: 1-3 → 1-全）既存チャンクの埋め込みを再利用し、不足分だけ埋め込みます。
  - **翻訳キャッシュ**: 文書単位で「チャンク本文のハッシュ → 日本語訳」を保存。ページ範囲をまたいで訳を再利用します。

主な API:

| メソッド | パス | 説明 |
| --- | --- | --- |
| POST | `/api/index` | URL から抽出＋埋め込み（RAG構築）を開始し `job_id` を返す |
| GET | `/api/index/{job_id}` | 進捗（`status` / `phase` / `indexed` / `done` / `total` / `embed_done` / `embed_total` など）を返す |
| GET | `/api/paper/{job_id}` | 抽出した原文チャンクと既訳を JSON で返す |
| POST | `/api/translate` | 指定チャンクを ja に翻訳（既訳はキャッシュから返す） |
| POST | `/api/chat` | RAG 文脈つきで SSE ストリーミング回答（要インデックス構築） |
| POST | `/api/suggest` | 直近の会話から次の質問候補を 3 件提案 |

リクエスト / レスポンスの形:

- `POST /api/index` — リクエスト `{"url": "https://arxiv.org/abs/1706.03762", "pages": "1-3"}`（`pages` は省略可）。レスポンス `{"job_id": "..."}`。`GET /api/index/{job_id}` をポーリングし、`status` は `running` → `done`（失敗時 `error`）、`phase` は `extracting` → `embedding` → `done`。
- `GET /api/paper/{job_id}` — レスポンス `{"title", "chunks": [{"i", "page", "text"}], "translations": {"<i>": "訳文"}}`（**JSON**。HTML ではありません）。
- `POST /api/translate` — リクエスト `{"job_id", "index"}`。レスポンス `{"index", "text"}`。
- `POST /api/chat` — リクエスト `{"job_id", "message"}`。レスポンスは SSE: `data: {"delta": "..."}` を繰り返し、末尾に `data: {"note": "参考ページ: ..."}` と `data: [DONE]`。エラー時は `data: {"error": "..."}`。
- `POST /api/suggest` — リクエスト `{"job_id"}`。レスポンス `{"suggestions": ["...", "...", "..."]}`。

## テスト

```sh
uv run pytest
```

`tests/` に pytest スイートがあります。**完全にオフライン**で動作し、HTTP は monkeypatch されるため API キーもネットワークも不要です（キャッシュは一時ディレクトリに退避）。対象は `cache` / `pdftranslate` / `rag` / `cli` / `translate` の単体ロジックと、`webapp` の各エンドポイント（`TestClient` 使用）。

## レート制限と注意

Sarashina API はモデルごとに **1 分あたりのリクエスト数（RPM）** が制限されます。

| モデル | RPM |
| --- | --- |
| sarashina3-mini | 6 |
| sarashina3-guard | 6 |
| sarashina3-translate | 6 |
| sarashina3-nano | 60 |
| sarashina3-embedding | 60 |
| sarashina3-rerank | 60 |

- `pdftranslate` は 6 RPM を守るため各リクエスト間に待機を入れ、`429` は指数バックオフで再試行します。
- `sarashina-web` は埋め込み（60 RPM）で `429` 時に指数バックオフ、質問ごとに `translate`（6 RPM）で 1 回クエリを英訳します。
- 論文全体の埋め込みはチャンク数だけ時間がかかります。ページ範囲で絞るのが現実的です。
- スキャン画像のみの PDF はテキストを抽出できないため、事前に OCR が必要です。

## 構成

```
src/sarashina_client/
  cli.py            # chat
  translate.py      # translate
  pdftranslate.py   # PDF/arXiv の抽出・翻訳・Markdown/HTML/PDF 生成
  rag.py            # embedding / rerank / 検索（コサイン top-k）
  cache.py          # 索引のディスクキャッシュ（arXiv ID / URL 単位）
  webapp.py         # FastAPI アプリ（sarashina-web）
tests/              # pytest スイート（オフライン / HTTP はモック）
```
