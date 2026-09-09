"""AI Chat Retro — local-only Flask app over normalized chat exports.

Serves the SPA (static/) plus a read-only JSON API over every conversation
parsed from exports/. Binds to 127.0.0.1 only — nothing leaves the machine.

  python3 app.py          # then open http://localhost:5000
"""
import os

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from parsers import (
    SERVICE_LABELS,
    chatgpt_asset_dir,
    gemini_asset_dir,
    load_all,
)
from parsers.base import mime_for

BASE = os.path.dirname(os.path.abspath(__file__))
EXPORTS_ROOT = os.path.join(BASE, "exports")
STATIC = os.path.join(BASE, "static")

# Build (or load from pickle cache) once at startup. Immutable after this point.
DATA = load_all(EXPORTS_ROOT)

CONVERSATIONS = DATA["conversations"]   # {service/id: full normalized conversation}
SUMMARIES = DATA["summaries"]           # list, sorted updated_at desc, has private _blob
SERVICES = DATA["services"]             # per-service meta

STATIC_DIR_CACHE = {
    "chatgpt": chatgpt_asset_dir(EXPORTS_ROOT),
    "deepseek": None,
    "gemini": gemini_asset_dir(EXPORTS_ROOT),
}

app = Flask(__name__, static_folder=STATIC, static_url_path="")


# --------------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.route("/api/services")
def api_services():
    return jsonify({"services": SERVICES})


def _int_arg(name, default):
    try:
        return max(0, int(request.args.get(name, default)))
    except (TypeError, ValueError):
        return default


def _summary(sum):
    return {k: v for k, v in sum.items() if k != "_blob"}


@app.route("/api/conversations")
def api_conversations():
    service = request.args.get("service", "all")
    q = (request.args.get("search") or "").lower()
    limit = _int_arg("limit", 200)

    matched = 0
    out = []
    for s in SUMMARIES:
        if service != "all" and s["service"] != service:
            continue
        if q:
            blob = s.get("_blob") or ""
            if q not in blob:
                continue
        matched += 1
        if len(out) < limit:
            out.append(_summary(s))
    return jsonify({"conversations": out, "total": matched})


@app.route("/api/conversation/<service>/<id>")
def api_conversation(service, id):
    conv = CONVERSATIONS.get("{}/{}".format(service, id))
    if conv is None:
        abort(404)
    return jsonify(conv)


def _window(text, center, before=160, after=120):
    start = max(0, center - before)
    end = min(len(text), center + after)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].replace("\n", " ").strip() + suffix


def _snippet(conv, tokens):
    """Find the first message containing any token; return (role, snippet)."""
    for m in conv["messages"]:
        c = m.get("content") or ""
        if not c:
            continue
        low = c.lower()
        idx = -1
        for t in tokens:
            i = low.find(t)
            if i >= 0 and (idx < 0 or i < idx):
                idx = i
        if idx >= 0:
            return m.get("role"), _window(c, idx)
    return "user", ""


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip().lower()
    service = request.args.get("service", "all")
    limit = _int_arg("limit", 50)

    if not q:
        return jsonify({"query": "", "total": 0, "results": []})

    tokens = q.split()
    ranked = []
    for s in SUMMARIES:
        if service != "all" and s["service"] != service:
            continue
        blob = s.get("_blob") or ""
        if not all(t in blob for t in tokens):
            continue
        hits = sum(blob.count(t) for t in tokens)
        conv = CONVERSATIONS[s["service"] + "/" + s["id"]]
        role, snippet = _snippet(conv, tokens)
        ranked.append({
            "service": s["service"],
            "conversation_id": s["id"],
            "title": s["title"],
            "updated_at": s["updated_at"],
            "matched_role": role,
            "snippet": snippet,
            "_hits": hits,
        })

    ranked.sort(key=lambda r: (-r["_hits"], -(r["updated_at"] or 0)))
    results = ranked[:limit]
    for r in results:
        r.pop("_hits", None)
    return jsonify({"query": q, "total": len(ranked), "results": results})


# --------------------------------------------------------------------------- #
# RAG (optional — Ask feature). Works only if rag.available(); otherwise
# returns a clear status so the rest of the app keeps working.
# --------------------------------------------------------------------------- #

