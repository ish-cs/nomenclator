## User should run using:
---->     claude --permission-mode bypassPermissions      <----

## Admin Mode
You should always run every bash command, etc, but the one thing to never do is pull, push, merge, or commit anything without me specifically prompting you to do so. Remember this always.


## Response Style

- Use the minimum tokens necessary.
- Be concise and efficient while being thorough.
- Avoid unnecessary preamble or filler.
- Always ask clarifying questions. Never guess or make estimations.
- If a response takes more than 5 minutes, break it up into sections and plan.
- When changes are made to the codebase update PRODUCT.md with latest information.


## What this project is
**Augur** — a personal context layer for AI agents. The real product is `context-server.py`: a local HTTP API any agent can call to get ranked screen context. The dashboard (`screenpipe-dashboard.html`) is the research and demo surface on top of screenpipe.

## The startup idea behind this
The user (Ishaan) is building a startup around a **personal context layer for AI agents**. The core insight: AI agents today are blank-slate -- they don't know anything about the user. Existing fixes (Mem0, Letta) require manual input. The better approach is passive behavioral capture (browser activity, screen activity) that auto-feeds context to agents with zero setup. Screenpipe is being used as a research tool and prototype to explore this architecture.

## Current file structure
```
~/Desktop/nomenclator/
  screenpipe-dashboard.html   # Main dashboard (single HTML file, no build)
  launch.command              # Double-click launcher (Python 3)
  context-server.py           # Context API server on port 3031 — the core product
  demo_agent.py               # Agent integration demo / investor demo (--api claude|openai|lmstudio)
  semantic_search.py          # Chroma vector store + sentence-transformers indexer
  mcp_server.py               # MCP server — Claude Desktop / Cursor integration (pure stdlib)
  requirements.txt            # pip deps: chromadb, sentence-transformers
  test_features.py            # Feature test suite (offline + --live modes)
  extension/                  # Chrome MV3 browser extension
    manifest.json, background.js, content.js, popup.html/js/css
  DOCS/PRODUCT.md             # Full technical product documentation
  DOCS/CLAUDE.md              # This file
```

## Tech stack
- **screenpipe**: runs locally, exposes REST API at `http://localhost:3030`
  - `GET /health` — status check
  - `GET /search?q=QUERY&limit=N` — full-text search across all captures
  - `GET /search?limit=N` — recent captures
  - `POST /raw_sql` — direct SQLite queries
  - Returns OCR frames: `app_name`, `window_name`, `browser_url`, `text`, `timestamp`
- **LM Studio**: local LLM server at `http://127.0.0.1:1234`
  - Model auto-detected via `/v1/models` (first non-embedding model)
  - OpenAI-compatible API at `/v1/chat/completions`
  - **Important**: system prompt merged into first user message — do NOT use `system` role (breaks some models)
  - CORS must be enabled in LM Studio settings
  - Context length must be 8192+ in LM Studio server settings
- **Context API** (`context-server.py`): runs at `http://localhost:3031`
  - Pure Python stdlib (no external deps)
  - `GET /health`, `GET /context?q=...&limit=N&window_hours=H`, `GET /summary?date=YYYY-MM-DD`
  - `GET /anomalies?days=N`, `GET /browser-captures?limit=N`, `POST /browser-capture`
  - `GET /profile?days=N`, `GET /context-card?days=N`, `GET /semantic?q=...`
  - CORS on all responses; `SO_REUSEADDR` via `ReuseHTTPServer` subclass
  - Hybrid scoring: `(kw_matches × 3) + recency + semantic_bonus(cosine×2.0) + [time_bon + sel_bon for browser]`
  - Browser UID format: `browser_XXXXXXXXX` (avoids collision with integer frame_ids)
- **MCP server** (`mcp_server.py`): pure stdlib JSON-RPC 2.0 over stdio (Content-Length framing)
  - 5 tools: `get_context`, `get_daily_summary`, `get_anomalies`, `get_user_profile`, `get_browser_activity`
  - Notifications (no `id`) return None — never write a response frame for them
  - All output to `sys.stdout.buffer`, all logging to `sys.stderr`
