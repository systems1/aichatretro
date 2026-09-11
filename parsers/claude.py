"""Claude export parser.

Format (verified from a Claude data export, 2026-09-11):
  - Shards: exports/claude/conversations/conversations-<NNN>/conversations.json,
    each a JSON array of conversation objects. Discovery mirrors chatgpt:
    glob the shard subfolders (no manifest needed for parsing). Zips dropped
    alongside an unextracted shard folder are auto-extracted on load.
  - conversation: {uuid, name, summary, created_at, updated_at, account,
                   chat_messages: [...]}
  - message: {uuid, text, content[], sender 'human'|'assistant', created_at,
              updated_at, attachments, files, parent_message_uuid}
    - m["text"] is the rendered markdown; content[] is a list of typed blocks:
      text | thinking | tool_use | tool_result. m["text"] can embed
      "This block is not supported..." placeholder fences that stand in for
      tool blocks — stripped here so display/RAG see clean prose.
    - thinking blocks: {thinking (may be redacted to ''), summaries:[{summary}]}
    - text blocks: {text, citations:[{details:{url,title,...}}]}
    - tool_result (web_search/web_fetch): content[] of knowledge {title,url,text}
    - files: [{file_uuid, file_name}] — uploaded references; this export ships
      no binary assets, so attachments carry name/mime but src=None.
  - No model metadata is included in the data export.
"""
import glob
import hashlib
import json
import os
import re
import zipfile

from .base import mime_for, truncate, utc_ts

SERVICE = "claude"

# Tool-result placeholders Claude renders in m["text"] in place of tool blocks.
_UNSUPPORTED_RE = re.compile(
    r"(?:```\s*This block is not supported on your current device yet\.\s*```\s*)+"
)
_WORD_WS_RE = re.compile(r"\n{3,}")


def _shard_paths(claude_dir):
    """Sorted list of conversation shard files under conversations/."""
    return sorted(
        glob.glob(os.path.join(claude_dir, "conversations", "conversations-*", "conversations.json"))
    )


def ensure_extracted(claude_dir):
    """Unzip any conversations-*.zip whose matching shard folder is missing.

    The manifest URLs are single-use, so if folders are absent the zips are
    the only recovery path. Extract into conversations/ (the zip carries the
    shard-dir prefix, so paths align with _shard_paths).
    """
    for zpath in sorted(glob.glob(os.path.join(claude_dir, "conversations", "*.zip"))):
        dest = zpath[:-4]
        if os.path.isdir(dest):
            continue
        try:
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(os.path.join(claude_dir, "conversations"))
        except (zipfile.BadZipFile, OSError):
            continue


def load_raw(claude_dir):
    """Concatenate all conversation shards into one list of raw dicts."""
    ensure_extracted(claude_dir)
    convos = []
    for fp in _shard_paths(claude_dir):
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                convos.extend(data)
        except (json.JSONDecodeError, OSError):
            continue
    return convos


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

def normalize_conv(conv):
    """Normalize a single raw Claude conversation dict to the unified schema."""
    raw = _ordered(conv.get("chat_messages") or [])
    messages = [c for c in (_card(m) for m in raw) if c]

    if not messages:
        return None

    created = utc_ts(conv.get("created_at"))
    updated = utc_ts(conv.get("updated_at")) or created
    title = (conv.get("name") or "").strip() or "(untitled)"

    preview = _preview(messages)
    if title == "(untitled)" or not title:
        title = preview and truncate(preview, 60) or "(untitled)"

    conv_id = conv.get("uuid")
    if not conv_id:
        conv_id = hashlib.sha1(f"{title}:{conv.get('created_at')}".encode()).hexdigest()[:16]

    return {
        "id": str(conv_id),
        "service": SERVICE,
        "title": title,
        "created_at": created,
        "updated_at": updated,
        "model": None,
        "msg_count": len(messages),
        "preview": preview,
        "archived": False,
        "starred": False,
        "messages": messages,
    }


def _ordered(messages):
    """Chronological order by created_at; stable (uuid) tiebreak.

    Claude messages carry parent_message_uuid, but in this export parents are
    always strictly sequential, so a timestamp sort reproduces the thread.
    """
    return sorted(
        messages,
        key=lambda m: (utc_ts(m.get("created_at")) or 0.0, m.get("uuid") or ""),
    )


def _card(m):
    """Build a normalized message card from a raw chat_message."""
    sender = m.get("sender") or "assistant"
    role = "user" if sender == "human" else "assistant"
    content = _clean_text(m.get("text"))
    attachments = _attachments(m)

    card = {
        "role": role,
        "content": content,
        "time": utc_ts(m.get("created_at")),
        "model": None,
        "kind": "text",
        "thoughts": _thinking(m),
        "recap": None,
        "audio_transcript": None,
        "images": [],
        "attachments": attachments,
        "citations": _citations(m),
        "search_results": _search_results(m),
    }
    # Drop cards with nothing to show (tool-only echoes with no rendered text).
    empty = not (content or attachments or card["thoughts"] or card["citations"])
    return None if empty else card


def _clean_text(text):
    """Strip tool-result placeholders and collapse excess newlines."""
    if not text:
        return ""
    t = _UNSUPPORTED_RE.sub("", text)
    t = _WORD_WS_RE.sub("\n\n", t)
    return t.strip()


def _thinking(m):
    """Join thinking blocks: full 'thinking' if present, else redacted summaries."""
    parts = []
    for b in (m.get("content") or []):
        if not isinstance(b, dict) or b.get("type") != "thinking":
            continue
        think = b.get("thinking")
        if isinstance(think, str) and think.strip():
            parts.append(think.strip())
        for s in b.get("summaries") or []:
            if isinstance(s, dict) and s.get("summary"):
                parts.append(s["summary"].strip())
    if not parts:
        return None
    return "\n\n".join(p for p in parts if p)


def _citations(m):
    """Collect url citations off text blocks into {title,url,snippet} cards."""
    out = []
    seen = set()
    for b in (m.get("content") or []):
        if not isinstance(b, dict) or b.get("type") != "text":
            continue
        for cite in b.get("citations") or []:
            if not isinstance(cite, dict):
                continue
            details = cite.get("details") or {}
            url = details.get("url") or cite.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "title": details.get("title") or cite.get("title") or url,
                "url": url,
                "snippet": details.get("snippet"),
            })
    return out


def _search_results(m):
    """Collect web_search/web_fetch tool_result knowledge entries."""
    out = []
    seen = set()
    for b in (m.get("content") or []):
        if not isinstance(b, dict):
            continue
        if b.get("type") != "tool_result" or b.get("name") not in ("web_search", "web_fetch"):
            continue
        for r in b.get("content") or []:
            if not isinstance(r, dict):
                continue
            url = r.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"title": r.get("title") or url, "url": url})
    return out


def _attachments(m):
    """Map uploaded files to attachment chips (no binary assets in export)."""
    out = []
    for f in m.get("files") or []:
        if not isinstance(f, dict):
            continue
        name = f.get("file_name") or f.get("file_uuid") or "file"
        out.append({"name": name, "mime": mime_for(name), "src": None, "size": None})
    return out


def _preview(messages):
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            return truncate(m["content"], 140)
    for m in messages:
        if m.get("content"):
            return truncate(m["content"], 140)
    return ""