def _rag_conversations():
    return CONVERSATIONS


@app.route("/api/rag/status")
def api_rag_status():
    try:
        from rag import status
        st = status()
    except Exception as e:
        st = {"available": False, "error": str(e)}
    return jsonify({"rag": st})


@app.route("/api/rag/build", methods=["POST"])
def api_rag_build():
    try:
        from rag import build_index
        n = build_index(_rag_conversations(), EXPORTS_ROOT)
        return jsonify({"ok": True, "chunk_count": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ask/sources")
def api_ask_sources():
    """Retrieval-only: return raw source chunks for a question (no LLM)."""
    q = (request.args.get("q") or "").strip()
    service = request.args.get("service", "all")
    limit = _int_arg("limit", 5)
    if not q:
        return jsonify({"query": "", "sources": []})
    try:
        from rag import ensure_built, retrieve
        ensure_built(_rag_conversations(), EXPORTS_ROOT)
        sources = retrieve(q, service=None if service == "all" else service, k=limit)
        return jsonify({"query": q, "sources": sources})
    except Exception as e:
        return jsonify({"query": q, "sources": [], "error": str(e)}), 200


@app.route("/api/ask")
def api_ask():
    """Full RAG: retrieve context and generate an answer (if a local LLM)."""
    q = (request.args.get("q") or "").strip()
    service = request.args.get("service", "all")
    limit = _int_arg("limit", 5)
    if not q:
        return jsonify({"query": "", "answer": None, "sources": [], "status": {"generated": False}})
    try:
        from rag import ensure_built, query
        ensure_built(_rag_conversations(), EXPORTS_ROOT)
        result = query(q, service=None if service == "all" else service, k=limit)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "query": q, "answer": None, "sources": [], "error": str(e),
            "status": {"generated": False},
        }), 200


@app.route("/api/stats")
def api_stats():
    per_service = [
        {
            "service": s["service"],
            "label": s["label"],
            "count": s["count"],
            "first_ts": s["first_ts"],
            "last_ts": s["last_ts"],
        }
        for s in SERVICES
    ]

    model_counts = {}
    for s in SUMMARIES:
        m = s.get("model")
        if m:
            model_counts[(m, s["service"])] = model_counts.get((m, s["service"]), 0) + 1
    models = [
        {"model": m, "service": svc, "count": c}
        for (m, svc), c in sorted(model_counts.items(), key=lambda kv: -kv[1])[:20]
    ]

    return jsonify({
        "total_conversations": len(SUMMARIES),
        "services": per_service,
        "models": models,
    })


# --------------------------------------------------------------------------- #
# Assets (media referenced by chats)
# --------------------------------------------------------------------------- #

@app.route("/asset/<service>/<path:filename>")
def asset(service, filename):
    if "/" in filename or "\\" in filename or filename != os.path.basename(filename):
        abort(400)  # path traversal blocked
    asset_dir = STATIC_DIR_CACHE.get(service)
    if not asset_dir or not os.path.isdir(asset_dir):
        abort(404)
    asset_root = os.path.realpath(asset_dir)
    fp = os.path.realpath(os.path.join(asset_root, filename))
    if os.path.commonpath((asset_root, fp)) != asset_root or not os.path.isfile(fp):
        abort(404)
    with open(fp, "rb") as f:
        blob = f.read(16)
    mime = mime_for(filename, blob)
    # Attach non-raster files so an uploaded HTML/SVG document cannot execute
    # in this app's origin when an attachment is opened in a new tab.
    inline = mime in {"image/gif", "image/jpeg", "image/png", "image/webp"}
    return send_file(fp, mimetype=mime, as_attachment=not inline, conditional=True)


if __name__ == "__main__":
    # Default local-only. For WSL -> Windows browsers, run:
    #   AICR_HOST=0.0.0.0 python3 app.py   (reachable via the WSL IP too)
    host = os.environ.get("AICR_HOST", "127.0.0.1")
    port = int(os.environ.get("AICR_PORT", "5000"))
    app.run(host=host, port=port, threaded=True)
