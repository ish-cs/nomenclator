# Screenpipe Dashboard

A local web dashboard that sits on top of [screenpipe](https://github.com/mediar-ai/screenpipe) — an open-source tool that continuously captures and OCRs your screen and audio. The dashboard gives you a live window into everything being captured, lets you search your full screen history, run raw SQL against the database, and ask an AI questions about what you've been doing.

Everything runs entirely on your machine. No cloud, no API keys, no data leaves your computer.

---

## What It Does

- **Live Feed** — real-time view of OCR captures and audio transcriptions as they come in
- **Search** — full-text search across your entire screen history
- **Raw SQL** — direct queries against the screenpipe SQLite database with preset shortcuts
- **Ask AI** — chat interface that automatically pulls relevant screen captures as context for every question
- **Today's Summary** — one-click AI-generated digest of your day: activities, apps used, topics, action items
- **Storage Management** — see how much data is accumulating and clean up old records

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Your Mac                           │
│                                                         │
│  ┌──────────────┐    REST API      ┌──────────────────┐ │
│  │  screenpipe  │ ◄──────────────► │  Dashboard HTML  │ │
│  │  (binary)    │  localhost:3030  │  (browser)       │ │
│  │  SQLite DB   │                  │  Vanilla JS      │ │
│  └──────────────┘                  └────────┬─────────┘ │
│                                             │           │
│  ┌──────────────┐   OpenAI-compat API       │           │
│  │  LM Studio   │ ◄─────────────────────────┘           │
│  │  (any LLM)   │  localhost:1234                       │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **macOS** | screenpipe is macOS-only (the dashboard itself is cross-platform HTML) |
| **[screenpipe](https://github.com/mediar-ai/screenpipe)** | The capture backend — install via their instructions |
| **[LM Studio](https://lmstudio.ai)** | For the AI chat features — free desktop app |
| **Python 3** | For the launcher script — pre-installed on macOS |
| **A browser** | Chrome recommended; dashboard opens as a local `file://` page |

---

## Installation

### 1. Install screenpipe

Follow the [official screenpipe installation guide](https://github.com/mediar-ai/screenpipe). Once installed, note the path to the binary — you'll need it in the next step.

The binary is typically at one of:
```
~/bin/screenpipe
/usr/local/bin/screenpipe
```

Verify it works:
```bash
screenpipe --help
```

### 2. Clone this repo

```bash
git clone https://github.com/ish-cs/nomenclator.git
cd nomenclator
```

### 3. Configure the launcher script

Open `launch.command` in a text editor and update the `SCREENPIPE_BIN` path at the top to match where your screenpipe binary lives:

```python
SCREENPIPE_BIN = os.path.expanduser("~/bin/screenpipe")
```

Change `~/bin/screenpipe` to the actual path on your machine.

### 4. Make the launcher executable

```bash
chmod +x launch.command
```

### 5. Install LM Studio

Download [LM Studio](https://lmstudio.ai) and install it. Then:

1. Open LM Studio
2. Go to the **Local Server** tab (left sidebar)
3. Load any LLM you want to use (7B–20B models work well; larger = better quality but slower)
4. In server **Settings**, set **Context Length** to `8192` or higher
5. Enable **CORS** in settings
6. Click **Start Server**

The dashboard automatically detects whichever model you have loaded — nothing is hardcoded.

---

## Running the Dashboard

### Option A: Double-click launcher (recommended)

Double-click `launch.command` in Finder.

It will:
1. Check if screenpipe is already running, and start it if not
2. Wait up to 15 seconds for screenpipe to come up
3. Check LM Studio (warns if offline but doesn't block)
4. Open `screenpipe-dashboard.html` in your browser automatically
5. Stay alive in the terminal, printing live status every 10 seconds

Press `Ctrl+C` to close the launcher window. Screenpipe will keep running in the background.

### Option B: Manual startup

**Terminal 1** — start screenpipe:
```bash
~/bin/screenpipe
```

**LM Studio** — start the local server (see setup above).

**Browser** — open `screenpipe-dashboard.html` directly:
```bash
open screenpipe-dashboard.html
```

### Stopping screenpipe

```bash
pkill screenpipe
```

---

## Using the Dashboard

### Live Feed

The default view. Shows the 20 most recent screen captures and audio transcriptions as cards. Each card displays:
- The source app and window title
- A timestamp
- The OCR text (clamped — click **expand** to see the full content)
- The browser URL, if a browser was active

Use **Auto-refresh** in the sidebar to poll for new captures every 5 seconds. Use **Refresh feed** to manually reload.

### Search

Type anything in the search bar and press `Enter` or click **SEARCH →**. Searches across the full text of every OCR frame screenpipe has ever captured. Matched terms are highlighted in results.

Use the limit selector (10 / 20 / 50 / 100) to control how many results come back.

### Raw SQL

Direct access to the screenpipe SQLite database. Type any query and press `Enter` or click **RUN →**.

Quick-access presets:
- **Top apps by screen time** — `SELECT app_name, window_name, COUNT(*) as count FROM frames GROUP BY app_name ORDER BY count DESC LIMIT 20`
- **Recent frames** — `SELECT * FROM frames ORDER BY timestamp DESC LIMIT 10`

Useful tables:
- `frames` — every OCR capture (`app_name`, `window_name`, `browser_url`, `text`, `timestamp`)
- `audio_transcriptions` — every audio transcription (`device_name`, `transcription`, `timestamp`)

### Ask AI

Chat with a local LLM that has automatic access to your screen history as context.

Every question triggers a smart context pipeline:
1. Keywords are extracted from your question (stop words removed)
2. screenpipe is searched for each keyword in parallel
3. The 20 most recent captures are always included
4. All results are merged, deduplicated, and **ranked by relevance** (keyword matches weighted 3x over recency)
5. The top 20 captures are injected as context into the prompt

The model badge in the top-right of the AI tab shows which model is currently loaded in LM Studio. It updates automatically — switch models in LM Studio and the dashboard picks it up on the next page load.

**Suggested questions to get started:**
- What have I been working on today?
- What apps did I use most?
- Summarize my recent browsing
- Any todos or action items on screen?

### Today's Summary

Click **◈ Today's Summary** in the sidebar to generate a structured AI digest of your day. The dashboard queries all of today's captures, builds an app-usage breakdown, and asks the model to produce:

- **Main Activities** — what you were primarily working on
- **Apps & Tools Used** — a brief breakdown
- **Topics & Content** — what you were reading, browsing, or researching
- **Action Items Spotted** — todos or follow-ups visible on screen

### Storage Management

The **Storage** section in the sidebar shows:
- Total frame and audio chunk counts
- Estimated database size (~30 KB per frame)
- A per-day breakdown for the last 7 days
- A color-coded progress bar (green → amber at 5 GB → red at 10 GB)

To clean up old records, select a retention window (7 / 14 / 30 days) and click **Clean**. This deletes database records older than the threshold.

> **Note:** The dashboard can only delete SQLite records. Raw video and audio files in `~/.screenpipe/data/` must be deleted manually:
> ```bash
> rm -rf ~/.screenpipe/data/*
> ```
> screenpipe generates roughly 1–2 GB of data per day, so regular cleanup is recommended.

### Export

Click **↓ Export JSON** in the sidebar to download the 100 most recent captures as a JSON file.

---

## Troubleshooting

### "Cannot connect to screenpipe at localhost:3030"

screenpipe isn't running. Either:
- Double-click `launch.command` to start everything, or
- Run `~/bin/screenpipe` in Terminal

### AI chat returns "LM Studio not reachable"

1. Open LM Studio
2. Go to the **Local Server** tab
3. Make sure the server is started (green toggle)
4. Make sure a model is loaded

### AI chat returns a 400 error

Your model's context window is too small for the prompt. In LM Studio:

1. Go to **Local Server → Settings**
2. Set **Context Length** to `8192` or higher
3. Restart the server

### launch.command opens a blank Terminal and closes immediately

The screenpipe binary path in `launch.command` is wrong. Open the file in a text editor and update `SCREENPIPE_BIN` to the correct path.

### screenpipe starts but captures nothing

Make sure you've granted screenpipe the necessary macOS permissions:
- **Screen Recording** — System Settings → Privacy & Security → Screen Recording
- **Microphone** — System Settings → Privacy & Security → Microphone

---

## File Structure

```
nomenclator/
├── screenpipe-dashboard.html   # The entire dashboard (single self-contained file)
├── launch.command              # Double-click launcher script (Python 3)
├── README.md                   # This file
└── DOCS/
    ├── PRODUCT.md              # Full technical product documentation
    └── CLAUDE.md               # Project context for AI-assisted development
```

Files created at runtime (not in this repo):
```
~/.screenpipe/
├── data/                       # Raw video/audio files (~1–2 GB/day)
├── db.sqlite                   # All OCR and audio transcription records
└── launcher.log                # Log output from launch.command
```

---

## Tech Stack

- **screenpipe** — background capture process; OCR via Tesseract, audio via Whisper
- **LM Studio** — local LLM runner with OpenAI-compatible API
- **Dashboard** — single HTML file, vanilla JS + CSS, no build step, no npm, no framework
- **Fonts** — JetBrains Mono + Syne (Google Fonts)
- **Data** — SQLite, accessed via screenpipe's `/raw_sql` REST endpoint

---

## Known Limitations

- **macOS only** — screenpipe is macOS-specific
- **No real-time streaming** — the live feed polls every 5 seconds, not true streaming
- **Session-only chat memory** — refreshing the page resets the conversation
- **DB-only cleanup** — the dashboard cannot delete raw files from `~/.screenpipe/data/`
- **String-match search** — screenpipe's search is exact string matching, not semantic
- **Stop button is advisory** — the browser cannot kill OS processes; the button just explains how to stop screenpipe manually

---

## Roadmap

- [ ] Expose screenpipe context as a local API endpoint for AI agents to query directly
- [ ] Semantic search via local vector embeddings (Chroma)
- [ ] Persistent chat memory via `localStorage`
- [ ] Timeline / gantt view of app usage across the day
- [ ] Auto-cleanup of raw files in `~/.screenpipe/data/`
- [ ] Browser extension for richer URL and selection context

---

## License

MIT
