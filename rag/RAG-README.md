# RAG over your chat history — "Ask"

**Ask natural-language questions across all your past AI chats — 100% offline, on your machine**

The RAG (Retrieval-Augmented Generation) feature lets you **ask questions in plain language** over
every conversation in your exports — ChatGPT, DeepSeek, and Gemini together — and get back the
relevant messages with citations. No cloud, no API keys, no data leaving your machine. The heavy
models (embeddings + optional local LLM) all run locally.

## Why RAG?

The built-in search needs exact keywords. RAG understands **intent**:

- Search requires you to guess the term — *"**Python regex for CSV**"* finds the message even if it said `re.findall(r'...', line)`.
- It searches **across all three services at once** as one knowledge base.
- Results carry **context** — which conversation, which role (user/assistant), which model, and when.

## UI

![RAG Ask panel](txtdata/rag-aichatretro-ui.JPG)

The Ask input lives in the top bar next to search. Asking a question returns the most relevant
message chunks across all your exports — with 💬 open-in-conversation links — and, when a local
LLM is present, a generated summary answer citing those conversations.

## How it works

```
your question
   ↓
[embed question]  ──────────────┐
   ↓                            │  vector similarity (all-MiniLM-L6-v2, 384-dim)
[vector top-k]                  │
   ↓                            │
[FTS5 keyword boost]  ← local SQLite+FTS5 index — catches exact terms vector missed
   ↓
[rerank] 0.70 similarity · 0.15 recency · 0.15 type
   ↓
[optional local LLM]  → summarized answer citing conversation titles & dates
```

Pipeline: `chunker.py` → `embedder.py` → `vectorstore.py` (FAISS) → `retriever.py` → `generator.py` (optional).

### Persistence

The index (≈7,600 chunks for ~1,193 conversations) is persisted to `rag/index/rag.sqlite3`
(Python's built-in SQLite + FTS5 — no extra service). On startup the app fingerprints your
`exports/` folder; if exports are unchanged, chunks + embeddings load straight from SQLite and you
**skip the ~15–30s re-embedding**. Exports changed → it rebuilds and re-persists automatically.
This is the only cache the app keeps — parsed conversations are still read fresh from `exports/`
every start.

## How to Run

RAG is built into the app — the base app works without it (the Ask panel shows a clear status).
To enable retrieval (no LLM generation):

```bash
pip install -r requirements-rag.txt
```

This pulls `sentence-transformers`, `faiss-cpu`, `numpy` (and `llama-cpp-python` for the LLM).
On first use, sentence-transformers downloads the `all-MiniLM-L6-v2` embedding model (~80MB) and
builds + persists the index. Subsequent starts load from SQLite — fast.

To enable **generated answers** (optional), drop a GGUF model in `models/`:

```bash
mkdir -p models
wget -P models/ https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf   # ~2.3GB
```

Then run the app as usual:

```bash
python3 app.py     # open http://localhost:5000
```

### Docker (RAG variant)

```bash
docker compose -f docker-compose.rag.yml up -d --build
```

Mounts `./exports` and `./models` read-only. Port 5000 as with the base image.

## How to use it

- Click the **Ask** input in the top bar (next to search).
- Type a question — e.g. *"what did I ask about Docker networking?"*
- You get a list of **source chunks** (with 💬 open-in-conversation links). With a local LLM present
  you also get a **generated summary answer** citing the source conversations.

| Route | Purpose |
|---|---|
| `GET /api/ask?q=&limit=` | Retrieval + optional LLM-generated answer |
| `GET /api/ask/sources?q=&limit=` | Retrieval only — raw source chunks, no LLM |
| `POST /api/rag/build` | Force a fresh index build + re-persist (auto-runs when exports change) |
| `GET /api/rag/status` | Status: `storage` (`sqlite`/`built`/`none`), chunk count, model, `db_exists` |

## Feature status

| Status | Feature |
|---|---|
| ✅ Working | Semantic "find that thing" (Scenario 1) |
| ✅ Working | Decision audit trail — "why did I choose Flask over FastAPI?" with timestamps (Scenario 4) |
| ✅ Working | "What did I forget?" — surface a solved problem by describing it (Scenario 7) |
| ⚠️ Partial | Cross-service synthesis — retrieval covers all services; compare/contrast generation not built |
| ⚠️ Partial | Project/topic summaries — retrieves across a topic; clustering + structured summary not built |
| ⚠️ Partial | Code archaeology — finds messages; no code-block extraction / assistant-only filter yet |
| ❌ Not built | Learning quiz, trend/topic analytics |

Full detail (8 scenarios, architecture, roadmap) in [`RAG-PLAN.md`](RAG-PLAN.md).

## FAQ

**Is my data uploaded anywhere?** No. Everything runs locally. The embedding model and LLM run on
your machine; the only downloads are the public model files themselves, once.

**Do I need the LLM for Ask to work?** No. Without a GGUF model in `models/`, `/api/ask` still
returns the retrieved source chunks — only the derived summary answer is skipped.

**When does rebuilding happen?** On the first Ask after you add/change `exports/` files. The app
detects it via a fingerprint (path + size + mtime) and rebuilds + re-persists automatically.

**What stays on disk?** Only the RAG index (`rag/index/` — gitignored) plus your own `exports/`.
No conversation cache is written; parsed conversations live in memory per start.