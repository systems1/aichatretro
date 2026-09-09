"""Persistent datastore — SQLite + FTS5 for the RAG index.

Why persistence: without it, every app restart re-embeds every chunk via
sentence-transformers (~15-30s for 7,662 chunks). With a local SQLite
datastore the first build persists chunks + embeddings + an FTS5 text index;
later restarts load straight from SQLite (fast, no re-embedding) whenever the
exports fingerprint is unchanged, falling back to a fresh build otherwise.

This is a deliberate, user-requested exception to the app's "parse fresh, no
cache" rule: it caches ONLY the RAG index. Parsed conversations are still
re-read from exports/ on every app start.

Schema:
  meta        key/value: fingerprint, dim, model_name, chunk_count
  chunks      full chunk metadata + text + embedding BLOB (idx-ordering)
  chunks_fts  FTS5 virtual table over (chunk_id, text) — keyword/BM25 search
"""
import hashlib
import json
import os
import re
import sqlite3

import numpy as np

RAG_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(RAG_DIR, "index")
DB_PATH = os.path.join(INDEX_DIR, "rag.sqlite3")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
  idx               INTEGER PRIMARY KEY,
  chunk_id          TEXT UNIQUE NOT NULL,
  service           TEXT, conversation_key TEXT, conversation_id TEXT,
  conversation_title TEXT, role TEXT, model TEXT,
  timestamp         REAL, chunk_type TEXT, content_preview TEXT,
  text              TEXT NOT NULL,
  embedding         BLOB NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED,
  text,
  tokenize='porter'
);
"""


def exports_fingerprint(exports_root):
    """Hash of (relpath, size, mtime_ns) for every file under exports/.

    Used to detect whether exports changed since the index was built, so we
    know whether the persisted index is still valid.
    """
    h = hashlib.sha256()
    files = []
    for dirpath, _dirs, names in os.walk(exports_root):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((os.path.relpath(p, exports_root), st.st_size, st.st_mtime_ns))
    for item in sorted(files):
        h.update(json.dumps(item).encode())
    return h.hexdigest()


def db_exists(path=DB_PATH):
    return os.path.isfile(path) and os.path.getsize(path) > 0


def open_db(path=DB_PATH):
    """Open (creating if needed) the SQLite datastore.

    check_same_thread=False because Flask runs threaded=True; only reads occur
    once the index is loaded, so cross-thread access is safe.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #

def save_index(conn, chunks, vectors, fingerprint, model_name):
    """Persist chunks + embeddings + FTS5 text index (full replace)."""
    if len(chunks) != vectors.shape[0]:
        raise ValueError("chunks/vectors length mismatch")

    with conn:
        # meta
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('fingerprint', ?)", (fingerprint,))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('model_name', ?)", (model_name,))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('dim', ?)", (str(vectors.shape[1]),))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('chunk_count', ?)", (str(len(chunks)),))

        # full replace (rowids keep counting — harmless for FTS/sqlite_sequence)
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM chunks_fts")

        rows = [
            (
                i,
                c["chunk_id"],
                c.get("service"), c.get("conversation_key"), c.get("conversation_id"),
                c.get("conversation_title"), c.get("role"), c.get("model"),
                c.get("timestamp"), c.get("chunk_type"), c.get("content_preview"),
                c.get("text"), vectors[i].tobytes(),
            )
            for i, c in enumerate(chunks)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO chunks (idx, chunk_id, service, conversation_key,"
            " conversation_id, conversation_title, role, model, timestamp, chunk_type,"
            " content_preview, text, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
            [(c["chunk_id"], c.get("text") or "") for c in chunks],
        )
    conn.commit()


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #

def load_index(conn, expected_fingerprint, expected_dim, expected_model=None):
    """Return (chunks, vectors) if the persisted index is fresh, else None.

    Freshness = stored fingerprint matches exports AND embedding dim (and,
    optionally, model name) match the current configuration.
    """
    def meta(key):
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    stored_fp = meta("fingerprint")
    stored_dim = meta("dim")
    stored_model = meta("model_name")
    stored_count = meta("chunk_count")

    if stored_fp is None or stored_fp != expected_fingerprint:
        return None
    if stored_dim is None or int(stored_dim) != expected_dim:
        return None
    if expected_model is not None and stored_model != expected_model:
        return None

    count = int(stored_count or 0)
    if count <= 0:
        return None

    rows = conn.execute(
        "SELECT * FROM chunks ORDER BY idx LIMIT ?", (count,)
    ).fetchall()
    if len(rows) != count:
        return None

    dim = int(stored_dim)
    vectors = np.zeros((count, dim), dtype=np.float32)
    chunks = []
    for r in rows:
        chunk = {
            "chunk_id": r["chunk_id"],
            "service": r["service"],
            "conversation_key": r["conversation_key"],
            "conversation_id": r["conversation_id"],
            "conversation_title": r["conversation_title"],
            "role": r["role"],
            "model": r["model"],
            "timestamp": r["timestamp"],
            "chunk_type": r["chunk_type"],
            "content_preview": r["content_preview"],
            "text": r["text"],
        }
        chunks.append(chunk)
        vectors[r["idx"], :] = np.frombuffer(r["embedding"], dtype=np.float32)

    return chunks, vectors


# --------------------------------------------------------------------------- #
# FTS5 keyword search (hybrid retrieval boost)
# --------------------------------------------------------------------------- #

def fts_available(conn):
    try:
        conn.execute("INSERT INTO chunks_fts (chunk_id, text) VALUES ('__probe__', 'probe')")
        conn.execute("DELETE FROM chunks_fts WHERE chunk_id = '__probe__'")
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def keyword_search(conn, query, k=20):
    """Return [(chunk_id, score), ...] ranked by FTS5 BM25.

    Terms are quoted + OR-joined so punctuation in the query can't break the
    MATCH syntax. Returns [] if the query yields no tokens.
    """
    tokens = re.findall(r"\w{2,}", query.lower())[:20]
    if not tokens:
        return []
    match = " OR ".join('"{}"'.format(t) for t in tokens)
    try:
        rows = conn.execute(
            "SELECT chunk_id, bm25(chunks_fts, 5.0) AS score "
            "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
            (match, k),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [(r["chunk_id"], r["score"]) for r in rows]