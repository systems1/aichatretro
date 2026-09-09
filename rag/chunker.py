"""Chunking — split normalized conversations into retrievable text chunks.

Each message becomes one chunk (atomic unit), so retrieval can point the user
at the exact message that answers their question. Reasoning (chain-of-thought)
is kept as a separate chunk so it can be retrieved but weighted differently.

Input: the CONVERSATIONS dict {service/id: normalized conversation} from
parsers.load_all(). Output: a flat list of chunk dicts, one per message.
"""
import re

# Strip markdown so embeddings see clean prose, not markup symbols.
_MD_RE = re.compile(r"[`*_~#>|\[\]()]")
_WS_RE = re.compile(r"\s+")
_MAX_TEXT = 2000  # cap a single chunk's text; long messages are trimmed


def _clean(text):
    """Normalize markdown text to a plain, compact string for embedding."""
    if not text:
        return ""
    t = _MD_RE.sub(" ", text)
    t = _WS_RE.sub(" ", t).strip()
    return t[: _MAX_TEXT]


def chunk_conversations(conversations):
    """Return a flat list of chunk dicts across all conversations."""
    chunks = []

    for key, conv in conversations.items():
        service = conv.get("service", "")
        cid = conv.get("id", "")
        title = conv.get("title") or "(untitled)"
        model = conv.get("model")

        messages = conv.get("messages") or []
        for i, m in enumerate(messages):
            content = _clean(m.get("content"))
            if not content:
                # Skip image-only / attachment-only / empty messages.
                continue

            chunks.append({
                "chunk_id": f"{key}/msg_{i}",
                "service": service,
                "conversation_id": cid,
                "conversation_title": title,
                "conversation_key": key,
                "role": m.get("role"),
                "model": m.get("model", model),
                "timestamp": m.get("time"),
                "chunk_type": "message",
                "text": content,
                "content_preview": content[:100],
            })

            # Reasoning is a separate chunk, lower-weighted at retrieval time.
            thoughts = _clean(m.get("thoughts"))
            if thoughts:
                chunks.append({
                    "chunk_id": f"{key}/msg_{i}/thoughts",
                    "service": service,
                    "conversation_id": cid,
                    "conversation_title": title,
                    "conversation_key": key,
                    "role": m.get("role"),
                    "model": m.get("model", model),
                    "timestamp": m.get("time"),
                    "chunk_type": "reasoning",
                    "text": thoughts,
                    "content_preview": thoughts[:100],
                })

    return chunks
