"""RAG over chat history — optional, fully offline, imported by app.py.

A library (like parsers/), not a standalone app. app.py passes the already
parsed CONVERSATIONS dict into build_index(), so there is zero re-parsing of
exports/ and no duplicated code. The server always runs from the root folder
(python3 app.py); this package is imported via `from rag import ...`.

Pipeline: chunker -> embedder -> vectorstore -> retriever -> generator

Heavy deps (sentence-transformers, faiss, llama-cpp-python) are imported
lazily so the core app still runs without them; /api/ask returns a clear
status if they are missing.

## Persistence (SQLite + FTS5)

The index is cached in `rag/index/rag.sqlite3` (a deliberate, user-requested
exception to the app's "no cache" rule — it caches ONLY the RAG index, never
the parsed conversations). On startup:

  1. fingerprint `exports/` (paths + sizes + mtimes)
  2. if a persisted index exists AND its fingerprint matches AND its
     embedding dim/model match  ->  LOAD chunks + embeddings from SQLite
        (fast: no re-embedding; FAISS is rebuilt in-memory from stored vectors)
  3. otherwise -> BUILD fresh (chunk + embed + index) and PERSIST to SQLite

Exports changed or model swapped -> fingerprint/dim mismatch -> fresh build.
"""
import os
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE)
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")

# Lazy module-level singletons, guarded by a lock so the first query builds
# the index on demand without blocking other requests.
_lock = threading.Lock()
_chunks = None      # list[dict] metadata, parallel to embedding rows
_vectors = None     # numpy (N, D) float32
_index = None       # faiss.Index
_chunk_ids = None   # list[str] parallel to rows
_embedder = None    # lazy SentenceTransformer (None when loaded from sqlite)
_db_conn = None     # open sqlite3 connection for FTS5 hybrid queries
_load_source = "none"   # "sqlite" | "built" | "none"


def _default_exports_root():
    return os.path.join(PROJECT_ROOT, "exports")


def available():
    """True if the optional embedding/faiss deps are importable."""
    try:
        import numpy  # noqa: F401
        import faiss  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
        return True
    except Exception:
        return False


def llm_available():
    """True if a local GGUF model was found and llama-cpp-python imports."""
    try:
        from rag import generator
        return generator.find_model() is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Index build / load
# --------------------------------------------------------------------------- #

def _build_fresh(conversations, exports_root):
    """Chunk + embed + index + persist. Caller MUST hold _lock."""
    from rag import chunker, embedder, store, vectorstore

    global _chunks, _vectors, _index, _chunk_ids, _embedder, _db_conn, _load_source

    chunks = chunker.chunk_conversations(conversations)
    texts = [c["text"] for c in chunks]

    emb = embedder.get_embedder()
    vectors = embedder.encode_many(emb, texts)

    index, _ = vectorstore.build(vectors)

    conn = store.open_db()
    fp = store.exports_fingerprint(exports_root)
    store.save_index(conn, chunks, vectors, fp, embedder.model_name())
    _db_conn = conn

    _chunks, _vectors, _index = chunks, vectors, index
    _chunk_ids = [c["chunk_id"] for c in chunks]
    _embedder = emb
    _load_source = "built"
    return len(chunks)


def build_index(conversations, exports_root=None):
    """Force a fresh build + persist. Idempotent; blocks until done."""
    with _lock:
        return _build_fresh(conversations, exports_root or _default_exports_root())


def ensure_built(conversations, exports_root=None):
    """Load from the SQLite datastore if fresh; otherwise build + persist.

    Thread-safe; builds/loads at most once per process.
    """
    from rag import store, vectorstore

    global _chunks, _vectors, _index, _chunk_ids, _embedder, _db_conn, _load_source

    with _lock:
        if _chunks is not None:
            return len(_chunks)

        exports_root = exports_root or _default_exports_root()
        conn = store.open_db()

        fp = store.exports_fingerprint(exports_root)

        from rag import embedder
        dim = _expected_dim()
        loaded = store.load_index(conn, fp, dim, embedder.model_name())
        if loaded is not None:
            chunks, vectors = loaded
            index, _ = vectorstore.build(vectors)
            _chunks, _vectors, _index = chunks, vectors, index
            _chunk_ids = [c["chunk_id"] for c in chunks]
            _embedder = None   # lazily loaded from the model at query time
            _db_conn = conn
            _load_source = "sqlite"
            return len(chunks)

        return _build_fresh(conversations, exports_root)


def _expected_dim():
    """Embedding dimension expected from the configured model."""
    from rag import embedder
    return embedder.get_embedder().get_sentence_embedding_dimension()


# --------------------------------------------------------------------------- #
# Status / queries
# --------------------------------------------------------------------------- #

def status():
    """Return a status dict for /api/rag/status."""
    from rag import store
    with _lock:
        n = len(_chunks) if _chunks is not None else 0
        dim = _vectors.shape[1] if _vectors is not None else 0
    return {
        "available": available(),
        "built": _chunks is not None,
        "chunk_count": n,
        "embedding_dim": dim,
        "storage": _load_source,
        "db_path": store.DB_PATH,
        "db_exists": store.db_exists(),
        "llm_available": llm_available(),
        "models_dir": MODELS_DIR,
    }


def retrieve(question, service=None, k=5):
    """Retrieve top-k chunks for a natural-language question.

    Returns list of chunk dicts (metadata + text + score), reranked.
    """
    from rag import embedder, retriever
    with _lock:
        if _index is None or _vectors is None:
            raise RuntimeError("RAG index not built")
        idx, chunks, ids, db = _index, _chunks, _chunk_ids, _db_conn
    emb = _embedder or embedder.get_embedder()
    return retriever.retrieve(emb, idx, chunks, ids, question,
                              service=service, k=k, fts_conn=db)


def query(question, service=None, k=5, generate=True):
    """Full RAG: retrieve context, optionally generate an answer.

    Returns dict with 'answer' (or None), 'sources' (chunk list), and 'status'.
    """
    from rag import generator

    sources = retrieve(question, service=service, k=k)

    answer = None
    if generate and llm_available():
        answer = generator.generate(question, sources)

    return {
        "query": question,
        "answer": answer,
        "sources": sources,
        "status": {
            "built": True,
            "generated": answer is not None,
        },
    }