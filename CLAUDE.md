# CLAUDE.md — AI-Chat-Retro project notes

## Purpose
Local-only web viewer for reading past AI chat exports from `exports/` (ChatGPT, DeepSeek, Gemini)
offline. Python + Flask backend, vanilla-JS SPA (light theme — was dark; switched by user request).
App reads the `exports/` folder it ships next to — user decision: **no persistent cache; parse fresh
on every start**.

## Running
```bash
cd aichatretro
pip install flask
python3 app.py        # then open http://localhost:5000
```
- Binds `127.0.0.1:5000` only (local machine). App reads `exports/` (gitignored).
- **WSL → Windows browser:** `AICR_HOST=0.0.0.0 python3 app.py`, open `http://<wsl-ip>:5000`
  (`ip addr show eth0 | grep inet`), **Ctrl+F5** once to clear cached old CSS/JS. Otherwise you can
  get a silent blank/dark page even though the server is up.
- **No cache**: `data/cache.pkl` was removed by user decision — the app must parse `exports/`
  fresh at every start so each person's own exports are what loads. Keep it that way.

### Docker (Docker Desktop on Windows 10)
```bash
docker compose up -d --build
# open http://localhost:5000  — Docker Desktop forwards published ports to host localhost
```
- **Critical:** the Dockerfile sets `AICR_HOST=0.0.0.0`. Inside a container a `127.0.0.1` bind is
  the container's own loopback, so `-p 5000:5000` would silently fail → dark/dead page.
- `exports/` is **mounted read-only** (`./exports:/app/exports:ro`), never baked into the image;
  `.dockerignore` excludes it. New exports → `docker compose restart`.
- Stop the native server first or it holds port 5000: `kill $(pgrep -f 'python3 app.py')`.
- To leave the image at the native host Python, keep `pip install flask` on the host instead.

## Startup data loading (automatic, server-side)
When `python3 app.py` runs, `load_all(EXPORTS_ROOT)` fires **immediately at import time**
(`app.py` line 25), **before** the Flask server starts accepting requests. No button, no
browser action — the parse is a blocking one-time step at boot. After that:
- Three module-level dicts are built and frozen: `CONVERSATIONS`, `SUMMARIES`, `SERVICES`.
- The SPA just calls `/api/conversations` and `/api/services` to list pre-parsed data.
- Clicking a chat calls `/api/conversation/<service>/<id>` — a Python dict lookup, not a file read.
- To pick up new/changed exports, restart the server. No other mechanism exists.

## Data & parsing (`parsers/`)
One module per service; all normalize to one unified schema (timestamps = unix UTC floats,
rendered content = markdown). Conversation key = `{service}/{id}`.
Sample-data counts: **1130 ChatGPT + 24 DeepSeek + 41 Gemini threads = 1193**.

- **chatgpt.py** — sharded `conversations-*.json`, discovered via `export_manifest.json`
  (`logical_files.conversations.json.files`). Live thread = walk `parent` from `current_node`
  to root (root has `message:null, parent:null`). Fold `thoughts`/`reasoning_recap` into the
  previous assistant card. Media from `asset_pointer` (`sediment://file_X` → `file_X.dat` on
  disk); ~19 audio assets referenced but missing → render transcript, skip broken media.
  Friendly names from `conversation_asset_file_names.json`.
- **deepseek.py** — single `conversations.json`. Thread = follow `children[0]` from root
  (root = `parent:null`). Role from fragment `type`: REQUEST=user; one node with
  SEARCH+RESPONSE+THINK → one assistant card (citations + reasoning).
- **gemini.py** — flat `MyActivity.json` activity log; group **consecutive** entries by
  `details[0].url`; no-url entries break adjacency. Each **"Prompted "** entry carries its own
  response in `safeHtmlItem[0].html` (prompt + reply in the same entry). `html_to_markdown()`
  (stdlib HTMLParser subclass) converts responses. Skip `Used/Cleared/Selected` meta activity.

## Time formats gotcha
- ChatGPT: unix float seconds.
- DeepSeek: ISO `+08:00` — `datetime.fromisoformat` OK on Python 3.8.
- Gemini: RFC3339 `...Z` — **Python 3.8 `fromisoformat` fails on `Z`**; `utc_ts()` in
  `parsers/base.py` replaces `Z`→`+00:00` before parsing.

## Frontend notes
- `static/js/markdown.js` — hand-written offline renderer (no CDN). API: `renderMarkdown(str)`.
- `static/js/app.js` — SPA state machine: service tabs, live sidebar filter, Enter = full-text
  search, reasoning accordions, image lightbox, "Load earlier" chunking for >400-msg chats.
- `static/css/style.css` — CSS vars at top (`--accent:#10a37f`, per-service colors). Light theme.
- Asset route `/asset/<service>/<filename>` serves MIME magic-sniffed; rejects path traversal.
- **CSS `[hidden]` specificity gotcha (fixed 2026-09-08):** the `.overlay { display: flex }` and
  `.lightbox { display: flex }` rules overrode the HTML `hidden` attribute's UA `display: none`,
  causing both overlays to render on every page load (dark backdrop + blocking modal). Always
  include `[hidden] { display: none !important; }` in the CSS when using the `hidden` attribute
  alongside layout rules. The `.hidden` class rule alone does NOT match `hidden` attribute nodes.

## Roadmap (not built)
RAG over chats; `ads.json` reference view; per-conversation export; inverted search index; SQLite backend if data grows.
