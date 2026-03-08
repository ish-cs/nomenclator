# Augur

A **personal context layer for AI agents** — built on top of [screenpipe](https://github.com/mediar-ai/screenpipe).

Screenpipe continuously captures and OCRs your screen and audio. Augur sits on top of it and exposes that data as a ranked context feed that any AI agent can query. It also ships a full browser dashboard for exploring, searching, and chatting with your screen history.

Everything runs locally. No cloud, no API keys, no data leaves your machine.

---

## What It Does

### Core: Context API (port 3031)
```bash
curl "http://localhost:3031/context?q=what+was+I+working+on"
```
Returns relevance-ranked screen captures + browser activity for any natural language query. Any AI agent or tool can call this. Results are scored by keyword matches, recency, semantic similarity (if the indexer is running), and browser behavior signals (dwell time, text selection).

### MCP Server
Drop-in integration with Claude Desktop, Cursor, and any MCP-compatible AI agent. Configure once — your AI agent automatically has access to your screen context with no code changes.

### Dashboard
- **Live Feed** — real-time view of OCR captures and audio transcriptions
- **Ask AI** — chat with a local LLM (or Claude/OpenAI) that has automatic access to your screen context
- **Search** — keyword search or semantic search (requires semantic indexer) across your entire screen history
- **Raw SQL** — direct queries against the screenpipe SQLite database
- **Timeline** — gantt-style chart of today's app activity by hour
- **Anomalies** — behavioral anomaly detection vs rolling N-day baseline
- **Browser Activity** — recent browser extension captures: URL, title, dwell time, scroll depth, text selections
- **Context Snapshot** — export a 7-day behavioral profile as structured JSON

---

## Architecture

```
+------------------------------------------------------------------+
|                           Your Mac                               |
|                                                                  |
|  +--------------+  REST API   +--------------------------------+ |
|  |  screenpipe  | <---------> |  screenpipe-dashboard.html     | |
|  |  :3030       |             +----------------+---------------+ |
|  |  SQLite DB   |                              | OpenAI-compat   |
|  +---------+----+                     +--------v--------+        |
|            |                          |   LM Studio     |        |
|            | REST API                 |   (any LLM)     |        |
|  +---------v----+                     |   :1234         |        |
|  | context-     |<-- GET /context ----+-----------------+        |
|  | server.py    |                                                 |
|  |  :3031       |<-- MCP stdio --- mcp_server.py                  |
|  +--------------+                                                 |
|            ^                                                      |
|            | POST /browser-capture                                |
|  +---------+----+                                                 |
|  | Chrome       |  (browser extension)                           |
|  | extension    |                                                 |
|  +--------------+                                                 |
+------------------------------------------------------------------+
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **macOS** | screenpipe is macOS-only |
| **[screenpipe](https://github.com/mediar-ai/screenpipe)** | The capture backend. Install via their guide. |
| **Python 3** | Pre-installed on macOS. Used for `context-server.py`, `launch.command`, `mcp_server.py`. |
| **Google Chrome** | Recommended for the dashboard and browser extension. |
| **[LM Studio](https://lmstudio.ai)** | Optional. For the AI chat feature using a local LLM. |

---

## Installation

### 1. Install screenpipe

Follow the [official guide](https://github.com/mediar-ai/screenpipe). Verify it works:
```bash
screenpipe --help
```
The binary is typically at `~/bin/screenpipe`. If it's elsewhere, update `SCREENPIPE_BIN` in `launch.command`.

### 2. Clone this repo

```bash
git clone https://github.com/ish-cs/nomenclator.git
cd nomenclator
```

### 3. Make the launcher executable

```bash
chmod +x launch.command
```

### 4. Install GUI launcher support (required for the Augur window)

The launcher opens a native GUI window. This requires Python's Tk bindings, which Homebrew Python does not include by default:

```bash
# Find your Python version
python3 --version

# Install matching tkinter support (replace 3.14 with your version)
brew install python-tk@3.14
```

Without this, the launcher falls back to terminal mode and still works — but you won't get the GUI window.

### 6. (Optional) Install semantic search dependencies

For vector-based semantic search, install:
```bash
pip install chromadb sentence-transformers
```

This enables the **Semantic** search mode in the dashboard and improves context ranking quality. Without it, keyword scoring still works fully.

### 7. (Optional) Install cloud LLM backends

For the `demo_agent.py` cloud backends:
```bash
pip install anthropic   # for --api claude
pip install openai      # for --api openai
```

### 8. (Optional) Set up LM Studio

Download [LM Studio](https://lmstudio.ai), then:
1. Go to the **Local Server** tab
2. Load any LLM (7B–20B models work well)
3. Set **Context Length** to `8192` or higher in server settings
4. Enable **CORS**
5. Click **Start Server**

---

## Running

### Option A: Double-click launcher (recommended)

Double-click `launch.command` in Finder. It opens a GUI window titled "Augur" and immediately:
1. Cleans up raw screenpipe files older than 7 days (`~/.screenpipe/data/`)
2. Starts screenpipe if not running (waits up to 15s for startup)
3. Starts the Context API server on port 3031 (background subprocess)
4. Starts the semantic indexer if chromadb + sentence-transformers are installed
5. Checks LM Studio (warns if offline, non-blocking)
6. Opens the dashboard in Chrome automatically

The GUI window shows live status indicators for all 4 services and provides Start/Stop Screenpipe controls. Closing the window does not kill background services — screenpipe and the context server keep running.

### Option B: Manual

**Terminal 1** — screenpipe:
```bash
~/bin/screenpipe
```

**Terminal 2** — Context API:
```bash
python3 context-server.py
```

**Terminal 3** — Semantic indexer (optional):
```bash
python3 semantic_search.py
```

**Browser** — dashboard:
```bash
open screenpipe-dashboard.html
```

---

## Using the Context API

The core product. Any script or agent can call it:

```bash
# Health check
curl http://localhost:3031/health

# Get ranked context for a query
curl "http://localhost:3031/context?q=what+was+I+working+on&limit=10"

# Today's summary
curl "http://localhost:3031/summary?date=2026-03-06"

# Behavioral anomalies (vs 7-day baseline)
curl "http://localhost:3031/anomalies?days=7"

# Semantic search (requires chromadb)
curl "http://localhost:3031/semantic?q=typescript+react&limit=10"

# Recent browser captures
curl "http://localhost:3031/browser-captures?limit=20"

# 7-day behavioral profile
curl "http://localhost:3031/profile?days=7"

# Compact profile string for LLM injection
curl "http://localhost:3031/context-card"
```

### `/context` parameters
| Parameter | Default | Description |
|---|---|---|
| `q` | required | Natural language query |
| `limit` | 15 | Max results to return |
| `window_hours` | 24 | Recency window for scoring |

### Scoring formula
```
score = (keyword_matches x 3) + recency + semantic_bonus + [time_bonus + selection_bonus]
```
- `keyword_matches x 3` — substring hits in text, app, window, url
- `recency` — decays from 1.0 to 0 over the window period
- `semantic_bonus` — cosine similarity x 2.0, only when Chroma index is populated
- `time_bonus` (browser only) — up to 1.0 for 5+ minutes dwell time
- `selection_bonus` (browser only) — +2.0 if text was selected on that page

### Response shape
```json
{
  "query": "what was I working on",
  "results": [
    {
      "frame_id": "12345",
      "timestamp": "2026-03-06T14:23:00Z",
      "app": "VS Code",
      "window": "context-server.py",
      "text": "def gather_context(query, limit, window_hours):",
      "score": 5.4,
      "source": "ocr"
    },
    {
      "frame_id": "browser_98765432",
      "timestamp": "2026-03-06T14:20:00Z",
      "app": "Browser",
      "url": "https://github.com/ish-cs/nomenclator",
      "text": "personal context layer for AI agents",
      "score": 3.7,
      "source": "browser"
    }
  ],
  "browser_captures_included": 1,
  "semantic_enhanced": true
}
```

---

## MCP Server (Claude Desktop / Cursor)

Configure Augur as an MCP server so Claude Desktop or Cursor automatically have access to your screen context.

### 1. Add to `~/.claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "augur": {
      "command": "python3",
      "args": ["/Users/YOUR_NAME/Desktop/nomenclator/mcp_server.py"]
    }
  }
}
```

Replace `/Users/YOUR_NAME/Desktop/nomenclator` with the actual path to this repo.

### 2. Restart Claude Desktop

The following tools appear automatically in Claude Desktop:

| Tool | Description |
|---|---|
| `get_context` | Ranked screen captures for any natural language query |
| `get_daily_summary` | Today's activity breakdown by app and topic |
| `get_anomalies` | Behavioral anomalies vs N-day baseline |
| `get_user_profile` | 7-day behavioral profile (apps, hours, domains, topics) |
| `get_browser_activity` | Recent browser extension captures |

The Context API (`context-server.py`) must be running for MCP tools to return data.

### For Cursor
Add the same configuration to Cursor's MCP settings (Settings -> MCP).

---

## Agent Integration Demo

```bash
# Single query — uses LM Studio by default
python3 demo_agent.py "what have I been working on today?"

