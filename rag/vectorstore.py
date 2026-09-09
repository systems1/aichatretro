"""Vector store — FAISS index over normalized embeddings.

Vectors are already L2-normalized by sentence-transformers
(normalize_embeddings=True), so Inner Product == cosine similarity.
Build is exact (IndexFlatIP) — plenty fast at this data scale (<50K chunks).
"""
import os

import numpy as np

from . import store as _store  # INDEX_DIR lives in store.py now


def build(vectors):
    """Build a FAISS index from a (N, D) float32 numpy array.

    Returns (index, id_map) where id_map is an identity range. We keep the
    metadata in parallel Python lists (rag._chunks), so FAISS positions map
    directly to chunk indices.
    """
    import faiss

    n, dim = vectors.shape
    index = faiss.IndexFlatIP(dim)
    if n:
        index.add(vectors.astype("float32"))
    id_map = np.arange(n)
    return index, id_map


def search(index, query_vector, k):
    """Return (scores, positions) for the top-k nearest neighbors.

    query_vector: (1, D) float32 array. scores are cosine similarities
    (higher = closer) because vectors are L2-normalized.
    """
    k = min(k, index.ntotal)
    if k <= 0:
        return [], []
    scores, positions = index.search(query_vector.astype("float32"), k)
    return scores[0].tolist(), positions[0].tolist()


def save(index, path=None):
    """Persist a FAISS index to disk (personalization fallback)."""
    import faiss
    path = path or os.path.join(_store.INDEX_DIR, "index.faiss")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    faiss.write_index(index, path)
    return path


def load(path=None):
    """Load a FAISS index from disk (personalization fallback)."""
    import faiss
    path = path or os.path.join(_store.INDEX_DIR, "index.faiss")
    if not os.path.exists(path):
        return None
    return faiss.read_index(path)
