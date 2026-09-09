"""Embedder — lazy wrapper around sentence-transformers (all-MiniLM-L6-v2).

Imported lazily so the app runs without sentence-transformers installed.
Model is loaded once and cached; the optional dependency is only required
when RAG is actually used.
"""
import os
# May be overridden with RAG_EMBED_MODEL env var.
MODEL_NAME = os.environ.get("RAG_EMBED_MODEL", "all-MiniLM-L6-v2")  # 384-dim, fully offline


def model_name():
    return MODEL_NAME


def get_embedder():
    """Return a cached SentenceTransformer, loading it on first use."""
    from sentence_transformers import SentenceTransformer
    if not hasattr(get_embedder, "_model"):
        get_embedder._model = SentenceTransformer(MODEL_NAME)
    return get_embedder._model


def encode_many(model, texts, batch_size=64):
    """Embed a list of strings into a float32 numpy array (N, dim)."""
    return model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )


def encode_one(model, text):
    """Embed a single query string; returns a (1, dim) float32 array."""
    return encode_many(model, [text])
