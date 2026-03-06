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
Returns relevance-ranked screen captures for any natural language query. Any AI agent or tool can call this.

### Dashboard
- **Live Feed** — real-time view of OCR captures and audio transcriptions
- **Search** — full-text search across your entire screen history
- **Raw SQL** — direct queries against the screenpipe SQLite database
- **Ask AI** — chat with a local LLM that has automatic access to your screen context
- **Timeline** — gantt-style chart of today's app activity by hour
- **Today's Summary** — one-click AI-generated digest: activities, apps, topics, action items
- **Context Snapshot** — export a 7-day behavioral profile as structured JSON
- **Storage Management** — view DB size, clean up old records

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                           Your Mac                               │
│                                                                  │
│  ┌──────────────┐  REST API   ┌────────────────────────────────┐ │
│  │  screenpipe  │ ◄─────────► │  screenpipe-dashboard.html     │ │
│  │  :3030       │             └────────────────┬───────────────┘ │
│  │  SQLite DB   │                              │ OpenAI API      │
│  └──────┬───────┘                     ┌────────▼────────┐        │
│         │                             │   LM Studio     │        │
│         │ REST API                    │   (any LLM)     │        │
│  ┌──────▼───────┐                     │   :1234         │        │
│  │ context-     │◄─── GET /context ───┴─────────────────┘        │
│  │ server.py    │     from agents                                 │
│  │  :3031       │                                                 │
│  └──────────────┘                                                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **macOS** | screenpipe is macOS-only |
| **[screenpipe](https://github.com/mediar-ai/screenpipe)** | The capture backend |
| **Python 3** | Pre-installed on macOS. For `context-server.py` and `launch.command` |
| **[LM Studio](https://lmstudio.ai)** | For the AI chat features (optional) |
| **A browser** | Chrome recommended |

---

## Installation

### 1. Install screenpipe

Follow the [official guide](https://github.com/mediar-ai/screenpipe). The binary is typically at `~/bin/screenpipe` or `/usr/local/bin/screenpipe`.

```bash
screenpipe --help   # verify it works
```

### 2. Clone this repo

```bash
git clone https://github.com/ish-cs/nomenclator.git
cd nomenclator
```

### 3. Configure the launcher

Open `launch.command` and set `SCREENPIPE_BIN` to your screenpipe binary path:

```python
SCREENPIPE_BIN = os.path.expanduser("~/bin/screenpipe")
```

### 4. Make the launcher executable

```bash
chmod +x launch.command
```

### 5. (Optional) Set up LM Studio

Download [LM Studio](https://lmstudio.ai), then:
1. Go to the **Local Server** tab
2. Load any LLM (7B–20B models work well)
3. Set **Context Length** to `8192` or higher in server settings
4. Enable **CORS**
5. Click **Start Server**

---

## Running

### Option A: Double-click launcher (recommended)

Double-click `launch.command` in Finder. It:
1. Cleans up raw screenpipe files older than 7 days
2. Starts screenpipe if not running (waits up to 15s)
3. Starts the Context API server on port 3031
4. Checks LM Studio (warns if offline, non-blocking)
5. Opens the dashboard in your browser
6. Prints live status every 10s

Press `Ctrl+C` to exit the launcher. screenpipe and the context server keep running.

### Option B: Manual

**Terminal 1** — screenpipe:
```bash
~/bin/screenpipe
```

**Terminal 2** — Context API:
```bash
python3 context-server.py
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

# Get context for a query
curl "http://localhost:3031/context?q=typescript+react&limit=10"

# Daily summary
curl "http://localhost:3031/summary?date=2026-03-05"
```

**Parameters for `/context`:**
- `q` — natural language query (required)
- `limit` — number of results (default: 15)
- `window_hours` — recency window for scoring (default: 24)

Results are scored by `(keyword_matches × 3) + recency` and returned ranked.

---

## Agent Integration Demo

```bash
# Single query mode
python3 demo_agent.py "what have I been working on today?"

# Watch mode — polls every 30s, summarizes activity changes
python3 demo_agent.py --watch
```

The demo script calls the Context API, injects the results as context, then calls LM Studio to answer. Watch mode shows agents being proactively fed context as your activity changes.

---

## File Structure

```
nomenclator/
├── screenpipe-dashboard.html   # Full browser dashboard (single self-contained file)
├── launch.command              # Double-click launcher — starts everything
├── context-server.py           # Context API server (port 3031) — the core product
├── demo_agent.py               # Agent integration demo / investor demo
├── README.md
└── DOCS/
    ├── PRODUCT.md              # Full technical product documentation
    └── CLAUDE.md               # Project context for AI-assisted development
```

---

## Dashboard Features

### Search
Type in the search bar + `Enter`. Searches all OCR text screenpipe has ever captured. Matched terms highlighted in results.

### Ask AI
Every question automatically pulls relevant screen captures as context:
1. Keywords extracted from the question
2. screenpipe searched per keyword in parallel
3. Results merged, deduplicated, ranked by relevance
4. Top captures injected into the LLM prompt

Chat history persists across page refreshes (up to 50 messages, stored in `localStorage`). Use the "↺ Clear" button to reset.

### Timeline Tab
Visual gantt chart of today's app activity. Apps on Y-axis, hours 00–23 on X-axis. Click any block to see sample captures from that app and hour.

### Context Snapshot
Click **◈ Export Context Snapshot** in the sidebar to download a structured JSON profile of your last 7 days:

```json
{
  "generated_at": "...",
  "window_days": 7,
  "profile": {
    "top_apps": [{"app": "VS Code", "hours": 12.3}],
    "active_hours": [14, 15, 16, 10],
    "topics": ["typescript", "screenpipe", "react"],
    "urls_visited": 47,
    "audio_minutes": 23
  }
}
```

### Storage Management
The **Storage** section in the sidebar shows DB size, per-day breakdowns, and a cleanup control. Raw files in `~/.screenpipe/data/` are automatically pruned by `launch.command` at startup (configurable `CLEANUP_DAYS`, default 7).

---

## Troubleshooting

### "Cannot connect to screenpipe at localhost:3030"
screenpipe isn't running. Double-click `launch.command`, or run `~/bin/screenpipe` in Terminal.

### Context API returns empty results
screenpipe isn't running or has no data yet. Check `curl http://localhost:3030/health`.

### AI chat: "LM Studio not reachable"
Open LM Studio → Local Server tab → start the server with a model loaded.

### AI chat returns a 400 error
Model context window too small. In LM Studio → Local Server → Settings → set **Context Length** to `8192` or higher.

### launch.command closes immediately
The screenpipe binary path is wrong. Edit `SCREENPIPE_BIN` in `launch.command`.

### screenpipe captures nothing
Grant permissions in System Settings → Privacy & Security:
- **Screen Recording**
- **Microphone**

---

## Known Limitations

- **macOS only** — screenpipe is macOS-specific
- **String-match search** — screenpipe search is exact match, not semantic
- **No real-time streaming** — live feed polls every 5 seconds
- **AI context window** — LM Studio must be configured to 8192+ context length
- **Stop button is advisory** — the browser cannot kill OS processes

---

## Roadmap

### v0.1 (shipped)
- [x] Context API server (port 3031) — any agent can call it
- [x] Agent integration demo with single-query and watch modes
- [x] Timeline tab (gantt chart of daily app activity)
- [x] Persistent chat memory (localStorage, 50-message cap)
- [x] Auto-cleanup of raw files at launch
- [x] Context snapshot export (7-day behavioral profile JSON)

### v0.2 (next)
- [ ] Semantic search via local vector embeddings (Chroma)
- [ ] Browser extension for richer URL and selection context
- [ ] Anomaly detection

---

## License

MIT
