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
  demo_agent.py               # Agent integration demo / investor demo
  semantic_search.py          # Chroma vector store + sentence-transformers indexer
  mcp_server.py               # MCP server (v0.3) — Claude Desktop / Cursor integration
  requirements.txt            # pip deps: chromadb, sentence-transformers
  test_features.py            # Feature test suite (offline + --live modes)
  PLAN.md                     # v0.3 deep implementation plan
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
  - CORS on all responses; `SO_REUSEADDR` via `ReuseHTTPServer` subclass
- **Frontend**: single HTML file, no build system, vanilla JS + CSS
  - Fonts: JetBrains Mono + Syne from Google Fonts
  - Dark terminal aesthetic, green (#00ff87) accent color

## Dashboard features (v0.2)
1. **Live Feed tab** — recent OCR+audio captures as expandable cards
2. **Search Results tab** — full-text search, highlighted matches
3. **Raw SQL tab** — direct SQLite queries, auto-detected table output, presets
4. **Ask AI tab** — chat with LM Studio; smart context injection; persistent localStorage memory (50-msg cap, clear button)
5. **Timeline tab** — gantt chart of today's app activity by hour; click block → detail panel
6. **Anomalies tab** (v0.2) — behavioral anomaly detection vs N-day rolling baseline; requires context-server.py
7. **Sidebar**: Refresh, Auto-refresh, Export JSON, Today's Summary, Export Context Snapshot, Stop screenpipe

## AI context system (important)
Always-on smart search. For every question:
1. Extract keywords (strip stop words, up to 6)
2. `GET /search?limit=40` + `GET /search?q={kw}&limit=10` per keyword in parallel
3. Deduplicate by frame_id/timestamp
4. Score: `(keyword_matches × 3) + recency_score` — recency decays to 0 at 24h
5. Top 20 fed as formatted context block into LLM prompt

## launch.command startup sequence
1. `cleanup_old_files()` — deletes `~/.screenpipe/data/` files older than `CLEANUP_DAYS` (default 7)
2. Check/start screenpipe on :3030
3. Check/start `context-server.py` on :3031 as subprocess
4. Check LM Studio on :1234 (non-blocking warning)
5. Open dashboard in browser
6. Keep-alive loop: `[screenpipe: up] [context-api: up] [LM Studio: up]`

## Known gotchas
- Timestamps: strip timezone for naive comparison — `replace('Z', '+00:00').replace('+00:00', '')`
- `datetime.utcnow()` deprecated in Python 3.12+ — use `datetime.now(timezone.utc).replace(tzinfo=None)`
- localStorage persistence: use `_suppressPersist = true` during `loadPersistedChat()` to prevent exponential duplication
- Tab indices: 0=feed, 1=search, 2=raw, 3=AI, 4=timeline — `dailySummary()` hardcodes index 3
- `SO_REUSEADDR` must be set as class attribute before `HTTPServer.__init__` binds the socket

## Screenpipe binary location
```
/Users/ish/bin/screenpipe
```

## How to start everything manually
1. Run `/Users/ish/bin/screenpipe` in Terminal
2. Run `python3 context-server.py` in Terminal
3. Open LM Studio → Local Server → enable CORS → load a model → Start Server
4. Open `screenpipe-dashboard.html` in Chrome

## Next things to build (v0.3) — see PLAN.md for full detail
- Browser extension captures wired into `/context` ranking (dwell time + selection bonuses)
- Hybrid context scoring: semantic similarity blended with keyword score
- Auto-start semantic indexer from `launch.command`
- Claude + OpenAI API backends in `demo_agent.py` via `--api` flag
- MCP server (`mcp_server.py`) for native Claude Desktop / Cursor integration
- `/profile` and `/context-card` API endpoints
- Dashboard: browser captures tab + semantic search mode toggle

