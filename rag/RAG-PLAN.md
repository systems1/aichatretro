# RAG Pipeline Plan — AI Chat Retro

## Context

AI Chat Retro currently loads ~1,193 conversations (1,130 ChatGPT + 24 DeepSeek + 52 Gemini) from `exports/` into memory at startup. The existing search is naive substring matching over a lowercased text blob (`_blob` in `parsers/__init__.py`). RAG is the #1 unimplemented roadmap item in both `PLAN.md` and `CLAUDE.md`.

This plan designs a RAG (Retrieval-Augmented Generation) pipeline that lets users **ask natural-language questions across their entire chat history** and get sourced, contextual answers — fully offline, matching the project's privacy-first design.

---

## Part 1: What Can You Do With Your Entire Chat History via RAG?

### Scenario 1 — "Find that thing I asked about"
> *User: "What was the Python regex I used to parse CSV headers back in March?"*

The system retrieves the 3-5 most relevant messages across all 1,193 conversations, surfaces the exact code snippet with conversation context, and generates a summary answer.

**Value:** Current keyword search requires you to guess the exact term. RAG understands intent — "Python regex for CSV" finds it even if the message said `re.findall(r'...', line)`.

---

### Scenario 2 — Cross-service knowledge synthesis
> *User: "I asked both ChatGPT and DeepSeek about Docker networking. What were the differences in their advice?"*

RAG retrieves Docker-related messages from both services, compares the answers side-by-side, and synthesizes a response noting where the models agreed or disagreed.

**Value:** The app already normalizes chats from 3 services into one schema — RAG makes that normalization *useful* by letting you query across services as one knowledge base.

---

### Scenario 3 — "Summarize what I've been working on"
> *User: "Summarize all my coding projects from the last 3 months."*

RAG clusters retrieved messages by topic/project, generates a structured summary with links back to the original conversations, showing the arc of each project over time.

**Value:** Turns 1,193 conversations into a personal knowledge dashboard.

---

### Scenario 4 — Decision audit trail
> *User: "Why did I choose Flask over FastAPI for the chat viewer?"*

RAG retrieves the conversation(s) where this decision was discussed, surfaces the reasoning, tradeoffs considered, and the final choice — with timestamps.

**Value:** Reconstruct your decision history across months of AI-assisted thinking.

---

### Scenario 5 — Personal learning review
> *User: "What Kubernetes concepts have I been learning? Quiz me on them."*

RAG retrieves all K8s-related messages, extracts the concepts discussed, and generates a quiz with answers drawn from the user's own chat context.

**Value:** Turns your chat history into a spaced-repetition study tool.

---

### Scenario 6 — Code archaeology
> *User: "Show me all the Flask route definitions I've ever written with Claude."*

RAG retrieves messages containing Flask route code, filters to assistant responses, and presents them as a browsable collection with conversation context.

**Value:** Your AI chats become a searchable code snippet library.

---

### Scenario 7 — "What did I forget?"
> *User: "Remind me about the CSS hidden attribute gotcha I hit last week."*

RAG finds the conversation about the `[hidden]` specificity fix, surfaces the problem description and solution, and links to the full conversation.

**Value:** Surfaces problems you solved but may have forgotten about.

---

### Scenario 8 — Trend analysis
> *User: "What topics do I discuss most with AI? Show me a breakdown."*

RAG analyzes all message embeddings, clusters them by topic, and generates a frequency breakdown with time-series data.

**Value:** Understand your own AI usage patterns and knowledge gaps.

---

### Implementation status of the scenarios (2026-09-08)

Assessment of each scenario against the code that is actually implemented in `rag/`
(retrieval = `retriever.retrieve()` vector + FTS5 hybrid search + rerank; generation =
`generator.generate()` local GGUF LLM; API = `/api/ask`, `/api/ask/sources`,
`/api/rag/build`, `/api/rag/status`; UI = `static/js/ask.js`).

| # | Scenario | Status | What's needed |
|---|----------|--------|---------------|
| 1 | Find that thing I asked about | ✅ Works | LLM generation optional (needs a GGUF model in `models/`) |
| 2 | Cross-service knowledge synthesis | ⚠️ Partial | Service-grouping + compare/contrast synthesis logic |
| 3 | Summarize what I've been working on | ⚠️ Partial | Topic clustering + structured project summary |
| 4 | Decision audit trail | ✅ Works | — |
| 5 | Personal learning review / quiz | ❌ Missing | Topic extraction + quiz generator |
| 6 | Code archaeology | ⚠️ Partial | Code-block extraction + assistant-only role filter |
| 7 | "What did I forget?" | ✅ Works | — |
| 8 | Trend analysis | ❌ Missing | Full analytics pipeline (topic clustering + time-series) |

