"""DeepSeek export parser.

Format (verified):
  - conversations.json is a JSON array of conversation objects
  - each: {id, title, inserted_at (ISO +08:00), updated_at, mapping}
  - mapping: {node_id: {id, parent, children:[...], message: {...}}}
  - root node: parent null, message null, children ["1"]
  - message: {model, inserted_at, fragments:[{type: REQUEST|RESPONSE|SEARCH|THINK, ...}]}
  - role is encoded in fragment type: REQUEST=user, RESPONSE=assistant,
    SEARCH=assistant web-search results, THINK=assistant chain-of-thought
"""
import json
import os

from .base import truncate, utc_ts

SERVICE = "deepseek"

CONVERSATIONS_FILE = "conversations.json"


def load_raw(deepseek_dir):
    fp = os.path.join(deepseek_dir, CONVERSATIONS_FILE)
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def linearize(conv):
    """Return ordered node ids following children[0] from root."""
    mapping = conv.get("mapping") or {}
    if not mapping:
        return []
    root = None
    for nid, node in mapping.items():
        if node.get("parent") is None:
            root = nid
            break
    if root is None:
        # no explicit root; find the node whose parent points nowhere
        parents = {n.get("parent") for n in mapping.values()}
        for nid in mapping:
            if nid not in parents:
                root = nid
                break
    order = []
    seen = set()
    cur = root
    while cur and cur in mapping and cur not in seen:
        seen.add(cur)
        order.append(cur)
        node = mapping[cur]
        children = node.get("children") or []
        cur = children[0] if children else None
    return order


def normalize_conv(conv):
    """Normalize a single DeepSeek conversation dict to the unified schema."""
    mapping = conv.get("mapping") or {}
    order = linearize(conv)

    messages = []
    for nid in order:
        node = mapping.get(nid)
        if not node:
            continue
        msg = node.get("message")
        if not msg:
            continue
        frags = msg.get("fragments") or []
        if not frags:
            continue

        inserted = utc_ts(msg.get("inserted_at"))
        model = msg.get("model")

        # Classify: does this node carry a user REQUEST?
        has_request = any(f.get("type") == "REQUEST" for f in frags)
        if has_request:
            content = "".join(f.get("content") or "" for f in frags if f.get("type") == "REQUEST")
            messages.append(_card("user", content, inserted, model, None, None))
            continue

        # Assistant node: fold RESPONSE/THINK/SEARCH
        thoughts, search_results, resp_parts = [], [], []
        for f in frags:
            t = f.get("type")
            if t == "RESPONSE":
                resp_parts.append(f.get("content") or "")
            elif t == "THINK":
                thoughts.append(f.get("content") or "")
            elif t == "SEARCH":
                for r in f.get("results") or []:
                    if isinstance(r, dict) and r.get("url"):
                        search_results.append({
                            "title": r.get("title") or r.get("url"),
                            "url": r["url"],
                        })
        if not resp_parts and not thoughts:
            # Only SEARCH node (no response) — render as assistant with links only
            pass
        if not resp_parts and not search_results and not thoughts:
            continue
        messages.append(_card(
            "assistant",
            "\n\n".join(resp_parts).strip(),
            inserted, model,
            "\n\n".join(t for t in thoughts if t).strip() or None,
            search_results,
        ))

    if not messages:
        return None

    created = utc_ts(conv.get("inserted_at"))
    updated = utc_ts(conv.get("updated_at")) or created
    title = (conv.get("title") or "").strip() or "(untitled)"

    preview = _preview(messages)
    if title == "(untitled)" or not title:
        title = preview and truncate(preview, 60) or "(untitled)"

    return {
        "id": str(conv.get("id") or ""),
        "service": SERVICE,
        "title": title,
        "created_at": created,
        "updated_at": updated,
        "model": messages[-1].get("model"),
        "msg_count": len(messages),
        "preview": preview,
        "archived": False,
        "starred": False,
        "messages": messages,
    }


def _card(role, content, inserted, model, thoughts, search_results):
    return {
        "role": role,
        "content": content,
        "time": inserted,
        "model": model,
        "kind": "text",
        "thoughts": thoughts,
        "recap": None,
        "audio_transcript": None,
        "images": [],
        "attachments": [],
        "citations": [],
        "search_results": search_results or [],
    }


def _preview(messages):
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            return truncate(m["content"], 140)
    for m in messages:
        if m.get("content"):
            return truncate(m["content"], 140)
    return ""