# Use Claude API (requires ANTHROPIC_API_KEY env var)
python3 demo_agent.py "what have I been working on?" --api claude

# Use OpenAI API (requires OPENAI_API_KEY env var)
python3 demo_agent.py "what have I been working on?" --api openai

# Watch mode — polls every 30s, summarizes activity changes
python3 demo_agent.py --watch
python3 demo_agent.py --watch --api claude
```

The demo calls the Context API, injects ranked screen context, then calls the chosen LLM backend. Watch mode shows agents being proactively fed context as your activity changes.

**Setup for cloud backends:**
```bash
pip install anthropic        # for --api claude
pip install openai           # for --api openai
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-4o  # optional, default: gpt-4o
```

---

## Browser Extension

### Install
1. Open Chrome and go to `chrome://extensions/`
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked** and select the `extension/` folder in this repo
4. The Augur icon appears in your toolbar

### What it captures
For every page you visit, the extension sends to the Context API:
- Page URL and title
- Time spent on the page (seconds)
- Scroll depth (percentage)
- Any text you selected/highlighted

This data appears in the **Browser Activity** dashboard tab and is blended into `/context` ranking results.

### Requirements
The extension POSTs to `http://localhost:3031/browser-capture` — the Context API must be running.

---

## Running the Tests

```bash
# Offline tests (no server needed) — verifies all v0.3 features
python3 test_features.py

# Live tests (requires context-server.py running at :3031)
python3 test_features.py --live
```

