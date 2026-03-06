# Augur — Product Document

# CURRENT VERSION: v0.2 (v0.3 in planning — see PLAN.md)

---

## What This Is

Augur is a **personal context layer for AI agents** — built on top of [screenpipe](https://github.com/mediar-ai/screenpipe), an open-source tool that continuously captures and OCRs your screen and audio.

**v0.2 shipped five things:**

1. **A Context API** (`context-server.py`) — a local HTTP server any AI agent can query to get ranked, relevant screen context. This is the actual product.
2. **A Dashboard** (`screenpipe-dashboard.html`) — a browser UI for exploring captured data, with AI chat, search, SQL access, timeline, anomaly detection, and more. This is the research/demo surface.
3. **Semantic search** (`semantic_search.py`) — Chroma vector store + sentence-transformers embeddings. Indexes screenpipe captures for meaning-based retrieval beyond keyword matching.
4. **Browser extension** (`extension/`) — MV3 Chrome extension that captures URL, title, selected text, time on page, and scroll depth, and sends them to the Context API.
5. **Anomaly detection** — Dashboard tab + `/anomalies` API endpoint that surfaces unusual behavioral patterns by comparing today's app usage against a rolling N-day baseline.

Everything runs locally. No data leaves the machine. No API keys required.

---

## The Startup Idea

The core problem: AI agents today are blank-slate. Every conversation starts from zero. They know nothing about who the user is, what they've been doing, or what they care about. Existing memory solutions (Mem0, Letta) require manual input.

The better approach: **passive behavioral capture** that auto-feeds context to agents with zero setup. If an agent can see you've been reading about venture-backed SaaS pricing for the last three hours, it doesn't need you to explain your context — it already knows.

Screenpipe provides the capture layer. Augur builds the retrieval and delivery infrastructure on top of it.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                           User's Mac                             │
│                                                                  │
│  ┌──────────────┐  REST API   ┌────────────────────────────────┐ │
│  │  screenpipe  │ ◄─────────► │  screenpipe-dashboard.html     │ │
│  │  (binary)    │  :3030      │  (browser, file://)            │ │
│  │  SQLite DB   │             └────────────────┬───────────────┘ │
│  └──────┬───────┘                              │ OpenAI API      │
│         │ REST API                    ┌─────────▼───────┐        │
│         │                            │   LM Studio     │        │
│  ┌──────▼───────┐                    │   (any LLM)     │        │
│  │ context-     │  GET /context       │   :1234         │        │
│  │ server.py    │ ◄──────────────────►└─────────────────┘        │
│  │  :3031       │                                                 │
│  └──────────────┘                                                 │
│         ▲                                                         │
│         │ GET /context?q=...                                      │
│  ┌──────┴───────┐                                                 │
│  │ demo_agent   │  (or any AI agent / tool)                       │
│  │ .py          │                                                 │
│  └──────────────┘                                                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
nomenclator/
├── screenpipe-dashboard.html   # Full browser dashboard
├── launch.command              # Double-click launcher (starts everything)
├── context-server.py           # Context API server — the actual product
├── demo_agent.py               # Agent integration demo / investor demo
├── semantic_search.py          # Chroma vector store indexer + semantic query
├── requirements.txt            # pip deps (chromadb, sentence-transformers)
├── test_features.py            # Feature test suite
├── extension/                  # Chrome browser extension (MV3)
│   ├── manifest.json
│   ├── background.js           # Service worker: sends tab data to context API
│   ├── content.js              # Page script: tracks time, scroll, selections
│   ├── popup.html/js/css       # Extension popup UI
├── README.md
└── DOCS/
    ├── PRODUCT.md              # This file
    └── CLAUDE.md               # Project context for AI-assisted dev
```

Supporting binaries / apps (not in this repo):
```
~/bin/screenpipe                       # screenpipe binary
LM Studio.app                          # GUI app for running local LLMs
~/.screenpipe/data/                    # Raw video/audio (~1–2 GB/day, auto-cleaned)
~/.screenpipe/db.sqlite                # SQLite database with OCR + audio records
~/.screenpipe/launcher.log             # Log file from launch.command
~/.screenpipe/browser_captures.json   # Browser extension captures (max 1000)
~/.screenpipe/augur_semantic_db/       # Chroma persistent vector store
```

---

## Tech Stack

### screenpipe
Runs as a background process; exposes a REST API at `http://localhost:3030`.

**Key endpoints:**
| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Status, last frame timestamp, device info |
| `/search?q=QUERY&limit=N` | GET | Full-text search across all captures |
| `/search?limit=N&offset=0` | GET | Recent captures (no query = all) |
| `/raw_sql` | POST | Direct SQLite query (`{ query: "SELECT ..." }`) |

**OCR frame shape:**
```json
{
  "type": "OCR",
  "content": {
    "frame_id": 12345,
    "timestamp": "2026-03-05T17:13:00Z",
    "app_name": "Google Chrome",
    "window_name": "Hacker News - Google Chrome",
    "browser_url": "https://news.ycombinator.com",
    "text": "...full OCR text..."
  }
}
```

**Audio frame shape:**
```json
{
  "type": "Audio",
  "content": {
    "timestamp": "2026-03-05T17:13:00Z",
    "device_name": "MacBook Pro Microphone (input)",
    "transcription": "...Whisper transcription..."
  }
}
```

### Context API (`context-server.py`)
Python stdlib HTTP server running at `http://localhost:3031`.

**Endpoints:**
| Endpoint | Description |
|---|---|
| `GET /health` | Returns `{ status, screenpipe, port }` |
| `GET /context?q=...&limit=N&window_hours=H` | Keyword-ranked context for a query (+ browser captures in v0.3) |
| `GET /semantic?q=...&limit=N` | Semantic (embedding-based) context search |
| `GET /summary?date=YYYY-MM-DD` | Structured daily summary JSON |
| `GET /anomalies?days=N` | Behavioral anomaly report vs N-day baseline |
| `GET /browser-captures?limit=N` | Recent browser extension captures |
| `POST /browser-capture` | Receive a capture from browser extension |
| `GET /profile?days=N` | Rich behavioral profile over N days (v0.3) |
| `GET /context-card?days=N` | Ultra-compact profile string for LLM injection (v0.3) |

**Context response shape (v0.3):**
```json
{
  "query": "what was I working on",
  "keywords": ["working"],
  "total_candidates": 47,
  "browser_captures_included": 3,
  "semantic_enhanced": true,
  "results": [
    {
      "frame_id": "...",
      "timestamp": "2026-03-05T14:23:00Z",
      "app": "VS Code",
      "window": "nomenclator/screenpipe-dashboard.html",
      "text": "function sendAI() {",
      "url": null,
      "score": 4.2,
      "source": "ocr"
    },
    {
      "frame_id": "browser_98765432",
      "timestamp": "2026-03-05T14:20:00Z",
      "app": "Browser",
      "window": "Augur Context Layer - GitHub",
      "text": "personal context layer for AI agents",
      "url": "https://github.com/ish-cs/nomenclator",
      "score": 3.7,
      "source": "browser"
    }
  ],
  "generated_at": "2026-03-05T14:25:00Z"
}
```

**Scoring algorithm (v0.3 hybrid):**
```
score = (keyword_match_count × 3) + recency_score + semantic_bonus + [time_bonus + selection_bonus for browser]
```
- `keyword_match_count`: substring hits across text + app + window + url, capped at 5 per keyword
- `recency_score`: `max(0, 1 - age_seconds / window_hours_in_seconds)` — decays to 0 at the window boundary
- `semantic_bonus`: `cosine_similarity × 2.0` — only when Chroma index is populated (otherwise 0)
- `time_bonus` (browser only): `min(time_on_page_s / 300, 1.0)` — up to 1.0 for 5+ minutes dwell time
- `selection_bonus` (browser only): `+2.0` if user selected text on that page (strong intent signal)

**CORS:** all responses include `Access-Control-Allow-Origin: *`.

### LM Studio
Desktop app for running local LLMs with an OpenAI-compatible server.
- Runs at `http://127.0.0.1:1234`
- Model is auto-detected from `GET /v1/models` — nothing hardcoded
- Context length should be set to 8192+ in LM Studio settings
- System prompt merged into first user message for universal model compatibility

### Dashboard Frontend
- Single HTML file, no framework, no build system
- Vanilla JavaScript + CSS
- Fonts: JetBrains Mono + Syne (Google Fonts)
- Opens directly via `file://` protocol

---

## UI Design System

### Visual Language
- **Aesthetic:** Dark terminal / hacker dashboard
- **Background:** `#0a0a0a`
- **Surfaces:** `#111111`
- **Borders:** `#1e1e1e` / `#2a2a2a` (bright)
- **Text:** `#e8e8e8` / `#555` (dim) / `#333` (dimmer)
- **Accent green:** `#00ff87` — active states, live indicators, primary actions
- **Red:** `#ff3b3b` — errors, offline
- **Amber:** `#ffb800` — warnings, storage
- **Blue:** `#4d9fff` — URLs, OCR badge

### Layout
Two-column grid: 280px sidebar + fluid main content. Full viewport height minus sticky header.

---

## Dashboard Features (v0.1)

### Header
- Logo with pulsing green dot
- Status pill: `● RECORDING` / `● offline` (polls `/health` every 5s)
- Last capture timestamp ("last capture Xs ago")

### Sidebar

#### Controls
1. **Refresh feed** — re-fetches the live feed
2. **Auto-refresh ON/OFF** — 5-second poll interval
3. **Export JSON** — downloads 100 most recent records
4. **Today's Summary** — one-click AI daily digest
5. **Export Context Snapshot** — downloads 7-day behavioral profile JSON (new in v0.1)
6. **Stop screenpipe** — advisory dialog with stop instructions

#### Storage
- Frame + audio chunk counts, estimated DB size, 7-day history
- Progress bar: green → amber at 5 GB → red at 10 GB
- Cleanup controls: delete DB records older than 7 / 14 / 30 days
- Raw file cleanup handled automatically by `launch.command` at startup

### Tab 1: Live Feed
20 most recent captures as cards. Each card: type badge, app/window, timestamp, OCR text (expandable), URL.

### Tab 2: Search Results
Full-text search. Matched terms highlighted in green. Result count shown.

### Tab 3: Raw SQL
Direct SQLite queries. Results as auto-detected table. Two preset shortcuts (top apps, recent frames).

### Tab 4: Ask AI ✦
Chat interface with automatic context injection. See AI Features section below.

### Tab 6: Anomalies (new in v0.2)
Behavioral anomaly detection. Compares today's per-app frame counts against a rolling N-day baseline (default 7). Flags:
- **▲ More than usual** — app usage ≥2x daily average (minimum 20 frames)
- **▼ Less than usual** — app usage ≤0.3x daily average
- **★ New app** — app not seen in the baseline window
Configurable window (7/14/30 days). Refresh on demand. Requires context-server.py running.

### Tab 5: Timeline (new in v0.1)
Gantt-style chart of today's app activity:
- Y-axis: apps sorted by total frame count
- X-axis: hours 00–23
- Blocks: color-coded by app (consistent hash), opacity = activity density
- Click any block → shows sample captures from that app/hour in a detail panel

---

## AI Features

### Smart Context System
Every question goes through four phases:

**1 — Keyword extraction:** Question lowercased, stop words removed, up to 6 meaningful words extracted.

**2 — Context assembly:**
- `GET /search?limit=40` — most recent captures (always included)
- `GET /search?q={kw}&limit=10` — per-keyword targeted search, run in parallel

**3 — Scoring and ranking:**
```
score = (keyword_match_count × 3) + recency_score
```
Top 20 taken, sorted by score descending.

**4 — Prompt construction:**
Captures formatted as `[HH:MM:SS] [App / Window]\n<150 chars of text>`, appended to system prompt.

System prompt merged into first user message (not a `system` role) for universal model compatibility.

### Chat Interface
- Message bubbles: user right-aligned (green tint), AI left-aligned (dark surface)
- Typing indicator (three-dot bounce)
- `Enter` to send, `Shift+Enter` for newline
- Chat history: last 8 messages passed as multi-turn context
- **Persistent chat memory** (new in v0.1): history saved to `localStorage`, survives page refresh (capped at 50 messages)
- **Clear button** (new in v0.1): wipes history from memory and storage

### Today's Summary
One-click structured daily digest. Queries SQL for today's frames + app breakdown, sends to LLM with a structured prompt. Outputs: Main Activities, Apps & Tools Used, Topics & Content, Action Items Spotted.

### Context Snapshot Export (new in v0.1)
"◈ Export Context Snapshot" button in sidebar. Aggregates last 7 days of behavioral data:
```json
{
  "generated_at": "...",
  "window_days": 7,
  "profile": {
    "top_apps": [{"app": "VS Code", "hours": 12.3}],
    "active_hours": [14, 15, 16, 10],
    "topics": ["typescript", "screenpipe", "dashboard"],
    "urls_visited": 47,
    "audio_minutes": 23
  }
}
```
Downloads as `context-snapshot-YYYY-MM-DD.json`.

---

## Context API (`context-server.py`)

The standalone product. Any AI agent or tool can call it:

```bash
# Start the server
python3 context-server.py

# Query it from any agent
curl "http://localhost:3031/context?q=what+was+I+working+on&limit=15"
curl "http://localhost:3031/summary?date=2026-03-05"
curl "http://localhost:3031/health"
```

**Parameters:**
- `q` (required): natural language query
- `limit` (optional, default 15): max results to return
- `window_hours` (optional, default 24): recency window for scoring

The server uses `SO_REUSEADDR` (via `ReuseHTTPServer` subclass) so it can be killed and restarted without waiting for TIME_WAIT.

---

## Agent Integration Demo (`demo_agent.py`)

Single-query mode:
```bash
python demo_agent.py "what have I been working on today?"
```
Flow: calls `/context`, formats results, calls LM Studio, prints answer.

Watch mode:
```bash
python demo_agent.py --watch
```
Polls every 30 seconds. When activity changes (detected by frame ID fingerprint), auto-summarizes what changed. Designed as an investor demo showing agents being proactively fed context.

---

## Launch Script (`launch.command`)

Double-click in Finder or run in Terminal. Steps:

1. **Cleanup:** deletes raw screenpipe files in `~/.screenpipe/data/` older than `CLEANUP_DAYS` (default: 7). Prints freed MB.
2. **screenpipe:** checks if running on :3030; starts it if not, polls up to 15s for startup
3. **Context API:** checks if running on :3031; starts `context-server.py` as a background subprocess if not
4. **LM Studio:** checks :1234, warns if offline (non-blocking)
5. **Dashboard:** checks HTML file exists, opens in browser
6. **Keep-alive:** prints `[screenpipe: up] [context-api: up] [LM Studio: up]` every 10 seconds

**Config knobs at top of file:**
```python
CLEANUP_DAYS = 7   # delete raw files older than this
```

---

## Data Model

**`frames`**
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | Primary key |
| `timestamp` | TEXT | ISO 8601 |
| `app_name` | TEXT | Frontmost app |
| `window_name` | TEXT | Window title |
| `browser_url` | TEXT | URL if browser active |
| `text` | TEXT | Full OCR text |

**`audio_transcriptions`**
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | Primary key |
| `timestamp` | TEXT | ISO 8601 |
| `device_name` | TEXT | Microphone name |
| `transcription` | TEXT | Whisper output |

---

## Known Limitations

### Search Quality
screenpipe's `/search` is full-text string matching. No semantic understanding. The relevance scoring in Augur improves ordering but not recall.

### AI Context Window
LM Studio context length must be set to 8192+ for the AI features to work reliably. The dashboard surfaces the fix when a 400 is returned.

### Model Compatibility
System prompt always merged into the first user message. Later turns in a multi-turn conversation don't re-inject context.

### Stop Button
The browser cannot kill OS processes. The stop button explains how to stop screenpipe via Terminal.

### Raw File Cleanup
`launch.command` cleans `~/.screenpipe/data/` at startup. The dashboard can only delete SQLite records (not raw files) via the cleanup controls.

### No Real-Time Streaming
The live feed polls every 5 seconds. The "LIVE" badge is cosmetic.

---

## Roadmap

### v0.1 (shipped)
- [x] Context API server (`context-server.py`, port 3031)
- [x] Agent integration demo (`demo_agent.py`)
- [x] Timeline tab (gantt chart of daily app activity)
- [x] Persistent chat memory (localStorage, 50-message cap)
- [x] Auto-cleanup of raw files at launch
- [x] Context snapshot export (7-day behavioral profile JSON)

### v0.2 (shipped)
- [x] Semantic search — Chroma vector store + `all-MiniLM-L6-v2` embeddings (`semantic_search.py`, `/semantic` endpoint)
- [x] Browser extension — MV3 Chrome extension (`extension/`), captures URL + title + selected text + time on page + scroll depth
- [x] Anomaly detection — dashboard tab + `/anomalies` API, compares today vs rolling N-day baseline

### v0.3 (planned — see PLAN.md)
- [ ] Browser extension captures wired into `/context` ranking (dwell time + selection bonus scoring)
- [ ] Hybrid context scoring — semantic similarity blended with keyword score when Chroma index is populated
- [ ] Auto-start semantic indexer from `launch.command` as a 4th managed service
- [ ] Claude + OpenAI API backends in `demo_agent.py` alongside LM Studio (`--api claude|openai`)
- [ ] MCP server (`mcp_server.py`) — expose Augur tools natively to Claude Desktop, Cursor, and any MCP agent
- [ ] `/profile` endpoint — rich 7-day behavioral profile (top apps + hours + domains + topics)
- [ ] `/context-card` endpoint — ultra-compact profile string for drop-in LLM system prompt injection
- [ ] Dashboard: Browser Activity tab showing recent extension captures
- [ ] Dashboard: Semantic search mode toggle in the Search tab
