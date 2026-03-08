# Augur v0.3.1 — Implementation Plan

**Status:** Planning
**Version:** v0.3.1
**Branch:** pandey
**Based on:** v0.3 shipped
**Goal:** Polish pass — GUI launcher, UX cleanup, branding consistency, and bug fixes.

---

## Summary of Changes

| # | Goal | Subagent | Primary File(s) |
|---|------|----------|-----------------|
| 1 | Replace terminal launcher with tkinter GUI app | A | `launch.command` |
| 2 | Fix LM Studio context window overflow error | B | `screenpipe-dashboard.html` |
| 3 | Rename "screenpipe" → "Augur" in all app UI | A + B | `launch.command`, `screenpipe-dashboard.html` |
| 4 | Remove Stop/Summary sidebar buttons; auto-refresh ON by default | B | `screenpipe-dashboard.html` |
| 5 | Ask AI: fixed-height scrollable chat, page non-scrollable | B | `screenpipe-dashboard.html` |
| 6 | Move Ask AI tab to immediately right of Live Feed | B | `screenpipe-dashboard.html` |
| 7 | Fix Browser Captures tab (currently not loading data) | B | `screenpipe-dashboard.html` |
| 8 | Update all documentation to v0.3.1 | C | `README.md`, `DOCS/PRODUCT.md`, `DOCS/CLAUDE.md` |

---

## Parallel Subagent Structure

Three subagents run concurrently. Scope is strict: each subagent reads and writes only the files listed under its heading. No exceptions.

---

## Subagent A — Launcher GUI

**Scope:** `launch.command` only
**Goals covered:** 1, 3 (launcher side)

### A1 — Replace terminal window with tkinter GUI

Currently `launch.command` runs a blocking keep-alive loop in the terminal with `print()` statements. Replace this with a `tkinter.Tk()` window.

**Window layout (top to bottom):**
1. Title: `Augur` (large, styled with `font=("Helvetica", 18, "bold")`)
2. Subtitle: `personal context layer` (small, grey)
3. Horizontal rule / separator
4. Status grid — 4 rows, one per service:
   - `screenpipe` — green dot + "running" / red dot + "stopped"
   - `Context API` — same pattern
   - `Semantic Indexer` — same pattern
   - `LM Studio` — same pattern
5. Two action buttons side by side:
   - `Start Screenpipe` — triggers startup of screenpipe only (context-api and semantic indexer already started at launch); grays out while starting; re-enables when screenpipe is up
   - `Stop Screenpipe` — runs `pkill screenpipe`; does not touch other services
6. Scrollable log text area (bottom) — replaces all `print()` terminal output; auto-scrolls to latest line

**Behavior:**
- All existing startup logic runs immediately on launch (same as current): cleanup old files, start screenpipe, start context-server.py, start semantic indexer, check LM Studio, open browser
- Log output goes to the text area, not stdout
- Status indicators refresh every 5 seconds via `root.after(5000, poll_status)`
- Window close (`WM_DELETE_WINDOW`): destroy window and exit; screenpipe keeps running in background (no pkill on close)
- Startup must be non-blocking — run the service startup sequence in a `threading.Thread` so the window renders immediately and the log fills in as services start

**Library constraint:** Use only Python stdlib: `tkinter`, `tkinter.scrolledtext`, `threading`, `subprocess`, `time`, `os`, `pathlib`, `urllib.request`, `urllib.error`, `datetime`

### A2 — Rename launcher branding

- Window title bar: `Augur`
- All log messages that currently say "screenpipe launcher": replace with `Augur`
- The print_header box (now goes to log area): update text to `Augur`

---

## Subagent B — Dashboard

**Scope:** `screenpipe-dashboard.html` only
**Goals covered:** 2, 3 (dashboard side), 4, 5, 6, 7

Read the full file before making changes. All changes are within this single file.

### B1 — Fix LM Studio context window overflow (Goal 2)

The error "Could not generate summary. The prompt exceeded your model's context window" appears when too many screenpipe frames are assembled into a prompt before sending to the LLM.

**Fix locations:**
1. **AI chat context injection** (`buildContext()` or equivalent) — find where captured frames are formatted into a context block and appended to the prompt. After assembling the full text block, hard-cap it at **6000 characters**. If truncated, append `\n[context truncated to fit model context window]`.
2. **Any remaining summary generation functions** — apply the same 6000-character cap on text sent to the LLM.
3. **Error surface** — where the 400/context error is caught, show a clear user-facing message: `"Could not generate a response — the context was too large. This should be fixed automatically. If it persists, increase Context Length to 8192+ in LM Studio → Local Server settings."` Do not expose the raw API error object.