---

## File Structure

```
nomenclator/
+-- screenpipe-dashboard.html   # Full browser dashboard (single self-contained HTML file)
+-- launch.command              # Double-click launcher — opens Augur GUI app with service status + start/stop controls
+-- context-server.py           # Context API server (port 3031) — the core product
+-- demo_agent.py               # Agent integration demo (LM Studio, Claude, OpenAI)
+-- semantic_search.py          # Chroma vector store indexer + semantic query engine
+-- mcp_server.py               # MCP server for Claude Desktop / Cursor
+-- requirements.txt            # pip deps (chromadb, sentence-transformers)
+-- test_features.py            # Feature test suite (offline + --live modes)
+-- extension/                  # Chrome browser extension (MV3)
|   +-- manifest.json           # Extension manifest (permissions, service worker)
|   +-- background.js           # Service worker: captures tab events, sends to API
|   +-- content.js              # Page script: tracks time, scroll depth, selections
|   +-- popup.html/js/css       # Extension popup UI
+-- README.md
+-- DOCS/
    +-- PRODUCT.md              # Full technical product documentation
    +-- CLAUDE.md               # Project context for AI-assisted development
```

**Runtime files (auto-created, not in repo):**
```
~/bin/screenpipe                        # screenpipe binary
~/.screenpipe/db.sqlite                 # SQLite database (OCR + audio records)
~/.screenpipe/data/                     # Raw video/audio files (auto-cleaned, ~1-2 GB/day)
~/.screenpipe/browser_captures.json    # Browser extension captures (max 1000 entries)
~/.screenpipe/augur_semantic_db/        # Chroma persistent vector store
~/.screenpipe/semantic_indexer.pid      # PID file for semantic indexer process
~/.screenpipe/launcher.log              # Log from launch.command
```

---

## Dashboard Features

### Live Feed
The most recent 20 captures as expandable cards. Each card shows: source type (OCR/Audio), app name, window title, timestamp, URL (if browser), and the captured text. Click to expand the full text.

### Search
Type in the search bar and press Enter. Two modes:

- **Keyword** (default) — searches screenpipe's full-text index. Fast, exact match. Results highlighted in green.
- **Semantic** — calls the Context API's hybrid scorer. Results ranked by meaning, not just keyword. Requires the semantic indexer to be running (install: `pip install chromadb sentence-transformers`).

### Raw SQL
Write any SQL query directly against the screenpipe SQLite database and see results in a table. Two preset buttons for common queries.

### Ask AI
Every question automatically pulls relevant screen captures as context:
1. Keywords extracted from the question (stop words removed)
2. screenpipe searched per keyword in parallel
3. Results merged, deduplicated, ranked by relevance
4. Top captures injected into the LLM prompt

