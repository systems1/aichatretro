"""ChatGPT export parser.

Reads sharded conversations-*.json from the chatgpt export dir, linearizes
each conversation by walking `parent` from `current_node` back to the root,
and normalizes to the unified schema (see parsers/base.py).

Key facts (verified):
  - shards discovered via export_manifest.json -> logical_files["conversations.json"].files
  - mapping: {node_id: {id, message, parent}} — tree, NO children field
  - root node has message:null and parent:null
  - current_node = id of the LAST message in the live thread
  - content types: text | multimodal_text | thoughts | reasoning_recap
"""
import glob
import json
import os

from .base import (
    asset_key_from_pointer,
    asset_url,
    first_parts_text,
    mime_for,
    truncate,
    utc_ts,
)

SERVICE = "chatgpt"

# Content types that produce no standalone card but fold into the previous one
_FOLDABLE = ("thoughts", "reasoning_recap")


def discover_shards(chatgpt_dir):
    """Return sorted list of shard file paths. Discover via manifest, else glob."""
    manifest = os.path.join(chatgpt_dir, "export_manifest.json")
    if os.path.exists(manifest):
        try:
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
            files = (data.get("logical_files") or {}).get("conversations.json", {}).get("files", [])
            if files:
                shards = [
                    fn for fn in sorted(files)
                    if isinstance(fn, str) and os.path.basename(fn) == fn and fn.endswith(".json")
                ]
                if shards:
                    return [os.path.join(chatgpt_dir, fn) for fn in shards]
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    return sorted(glob.glob(os.path.join(chatgpt_dir, "conversations-*.json")))


def load_raw_shards(chatgpt_dir):
    """Concatenate all shards into one list of raw conversation dicts."""
    convos = []
    for fp in discover_shards(chatgpt_dir):
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                convos.extend(data)
        except (json.JSONDecodeError, OSError):
            continue
    return convos


def linearize(conv):
    """Return the ordered list of mapping-node ids for the live thread.

    Start at current_node, follow `parent` until root (message:null), reverse.
    Fallback when current_node missing/null: leaf node with max message create_time.
    """
    mapping = conv.get("mapping") or {}
    current = conv.get("current_node")
    if not current or current not in mapping:
        current = None
        best = None
        for nid, node in mapping.items():
            msg = node.get("message")
            if not msg:
                continue
            t = utc_ts(msg.get("create_time")) or 0
            if best is None or t > best[0]:
                best = (t, nid)
        if best is not None:
            current = best[1]
    if not current:
        return []

    chain = []
    seen = set()
    node = mapping.get(current)
    while node and node.get("message"):
        if current in seen:
            break
        seen.add(current)
        chain.append(current)
        current = node.get("parent")
        node = mapping.get(current)
        if not node:
            break
    chain.reverse()
    # Include the implicit root is unnecessary (message:null); chain is clean.
    return chain


def _friendly_names(chatgpt_dir):
    """Map on-disk .dat filename -> original uploaded filename."""
    fp = os.path.join(chatgpt_dir, "conversation_asset_file_names.json")
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _asset_files(chatgpt_dir):
    """Set of all *.dat filenames present on disk."""
    try:
        return {n for n in os.listdir(chatgpt_dir) if n.endswith(".dat")}
    except OSError:
        return set()


def normalize_conv(conv, friendly, on_disk):
    """Normalize a single raw conversation dict to the unified schema."""
    mapping = conv.get("mapping") or {}
    chain = linearize(conv)

    messages = []
    last_assistant = None  # index in messages of the last assistant card

    for node_id in chain:
        node = mapping.get(node_id)
        if not node:
            continue
        raw = node.get("message")
        if raw is None:
            continue  # deleted/regenerated leaf marker

        content = raw.get("content") or {}
        ctype = content.get("content_type")
        role = (raw.get("author") or {}).get("role") or "assistant"

        if ctype in _FOLDABLE:
            # Fold thoughts / reasoning_recap into previous assistant card.
            if last_assistant is None:
                card = _empty_assistant_card(raw, role)
                messages.append(card)
                last_assistant = len(messages) - 1
            target = messages[last_assistant]
            if ctype == "thoughts":
                thought_text = _thoughts_text(content)
                if thought_text:
                    target["thoughts"] = (target.get("thoughts") or "") + thought_text
            elif ctype == "reasoning_recap":
                recap = content.get("content")
                if recap:
                    target["recap"] = recap
            # keep card time/model up to date
            t = utc_ts(raw.get("create_time"))
            if t:
                target["time"] = t
            continue

        if ctype not in ("text", "multimodal_text"):
            continue

        card = _build_card(raw, content, ctype, friendly, on_disk, role, node_id)
        if card is None:
            continue
        messages.append(card)
        if role == "assistant":
            last_assistant = len(messages) - 1

    if not messages:
        return None

    created = utc_ts(conv.get("create_time"))
    updated = utc_ts(conv.get("update_time")) or created
    title = (conv.get("title") or "").strip() or "(untitled)"

    preview = _preview(messages)
    if title == "(untitled)" or not title:
        title = preview and truncate(preview, 60) or "(untitled)"

    conv_id = conv.get("conversation_id") or conv.get("id")
    if not conv_id:
        import hashlib
        conv_id = hashlib.sha1(f"{title}:{conv.get('create_time')}".encode()).hexdigest()[:16]

    return {
        "id": str(conv_id),
        "service": SERVICE,
        "title": title,
        "created_at": created,
        "updated_at": updated,
        "model": conv.get("default_model_slug"),
        "msg_count": len(messages),
        "preview": preview,
        "archived": bool(conv.get("is_archived")),
        "starred": bool(conv.get("is_starred")),
        "messages": messages,
    }