Do not change the number of context frames fetched — only cap the final assembled text before it goes into the prompt.

### B2 — Rename "screenpipe" → "Augur" in dashboard UI (Goal 3)

Rename all **visible user-facing text**. Do not rename:
- Internal JS variable/function names (too risky to break logic)
- API endpoint paths (`localhost:3030`, `localhost:3031`)
- HTML element IDs and classes
- Code comments

**What to rename:**
- `<title>` element: → `Augur`
- Header logo / brand text: → `Augur`
- Any sidebar section headings, status messages, or button labels that say "screenpipe"
- Any tab labels or panel headings that reference "screenpipe"
- Toast/notification text referencing "screenpipe" in user-facing strings

### B3 — Sidebar cleanup (Goal 4)

1. **Remove** the `Stop Screenpipe` button from the sidebar entirely (HTML + any JS handler)
2. **Remove** the `Today's Summary` button from the sidebar entirely (HTML + any JS handler + the `dailySummary()` function can be removed too if it has no other callers)
3. **Auto-refresh default:** find where auto-refresh state is initialized (likely a variable like `autoRefresh = false`) and change the default to `true`. Trigger the first auto-refresh immediately on page load.

### B4 — Ask AI: fixed-height scrollable chat (Goal 5)

The Ask AI tab currently causes the whole page to scroll as the chat grows. Fix:

1. The tab content container for Ask AI must use `display: flex; flex-direction: column` with a fixed or `flex: 1` height that fills the viewport minus the header and tab bar
2. The chat messages container (where bubbles appear) must have `flex: 1; overflow-y: auto` — it scrolls internally
3. The input row (textarea + send button) must be `flex-shrink: 0` at the bottom, always visible without page scrolling
4. After each new message is appended, scroll the messages container to its `scrollHeight` (not `window.scrollTo`)
5. The outer page (`body` or main content wrapper) must **not** scroll when the Ask AI tab is active

Approach: use CSS `height: calc(100vh - Npx)` on the tab panel, where N accounts for header + tab bar height, combined with the flex layout described above. Measure the actual pixel heights from the existing layout.

### B5 — Move Ask AI tab button (Goal 6)

**Current tab order (left → right):**
Live Feed | Search Results | Raw SQL | Ask AI | Timeline | Anomalies | Browser

**New tab order:**
Live Feed | Ask AI | Search Results | Raw SQL | Timeline | Anomalies | Browser

Move both:
1. The tab button element in the tab bar
2. The corresponding tab panel in the content area

No other changes — tab switching logic, IDs, and functions stay identical.

### B6 — Fix Browser Captures tab (Goal 7)

The Browser Captures tab currently does not load data. Diagnose and fix.

**Likely causes to check:**
- The fetch call to `http://localhost:3031/browser-captures?limit=50` may have a bug (wrong URL, wrong method, unhandled promise rejection)
- The response JSON may not be parsed correctly
- The render function may expect a different data shape than what the API returns
- The tab may not be triggering a load on first visit

**Fix requirements:**
- On first tab activation, automatically call the load function (same pattern other tabs use)
- Display each capture with all available fields: **URL**, **page title**, **time on page** (formatted as "Xs" or "X min Xs"), **scroll depth** (as percentage), **selected text** (if any, in a highlighted block), **timestamp** (formatted, relative preferred)
- Empty state: `"No browser captures yet. Install the browser extension and visit some pages."`
- API offline state: `"Context API not running — start Augur via launch.command"`
- Add a Refresh button to the tab panel that re-fetches manually

---

## Subagent C — Documentation

**Scope:** `README.md`, `DOCS/PRODUCT.md`, `DOCS/CLAUDE.md` only
**Goals covered:** 8 (documentation update)

Read each file fully before editing. Do not modify any source code files.

### C1 — Update DOCS/CLAUDE.md

- Update the version section: `v0.3.1 — shipped` (after implementation)
- **launch.command** section: replace terminal/print-loop description with tkinter GUI description:
  - Opens a `tkinter.Tk()` window on launch (not a terminal window)
  - Shows live status for 4 services (screenpipe, Context API, Semantic Indexer, LM Studio), updated every 5s via `root.after()`
  - `Start Screenpipe` and `Stop Screenpipe` buttons
  - Scrollable log area for all output
  - Startup sequence (cleanup → services → browser open) still runs on launch in a background thread
