# AI-Chat-Retro — Offline Chat Viewer

Local-only web app to review all your past AI chat exports — offline, in your browser.

## Goal

Read every export under `exports/` (ChatGPT, DeepSeek, Gemini, Claude) and present the chats in a
web UI that behaves like the online apps: a searchable sidebar of every conversation, a
main pane rendering each chat as user/assistant bubbles, inline images, reasoning
fold-ups, citations, voice-message transcripts, and cross-service full-text search.
100% local — no internet, no CDN, no accounts.

## Data formats (verified against this repo's exports)

| Service | Files | Count | Structure |
|---|---|---|---|
| ChatGPT | `chatgpt/chatgpt/conversations-000..011.json` (+50 `.dat` assets) | 1,130 | Array of convos; `mapping` tree with `parent` pointers; live thread = walk `parent` from `current_node` to root |
| DeepSeek | `deepseek/deepseek_data-2026-08-22/conversations.json` | 24 | Array of convos; `mapping` tree with `children[0]`; role encoded in fragment `type` (REQUEST=user, RESPONSE/SEARCH/THINK=assistant) |
| Gemini | `gemini/Takeout/My Activity/Gemini Apps/MyActivity.json` | 41 groups (39 unique ids) | Flat activity log; group consecutive entries by `details[0].url`; each "Prompted" entry carries prompt (title) + response (safeHtmlItem) |
| Claude | `claude/conversations/conversations-000/conversations.json` (sharded) | 4 | Array of convos; `chat_messages[]` ordered by `created_at`; role = `sender` (`human`=user/`assistant`); `content[]` typed blocks text/thinking/tool_use/tool_result |

Full file inventory in `data-export-reference-conversations.txt`.

## Architecture

```
Browser (static SPA)  ──JSON API──►  Flask app.py  ──►  parsers/  ──►  exports/*
  index.html               /api/*          /asset/*      base.py (helpers)
  app.js  ├─state                                         chatgpt.py
  markdown.js (offline renderer)                         deepseek.py
  style.css (light theme)                                gemini.py
                                                         claude.py
```

**Unified schema invariant:** every conversation becomes
`{id, service, title, created_at, updated_at, model, msg_count, preview, archived, starred, messages[]}`.
Timestamps are **unix UTC floats**; all rendered content is **markdown**. Conversation key = `{service}/{id}`.

Each message: `{role, content, time, model, kind, thoughts, recap, audio_transcript, images[], attachments[], citations[], search_results[]}`.

## API surface

| Route | Purpose |
|---|---|
| `GET /` | SPA shell (`static/index.html`) |
| `GET /api/services` | per-service count + date range |
| `GET /api/conversations?service=&search=&limit=` | conversation summaries (sorted updated desc) |
| `GET /api/conversation/<service>/<id>` | full normalized conversation |
| `GET /api/search?q=&service=&limit=` | full-text search → snippets |
| `GET /api/stats` | per-service / per-model counts |
| `GET /asset/<service>/<filename>` | raw media, MIME magic-sniffed |

## Key edge cases handled

- **ChatGPT** parent-walk from `current_node` (not BFS) — regenerated/deleted branches excluded.
  `thoughts` + `reasoning_recap` folded into the previous assistant card. `multimodal_text` parts
  → inline images / audio transcription. **19 of 52 referenced audio assets are missing on disk** —
  render transcript, never a broken image.
- **DeepSeek** multi-fragment nodes (SEARCH+RESPONSE+THINK in one) → single assistant card with
  citations + reasoning fold-up.
- **Gemini** `fromisoformat` on Python 3.8 **fails on `Z` suffix** — normalized to `+00:00` first.
  `safeHtmlItem` HTML → markdown via local converter. 2 of 41 thread groups resolve to a shared id
  (duplicate `details[0].url`) → 39 unique keys shown.
- **Claude** `chat_messages[]` sorted by `created_at` (parents are always sequential). `m["text"]`
  can embed "This block is not supported..." placeholder fences standing in for tool blocks —
  stripped before display/embedding. `thinking` is **redacted to summaries** (full CoT absent).
  Text-block `citations[].details.url` → citations; `web_search`/`web_fetch` `tool_result`
  knowledge → search results; `files[]` uploaded refs (no binary assets in the export → name-only
  attachment chips). Unextracted `conversations-*.zip` shards auto-extract on load.
- MIME sniffing by **magic bytes** (`.dat` has no extension), extension fallback.
- Asset routes reject anything ≠ `basename()` (path traversal safe).
- **CSS `[hidden]` specificity** — layout rules like `.overlay { display: flex }` override the
  HTML `hidden` attribute's UA `display: none` in the cascade. Must use `[hidden] { display: none !important; }`
  in the stylesheet, or the hidden attribute is silently ignored.
- No persistent cache — `exports/` is parsed fresh on every app start, so each
  person's own exports folder is exactly what gets loaded.

## Frontend (light theme)

- Sticky topbar: service tabs (All | ChatGPT | DeepSeek | Gemini | Claude), debounced search, Stats button.
- 320px sidebar: progressive-render conversation list (windowed + IntersectionObserver), titles +
  relative dates + service dots.
- Main pane: message bubbles (user right, assistant left), reasoning accordions, inline images →
  lightbox, "Voice message:" quote blocks, Sources chips, model + timestamp footers.
- Chunked rendering for conversations > 400 messages ("Load earlier").
- Offline markdown renderer `static/js/markdown.js` — no CDN dependency.

## How to run

```bash
cd aichatretro
pip install flask
python3 app.py
# open http://localhost:5000
```

## Roadmap (out of scope for v1)

- RAG / semantic search over chats
- `ads.json` reference view
- Per-conversation Markdown/JSON export buttons
- Inverted search index at scale
- SQLite backend if data grows