def _empty_assistant_card(raw, role):
    t = utc_ts(raw.get("create_time"))
    return {
        "role": "assistant",
        "content": "",
        "time": t,
        "model": _model_slug(raw),
        "kind": "text",
        "thoughts": None,
        "recap": None,
        "audio_transcript": None,
        "images": [],
        "attachments": [],
        "citations": [],
        "search_results": [],
    }


def _model_slug(raw):
    meta = raw.get("metadata") or {}
    return meta.get("model_slug")


def _thoughts_text(content):
    thoughts = content.get("thoughts") or []
    parts = []
    for t in thoughts:
        if isinstance(t, dict):
            txt = t.get("content") or ""
            if txt:
                parts.append(txt)
    return ("\n\n".join(parts)).strip() or None


def _build_card(raw, content, ctype, friendly, on_disk, role, node_id):
    """Build a message card from a text/multimodal_text message."""
    t = utc_ts(raw.get("create_time"))
    meta = raw.get("metadata") or {}
    model = meta.get("model_slug") or _model_slug(raw)
    images, attachments, audio_transcript = [], [], None
    citations = _citations(meta)
    search_results = _search_results(meta)

    if ctype == "text":
        text = first_parts_text(content.get("parts"))
        kind = "text"
        audio_transcript = None
    else:
        parts = content.get("parts") or []
        text_parts, images, attachments, audio_transcript = _multimodal(parts, friendly, on_disk)
        text = "".join(text_parts)
        kind = "multimodal"

    # metadata.attachments may add extra uploaded files not in parts
    for att in meta.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        att_id = att.get("id")
        name = att.get("name") or (att_id + ".dat" if att_id else "attachment")
        fname = att_id if (att_id and att_id.endswith(".dat")) else (att_id + ".dat" if att_id else None)
        src = None
        mime = att.get("mime_type") or mime_for(name)
        if fname and fname in on_disk:
            src = asset_url(SERVICE, fname)
        elif fname and not att_id:
            pass
        attachments.append({
            "name": name,
            "mime": mime,
            "src": src,
            "size": att.get("size"),
        })

    card = {
        "role": role,
        "content": text.strip(),
        "time": t,
        "model": model,
        "kind": kind,
        "thoughts": None,
        "recap": None,
        "audio_transcript": audio_transcript,
        "images": images,
        "attachments": attachments,
        "citations": citations,
        "search_results": search_results,
    }
    return card


def _multimodal(parts, friendly, on_disk):
    """Split multimodal parts into text + images + attachments + transcript."""
    text_parts = []
    images = []
    attachments = []
    audio_transcript = None
    for p in parts:
        if isinstance(p, str):
            text_parts.append(p)
            continue
        if not isinstance(p, dict):
            continue
        ct = p.get("content_type")
        if ct == "image_asset_pointer":
            _push_asset(p.get("asset_pointer"), friendly, on_disk, images)
        elif ct == "audio_transcription":
            if p.get("text"):
                audio_transcript = (audio_transcript or "") + p["text"] + "\n"
        elif ct == "audio_asset_pointer":
            # Serve audio, but skip silently if file missing on disk (verified: many missing)
            _push_asset(p.get("asset_pointer"), friendly, on_disk, attachments, audio=True)
        elif ct == "real_time_user_audio_video_asset_pointer":
            ap = (p.get("audio_asset_pointer") or {}).get("asset_pointer")
            if ap:
                _push_asset(ap, friendly, on_disk, attachments, audio=True)
            for f in p.get("frames_asset_pointers") or []:
                _push_asset(f.get("asset_pointer"), friendly, on_disk, images)
        # unknown dict parts ignored
    if audio_transcript:
        audio_transcript = audio_transcript.rstrip("\n")
    return text_parts, images, attachments, audio_transcript


def _push_asset(pointer, friendly, on_disk, target, audio=False):
    fname = asset_key_from_pointer(pointer)
    if not fname:
        return
    name = friendly.get(fname, fname)
    if fname in on_disk:
        target.append({
            "src": asset_url(SERVICE, fname),
            "name": name,
            "mime": mime_for(name),
        })
    # if not on disk: don't push a broken entry


def _citations(meta):
    out = []
    for ref in meta.get("content_references") or []:
        if not isinstance(ref, dict):
            continue
        url = ref.get("url")
        if not url:
            continue
        out.append({
            "title": ref.get("title") or ref.get("attribution") or url,
            "url": url,
            "snippet": ref.get("snippet"),
        })
    return out


def _search_results(meta):
    out = []
    for group in meta.get("search_result_groups") or []:
        if not isinstance(group, dict):
            continue
        conf = group.get("confidences") or []
        for entry in group.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not url:
                continue
            out.append({
                "title": entry.get("title") or url,
                "url": url,
            })
    return out


def _preview(messages):
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            return truncate(m["content"], 140)
    for m in messages:
        if m.get("content"):
            return truncate(m["content"], 140)
    return ""