- **Frontend**: single HTML file, no build system, vanilla JS + CSS
  - Fonts: JetBrains Mono + Syne from Google Fonts
  - Dark terminal aesthetic, green (#00ff87) accent color

## Dashboard features (v0.3.1)
1. **Live Feed tab** — recent OCR+audio captures as expandable cards
2. **Ask AI tab** — chat with LM Studio; smart context injection; persistent localStorage memory (50-msg cap, clear button); fixed-height scrollable chat (page no longer scrolls); tab is now second position (right of Live Feed)
3. **Search Results tab** — keyword search + semantic search mode toggle (Keyword / Semantic buttons)
4. **Raw SQL tab** — direct SQLite queries, auto-detected table output, presets
5. **Timeline tab** — gantt chart of today's app activity by hour; click block → detail panel
6. **Anomalies tab** — behavioral anomaly detection vs N-day rolling baseline; requires context-server.py
7. **Browser tab** — recent browser extension captures; displays all fields: URL, title, time on page, scroll depth, selected text, timestamp; requires context-server.py
8. **Sidebar**: Refresh, Auto-refresh (defaults to ON), Export JSON, Export Context Snapshot

## AI context system (important)
Always-on smart search. For every question:
1. Extract keywords (strip stop words, up to 6)
2. `GET /search?limit=40` + `GET /search?q={kw}&limit=10` per keyword in parallel
3. Merge browser captures from `_browser_captures` in-memory list (filtered by window_hours)
4. Deduplicate by uid (frame_id or `browser_XXXXXXXXX`)
5. Score: `(keyword_matches × 3) + recency + semantic_bonus + [time_bon + sel_bon]`
6. Semantic bonus from Chroma if index populated: `cosine × 2.0`
7. Top N fed as formatted context block into LLM prompt

## launch.command startup sequence
`launch.command` is now a tkinter GUI app, not a terminal script.

**On launch:**
- Opens a `tkinter.Tk()` window titled "Augur"
- Startup sequence runs immediately in a background thread (same steps as before):
  1. `cleanup_old_files()` — deletes `~/.screenpipe/data/` files older than `CLEANUP_DAYS` (default 7)
  2. Check/start screenpipe on :3030
  3. Check/start `context-server.py` on :3031 as subprocess
  4. Check deps (`chromadb`, `sentence_transformers`) via `importlib.import_module`; if available and indexer not running, start `semantic_search.py` as detached subprocess; write PID to `~/.screenpipe/semantic_indexer.pid`
  5. Check LM Studio on :1234 (non-blocking warning)
  6. Open dashboard in browser (Chrome)

**GUI window:**
- Live status indicators for 4 services (screenpipe, Context API, Semantic Indexer, LM Studio), updated every 5s via `root.after()`
- `Start Screenpipe` button: starts screenpipe in a thread, disables while starting, re-enables when port 3030 is open
- `Stop Screenpipe` button: runs `pkill screenpipe`
- Scrollable log area replaces all terminal print output
- Closing the window does NOT kill screenpipe — services keep running

## Known gotchas
- Timestamps: strip timezone for naive comparison — `replace('Z', '+00:00').replace('+00:00', '')`
- `datetime.utcnow()` deprecated in Python 3.12+ — use `datetime.now(timezone.utc).replace(tzinfo=None)`
- localStorage persistence: use `_suppressPersist = true` during `loadPersistedChat()` to prevent exponential duplication
- Tab names are strings not integers: `switchTab('browser', this)` — `dailySummary()` still hardcodes `querySelectorAll('.tab')[3]` for AI tab
- `SO_REUSEADDR` must be set as class attribute before `HTTPServer.__init__` binds the socket
- MCP notifications have no `id` field — check before calling `_write_message()` or you'll crash the protocol
- Browser captures UID format: `browser_XXXXXXXXX` (timestamp-based) — never collides with integer OCR frame_ids
- Semantic globals `_semantic_embedder`, `_semantic_collection`, `_semantic_available` cached module-level; `None` = untested, `False` = unavailable
- `root.after()` must be used for all tkinter UI updates from threads — never update widgets directly from a non-main thread; use `root.after(0, lambda: ...)` pattern
- AI chat context text must be capped before sending to LLM — assembled context block hard-capped at 6000 chars to avoid LM Studio context window 400 errors

## Screenpipe binary location
```/
/Users/ish/bin/screenpipe
```

## How to start everything manually
1. Run `/Users/ish/bin/screenpipe` in Terminal
2. Run `python3 context-server.py` in Terminal
3. Open LM Studio → Local Server → enable CORS → load a model → Start Server
4. Open `screenpipe-dashboard.html` in Chrome

## v0.3.1 — shipped
All v0.3.1 features complete: tkinter GUI launcher, context window fix, Augur branding, sidebar cleanup, Ask AI scroll fix, Ask AI tab reorder, Browser tab fix.

## v0.3 — shipped
All v0.3 features complete:
- Browser captures merged into `/context` ranking (dwell time + selection bonuses)
- Hybrid scoring: semantic + keyword blended via module-level Chroma cache
- Semantic indexer auto-started from `launch.command` (PID tracked)
- `demo_agent.py` supports `--api claude|openai|lmstudio` with lazy imports
- `mcp_server.py` — pure stdlib MCP server (5 tools) for Claude Desktop / Cursor
- `/profile` and `/context-card` endpoints live
- Dashboard: Browser tab + Keyword/Semantic search mode toggle
- 74 tests (57 pass offline, 17 skipped = live-only)

