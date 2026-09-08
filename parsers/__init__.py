"""Parser orchestration: scan exports/, normalize every service, build index.

Data is parsed fresh from exports/ at app startup — no persistent cache — so
the webapp always reflects exactly the exports folder present on disk (each
person loads their own data).
"""
import json
import os

from . import chatgpt as chatgpt_mod
from . import deepseek as deepseek_mod
from . import gemini as gemini_mod

SERVICE_LABELS = {
    "chatgpt": "ChatGPT",
    "deepseek": "DeepSeek",
    "gemini": "Gemini",
}

# Directory layout inside exports/<service>/ for the *data* (ignoring zip).
CHATGPT_DATA = ("chatgpt", "chatgpt")
DEEPSEEK_DATA = ("deepseek", "deepseek_data-2026-08-22")
GEMINI_DATA = ("gemini", "Takeout", "My Activity", "Gemini Apps")


def _resolve(exports_root, parts):
    p = os.path.join(exports_root, *parts)
    return p if os.path.isdir(p) else None


def _assets_on_disk(chatgpt_dir):
    """Set of *.dat filenames present in the chatgpt export dir."""
    try:
        return {n for n in os.listdir(chatgpt_dir) if n.endswith(".dat")}
    except OSError:
        return set()


def _build(exports_root):
    """Normalize all services from scratch."""
    conversations = {}   # key -> full normalized conversation
    services = []
    summaries = []       # list of summary dicts (mirrors conversation index)

    chatgpt_dir = _resolve(exports_root, CHATGPT_DATA)
    if chatgpt_dir:
        friendly = chatgpt_mod._friendly_names(chatgpt_dir)
        on_disk = _assets_on_disk(chatgpt_dir)
        counts = [0, None, None]
        for conv in chatgpt_mod.load_raw_shards(chatgpt_dir):
            norm = chatgpt_mod.normalize_conv(conv, friendly, on_disk)
            if not norm:
                continue
            key = f"{chatgpt_mod.SERVICE}/{norm['id']}"
            conversations[key] = norm
            counts[0] += 1
            if counts[1] is None or (norm["created_at"] or 0) < counts[1]:
                counts[1] = norm["created_at"]
            if counts[2] is None or (norm["updated_at"] or 0) > counts[2]:
                counts[2] = norm["updated_at"]
        services.append(_service_meta(chatgpt_mod.SERVICE, counts))

    deepseek_dir = _resolve(exports_root, DEEPSEEK_DATA)
    if deepseek_dir:
        counts = [0, None, None]
        for conv in deepseek_mod.load_raw(deepseek_dir):
            norm = deepseek_mod.normalize_conv(conv)
            if not norm:
                continue
            key = f"{deepseek_mod.SERVICE}/{norm['id']}"
            conversations[key] = norm
            counts[0] += 1
            if counts[1] is None or (norm["created_at"] or 0) < counts[1]:
                counts[1] = norm["created_at"]
            if counts[2] is None or (norm["updated_at"] or 0) > counts[2]:
                counts[2] = norm["updated_at"]
        services.append(_service_meta(deepseek_mod.SERVICE, counts))

    gemini_dir = _resolve(exports_root, GEMINI_DATA)
    if gemini_dir:
        entries = gemini_mod.load_raw(gemini_dir)
        counts = [0, None, None]
        for group in gemini_mod.group_into_threads(entries):
            norm = gemini_mod.normalize_group(group, gemini_dir)
            if not norm:
                continue
            key = f"{gemini_mod.SERVICE}/{norm['id']}"
            conversations[key] = norm
            counts[0] += 1
            if counts[1] is None or (norm["created_at"] or 0) < counts[1]:
                counts[1] = norm["created_at"]
            if counts[2] is None or (norm["updated_at"] or 0) > counts[2]:
                counts[2] = norm["updated_at"]
        services.append(_service_meta(gemini_mod.SERVICE, counts))

    # Build summaries + per-conversation search blob
    for key, norm in conversations.items():
        blob = _search_blob(norm)
        summaries.append({
            "id": norm["id"],
            "service": norm["service"],
            "title": norm["title"],
            "created_at": norm["created_at"],
            "updated_at": norm["updated_at"],
            "msg_count": norm["msg_count"],
            "preview": norm["preview"],
            "model": norm["model"],
            "_blob": blob,
        })
    summaries.sort(key=lambda s: (s["updated_at"] or 0), reverse=True)

    return {
        "conversations": conversations,
        "summaries": summaries,
        "services": services,
    }


def _service_meta(service, counts):
    count, first, last = counts
    return {
        "service": service,
        "label": SERVICE_LABELS.get(service, service),
        "count": count,
        "first_ts": first,
        "last_ts": last,
    }


def _search_blob(norm):
    parts = [norm.get("title") or ""]
    for m in norm["messages"]:
        if m.get("content"):
            parts.append(m["content"])
        if m.get("thoughts"):
            parts.append(m["thoughts"])
    return " ".join(parts).lower()


def load_all(exports_root):
    """Load & normalize everything under exports/ for this machine.

    Parsed fresh from the exports folder every time the app starts (no
    persistent cache) — the webapp reflects exactly the data each person
    places in their own exports/ directory.
    """
    return _build(os.path.abspath(exports_root))


# Asset directory resolvers used by app.py
def chatgpt_asset_dir(exports_root):
    return _resolve(exports_root, CHATGPT_DATA)


def gemini_asset_dir(exports_root):
    return _resolve(exports_root, GEMINI_DATA)