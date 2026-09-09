"""Retriever — vector search + lightweight reranking.

Given a natural-language question, embed it, run FAISS nearest-neighbor
search, then rerank the candidates by combining:
  - cosine similarity (0.7)
  - recency bonus for newer messages (0.15)
  - reasoning chunks slightly down-weighted (0.15)
"""
import math

from . import vectorstore

# Weighting constants
W_SIM = 0.70
W_TIME = 0.15
W_TYPE = 0.15

# Reasoning chunks are a little less likely to be the primary answer.
_TYPE_WEIGHT = {"message": 1.0, "reasoning": 0.9}


def _norm_time(timestamp):
    """Normalize a unix timestamp to [0,1] recency within the dataset.

    Falls back to a neutral 0.5 when the timestamp is unknown.
    """
    if not timestamp:
        return 0.5
    # Values are only used relative to one another, so a fixed window is fine.
    ref = 1_750_000_000.0  # ~mid-2025 anchor
    x = math.tanh((timestamp - ref) / (365 * 86400))
    return (x + 1) / 2  # map to [0,1], newer = closer to 1


def retrieve(embedder, index, chunks, chunk_ids, question, service=None, k=5, fts_conn=None):
    """Return the top-k reranked chunk dicts for a question.

    embedder: cached SentenceTransformer; index: faiss index; chunks: list of
    chunk dicts parallel to the index rows; chunk_ids: list[str] same length.

    When fts_conn (an open SQLite+fts5 datastore) is provided, keyword/BM25
    hits missing from the vector results are added as lower-scored candidates,
    so exact-term matches still surface even when semantic similarity is low.
    """
    qv = embedder.encode([question], normalize_embeddings=True, convert_to_numpy=True)
    scores, positions = vectorstore.search(index, qv, k=max(k * 4, 20))

    by_cid = {}
    for pos in positions:
        chunk = chunks[pos]
        if service and chunk["service"] != service:
            continue
        by_cid[chunk["chunk_id"]] = chunk
    sim = {}
    for score, cid in zip(scores, [chunks[p]["chunk_id"] for p in positions]):
        if cid not in by_cid:
            continue
        sim.setdefault(cid, 0.0)
        sim[cid] = max(sim[cid], score)

    # FTS5 boost: keyword/BM25 matches that the vector search missed.
    if fts_conn is not None:
        by_cid_lookup = dict(zip(chunk_ids, chunks)) if by_cid else {}
        hits = _fts_hits(fts_conn, question, chunk_ids, chunks, k * 4)
        for cid, _score in hits:
            already = cid in sim
            if not already:
                chunk = by_cid_lookup.get(cid)
                if chunk is None or (service and chunk["service"] != service):
                    continue
                by_cid[cid] = chunk
            # New FTS-only candidates get a modest floor score; existing
            # vector hits keep their (likely higher) vector score.
            if not already:
                sim[cid] = 0.55

    scored = []
    for cid, chunk in by_cid.items():
        time_w = _norm_time(chunk.get("timestamp"))
        type_w = _TYPE_WEIGHT.get(chunk.get("chunk_type"), 1.0)
        sim_score = sim.get(cid, 0.0)
        final = (W_SIM * sim_score) + (W_TIME * time_w) + (W_TYPE * type_w)
        scored.append((final, chunk))

    # Dedup: keep only the best chunk per conversation so a single conversation
    # doesn't dominate the answer.
    best_per_conv = {}
    for final, chunk in sorted(scored, key=lambda x: -x[0]):
        key = chunk["conversation_key"]
        if key not in best_per_conv:
            best_per_conv[key] = (final, chunk)

    ranked = sorted(best_per_conv.values(), key=lambda x: -x[0])[:k]

    out = []
    for final, chunk in ranked:
        c = dict(chunk)
        c["score"] = round(final, 4)
        out.append(c)
    return out


def _fts_hits(conn, question, chunk_ids, chunks, k):
    """Return [(chunk_id, score)] from the FTS5 index, limited to known ids."""
    from . import store

    raw = store.keyword_search(conn, question, k=k)
    known = set(chunk_ids)
    return [(cid, score) for cid, score in raw if cid in known]