Chat history persists across page refreshes (up to 50 messages, stored in `localStorage`). Use the "Clear" button to reset.

### Timeline
Gantt chart of today's app activity. Apps on Y-axis, hours 00–23 on X-axis. Block density = how active that app was in that hour. Click any block to see sample captures from that app and hour.

### Anomalies
Compares today's per-app frame counts against a rolling N-day baseline (default: 7 days). Flags:
- **More than usual** — app usage >= 2x daily average
- **Less than usual** — app usage <= 0.3x daily average
- **New app** — not seen in the baseline window

Configurable window (7 / 14 / 30 days). Refresh on demand.

### Browser Activity
Shows recent browser extension captures: URL, page title, time spent, scroll depth, and any text you selected. Click "Refresh" to reload. Install the extension first — see [Browser Extension](#browser-extension).

### Storage Management
The sidebar **Storage** section shows estimated DB size, per-day frame counts, and cleanup controls. Delete old records directly from the dashboard. Raw files in `~/.screenpipe/data/` are automatically pruned by `launch.command` at startup (configurable `CLEANUP_DAYS`, default 7 days).

---

## Troubleshooting

### "Cannot connect to screenpipe at localhost:3030"
screenpipe isn't running. Double-click `launch.command`, or run `~/bin/screenpipe` in Terminal.

### Context API returns empty results
screenpipe isn't running or has no data yet. Check: `curl http://localhost:3030/health`

### AI chat: "LM Studio not reachable"
Open LM Studio → Local Server tab → load a model → start the server.

### AI chat returns a 400 error
Model context window too small. In LM Studio → Local Server → Settings → set **Context Length** to `8192` or higher.

### Semantic search returns no results / "indexer not running"
Install deps: `pip install chromadb sentence-transformers`
Then either restart `launch.command` (it auto-starts the indexer) or run `python3 semantic_search.py` manually. The indexer needs time to index existing captures on first run.

### Browser extension not sending data
Make sure the Context API is running (`curl http://localhost:3031/health`). Check `chrome://extensions/` → Augur → "Errors" for any permission issues.

### MCP tools not showing in Claude Desktop
1. Verify the path in `claude_desktop_config.json` is correct and absolute
2. Verify `context-server.py` is running
3. Restart Claude Desktop after any config changes

### launch.command closes immediately
The screenpipe binary path is wrong. Open `launch.command` in a text editor and update `SCREENPIPE_BIN` to match your actual screenpipe location.

### screenpipe captures nothing
Grant permissions in System Settings → Privacy & Security:
- **Screen Recording** — required for OCR
- **Microphone** — required for audio transcription

---

## Roadmap

### v0.1 (shipped)
- [x] Context API server (port 3031) — any agent can call it
- [x] Agent integration demo (single-query and watch modes)
- [x] Timeline tab (gantt chart of daily app activity)
- [x] Persistent chat memory (localStorage, 50-message cap)
- [x] Auto-cleanup of raw files at launch
- [x] Context snapshot export (7-day behavioral profile JSON)

### v0.2 (shipped)
- [x] Semantic search via local vector embeddings (Chroma + `all-MiniLM-L6-v2`)
- [x] Browser extension (MV3) — URL, title, dwell time, scroll depth, text selection
- [x] Anomaly detection — dashboard tab + `/anomalies` API

### v0.3 (shipped)
- [x] Browser captures merged into `/context` ranking (dwell time + selection scoring)
- [x] Hybrid scoring — semantic + keyword blended transparently
- [x] Semantic indexer auto-started from `launch.command`
- [x] Claude + OpenAI API backends in `demo_agent.py` (`--api claude|openai`)
- [x] MCP server — native integration with Claude Desktop, Cursor, and MCP-compatible agents
- [x] `/profile` + `/context-card` endpoints
- [x] Dashboard: Browser Activity tab + semantic search mode toggle

### v0.3.1 (shipped)
- [x] GUI launcher — double-clicking `launch.command` opens a native window with service status + Start/Stop controls (no more terminal window)
- [x] Fixed context window overflow — AI chat no longer errors with large screenpipe datasets
- [x] Full branding pass — app UI renamed from "screenpipe" to "Augur" throughout
- [x] Sidebar cleanup — removed Stop Screenpipe and Today's Summary buttons; auto-refresh ON by default
- [x] Ask AI UX — fixed-height scrollable chat; input always visible; tab moved next to Live Feed
- [x] Browser Captures tab fixed — loads and displays all captured fields

---

## License

MIT