**Notes on the ✅-working core (scenarios 1, 4, 7):** a single focused question returns
ranked chunks (vector similarity + FTS5 BM25 keyword boost + recency/type rerank) with
`conversation_title`, `role`, and `timestamp` metadata. When no local LLM is present,
`/api/ask` still returns the retrieved chunks (no summary answer).

**Notes on the ⚠️-partial scenarios:** retrieval works across all services / over broad
topics, but the *post-retrieval* logic (synthesis, clustering, structuring, filtering) is not
implemented — it would live in the generator or a dedicated endpoint, not the retriever.

**Notes on the ❌-missing scenarios:** these are entirely new features (no existing code
path) and would be built on top of the working retrieval core.

---

## Part 2: RAG Pipeline Architecture

### Design Principles (matching project philosophy)
- **Fully offline** — no external API calls, no cloud embedding services
- **Parse fresh, index cached** — conversations are still re-read from `exports/` on every boot (the app's "no cache" rule for conversations is unchanged), but the *RAG index* is persisted to SQLite+FTS5 (`rag/index/rag.sqlite3`) so restarts skip re-embedding ~7.6K chunks (~15-30s saved). This is a deliberate, user-requested exception: it caches ONLY the RAG index, never the parsed conversations.
- **Lightweight** — no heavy ML frameworks; pure Python with minimal dependencies
- **Incremental** — start simple, add sophistication over phases

---

### Phase 1: Embedding Index (Core RAG)

**Goal:** Build a searchable vector index over all chat messages at startup.

```
exports/ → parsers/ → CONVERSATIONS dict → [NEW] chunker → [NEW] embedder → [NEW] vector index
                                                                              ↓
                                                              [NEW] /api/ask endpoint
                                                                              ↓
                                                              [NEW] chat UI "Ask" panel
```

#### 1a. Chunking Strategy

Split each conversation into RAG-ready chunks:

| Chunk type | Source field | Chunk size | Overlap | Notes |
|---|---|---|---|---|
| Message chunk | `message.content` | 1 message | 0 | Atomic unit; each message is a chunk |
| Conversation context | `conversation.title` + first 3 messages | ≤500 tokens | — | Quick context for retrieval |
| Reasoning chunk | `message.thoughts` | 1 message | 0 | Separate index; lower weight in retrieval |

**Chunk metadata** (stored alongside each embedding):
```python
{
    "chunk_id": "chatgpt/abc123/msg_7",
    "service": "chatgpt",
    "conversation_id": "abc123",
    "conversation_title": "How to parse CSV in Python",
    "role": "assistant",
    "model": "gpt-4",
    "timestamp": 1725100800.0,  # unix UTC
    "chunk_type": "message",    # or "reasoning" or "context"
    "content_preview": "You can use csv.reader()...",  # first 100 chars
}
```

**Implementation file:** `rag/chunker.py`

#### 1b. Embedding Model

**Recommendation: `sentence-transformers` with `all-MiniLM-L6-v2`**
- 80MB download, runs on CPU, ~50ms per sentence
- 384-dimensional embeddings — good quality for retrieval
- Fully offline, MIT license
- Alternative: `nomic-embed-text` via `llama-cpp-python` if you want to stay in the Llama ecosystem

**Why not heavier models:** 1,193 conversations × ~20 messages avg = ~24K chunks. MiniLM handles this in <30 seconds on boot, uses ~50MB RAM for the index. Heavier models (BGE, E5) add complexity with marginal quality gain at this scale.

**Implementation file:** `rag/embedder.py`

#### 1c. Vector Store

**Recommendation: `faiss-cpu` (Facebook AI Similarity Search)**
- Pure CPU, no GPU required
- Indexes 24K vectors in <1 second
- Sub-millisecond nearest-neighbor search
- Zero config, no external services

**Alternative:** `numpy` brute-force cosine similarity — simpler but slower at scale. Fine for <50K chunks.

**Index lifecycle (implemented — SQLite+FTS5 persistence):**
1. At startup, `ensure_built(CONVERSATIONS, EXPORTS_ROOT)` fingerprints `exports/` (path+size+mtime hash)
2. If `rag/index/rag.sqlite3` exists AND its stored fingerprint/dim/model match AND FTS5 is usable → **load** chunks + embeddings straight from SQLite (fast restart, no re-embedding; FAISS rebuilt in-memory)
3. Otherwise → **build** fresh (chunk + embed + index) and **persist** to SQLite (`store.save_index`), storing chunks, float32 embedding BLOBs, and an FTS5 text table for hybrid keyword+vector retrieval
4. Exports changed or embedding model swapped → fingerprint/dim mismatch → fresh build + repersist (self-healing fallback to `build_index`)

**Implementation file:** `rag/vectorstore.py`

#### 1d. Retrieval Pipeline

```
User question → embed question → top-K retrieval (K=10) → rerank by relevance → context assembly → LLM answer
```

**Retrieval steps:**
1. Embed the user's question (same model as indexing)
2. FAISS `index.search(question_embedding, k=10)` → top 10 chunks
3. **Rerank** by combining:
   - Vector similarity score (0.7 weight)
   - Recency bonus: newer messages score higher (0.15 weight)
   - Conversation relevance: chunks from the same conversation cluster together (0.15 weight)
4. Assemble context: top 5 chunks + their conversation titles + timestamps
5. Generate answer via local LLM (Phase 2) or return raw chunks (Phase 1)

**Implementation file:** `rag/retriever.py`

---

### Phase 2: Local LLM Answer Generation

**Goal:** Generate natural-language answers with citations back to source conversations.

#### LLM Options (all fully offline)

| Model | Size | RAM | Speed | Quality | Recommendation |
|---|---|---|---|---|---|
| `Phi-3.5-mini` | 3.8B Q4 | 3GB | Fast | Good | **Best for most users** |
| `Qwen2.5-3B` | 3B Q4 | 2.5GB | Fast | Good | Alternative to Phi |
| `Llama-3.2-3B` | 3B Q4 | 2.5GB | Fast | Good | Meta ecosystem |
| `Gemma-2-2B` | 2B Q4 | 2GB | Fastest | Acceptable | Low-RAM machines |
| `Phi-3.5-medium` | 14B Q4 | 9GB | Slow | Excellent | Power users |

**Implementation:** `llama-cpp-python` wrapper loading GGUF quantized models from `models/` directory.

**Prompt template:**
```
You are answering questions about a user's personal chat history with AI assistants.
Use ONLY the provided context chunks to answer. If the context doesn't contain
enough information, say so. Always cite which conversation(s) you're referencing.

Context chunks:
{retrieved_chunks_with_metadata}

User question: {question}

Answer (cite conversation titles and dates):
```

**Implementation file:** `rag/generator.py`

---

### Phase 3: API & Frontend Integration

#### New API Endpoints

| Route | Purpose |
|---|---|
| `GET /api/ask?q=&service=&model=` | RAG query — returns answer + source citations |
| `GET /api/ask/sources?q=` | Retrieve-only (no LLM) — returns raw chunks for UI preview |
| `POST /api/rag/build` | Rebuild the index (for when exports change) |
| `GET /api/rag/status` | Index status: chunk count, model loaded, memory usage |

**Implementation file:** `app.py` (add routes)

#### Frontend: "Ask" Panel

New UI element in the chat interface:
- **Ask input** — appears in the top bar next to search
- **Answer panel** — renders above the chat pane, with:
  - Generated answer (markdown-rendered)
  - "Sources" chips linking to each referenced conversation
  - "View conversation" button jumps to the source chat
  - Confidence indicator (high/medium/low based on retrieval scores)
- **Toggle** — "Search" vs "Ask" mode switch (existing search stays, Ask is additive)

**Implementation files:** `static/js/ask.js`, `static/css/ask.css`

---

## Part 3: File Structure for RAG

```
aichatretro/
├── rag/                          # NEW directory
│   ├── __init__.py              # Package init, exports build_index(), query()
│   ├── chunker.py               # Message → chunk splitting + metadata
│   ├── embedder.py              # sentence-transformers wrapper
│   ├── vectorstore.py           # FAISS index build/search
│   ├── retriever.py             # Retrieval + reranking logic
│   ├── generator.py             # llama-cpp-python LLM wrapper
│   └── index/                   # Runtime index files (gitignored)
├── models/                      # GGUF model files (gitignored, user downloads)
├── app.py                       # Add /api/ask, /api/rag/* routes
├── parsers/__init__.py          # Existing; _search_blob stays for fallback
├── static/js/ask.js             # NEW: Ask panel frontend logic
├── static/css/ask.css           # NEW: Ask panel styles
├── static/index.html            # Add Ask input + panel HTML
├── requirements-rag.txt         # NEW: rag-specific deps (sentence-transformers, faiss-cpu, llama-cpp-python)
├── Dockerfile.rag               # NEW: optional heavier Docker image with RAG deps
└── docker-compose.rag.yml       # NEW: optional compose for RAG variant
```

---

## Part 4: Implementation Sequence

### Step 1 — Chunker (`rag/chunker.py`)
- Input: `CONVERSATIONS` dict from `parsers/__init__.py`
- Output: list of chunk dicts with metadata
- Handle: empty content, reasoning-only messages, conversations with <2 messages
- Test: verify chunk count matches expected (~24K chunks for 1,193 conversations)

### Step 2 — Embedder (`rag/embedder.py`)
- Lazy-load `sentence-transformers` model on first use
- Batch-encode all chunks (batch size 64 for CPU efficiency)
- Store embeddings as numpy array
- Test: verify embedding dimensions are 384, shape matches chunk count

### Step 3 — Vector Store (`rag/vectorstore.py`)
- Build FAISS index from embeddings (Inner Product for cosine similarity after L2-normalization)
- Implement `search(query_embedding, k)` returning chunk IDs + scores
- Test: verify search returns relevant results for known queries

### Step 4 — Retriever (`rag/retriever.py`)
- Combine vector search + reranking logic
- Implement `retrieve(question, service_filter=None, k=5)` returning ranked chunks with metadata
- Test: verify reranking improves over raw vector search

### Step 5 — Generator (`rag/generator.py`)
- Load GGUF model via `llama-cpp-python`
- Implement `generate(question, context_chunks)` returning answer + citations
- Fallback: if no LLM loaded, return raw chunks without generation
- Test: verify answer references provided context chunks

### Step 6 — API Routes (in `app.py`)
- `GET /api/ask?q=...` — full RAG pipeline
- `GET /api/ask/sources?q=...` — retrieval only (no LLM)
- `POST /api/rag/build` — rebuild index
- `GET /api/rag/status` — index health check
- Test: curl each endpoint, verify JSON responses

### Step 7 — Frontend (ask.js, ask.css, index.html)
- Add "Ask" input next to search
- Add answer panel with sources
- Wire to `/api/ask` endpoint
- Add loading states, error handling
- Test: manual browser testing with sample questions

### Step 8 — Docker RAG variant
- `Dockerfile.rag` — extends base with RAG dependencies
- `docker-compose.rag.yml` — mounts `models/` for GGUF files
- `requirements-rag.txt` — `sentence-transformers`, `faiss-cpu`, `llama-cpp-python`
- Test: `docker compose -f docker-compose.rag.yml up` works

---

## Part 5: Dependencies

### Python (requirements-rag.txt)
```
sentence-transformers>=3.0.0
faiss-cpu>=1.7.4
llama-cpp-python>=0.3.0
numpy>=1.24.0
```

### Model download (manual, user-initiated)
```bash
# Download a GGUF model (one-time, ~2-4GB)
mkdir models/
# Option A: Phi-3.5-mini (recommended)
wget -P models/ https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf
# Option B: Qwen2.5-3B
wget -P models/ https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
```

### Optional (for better embeddings)
```
# If MiniLM isn't good enough, upgrade to BGE:
# pip install FlagEmbedding
# Then swap embedder.py to use BAAI/bge-small-en-v1.5 (130M, 384d)
```

---

## Part 6: Performance Estimates

| Metric | Estimate | Notes |
|---|---|---|
| Boot time (index build) | 15-30 seconds | sentence-transformers loading + encoding 24K chunks |
| Index memory | ~50MB RAM | 24K × 384d × 4 bytes + overhead |
| Query latency (retrieval only) | <10ms | FAISS exact search on 24K vectors |
| Query latency (with LLM) | 2-10 seconds | Depends on model size and CPU |
| Disk (embeddings) | ~35MB | 24K × 384d × 4 bytes |
| Disk (LLM model) | 2-4GB | User downloads separately |

---

## Part 7: Privacy & Security

- **All processing is local** — no data leaves the machine, matching the core project promise
- **Embeddings contain no raw text** — vector representations are not reversible to original text
- **LLM runs locally** — no API calls to OpenAI/Anthropic/Google
- **Index persists locally** — chunks + embeddings + FTS5 text live in `rag/index/rag.sqlite3` (gitignored), so restarts skip re-embedding. The SQLite cache holds raw message text, same as the in-memory conversation cache the app already keeps; it never leaves the disk. Storage is full-replace on each fresh build, so staleness self-heals.
- **Conversations themselves are never cached to disk** — the app still parses `exports/` fresh on every boot; only the RAG index is persisted (the single, user-requested exception).
- **Docker RAG variant** — `models/` directory mounted read-only, same as `exports/`; `rag/index/` (if present) is runtime-only.

---

## Part 8: Future Enhancements (post-MVP)

1. **Topic clustering** — Use UMAP + HDBSCAN on embeddings to auto-discover topic clusters (Scenario 8)
2. **Conversation summaries** — Auto-generate per-conversation summaries stored as metadata
3. **Cross-conversation linking** — Detect related conversations via embedding similarity
4. **Chat history analytics** — Usage patterns, topic trends, model comparison dashboards
5. **Export answers** — Save RAG Q&A sessions as markdown files
6. **Multi-turn RAG** — Conversational follow-up questions with context window
7. **Hybrid search** — Combine vector search with the existing keyword search for better recall