- **Dashboard features** list: update to reflect v0.3.1 state:
  - Remove: `Stop screenpipe` from sidebar controls
  - Remove: `Today's Summary` from sidebar controls
  - Update: Auto-refresh defaults to ON
  - Update: Tab order — Ask AI is now tab 2 (right of Live Feed)
  - Update: Browser tab now displays all captured fields correctly
- **Known gotchas**: add any new ones identified during v0.3.1 implementation

### C2 — Update DOCS/PRODUCT.md

- Bump version header to `v0.3.1`
- **Launch Script section**: replace description of terminal keep-alive loop with description of tkinter GUI window and its controls
- **Sidebar controls list**: remove `Stop screenpipe` and `Today's Summary` entries; note auto-refresh defaults to ON
- **Dashboard features**: update tab order and Browser tab description
- **Roadmap**: add `### v0.3.1 (shipped)` section with all 7 items checked:
  ```
  - [x] tkinter GUI launcher with Start/Stop screenpipe and live status indicators
  - [x] Fixed LM Studio context window overflow in AI chat context injection
  - [x] Renamed all app UI from "screenpipe" to "Augur"
  - [x] Removed Stop Screenpipe and Today's Summary buttons from sidebar
  - [x] Auto-refresh defaults to ON
  - [x] Ask AI tab: fixed-height scrollable chat (page no longer scrolls)
  - [x] Ask AI tab moved to second position (right of Live Feed)
  - [x] Browser Captures tab fixed — loads and displays all captured fields
  ```

### C3 — Update README.md

- **Running section**: update "Option A: Double-click launcher" description — now opens a GUI window (not a terminal), shows service status, has Start/Stop controls. Keep-alive loop description → remove. Browser still opens automatically.
- **Dashboard Features list**: update to match new tab order and remove mention of Stop Screenpipe sidebar button
- **Troubleshooting**: remove or update any entry referencing the Stop Screenpipe button
- **Roadmap**: add `### v0.3.1 (shipped)` with the same 8-item list as PRODUCT.md
- **File Structure**: update `launch.command` description to note it now opens a GUI app

---

## Constraints

- **No commits.** No git operations of any kind.
- **Strict file scope.** Each subagent touches only the files listed under its heading.
- **No new files.** The tkinter GUI lives inside the existing `launch.command`. No new `.py` or `.html` files.
- **No new pip dependencies.** Subagent A uses only Python stdlib. Subagent B uses no new JS libraries.
- **Minimal blast radius.** Fix only what is listed. Do not refactor or restructure surrounding code.
- **No renames of internal identifiers.** JS function names, Python variable names, HTML IDs/classes — leave these alone. Only rename visible user-facing text strings.

---

## Verification Checklist (manual, post-implementation)

**Launcher:**
- [ ] Double-clicking `launch.command` opens a GUI window, not a terminal
- [ ] Window title is `Augur`
- [ ] Live status shows for all 4 services, updates every 5s
- [ ] Start Screenpipe button starts screenpipe; grays out while starting
- [ ] Stop Screenpipe button kills screenpipe process
- [ ] Log area shows all startup messages
- [ ] Browser opens automatically on launch
- [ ] Closing the window does not kill screenpipe

**Dashboard branding:**
- [ ] Page `<title>` is `Augur`
- [ ] Header/logo shows `Augur`, not `screenpipe`
- [ ] No visible "screenpipe" labels remain in the UI

**Sidebar:**
- [ ] No `Stop Screenpipe` button
- [ ] No `Today's Summary` button
- [ ] Auto-refresh is ON when dashboard first loads

**Ask AI tab:**
- [ ] Tab is second from left (right of Live Feed)
- [ ] Chat messages scroll inside a fixed container; page does not scroll
- [ ] Input box is always visible without scrolling the page
- [ ] No context window overflow errors in normal use

**Browser Captures tab:**
- [ ] Tab loads data on first visit
- [ ] Each capture shows: URL, title, time on page, scroll depth, selected text, timestamp
- [ ] Empty state message shown when no captures exist
- [ ] Offline state message shown when Context API is not running
- [ ] Refresh button works
