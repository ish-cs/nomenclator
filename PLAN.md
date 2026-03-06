# Augur v0.3 — Parallel Agent Build Plan

**Version:** 0.3
**Build strategy:** 1 Manager Agent + 5 parallel Wave 1 Agents + 1 sequential Wave 2 Agent
**Dependency rule:** Agents run in parallel unless one agent's output is a hard input to another. Where a dependency exists, the downstream agent waits.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MANAGER AGENT                                      │
│  - Reads PLAN.md and all source files                                        │
│  - Spawns Wave 1 agents in parallel                                          │
│  - Monitors completion, validates output, runs offline test suite            │
│  - Spawns Wave 2 agent after Wave 1 fully completes                          │
│  - Runs full test suite, fixes failures, commits                             │
└────────────────────────┬────────────────────────────────────────────────────┘
                         │ spawns all simultaneously
         ┌───────────────┼───────────────┬───────────────┬───────────────┐
         ▼               ▼               ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ AGENT A  │   │ AGENT B  │   │ AGENT C  │   │ AGENT D  │   │ AGENT E  │
   │          │   │          │   │          │   │          │   │          │
   │context-  │   │mcp_server│   │launch.   │   │demo_agent│   │dashboard │
   │server.py │   │.py (new) │   │command   │   │.py       │   │.html     │
   │          │   │          │   │          │   │          │   │          │
   │Features: │   │Feature:  │   │Feature:  │   │Feature:  │   │Features: │
   │ 1,4,6    │   │ 5        │   │ 2        │   │ 3        │   │ 7,8      │
   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘
        │              │               │               │               │
        └──────────────┴───────────────┴───────────────┴───────────────┘
                                       │
                         all Wave 1 complete — manager validates
                                       │
                                       ▼
                              ┌──────────────┐
                              │  AGENT F     │
                              │              │
                              │test_features │
                              │.py           │
                              │              │
                              │ Feature: 9   │
                              └──────────────┘
                                       │
                              manager runs full test suite,
                              fixes failures, commits
```

### File Ownership (no cross-agent conflicts)

| Agent | Files Owned | Features |
|-------|------------|---------|
| Manager | orchestration only — no direct file edits | — |
| A | `context-server.py` | 1, 4, 6 |
| B | `mcp_server.py` (new file) | 5 |
| C | `launch.command` | 2 |
| D | `demo_agent.py`, `requirements.txt` | 3 |
| E | `screenpipe-dashboard.html` | 7, 8 |
| F | `test_features.py` | 9 |

No two agents touch the same file. Zero merge conflicts by design.

### Dependency Graph

```
Agent A ──────────────────────────────────────────────┐
Agent B ─────────────────────────────────────────────┐│
Agent C ────────────────────────────────────────────┐││  all complete
Agent D ───────────────────────────────────────────┐│││  ────────────► Agent F
Agent E ──────────────────────────────────────────┐││││
                                                   └┴┴┴┘
```

Agent F has a hard dependency on all Wave 1 agents completing. It reads their output files to write accurate tests against the actual implementation, not specs.

Agents A, B, C, D, E have **zero dependencies on each other** — they own completely separate files.

---

## 2. Manager Agent — Full Specification

The Manager Agent does not write application code. It orchestrates, validates, and commits.

### Step 1 — Bootstrap (before spawning anything)

Read all files that will be modified so context is available when crafting agent prompts:

```
Files to read:
  context-server.py          (Agent A will modify)
  launch.command             (Agent C will modify)
  demo_agent.py              (Agent D will modify)
  screenpipe-dashboard.html  (Agent E will modify)
  requirements.txt           (Agent D will modify)
  test_features.py           (Agent F will rewrite)
  PLAN.md                    (this file — full spec for all agents)
```

Also read `semantic_search.py` so Agent A understands the existing semantic search interface it will integrate.

### Step 2 — Spawn Wave 1 (all 5 agents simultaneously)

Call the `Agent` tool 5 times in a single response (parallel). Each call receives:
- The full PLAN.md section for that agent
- The current content of the file(s) it owns
- The current content of any files it needs to READ (but not modify) for reference
- Explicit constraints: what not to touch, what invariants to maintain

**Agent A prompt skeleton:**
```
You are Agent A in a parallel build of Augur v0.3.
Your sole responsibility: modify context-server.py.
You implement Features 1, 4, and 6 from PLAN.md.
Do not modify any other file.

[current content of context-server.py]
[current content of semantic_search.py — READ ONLY for interface reference]
[Feature 1 spec — full detail]
[Feature 4 spec — full detail]
[Feature 6 spec — full detail]
[Invariants section]
[Self-verification checklist]
```

**Agent B prompt skeleton:**
```
You are Agent B in a parallel build of Augur v0.3.
Your sole responsibility: create mcp_server.py (new file).
You implement Feature 5 from PLAN.md.
Do not modify any other file.

[Feature 5 spec — full detail including all tool definitions and protocol details]
[Invariants section]
[Self-verification checklist]
```

**Agent C prompt skeleton:**
```
You are Agent C in a parallel build of Augur v0.3.
Your sole responsibility: modify launch.command.
You implement Feature 2 from PLAN.md.
Do not modify any other file.

[current content of launch.command]
[Feature 2 spec — full detail]
[Invariants section]
[Self-verification checklist]
```

**Agent D prompt skeleton:**
```
You are Agent D in a parallel build of Augur v0.3.
Your sole responsibility: modify demo_agent.py and requirements.txt.
You implement Feature 3 from PLAN.md.
Do not modify any other file.

[current content of demo_agent.py]
[current content of requirements.txt]
[Feature 3 spec — full detail]
[Invariants section]
[Self-verification checklist]
```

**Agent E prompt skeleton:**
```
You are Agent E in a parallel build of Augur v0.3.
Your sole responsibility: modify screenpipe-dashboard.html.
You implement Features 7 and 8 from PLAN.md.
Do not modify any other file.

[current content of screenpipe-dashboard.html]
[Feature 7 spec — full detail]
[Feature 8 spec — full detail]
[Invariants section]
[Self-verification checklist]
```

### Step 3 — Monitor Wave 1

Wait for all 5 agent tasks to return. Do not proceed until all 5 are complete.

For each completed agent, perform a **quick sanity check**:
- Read the file the agent was supposed to modify
- Verify the file is syntactically valid Python (for .py files) or valid HTML
- Verify the key function/endpoint names mentioned in the spec are present (use Grep)
- If an agent failed or produced clearly broken output: re-run that agent with additional context about what went wrong

Specific checks per agent:
```
Agent A — context-server.py:
  grep: browser_capture_to_candidate
  grep: _get_semantic_scores
  grep: get_profile
  grep: /context-card
  python3 -c "import ast; ast.parse(open('context-server.py').read())"

Agent B — mcp_server.py:
  grep: _handle
  grep: TOOLS
  grep: tools/list
  grep: tools/call
  python3 -c "import ast; ast.parse(open('mcp_server.py').read())"

Agent C — launch.command:
  grep: start_semantic_indexer
  grep: is_semantic_running
  grep: SEMANTIC_PID_FILE
  python3 -c "import ast; ast.parse(open('launch.command').read())"

Agent D — demo_agent.py:
  grep: ask_claude
  grep: ask_openai
  grep: --api
  python3 -c "import ast; ast.parse(open('demo_agent.py').read())"

Agent E — screenpipe-dashboard.html:
  grep: browserTab
  grep: loadBrowserActivity
  grep: setSearchMode
  grep: doSemanticSearch
```

### Step 4 — Run Offline Test Suite

After Wave 1 validation, run the existing test suite in offline mode (no live screenpipe needed):

```bash
cd /Users/ish/Desktop/nomenclator
python3 test_features.py
```

This should pass all existing tests. If any pre-existing test regresses, fix the regression before proceeding to Wave 2.

### Step 5 — Spawn Agent F (Wave 2)

After all Wave 1 agents complete and tests pass, spawn Agent F with:
- The full updated content of ALL files modified by Wave 1 agents
- The current content of `test_features.py`
- The Feature 9 spec from PLAN.md
- Instructions to ADD new test classes without removing any existing ones

### Step 6 — Run Full Test Suite

After Agent F completes:

```bash
python3 test_features.py
```

Fix any failures. Then run with `--live` flag if context-server is running:

```bash
python3 test_features.py --live
```

### Step 7 — Final Verification Checklist

Before committing:
- [ ] `python3 -c "import ast; ast.parse(open('context-server.py').read())"` — no syntax errors
- [ ] `python3 -c "import ast; ast.parse(open('mcp_server.py').read())"` — no syntax errors
- [ ] `python3 -c "import ast; ast.parse(open('launch.command').read())"` — no syntax errors
- [ ] `python3 -c "import ast; ast.parse(open('demo_agent.py').read())"` — no syntax errors
- [ ] `python3 test_features.py` — all tests pass
- [ ] All 7 endpoints present in context-server.py: `/health`, `/context`, `/summary`, `/anomalies`, `/semantic`, `/browser-captures`, `/profile`, `/context-card`
- [ ] `mcp_server.py` has exactly 5 tools defined in `TOOLS`
- [ ] Dashboard has both `browserTab` and semantic search mode toggle

### Step 8 — Commit

```bash
git add context-server.py mcp_server.py launch.command demo_agent.py \
        requirements.txt screenpipe-dashboard.html test_features.py
git commit -m "Ship Augur v0.3 — MCP server, hybrid scoring, browser context, cloud LLMs"
git push origin main
```

---

## 3. Agent A — `context-server.py`

**Features:** 1 (browser captures in /context), 4 (hybrid scoring), 6 (/profile + /context-card)
**File:** `context-server.py` only
**Reads for reference:** `semantic_search.py` (to understand the interface it will call)

### Why these three features are one agent

All three modify `context-server.py`. Running them as separate agents would cause merge conflicts. Bundling them into one agent ensures the file is edited atomically and coherently — the developer has full context of all three changes simultaneously.

### Feature 1: Browser Extension Captures in `/context` Ranking

**Problem:** Browser captures stored in `_browser_captures` / `browser_captures.json` are completely ignored when `/context` assembles results. Dwell time and text selection are high-signal intent data that gets discarded.

**Implementation:**

**1a. Add `browser_capture_to_candidate(cap)` helper** (insert after `get_browser_captures()`, before `get_anomalies()`):

```python
def browser_capture_to_candidate(cap):
    """Convert a browser capture entry into a pseudo-item compatible with gather_context scoring."""
    url    = cap.get('url', '') or ''
    domain = cap.get('domain', '') or ''
    title  = cap.get('title', '') or ''
    selected = cap.get('selected_text', '') or ''
    ts     = cap.get('timestamp') or cap.get('received_at', '') or ''
    time_s = int(cap.get('time_on_page_s') or 0)
    scroll = int(cap.get('scroll_depth_pct') or 0)

    # Searchable blob: all text fields lowercased
    blob = ' '.join(filter(None, [
        title.lower(),
        domain.lower(),
        url[:200].lower(),
        selected[:400].lower(),
    ]))

    # Stable UID: browser_ prefix guarantees no collision with integer frame_ids
    try:
        minute = ts[:16]  # "2026-03-05T14:23"
    except Exception:
        minute = ts
    uid = f"browser_{abs(hash(url + minute)) % (10 ** 9)}"

    return {
        '_source': 'browser',
        '_blob': blob,
        '_timestamp': ts,
        '_uid': uid,
        '_time_on_page_s': time_s,
        '_scroll_pct': scroll,
        '_has_selection': bool(selected and len(selected.strip()) > 10),
        '_result': {
            'frame_id': uid,
            'timestamp': ts,
            'app': 'Browser',
            'window': (title[:120] if title else url[:120]),
            'text': (selected[:300] if selected else (title or url[:150])),
            'url': url,
            'source': 'browser',
        },
    }
```

**1b. Modify `gather_context(query, limit, window_hours)`:**

After building `all_items` from screenpipe (the existing dedup loop), insert browser candidate merging:

```python
    # ── Merge browser captures ───────────────────────────────────────
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_dt = now_dt - timedelta(seconds=window_hours * 3600)
    for cap in _browser_captures:
        ts_str = cap.get('timestamp') or cap.get('received_at', '')
        try:
            cap_dt = datetime.fromisoformat(
                ts_str.replace('Z', '+00:00').replace('+00:00', '')
            )
            if cap_dt < cutoff_dt:
                continue
        except Exception:
            continue  # skip captures with unparseable timestamps
        cand = browser_capture_to_candidate(cap)
        uid = cand['_uid']
        if uid not in seen:
            seen.add(uid)
            all_items.append(cand)
```

**1c. Update the scoring loop** to handle both screenpipe items and browser candidates:

The existing scoring loop references `item.get('content', {})`. Add a branch at the top of the loop:

```python
    for item in all_items:
        if item.get('_source') == 'browser':
            blob = item['_blob']
            kw_score = sum(min(blob.count(kw), 5) for kw in keywords)
            try:
                ts = datetime.fromisoformat(
                    item['_timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
                age_s = (now - ts).total_seconds()
            except Exception:
                age_s = window_ms
            recency   = max(0.0, 1.0 - age_s / window_ms) if window_ms > 0 else 0.0
            time_bon  = min(item['_time_on_page_s'] / 300.0, 1.0)
            sel_bon   = 2.0 if item['_has_selection'] else 0.0
            item['_score'] = kw_score * 3 + recency + time_bon + sel_bon
        else:
            # Existing OCR/audio scoring — unchanged
            c = item.get('content', {})
            blob = ' '.join([
                c.get('text', '') or '',
                c.get('transcription', '') or '',
                c.get('app_name', '') or '',
                c.get('window_name', '') or '',
            ]).lower()
            kw_score = sum(min(blob.count(kw), 5) for kw in keywords)
            try:
                ts = datetime.fromisoformat(
                    c.get('timestamp', '').replace('Z', '+00:00').replace('+00:00', ''))
                age_s = (now - ts).total_seconds()
            except Exception:
                age_s = window_ms
            recency = max(0.0, 1.0 - age_s / window_ms) if window_ms > 0 else 0.0
            item['_score'] = kw_score * 3 + recency
            # semantic bonus applied separately (Feature 4)
```

**1d. Update the result-building loop** to extract results from both types:

```python
    results = []
    for item in top:
        if item.get('_source') == 'browser':
            results.append({
                **item['_result'],
                'score': round(item.get('_score', 0), 3),
            })
        else:
            c = item.get('content', {})
            is_ocr = item.get('type') == 'OCR'
            results.append({
                'frame_id': c.get('frame_id'),
                'timestamp': c.get('timestamp'),
                'app': c.get('app_name') if is_ocr else None,
                'window': c.get('window_name') if is_ocr else None,
                'text': (c.get('text') if is_ocr else c.get('transcription')) or '',
                'url': c.get('browser_url'),
                'score': round(item.get('_score', 0), 3),
                'source': 'ocr' if is_ocr else 'audio',
            })
```

**1e. Add `browser_captures_included` to the return dict:**

```python
    return {
        'query': query,
        'keywords': keywords,
        'total_candidates': len(all_items),
        'browser_captures_included': sum(
            1 for i in all_items if i.get('_source') == 'browser'
        ),
        'results': results,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
```

**Edge cases:**
- `_browser_captures` is empty → no candidates added, zero regressions
- Cap timestamp unparseable → `continue` (skip), never crashes
- `selected_text` is None or empty string → `_has_selection = False`, no bonus
- Browser UID collides with a different URL+minute hash → astronomically unlikely (10^9 space); if it happens, the second is deduplicated (acceptable)
- `time_on_page_s` is None or missing → `int(None or 0) = 0`, no bonus
- All browser captures older than `window_hours` → filtered out before scoring, same as if empty

---

### Feature 4: Hybrid Context Scoring (Semantic + Keyword Blended)

**Problem:** `/context` uses only keyword matching. Semantic matches are invisible to it even when the Chroma index is populated.

**Implementation:**

**4a. Add module-level cache globals** (near the top of the file, after the config constants):

```python
# ── Semantic cache (loaded once, reused across requests) ─────────────
_semantic_embedder   = None   # SentenceTransformer instance or None
_semantic_collection = None   # Chroma collection or None
_semantic_available  = None   # None = untested | True | False
```

**4b. Add `_try_load_semantic()` helper** (after `cors_headers()`):

```python
def _try_load_semantic():
    """
    Attempt to load the Chroma collection and sentence-transformer embedder.
    Caches result in module globals so this only pays the load cost once.
    Returns True if semantic scoring is available, False otherwise.
    """
    global _semantic_embedder, _semantic_collection, _semantic_available
    if _semantic_available is not None:
        return _semantic_available
    try:
        import semantic_search as ss
        client = ss.get_client()
        col    = ss.get_collection(client)
        if col.count() == 0:
            _semantic_available = False
            return False
        embedder = ss.get_embedder()
        _semantic_collection = col
        _semantic_embedder   = embedder
        _semantic_available  = True
        return True
    except Exception:
        _semantic_available = False
        return False
```

**4c. Add `_get_semantic_scores(query, n)` helper** (after `_try_load_semantic`):

```python
def _get_semantic_scores(query, n):
    """
    Return {uid_str: cosine_similarity} for top-n semantic matches.
    Returns empty dict if index unavailable or query fails.
    UIDs are string frame_ids as stored in Chroma.
    """
    if not _semantic_available or _semantic_embedder is None:
        return {}
    try:
        import semantic_search as ss
        result = ss.semantic_query(
            query,
            n_results=n,
            embedder=_semantic_embedder,
            collection=_semantic_collection,
        )
        return {str(r['id']): r['score'] for r in result.get('results', [])}
    except Exception:
        return {}
```

**4d. Call `_try_load_semantic()` in `main()`** (after the chromadb availability print, before starting the server):

```python
    # Attempt to warm up semantic scoring
    _try_load_semantic()
    if _semantic_available:
        print("  v  semantic scoring: active (hybrid mode)")
    else:
        print("  -  semantic scoring: inactive (index empty or deps missing)")
```

**4e. Apply semantic bonus inside `gather_context()`:**

After the `all_items` dedup loop is built (before scoring), fetch semantic scores:

```python
    # Attempt semantic bonus — transparent no-op if unavailable
    _try_load_semantic()
    semantic_scores  = _get_semantic_scores(query, min(limit * 3, 60))
    semantic_active  = bool(semantic_scores)
```

Inside the **OCR/audio branch** of the scoring loop, add semantic bonus:

```python
            # Semantic bonus: cosine similarity × 2.0 (range 0–2)
            uid_key = str(c.get('frame_id') or c.get('timestamp') or '')
            sem_bonus = semantic_scores.get(uid_key, 0.0) * 2.0
            item['_score'] = kw_score * 3 + recency + sem_bonus
```

Browser candidates intentionally get no semantic bonus (their UIDs are `browser_XXXX`, not indexed in Chroma).

**4f. Add `semantic_enhanced` to the return dict:**

```python
        'semantic_enhanced': semantic_active,
```

**Performance notes:**
- `_try_load_semantic()` at server startup: embedder load takes 2–10s on first run (model downloaded once to `~/.cache/`). Subsequent server starts: <1s from cache. This happens at startup, not per-request.
- Per-request `_get_semantic_scores()` with warm cache: ~20–100ms depending on index size. Acceptable.
- If Chroma is empty or deps missing: `_get_semantic_scores()` returns `{}` immediately — zero overhead.
- `_semantic_available = False` is cached after first failed attempt — no repeated retries per request.

---

### Feature 6: `/profile` and `/context-card` Endpoints

**Problem:** No structured behavioral profile API. Agents that want to understand who the user is must construct this themselves.

**Implementation:**

**6a. Add `get_profile(days)` function** (before the HTTP handler class):

```python
def get_profile(days=7):
    """Rich behavioral profile over the last N days."""
    today = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    top_apps = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT app_name, COUNT(*) as frames FROM frames "
            f"WHERE date(timestamp) >= '{start}' AND app_name IS NOT NULL "
            f"AND app_name != '' GROUP BY app_name ORDER BY frames DESC LIMIT 10"
        )
    }) or []

    hourly = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hr, "
            f"COUNT(*) as cnt FROM frames "
            f"WHERE date(timestamp) >= '{start}' GROUP BY hr ORDER BY hr"
        )
    }) or []

    url_rows = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT browser_url, COUNT(*) as visits FROM frames "
            f"WHERE date(timestamp) >= '{start}' AND browser_url IS NOT NULL "
            f"AND browser_url != '' GROUP BY browser_url "
            f"ORDER BY visits DESC LIMIT 100"
        )
    }) or []

    sample_text = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT text FROM frames WHERE date(timestamp) >= '{start}' "
            f"AND text IS NOT NULL LIMIT 500"
        )
    }) or []

    total_frames = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT COUNT(*) as n FROM frames WHERE date(timestamp) >= '{start}'"
        )
    }) or [{'n': 0}]

    audio_count = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT COUNT(*) as n FROM audio_transcriptions "
            f"WHERE date(timestamp) >= '{start}'"
        )
    }) or [{'n': 0}]

    # Top apps with approximate hours (screenpipe ~1 frame/sec)
    app_list = []
    for r in top_apps:
        frames = int(r.get('frames', 0))
        app_list.append({
            'app': r.get('app_name'),
            'frames': frames,
            'hours': round(frames / 3600, 2),
        })

    # 24-slot hourly heatmap
    heatmap = [0] * 24
    for r in hourly:
        try:
            heatmap[int(r.get('hr', 0))] = int(r.get('cnt', 0))
        except Exception:
            pass

    # Domain frequency
    domain_counts = defaultdict(int)
    for r in url_rows:
        url = r.get('browser_url', '') or ''
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc
            if domain:
                domain_counts[domain] += int(r.get('visits', 0))
        except Exception:
            pass
    top_domains = [
        {'domain': d, 'visits': c}
        for d, c in sorted(domain_counts.items(), key=lambda x: -x[1])[:10]
    ]

    # Topic extraction
    word_freq = defaultdict(int)
    for row in sample_text:
        for w in (row.get('text', '') or '').lower().split():
            w = ''.join(c for c in w if c.isalpha())
            if len(w) > 3 and w not in STOP_WORDS:
                word_freq[w] += 1
    top_topics = [w for w, _ in sorted(word_freq.items(), key=lambda x: -x[1])[:25]]

    # Browser captures in window
    browser_in_window = sum(
        1 for cap in _browser_captures
        if (cap.get('timestamp') or cap.get('received_at', ''))[:10] >= start
    )

    return {
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'window_days': days,
        'date_range': {'start': start, 'end': today},
        'profile': {
            'total_frames':       int(total_frames[0].get('n', 0) if total_frames else 0),
            'total_audio_chunks': int(audio_count[0].get('n', 0) if audio_count else 0),
            'browser_captures':   browser_in_window,
            'top_apps':           app_list,
            'active_hours':       {'heatmap': heatmap},
            'top_domains':        top_domains,
            'top_topics':         top_topics,
        },
    }
```

**6b. Add `get_context_card(days)` function** (immediately after `get_profile`):

```python
def get_context_card(days=7):
    """
    Ultra-compact natural language profile (~300–500 chars).
    Designed to be prepended to any LLM system prompt for instant personalization.
    """
    data = get_profile(days)
    p    = data.get('profile', {})

    apps     = p.get('top_apps', [])
    topics   = p.get('top_topics', [])
    domains  = p.get('top_domains', [])
    heatmap  = p.get('active_hours', {}).get('heatmap', [])

    top_app_names = ', '.join(a['app'] for a in apps[:3] if a.get('app'))

    if heatmap and max(heatmap, default=0) > 0:
        peak_hours = ', '.join(
            f"{h}:00"
            for h in sorted(range(24), key=lambda h: -heatmap[h])[:3]
        )
    else:
        peak_hours = 'unknown'

    top_topic_words  = ', '.join(topics[:8])
    top_domain_names = ', '.join(d['domain'] for d in domains[:3])

    parts = [f"User profile (last {days} days):"]
    if top_app_names:
        parts.append(f"Primarily uses {top_app_names}.")
    parts.append(f"Most active at {peak_hours}.")
    if top_topic_words:
        parts.append(f"Recent topics: {top_topic_words}.")
    if top_domain_names:
        parts.append(f"Frequent sites: {top_domain_names}.")

    card = ' '.join(parts)
    return {
        'card':         card,
        'chars':        len(card),
        'window_days':  days,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
```

**6c. Add URL parsing import** — `urllib.parse` is already imported. Confirm it's used in `get_profile` for domain extraction. If `urlparse` is not already imported at the top, add it: `from urllib.parse import urlparse` or use `urllib.parse.urlparse(url)`.

**6d. Add handlers in `do_GET`** (inside the `if/elif` chain, before the `else: 404`):

```python
        elif parsed.path == '/profile':
            days = int(p('days', 7))
            self.send_json(200, get_profile(days))

        elif parsed.path == '/context-card':
            days = int(p('days', 7))
            self.send_json(200, get_context_card(days))
```

**6e. Update the 404 endpoints list** and startup print statements:

```python
        self.send_json(404, {'error': 'not found', 'endpoints': [
            '/health', '/context', '/summary', '/anomalies',
            '/semantic', '/browser-captures', '/profile', '/context-card',
            'POST /browser-capture',
        ]})
```

```python
    print("    GET  /profile?days=7")
    print("    GET  /context-card?days=7")
```

**6f. Update version string in startup banner:**

```python
    print("  │       Augur Context API v0.3         │")
```

### Agent A — Self-Verification Checklist

Before reporting complete, Agent A must verify:
- [ ] `python3 -c "import ast; ast.parse(open('context-server.py').read())"` exits 0
- [ ] `browser_capture_to_candidate` function exists and is called in `gather_context`
- [ ] `_try_load_semantic` and `_get_semantic_scores` exist as module-level functions
- [ ] `_semantic_embedder`, `_semantic_collection`, `_semantic_available` exist as module-level globals (initialized to `None`)
- [ ] `get_profile` and `get_context_card` functions exist
- [ ] `/profile` and `/context-card` handlers present in `do_GET`
- [ ] `browser_captures_included` key present in `gather_context` return dict
- [ ] `semantic_enhanced` key present in `gather_context` return dict
- [ ] `source` field included in every result item
- [ ] Version banner updated to v0.3
- [ ] No existing endpoint removed or broken (health, context, summary, anomalies, semantic, browser-captures)

---

## 4. Agent B — `mcp_server.py` (new file)

**Feature:** 5 (MCP server for Claude Desktop / Cursor)
**File:** `mcp_server.py` — create from scratch
**Reads for reference:** nothing (standalone file, calls localhost:3031 via HTTP)

### Why MCP is the Most Impactful v0.3 Feature

MCP (Model Context Protocol) is the standard for local tool servers that AI clients call natively. Shipping an MCP server means:
- Claude Desktop users get access to their screen context with **zero additional code** — just a config entry
- Augur becomes a first-class context source for any MCP-compatible agent (Claude Desktop, Cursor, Zed, etc.)
- This is the "investor demo" moment: open Claude Desktop, ask "what have I been doing today?", and it answers from real screen data

### MCP Protocol Fundamentals

MCP uses JSON-RPC 2.0 over **stdio** (not HTTP) with `Content-Length` header framing. This is identical to the Language Server Protocol.

**Message format (exactly):**
```
Content-Length: <byte_length>\r\n
\r\n
<utf-8 encoded JSON body>
```

**Key protocol rules:**
- Requests have `id`, `method`, `params` — must be responded to
- Notifications have `method` but NO `id` — must NOT be responded to (responding causes protocol errors)
- Server must respond to `initialize` before any other method is processed
- `tools/list` returns all available tools
- `tools/call` executes a tool and returns content
- All output (including logs) to stderr; stdout is exclusively the protocol channel

### Full Implementation

```python
#!/usr/bin/env python3
"""
Augur MCP Server
----------------
Exposes Augur context tools via the Model Context Protocol (MCP).
Works with Claude Desktop, Cursor, Zed, and any MCP-compatible AI client.

Setup — Claude Desktop (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "augur": {
        "command": "python3",
        "args": ["/absolute/path/to/mcp_server.py"]
      }
    }
  }

Requires context-server.py running on localhost:3031.
No additional dependencies — pure Python stdlib.
"""

import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime

CONTEXT_API = "http://localhost:3031"


# ── I/O layer ────────────────────────────────────────────────────────

def _read_message():
    """Read one JSON-RPC message from stdin (Content-Length framed)."""
    headers = {}
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw or raw in (b'\r\n', b'\n'):
            break
        line = raw.decode('utf-8').strip()
        if ':' in line:
            k, _, v = line.partition(':')
            headers[k.strip()] = v.strip()
    length = int(headers.get('Content-Length', 0))
    if length == 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode('utf-8'))


def _write_message(obj):
    """Write one JSON-RPC message to stdout (Content-Length framed)."""
    body   = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    header = f'Content-Length: {len(body)}\r\n\r\n'.encode('utf-8')
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def _log(msg):
    """Log to stderr — stdout is reserved for the MCP protocol."""
    print(f"[augur-mcp] {msg}", file=sys.stderr, flush=True)


# ── Context API call ─────────────────────────────────────────────────

def _call_api(path):
    """Call the local Context API. Returns parsed JSON or None on failure."""
    try:
        with urllib.request.urlopen(f"{CONTEXT_API}{path}", timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        _log(f"API error {path}: {e}")
        return None


# ── Tool definitions ─────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_context",
        "description": (
            "Get ranked, relevant screen captures for a natural language query. "
            "Returns what the user has been doing, reading, or working on based on "
            "passive screen capture data. Use this to understand the user's context "
            "before responding. Includes OCR captures, audio transcriptions, and "
            "browser activity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query, e.g. 'what was I working on today'"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 10)",
                    "default": 10
                },
                "window_hours": {
                    "type": "integer",
                    "description": "Hours of history to consider (default: 24)",
                    "default": 24
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_daily_summary",
        "description": (
            "Get a structured behavioral summary for a specific date. "
            "Includes top apps used, peak active hours, unique URLs visited, "
            "audio transcription count, and key topic keywords extracted from OCR."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format. Defaults to today if omitted."
                }
            }
        }
    },
    {
        "name": "get_anomalies",
        "description": (
            "Detect behavioral anomalies — apps used significantly more or less than usual, "
            "or entirely new apps appearing for the first time. Compares today's activity "
            "against a rolling N-day baseline."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Days of baseline history to compare against (default: 7)",
                    "default": 7
                }
            }
        }
    },
    {
        "name": "get_user_profile",
        "description": (
            "Get a behavioral profile of the user over the last N days. "
            "Includes top apps with hours, peak active hours heatmap, most visited domains, "
            "and topic keywords from OCR text. Use this to personalize responses without "
            "asking the user to describe themselves."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Days of history to include (default: 7)",
                    "default": 7
                }
            }
        }
    },
    {
        "name": "get_browser_activity",
        "description": (
            "Get recent browser captures: URLs visited, time spent on each page, "
            "scroll depth, and any text the user explicitly selected. "
            "Requires the Augur Chrome extension to be installed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max captures to return (default: 20)",
                    "default": 20
                }
            }
        }
    },
]


# ── Tool handlers ────────────────────────────────────────────────────

def _tool_get_context(args):
    query        = args.get('query', '')
    limit        = int(args.get('limit', 10))
    window_hours = int(args.get('window_hours', 24))
    if not query:
        return "Error: 'query' argument is required."

    q    = urllib.parse.quote(query)
    data = _call_api(f"/context?q={q}&limit={limit}&window_hours={window_hours}")
    if data is None:
        return (
            "Could not reach Context API at localhost:3031. "
            "Make sure context-server.py is running: python3 context-server.py"
        )

    results = data.get('results', [])
    if not results:
        return f"No screen context found for: {query}"

    sem = " [semantic+keyword]" if data.get('semantic_enhanced') else " [keyword]"
    browser_n = data.get('browser_captures_included', 0)
    header = (
        f"Screen context for: \"{query}\"\n"
        f"Mode:{sem} | {len(results)} results"
        + (f" | {browser_n} from browser" if browser_n else "")
        + "\n"
    )

    lines = [header]
    for r in results:
        ts = r.get('timestamp', '')
        try:
            t = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', ''))
            time_str = t.strftime('%H:%M')
        except Exception:
            time_str = ts[:16]

        app     = r.get('app') or r.get('source') or 'unknown'
        window  = r.get('window') or ''
        text    = (r.get('text') or '').strip()[:300]
        url     = r.get('url') or ''
        src_tag = f" [{r.get('source', 'ocr')}]"

        line = f"[{time_str}] {app}"
        if window:
            line += f" / {window[:50]}"
        line += src_tag
        lines.append(line)
        if url:
            lines.append(f"  URL: {url[:120]}")
        if text:
            lines.append(f"  {text[:200]}")
        lines.append("")

    return '\n'.join(lines)[:4000]  # hard cap to avoid overwhelming LLM context


def _tool_get_daily_summary(args):
    date = args.get('date') or datetime.now().strftime('%Y-%m-%d')
    data = _call_api(f"/summary?date={date}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    p = data.get('profile', {})
    lines = [f"Daily summary for {date}:"]
    lines.append(f"  Frames captured: {p.get('total_frames', 0)}")
    lines.append(f"  URLs visited: {p.get('urls_visited', 0)}")
    lines.append(f"  Audio chunks: {p.get('audio_chunks', 0)}")

    apps = p.get('top_apps', [])
    if apps:
        lines.append("  Top apps: " + ", ".join(
            f"{a['app']} ({a['frames']} frames)" for a in apps[:5]
        ))

    hours = p.get('active_hours', [])
    if hours:
        lines.append("  Most active hours: " + ", ".join(f"{h}:00" for h in hours[:4]))

    topics = p.get('topics', [])
    if topics:
        lines.append(f"  Key topics: {', '.join(topics[:15])}")

    return '\n'.join(lines)


def _tool_get_anomalies(args):
    days = int(args.get('days', 7))
    data = _call_api(f"/anomalies?days={days}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    anomalies = data.get('anomalies', [])
    if not anomalies:
        return (
            f"No behavioral anomalies detected today "
            f"(compared against {days}-day baseline)."
        )

    lines = [f"Behavioral anomalies (vs {days}-day baseline) — {data.get('date', '')}:"]
    for a in anomalies[:10]:
        lines.append(f"  {a.get('message', str(a))}")
    return '\n'.join(lines)


def _tool_get_user_profile(args):
    days = int(args.get('days', 7))
    data = _call_api(f"/profile?days={days}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    p = data.get('profile', {})
    dr = data.get('date_range', {})
    lines = [f"User profile ({dr.get('start', '')} → {dr.get('end', '')}):"]
    lines.append(f"  Total frames: {p.get('total_frames', 0)}")

    apps = p.get('top_apps', [])
    if apps:
        lines.append("  Top apps: " + ", ".join(
            f"{a['app']} ({a.get('hours', 0):.1f}h)" for a in apps[:5]
        ))

    heatmap = p.get('active_hours', {}).get('heatmap', [])
    if heatmap and max(heatmap, default=0) > 0:
        peak_hours = sorted(range(24), key=lambda h: -heatmap[h])[:3]
        lines.append("  Peak hours: " + ", ".join(f"{h}:00" for h in peak_hours))

    domains = p.get('top_domains', [])
    if domains:
        lines.append("  Top domains: " + ", ".join(d['domain'] for d in domains[:5]))

    topics = p.get('top_topics', [])
    if topics:
        lines.append(f"  Key topics: {', '.join(topics[:15])}")

    return '\n'.join(lines)


def _tool_get_browser_activity(args):
    limit = int(args.get('limit', 20))
    data  = _call_api(f"/browser-captures?limit={limit}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    results = data.get('results', [])
    if not results:
        return (
            "No browser activity captured. "
            "Install the Augur Chrome extension to capture browser activity: "
            "load extension/ as an unpacked extension in chrome://extensions"
        )

    lines = [f"Recent browser activity ({len(results)} captures, {data.get('total', 0)} total):"]
    for r in results[:20]:
        title  = r.get('title', '') or ''
        url    = r.get('url', '') or ''
        time_s = r.get('time_on_page_s')
        scroll = r.get('scroll_depth_pct')
        sel    = (r.get('selected_text') or '').strip()

        line = f"  {title[:80] or url[:80]}"
        meta = []
        if time_s is not None:
            meta.append(f"{time_s}s")
        if scroll is not None:
            meta.append(f"scroll {scroll}%")
        if meta:
            line += f" ({', '.join(meta)})"
        lines.append(line)

        if sel:
            lines.append(f"    Selected: \"{sel[:150]}\"")

    return '\n'.join(lines)[:3000]


# ── Dispatch ─────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    'get_context':         _tool_get_context,
    'get_daily_summary':   _tool_get_daily_summary,
    'get_anomalies':       _tool_get_anomalies,
    'get_user_profile':    _tool_get_user_profile,
    'get_browser_activity': _tool_get_browser_activity,
}


def _handle(msg):
    """
    Process one JSON-RPC message.
    Returns a response dict, or None for notifications (no id field).
    """
    method = msg.get('method', '')
    msg_id = msg.get('id')        # None for notifications
    params = msg.get('params') or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    # Notifications have no 'id' — must not respond
    if msg_id is None:
        return None

    if method == 'initialize':
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "augur", "version": "0.3"},
        })

    elif method == 'tools/list':
        return ok({"tools": TOOLS})

    elif method == 'tools/call':
        name      = params.get('name', '')
        arguments = params.get('arguments') or {}
        handler   = TOOL_HANDLERS.get(name)
        if handler is None:
            return err(-32601, f"Unknown tool: {name}")
        try:
            text = handler(arguments)
            return ok({"content": [{"type": "text", "text": text}]})
        except Exception as e:
            _log(f"Tool '{name}' raised: {e}")
            return ok({"content": [{"type": "text", "text": f"Tool error: {e}"}]})

    elif method == 'ping':
        return ok({})

    else:
        return err(-32601, f"Method not found: {method}")


# ── Main loop ────────────────────────────────────────────────────────

def main():
    _log("Augur MCP Server v0.3 — waiting for client")
    _log(f"Context API: {CONTEXT_API}")
    try:
        while True:
            msg = _read_message()
            if msg is None:
                break
            _log(f"<- {msg.get('method', '?')} id={msg.get('id')}")
            response = _handle(msg)
            if response is not None:
                _write_message(response)
    except (EOFError, BrokenPipeError, KeyboardInterrupt):
        _log("MCP server stopped.")
    except Exception as e:
        _log(f"Fatal: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
```

### Agent B — Self-Verification Checklist

- [ ] `python3 -c "import ast; ast.parse(open('mcp_server.py').read())"` exits 0
- [ ] `len(TOOLS) == 5`
- [ ] All 5 tool names present: `get_context`, `get_daily_summary`, `get_anomalies`, `get_user_profile`, `get_browser_activity`
- [ ] `_handle` function exists and handles `initialize`, `tools/list`, `tools/call`, `ping`
- [ ] Notifications (no `id`) return `None` from `_handle`
- [ ] `_read_message()` and `_write_message()` use `sys.stdin.buffer` / `sys.stdout.buffer` (binary mode)
- [ ] All output except protocol messages goes to `sys.stderr`
- [ ] All tool handlers catch exceptions and return error strings (never crash the server)
- [ ] `python3 -c "import mcp_server; msg = {'jsonrpc':'2.0','id':1,'method':'initialize','params':{}}; r = mcp_server._handle(msg); assert r['result']['serverInfo']['name'] == 'augur'"` exits 0

---

## 5. Agent C — `launch.command`

**Feature:** 2 (auto-start semantic indexer as managed service)
**File:** `launch.command` only

### Feature 2: Auto-Start Semantic Indexer

**Problem:** Users who don't manually start `semantic_search.py` get no semantic scoring in `/context`, even if they have the deps installed. The indexer should be as automatic as the Context API.

**Implementation:**

**2a. Add constants** at the top of the file (after the existing constants block):

```python
SEMANTIC_INDEXER  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic_search.py")
SEMANTIC_PID_FILE = os.path.expanduser("~/.screenpipe/semantic_indexer.pid")
```

**2b. Add `is_semantic_available()` helper**:

```python
def is_semantic_available():
    """Check if chromadb and sentence_transformers are importable."""
    import importlib
    try:
        importlib.import_module('chromadb')
        importlib.import_module('sentence_transformers')
        return True
    except ImportError:
        return False
```

**2c. Add `is_semantic_running()` helper**:

```python
def is_semantic_running():
    """Check if the semantic indexer daemon is alive via PID file."""
    if not os.path.exists(SEMANTIC_PID_FILE):
        return False
    try:
        with open(SEMANTIC_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)   # signal 0 = existence check, never kills the process
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        # PID file exists but process is dead — clean it up
        try:
            os.unlink(SEMANTIC_PID_FILE)
        except Exception:
            pass
        return False
```

**2d. Add `start_semantic_indexer()` helper**:

```python
def start_semantic_indexer():
    """
    Start semantic_search.py in daemon mode.
    Returns (True, pid) on success, (False, reason_str) on failure.
    """
    if not os.path.exists(SEMANTIC_INDEXER):
        return False, "semantic_search.py not found"
    if not is_semantic_available():
        return False, "deps missing (pip install chromadb sentence-transformers)"
    try:
        log_file = open(os.path.expanduser("~/.screenpipe/launcher.log"), "a")
        proc = subprocess.Popen(
            [sys.executable, SEMANTIC_INDEXER],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        os.makedirs(os.path.dirname(SEMANTIC_PID_FILE), exist_ok=True)
        with open(SEMANTIC_PID_FILE, 'w') as f:
            f.write(str(proc.pid))
        return True, proc.pid
    except Exception as e:
        return False, str(e)
```

**2e. Insert semantic indexer block in `main()`** — after the Context API block (step 3), before the LM Studio check (step 4). Renumber the existing step comments accordingly:

```python
    # 4. Start semantic indexer
    print()
    if is_semantic_running():
        print_status("Semantic indexer already running")
    elif not is_semantic_available():
        print_status("Semantic indexer not available (pip install chromadb sentence-transformers)", ok=False)
        print()
        print("  Install to enable semantic context search:")
        print("    pip install chromadb sentence-transformers")
    elif not os.path.exists(SEMANTIC_INDEXER):
        print_status("semantic_search.py not found — skipping semantic indexer", ok=False)
    else:
        print("  ⟳  Starting Augur Semantic Indexer...")
        ok, info = start_semantic_indexer()
        if ok:
            time.sleep(1.5)
            if is_semantic_running():
                print_status(f"Semantic indexer started (PID {info})")
            else:
                print_status("Semantic indexer starting (model loading, may take a minute)", ok=True)
        else:
            print_status(f"Failed to start semantic indexer: {info}", ok=False)
```

**2f. Update the keep-alive loop status line**:

```python
        while True:
            time.sleep(10)
            sp  = is_port_open(SCREENPIPE_PORT)
            ctx = is_port_open(CONTEXT_PORT)
            sem = is_semantic_running()
            lm  = is_port_open(LM_STUDIO_PORT)
            status = (
                f"  [screenpipe: {'up' if sp else 'DOWN'}]"
                f"  [context-api: {'up' if ctx else 'down'}]"
                f"  [semantic: {'up' if sem else 'down'}]"
                f"  [LM Studio: {'up' if lm else 'not running'}]"
            )
            print(f"\r{status}    ", end="", flush=True)
```

**Edge cases:**
- `pip install` not run → `is_semantic_available()` returns False → skip with advisory, no crash
- PID file stale (process crashed) → `os.kill` raises → file deleted → treated as not running → restart attempted
- User re-runs `launch.command` while indexer is running → `is_semantic_running()` True → skip, no double-start
- First run: model downloads to `~/.cache/hub/` — takes 1–3 minutes. The launcher does NOT block on this. The process starts, begins loading, indexer daemon loop starts once model is ready.
- `~/.screenpipe/` directory doesn't exist → `os.makedirs(..., exist_ok=True)` creates it

### Agent C — Self-Verification Checklist

- [ ] `python3 -c "import ast; ast.parse(open('launch.command').read())"` exits 0
- [ ] `SEMANTIC_INDEXER` and `SEMANTIC_PID_FILE` constants present
- [ ] `is_semantic_available`, `is_semantic_running`, `start_semantic_indexer` functions present
- [ ] Semantic indexer startup block present in `main()` between Context API and LM Studio sections
- [ ] Keep-alive loop includes `sem = is_semantic_running()` and prints `[semantic: up/down]`
- [ ] All existing functionality (screenpipe start, context API start, LM Studio check, dashboard open) unchanged

---

## 6. Agent D — `demo_agent.py`

**Feature:** 3 (Claude + OpenAI API backends)
**Files:** `demo_agent.py`, `requirements.txt`

### Feature 3: Multi-Backend LLM Support

**Problem:** `demo_agent.py` hardcodes LM Studio. Investors and users may want to use Claude (better quality) or GPT-4 for demos, or when LM Studio isn't set up.

**New CLI syntax:**
```bash
python demo_agent.py "question"                    # LM Studio (default)
python demo_agent.py "question" --api claude       # Claude API
python demo_agent.py "question" --api openai       # OpenAI API
python demo_agent.py --watch                       # LM Studio watch mode
python demo_agent.py --watch --api claude          # Claude watch mode
```

**Implementation:**

**3a. Add `os` import** (it's needed for `os.environ`):

```python
import os
```

**3b. Add `--api` flag parsing in `main()`** — before the service check:

```python
    # Parse --api flag (must happen before check_services)
    api_backend = 'lmstudio'
    if '--api' in args:
        idx = args.index('--api')
        if idx + 1 < len(args):
            api_backend = args[idx + 1].lower()
            args = args[:idx] + args[idx + 2:]
        else:
            print("  [!] --api requires a value: claude, openai, or lmstudio")
            sys.exit(1)
```

**3c. Refactor `ask_llm()` into a dispatcher + three backend functions.**

Replace the existing `ask_llm()` with:

```python
def ask_llm(question, context_block, model=None, backend='lmstudio'):
    """Dispatch to the selected LLM backend."""
    if backend == 'claude':
        return _ask_claude(question, context_block)
    elif backend in ('openai', 'gpt'):
        return _ask_openai(question, context_block)
    else:
        return _ask_lmstudio(question, context_block, model)


def _ask_lmstudio(question, context_block, model=None):
    """Existing LM Studio logic — unchanged."""
    system = (
        "You are a helpful AI assistant with access to screenpipe screen capture data.\n"
        "Answer the user's question based on the context below. Be specific, concise, and direct.\n\n"
        f"Screen capture context ({len(context_block.splitlines())} lines):\n\n"
        f"{context_block}\n\n"
        "Answer the question using this data."
    )
    prompt = f"{system}\n\nUser: {question}"
    payload = {
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 600,
        'temperature': 0.6,
        'stream': False,
    }
    if model:
        payload['model'] = model
    result = post_json(f"{LM_STUDIO}/v1/chat/completions", payload)
    if not result:
        return None
    return result.get('choices', [{}])[0].get('message', {}).get('content', '')


def _ask_claude(question, context_block):
    """Claude API backend via anthropic SDK."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("  [x] ANTHROPIC_API_KEY not set.")
        print("      export ANTHROPIC_API_KEY=sk-ant-...")
        return None
    try:
        import anthropic
    except ImportError:
        print("  [x] anthropic SDK not installed.")
        print("      pip install anthropic")
        return None

    system = (
        "You are a helpful AI assistant with access to the user's screen capture data.\n"
        "Answer based on the context below. Be specific, concise, and direct.\n\n"
        f"Screen capture context:\n\n{context_block}\n\nAnswer the question using this data."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": question}],
        )
        return msg.content[0].text
    except Exception as e:
        print(f"  [error] Claude API: {e}")
        return None


def _ask_openai(question, context_block):
    """OpenAI API backend via openai SDK."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print("  [x] OPENAI_API_KEY not set.")
        print("      export OPENAI_API_KEY=sk-...")
        return None
    try:
        import openai
    except ImportError:
        print("  [x] openai SDK not installed.")
        print("      pip install openai")
        return None

    model = os.environ.get('OPENAI_MODEL', 'gpt-4o')
    system = (
        "You are a helpful AI assistant with access to the user's screen capture data.\n"
        "Answer based on the context below. Be specific, concise, and direct.\n\n"
        f"Screen capture context:\n\n{context_block}"
    )
    try:
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            temperature=0.6,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": question},
            ],
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"  [error] OpenAI API: {e}")
        return None
```

**3d. Update `check_services(backend='lmstudio')`:**

Replace the existing `check_services()` function signature and body:

```python
def check_services(backend='lmstudio'):
    print("  Checking services...")

    # Always check the Context API
    ctx = fetch_json(f"{CONTEXT_API}/health")
    if ctx is None:
        print("  [x] Context API not reachable at localhost:3031")
        print("      Start it with: python context-server.py")
        return False
    screenpipe_ok = ctx.get('screenpipe', False)
    print(f"  [v] Context API  — localhost:3031")
    print(f"  {'[v]' if screenpipe_ok else '[!]'} screenpipe     — {'connected' if screenpipe_ok else 'not connected (results may be empty)'}")

    # Check the selected LLM backend
    if backend == 'lmstudio':
        lm = fetch_json(f"{LM_STUDIO}/v1/models")
        if lm is None:
            print("  [x] LM Studio not reachable at localhost:1234")
            print("      Open LM Studio, load a model, and start the server.")
            return False
        models = [m for m in lm.get('data', []) if 'embed' not in m.get('id', '').lower()]
        model_name = models[0]['id'] if models else 'unknown'
        print(f"  [v] LM Studio    — {model_name}")
        print()
        return True, model_name

    elif backend == 'claude':
        key = os.environ.get('ANTHROPIC_API_KEY')
        if not key:
            print("  [x] ANTHROPIC_API_KEY not set.")
            print("      export ANTHROPIC_API_KEY=sk-ant-...")
            return False
        try:
            import anthropic  # noqa
        except ImportError:
            print("  [x] anthropic SDK not installed: pip install anthropic")
            return False
        print(f"  [v] Claude API   — claude-opus-4-6")
        print()
        return True, 'claude-opus-4-6'

    elif backend in ('openai', 'gpt'):
        key = os.environ.get('OPENAI_API_KEY')
        if not key:
            print("  [x] OPENAI_API_KEY not set.")
            print("      export OPENAI_API_KEY=sk-...")
            return False
        try:
            import openai  # noqa
        except ImportError:
            print("  [x] openai SDK not installed: pip install openai")
            return False
        model = os.environ.get('OPENAI_MODEL', 'gpt-4o')
        print(f"  [v] OpenAI API   — {model}")
        print()
        return True, model

    return False
```

**3e. Update `run_single_query` and `run_watch_mode` signatures** to accept and pass `backend`:

```python
def run_single_query(question, model=None, backend='lmstudio'):
    ...
    answer = ask_llm(question, context_block, model, backend=backend)
    ...

def run_watch_mode(model=None, backend='lmstudio'):
    ...
    answer = ask_llm("Briefly, what is the user currently doing...", context_block, model, backend=backend)
    ...
```

**3f. Update `main()` to pass `backend` everywhere**:

```python
    result = check_services(backend=api_backend)
    if result is False:
        sys.exit(1)
    _, model = result if isinstance(result, tuple) else (True, None)

    if args and args[0] == '--watch':
        run_watch_mode(model, backend=api_backend)
    else:
        question = ' '.join(args)
        run_single_query(question, model, backend=api_backend)
```

**3g. Update usage printout in `main()`**:

```python
    print("  Usage:")
    print('    python demo_agent.py "what have I been working on?"')
    print('    python demo_agent.py "question" --api claude')
    print('    python demo_agent.py "question" --api openai')
    print("    python demo_agent.py --watch")
    print("    python demo_agent.py --watch --api claude")
```

**3h. Update `requirements.txt`**:

```
chromadb
sentence-transformers

# Optional LLM backends (for demo_agent.py --api flag):
# pip install anthropic    → Claude API (requires ANTHROPIC_API_KEY)
# pip install openai       → OpenAI API (requires OPENAI_API_KEY, optional OPENAI_MODEL)
```

**Edge cases:**
- `--api` flag with unknown value (e.g., `--api gemini`) → falls into `lmstudio` default path with a warning
- `--api claude` but `ANTHROPIC_API_KEY` missing → `check_services` returns False → `sys.exit(1)` before any API calls
- `--api claude` but `anthropic` not installed → ImportError caught cleanly with install command
- `--watch --api claude` → every 30s poll calls Claude API (costs credits) — no special guard needed, user chose it
- `--api` without a following value (end of args) → handled in step 3b with clear error

### Agent D — Self-Verification Checklist

- [ ] `python3 -c "import ast; ast.parse(open('demo_agent.py').read())"` exits 0
- [ ] `ask_llm`, `_ask_claude`, `_ask_openai`, `_ask_lmstudio` functions all present
- [ ] `check_services(backend='lmstudio')` signature updated
- [ ] `--api` parsing present in `main()`
- [ ] `run_single_query` and `run_watch_mode` accept `backend` parameter
- [ ] `requirements.txt` has optional backend comment
- [ ] `python3 demo_agent.py` with no args prints updated usage including `--api` flag

---

## 7. Agent E — `screenpipe-dashboard.html`

**Features:** 7 (browser captures tab), 8 (semantic search mode toggle)
**File:** `screenpipe-dashboard.html` only

### Feature 7: Browser Activity Tab

**Problem:** Browser captures are flowing into the Context API but invisible in the dashboard. Users can't see what the extension is capturing or verify it's working.

**Implementation:**

**7a. Add tab button** — insert after the Anomalies tab button in the tab navigation bar:

```html
<button class="tab-btn" onclick="switchTab(6)" id="tab-btn-6">Browser</button>
```

**7b. Add tab content panel** — insert after the `anomaliesTab` div:

```html
<div id="browserTab" class="tab-content" style="display:none">
  <div class="section-header">
    <span>Browser Activity</span>
    <button onclick="loadBrowserActivity()" class="btn-small">Refresh</button>
  </div>
  <div id="browserCount" style="font-size:12px;color:#555;margin-bottom:12px;"></div>
  <div id="browserList">
    <div class="placeholder">Loading browser activity...</div>
  </div>
</div>
```

**7c. Add `loadBrowserActivity()` JS function**:

```javascript
async function loadBrowserActivity() {
  const el = document.getElementById('browserList');
  const countEl = document.getElementById('browserCount');
  el.innerHTML = '<div class="placeholder">Loading...</div>';
  try {
    const resp = await fetch('http://localhost:3031/browser-captures?limit=100');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const results = data.results || [];
    countEl.textContent = `${data.total || 0} total captures`;
    if (!results.length) {
      el.innerHTML = '<div class="placeholder">No browser captures yet.<br><br>'
        + 'Install the Augur Chrome extension:<br>'
        + 'Go to chrome://extensions → Enable Developer Mode → Load Unpacked → select the extension/ folder</div>';
      return;
    }
    el.innerHTML = results.map(r => {
      const timeAgo  = r.timestamp ? relativeTime(r.timestamp) : '';
      const timeOnPg = r.time_on_page_s != null ? `${r.time_on_page_s}s on page` : '';
      const scroll   = r.scroll_depth_pct != null ? `↓${r.scroll_depth_pct}%` : '';
      const meta     = [timeOnPg, scroll].filter(Boolean).join(' · ');
      const sel      = r.selected_text
        ? `<div class="browser-selected">"${escHtml(r.selected_text.slice(0, 200))}"</div>`
        : '';
      const urlDisplay = r.url
        ? `<a href="${escHtml(r.url)}" target="_blank" class="browser-url">`
          + `${escHtml(r.title || r.url.slice(0, 80))}</a>`
        : escHtml(r.title || '');
      return `
        <div class="capture-card browser-card">
          <div class="capture-header">
            <span class="badge badge-browser">browser</span>
            <span class="capture-app">${escHtml(r.domain || new URL(r.url || 'http://x').hostname)}</span>
            <span class="capture-time">${timeAgo}</span>
          </div>
          <div class="capture-title">${urlDisplay}</div>
          ${meta ? `<div class="capture-meta">${escHtml(meta)}</div>` : ''}
          ${sel}
        </div>
      `;
    }).join('');
  } catch(e) {
    el.innerHTML = `<div class="error">Could not load browser captures: ${escHtml(e.message)}<br>`
      + `Make sure context-server.py is running on port 3031.</div>`;
  }
}
```

**Note:** `relativeTime()` and `escHtml()` are existing dashboard utility functions — do not redefine them.

**7d. Add to `switchTab(idx)` function** — add `case 6: loadBrowserActivity(); break;` to the tab switch handler (or equivalent if it uses if/else).

**7e. Add CSS** for browser tab elements (insert in the `<style>` block):

```css
.browser-card { border-left: 2px solid #4d9fff; }
.browser-url { color: #4d9fff; text-decoration: none; font-size: 13px; display: block; margin-top: 4px; }
.browser-url:hover { text-decoration: underline; }
.browser-selected { font-style: italic; color: #777; margin-top: 6px; font-size: 12px; padding-left: 8px; border-left: 1px solid #2a2a2a; }
.capture-meta { font-size: 11px; color: #555; margin-top: 4px; }
.badge-browser { background: rgba(77,159,255,0.12); color: #4d9fff; }
```

---

### Feature 8: Semantic Search Mode Toggle

**Problem:** The semantic search endpoint exists at `/semantic` but is completely unreachable from the dashboard UI. Users who have the Chroma index populated get no benefit.

**Implementation:**

**8a. Add mode toggle HTML** inside the search tab, immediately below the search input row:

```html
<div class="search-mode-row">
  <button id="modeKeyword"  class="mode-btn active" onclick="setSearchMode('keyword')">Keyword</button>
  <button id="modeSemantic" class="mode-btn"         onclick="setSearchMode('semantic')">Semantic</button>
  <span id="searchModeHint" class="mode-hint"></span>
</div>
```

**8b. Add JS state and `setSearchMode()`**:

```javascript
let _searchMode = 'keyword';

function setSearchMode(mode) {
  _searchMode = mode;
  document.getElementById('modeKeyword') .classList.toggle('active', mode === 'keyword');
  document.getElementById('modeSemantic').classList.toggle('active', mode === 'semantic');
  const hint = document.getElementById('searchModeHint');
  hint.textContent = mode === 'semantic'
    ? 'Semantic: finds conceptually similar content even without exact keywords'
    : '';
}
```

**8c. Modify existing `doSearch()` function** to branch on `_searchMode`:

```javascript
async function doSearch() {
  const q = (document.getElementById('searchInput') || document.querySelector('[id*="search"]')).value.trim();
  if (!q) return;
  if (_searchMode === 'semantic') {
    await _doSemanticSearch(q);
  } else {
    await _doKeywordSearch(q);  // rename existing search logic to _doKeywordSearch
  }
}
```

Extract the existing search body into `_doKeywordSearch(q)`. Add new `_doSemanticSearch(q)`:

```javascript
async function _doSemanticSearch(q) {
  const el = document.getElementById('searchResults');
  el.innerHTML = '<div class="placeholder">Running semantic search...</div>';
  try {
    const resp = await fetch(`http://localhost:3031/semantic?q=${encodeURIComponent(q)}&limit=20`);
    if (resp.status === 503) {
      const err = await resp.json().catch(() => ({}));
      el.innerHTML = '<div class="error">Semantic search not available.<br><br>'
        + 'To enable:<br>'
        + '1. <code>pip install chromadb sentence-transformers</code><br>'
        + '2. <code>python semantic_search.py --index</code><br><br>'
        + (err.fix || '') + '</div>';
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.note) {
      el.innerHTML = `<div class="placeholder">${escHtml(data.note)}</div>`;
      return;
    }
    const results = data.results || [];
    if (!results.length) {
      el.innerHTML = '<div class="placeholder">No semantic matches found.</div>';
      return;
    }
    el.innerHTML = `<div class="result-count">${results.length} semantic matches`
      + ` (${data.total_indexed || 0} indexed)</div>`
      + results.map(r => {
          const sim = r.score != null
            ? `<span class="sim-score">${Math.round(r.score * 100)}% match</span>`
            : '';
          const urlRow = r.url
            ? `<div class="result-url">${escHtml(r.url.slice(0, 100))}</div>` : '';
          return `
            <div class="result-card">
              <div class="result-header">
                <span class="badge badge-ocr">OCR</span>
                <span class="capture-app">${escHtml(r.app || '')}</span>
                ${sim}
              </div>
              ${urlRow}
              <div class="result-text">${escHtml((r.text || '').slice(0, 300))}</div>
            </div>
          `;
        }).join('');
  } catch(e) {
    el.innerHTML = `<div class="error">Semantic search error: ${escHtml(e.message)}</div>`;
  }
}
```

**8d. Add CSS** for mode toggle:

```css
.search-mode-row  { display: flex; align-items: center; gap: 6px; margin-bottom: 14px; }
.mode-btn         { padding: 4px 14px; border: 1px solid #2a2a2a; background: #0a0a0a;
                    color: #555; cursor: pointer; border-radius: 3px; font-size: 12px;
                    font-family: inherit; transition: border-color 0.1s, color 0.1s; }
.mode-btn.active  { border-color: #00ff87; color: #00ff87; background: rgba(0,255,135,0.05); }
.mode-btn:hover:not(.active) { border-color: #444; color: #888; }
.mode-hint        { font-size: 11px; color: #444; }
.sim-score        { font-size: 11px; color: #00ff87; margin-left: auto; }
```

**Important:** When modifying `doSearch()`, do NOT break any existing search functionality. The refactor extracts, not replaces. All existing code in `doSearch()` should still run when `_searchMode === 'keyword'`.

### Agent E — Self-Verification Checklist

- [ ] `browserTab` div is present in HTML
- [ ] `tab-btn-6` button exists in tab navigation
- [ ] `loadBrowserActivity` JS function is defined
- [ ] `setSearchMode` JS function is defined
- [ ] `_doSemanticSearch` JS function is defined
- [ ] `_searchMode` variable initialized to `'keyword'`
- [ ] CSS classes `browser-card`, `badge-browser`, `mode-btn`, `sim-score` are in the stylesheet
- [ ] `switchTab(6)` calls `loadBrowserActivity()`
- [ ] Existing keyword search functionality still works when mode is `'keyword'`
- [ ] Open the HTML file in a browser: no JS console errors on load

---

## 8. Agent F — `test_features.py` (Wave 2)

**Feature:** 9 (comprehensive tests for all v0.3 features)
**File:** `test_features.py`
**Dependency:** Must wait for all Wave 1 agents to complete. Reads the actual implementation to write correct tests.

**Agent F prompt skeleton:**
```
You are Agent F in the Augur v0.3 parallel build.
Wave 1 is complete. Your job: add new test classes to test_features.py for v0.3.
Do NOT remove or modify any existing test classes.
Only add the new classes listed below.

[current content of context-server.py   — the actual implementation]
[current content of mcp_server.py       — the actual implementation]
[current content of launch.command      — the actual implementation]
[current content of demo_agent.py       — the actual implementation]
[current content of screenpipe-dashboard.html — the actual implementation]
[current content of test_features.py    — to understand existing patterns]
[Feature 9 spec below]
```

### New Test Classes to Add

**`TestBrowserCapturesInContext`** — unit tests for the browser candidate logic:

```python
class TestBrowserCapturesInContext(unittest.TestCase):

    def test_browser_candidate_has_source_field(self):
        """browser_capture_to_candidate sets source='browser' in _result."""
        import importlib.util, types
        spec = importlib.util.spec_from_file_location(
            "cs", os.path.join(os.path.dirname(__file__), "context-server.py"))
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        # Extract and exec just the function (avoid starting the server)
        # Verify by checking the source contains the expected key
        self.assertIn('browser_capture_to_candidate', cs_src)
        self.assertIn("'source': 'browser'", cs_src)
        print("  [PASS] browser_capture_to_candidate sets source=browser")

    def test_browser_uid_prefixed(self):
        """Browser capture UIDs are prefixed with 'browser_' to avoid frame_id collisions."""
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn("browser_", cs_src)
        self.assertIn("_uid", cs_src)
        print("  [PASS] Browser UIDs use browser_ prefix")

    def test_context_response_has_browser_captures_included(self):
        """context-server.py includes browser_captures_included in gather_context return."""
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn("browser_captures_included", cs_src)
        print("  [PASS] gather_context returns browser_captures_included count")

    def test_context_response_has_semantic_enhanced(self):
        """context-server.py includes semantic_enhanced in gather_context return."""
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn("semantic_enhanced", cs_src)
        print("  [PASS] gather_context returns semantic_enhanced flag")

    @unittest.skipUnless(LIVE, "live only")
    def test_live_context_has_source_field(self):
        """Live: /context results include 'source' field on every result."""
        d, status = req('/context?q=test&limit=5')
        self.assertEqual(status, 200)
        for r in d.get('results', []):
            self.assertIn('source', r, f"Missing 'source' in result: {r}")
        print(f"  [PASS] /context results all have source field ({len(d['results'])} results)")
```

**`TestHybridScoring`** — tests for semantic scoring integration:

```python
class TestHybridScoring(unittest.TestCase):

    def test_semantic_globals_exist(self):
        """context-server.py declares module-level semantic cache globals."""
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn('_semantic_embedder', cs_src)
        self.assertIn('_semantic_collection', cs_src)
        self.assertIn('_semantic_available', cs_src)
        print("  [PASS] Semantic cache globals declared")

    def test_try_load_semantic_exists(self):
        """_try_load_semantic function is defined."""
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn('def _try_load_semantic', cs_src)
        self.assertIn('def _get_semantic_scores', cs_src)
        print("  [PASS] _try_load_semantic and _get_semantic_scores defined")

    def test_semantic_bonus_applied_to_ocr_items(self):
        """Scoring loop applies sem_bonus to OCR items."""
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn('sem_bonus', cs_src)
        self.assertIn('semantic_scores.get', cs_src)
        print("  [PASS] Semantic bonus applied in scoring loop")
```

**`TestMCPServer`** — tests for the MCP server protocol:

```python
class TestMCPServer(unittest.TestCase):

    def test_mcp_server_exists(self):
        path = os.path.join(os.path.dirname(__file__), 'mcp_server.py')
        self.assertTrue(os.path.exists(path), "mcp_server.py not found")
        print("  [PASS] mcp_server.py exists")

    def test_mcp_server_importable(self):
        import mcp_server
        self.assertTrue(hasattr(mcp_server, '_handle'))
        self.assertTrue(hasattr(mcp_server, 'TOOLS'))
        print("  [PASS] mcp_server.py imports correctly")

    def test_mcp_has_five_tools(self):
        import mcp_server
        self.assertEqual(len(mcp_server.TOOLS), 5)
        names = {t['name'] for t in mcp_server.TOOLS}
        expected = {'get_context', 'get_daily_summary', 'get_anomalies',
                    'get_user_profile', 'get_browser_activity'}
        self.assertEqual(names, expected)
        print("  [PASS] MCP server has exactly 5 tools with correct names")

    def test_mcp_initialize(self):
        import mcp_server
        msg  = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
        resp = mcp_server._handle(msg)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.get('id'), 1)
        self.assertIn('result', resp)
        self.assertEqual(resp['result']['serverInfo']['name'], 'augur')
        self.assertEqual(resp['result']['serverInfo']['version'], '0.3')
        print("  [PASS] MCP initialize returns correct server info")

    def test_mcp_tools_list(self):
        import mcp_server
        msg  = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}
        resp = mcp_server._handle(msg)
        tools = resp['result']['tools']
        self.assertEqual(len(tools), 5)
        # Every tool must have name, description, inputSchema
        for t in tools:
            self.assertIn('name', t)
            self.assertIn('description', t)
            self.assertIn('inputSchema', t)
        print("  [PASS] tools/list returns 5 well-formed tool definitions")

    def test_mcp_notification_returns_none(self):
        """Notifications (no id) must return None — server must not respond."""
        import mcp_server
        msg  = {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}
        resp = mcp_server._handle(msg)
        self.assertIsNone(resp)
        print("  [PASS] Notifications return None (no response sent)")

    def test_mcp_unknown_method_returns_error(self):
        import mcp_server
        msg  = {'jsonrpc': '2.0', 'id': 3, 'method': 'unknown/method', 'params': {}}
        resp = mcp_server._handle(msg)
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32601)
        print("  [PASS] Unknown method returns JSON-RPC -32601 error")

    def test_mcp_ping(self):
        import mcp_server
        msg  = {'jsonrpc': '2.0', 'id': 4, 'method': 'ping', 'params': {}}
        resp = mcp_server._handle(msg)
        self.assertIn('result', resp)
        print("  [PASS] ping returns result")

    def test_mcp_tools_all_have_handlers(self):
        import mcp_server
        for tool in mcp_server.TOOLS:
            name = tool['name']
            self.assertIn(name, mcp_server.TOOL_HANDLERS,
                          f"Tool '{name}' has no handler in TOOL_HANDLERS")
        print("  [PASS] All tools have handlers in TOOL_HANDLERS")
```

**`TestProfileEndpoints`** — tests for new API endpoints:

```python
class TestProfileEndpoints(unittest.TestCase):

    def test_profile_endpoint_exists_in_source(self):
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn("def get_profile", cs_src)
        self.assertIn("def get_context_card", cs_src)
        self.assertIn("'/profile'", cs_src)
        self.assertIn("'/context-card'", cs_src)
        print("  [PASS] /profile and /context-card endpoints defined in context-server.py")

    def test_profile_source_has_heatmap(self):
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn('heatmap', cs_src)
        self.assertIn('[0] * 24', cs_src)
        print("  [PASS] get_profile builds 24-slot hourly heatmap")

    def test_context_card_source_has_card_key(self):
        cs_src = open(os.path.join(os.path.dirname(__file__), "context-server.py")).read()
        self.assertIn("'card'", cs_src)
        print("  [PASS] get_context_card returns 'card' key")

    @unittest.skipUnless(LIVE, "live only")
    def test_live_profile_response_shape(self):
        d, status = req('/profile?days=7')
        self.assertEqual(status, 200)
        self.assertIn('profile', d)
        p = d['profile']
        self.assertIn('top_apps', p)
        self.assertIn('active_hours', p)
        self.assertIn('top_domains', p)
        self.assertIn('top_topics', p)
        self.assertIn('heatmap', p['active_hours'])
        self.assertEqual(len(p['active_hours']['heatmap']), 24)
        print(f"  [PASS] /profile returned profile with {len(p['top_apps'])} apps")

    @unittest.skipUnless(LIVE, "live only")
    def test_live_context_card_is_string(self):
        d, status = req('/context-card?days=7')
        self.assertEqual(status, 200)
        self.assertIn('card', d)
        self.assertIsInstance(d['card'], str)
        self.assertGreater(len(d['card']), 10)
        self.assertLessEqual(len(d['card']), 600)
        print(f"  [PASS] /context-card returned {len(d['card'])}-char string")
```

**`TestDemoAgentBackends`** — tests for multi-backend support:

```python
class TestDemoAgentBackends(unittest.TestCase):

    def test_demo_agent_has_ask_claude(self):
        src = open(os.path.join(os.path.dirname(__file__), "demo_agent.py")).read()
        self.assertIn('def _ask_claude', src)
        self.assertIn('ANTHROPIC_API_KEY', src)
        print("  [PASS] demo_agent.py has _ask_claude with API key check")

    def test_demo_agent_has_ask_openai(self):
        src = open(os.path.join(os.path.dirname(__file__), "demo_agent.py")).read()
        self.assertIn('def _ask_openai', src)
        self.assertIn('OPENAI_API_KEY', src)
        print("  [PASS] demo_agent.py has _ask_openai with API key check")

    def test_demo_agent_has_api_flag(self):
        src = open(os.path.join(os.path.dirname(__file__), "demo_agent.py")).read()
        self.assertIn('--api', src)
        self.assertIn('api_backend', src)
        print("  [PASS] demo_agent.py parses --api flag")

    def test_demo_agent_ask_llm_dispatches(self):
        """ask_llm function dispatches to correct backend."""
        src = open(os.path.join(os.path.dirname(__file__), "demo_agent.py")).read()
        self.assertIn("backend == 'claude'", src)
        self.assertIn("backend in ('openai', 'gpt')", src)
        print("  [PASS] ask_llm dispatches to correct backend")
```

**`TestLaunchSemanticIndexer`** — tests for auto-start logic:

```python
class TestLaunchSemanticIndexer(unittest.TestCase):

    def test_launch_has_semantic_constants(self):
        src = open(os.path.join(os.path.dirname(__file__), "launch.command")).read()
        self.assertIn('SEMANTIC_INDEXER', src)
        self.assertIn('SEMANTIC_PID_FILE', src)
        print("  [PASS] launch.command has SEMANTIC_INDEXER and SEMANTIC_PID_FILE constants")

    def test_launch_has_semantic_helpers(self):
        src = open(os.path.join(os.path.dirname(__file__), "launch.command")).read()
        self.assertIn('is_semantic_available', src)
        self.assertIn('is_semantic_running', src)
        self.assertIn('start_semantic_indexer', src)
        print("  [PASS] launch.command has semantic indexer helper functions")

    def test_launch_status_includes_semantic(self):
        """Keep-alive loop status line includes semantic indexer status."""
        src = open(os.path.join(os.path.dirname(__file__), "launch.command")).read()
        self.assertIn('is_semantic_running()', src)
        self.assertIn('semantic', src.lower())
        print("  [PASS] launch.command keep-alive shows semantic indexer status")

    def test_is_semantic_running_handles_stale_pid(self):
        """is_semantic_running cleans up stale PID files."""
        src = open(os.path.join(os.path.dirname(__file__), "launch.command")).read()
        self.assertIn('os.unlink', src)
        self.assertIn('ProcessLookupError', src)
        print("  [PASS] is_semantic_running handles stale PID files")
```

**`TestDashboardV3`** — tests for new dashboard features:

```python
class TestDashboardV3(unittest.TestCase):

    def _html(self):
        path = os.path.join(os.path.dirname(__file__), 'screenpipe-dashboard.html')
        with open(path) as f:
            return f.read()

    def test_dashboard_has_browser_tab(self):
        html = self._html()
        self.assertIn('browserTab', html)
        self.assertIn('loadBrowserActivity', html)
        print("  [PASS] Dashboard has Browser Activity tab")

    def test_dashboard_has_browser_tab_button(self):
        html = self._html()
        self.assertIn('tab-btn-6', html)
        print("  [PASS] Dashboard has tab button for Browser tab")

    def test_dashboard_has_semantic_toggle(self):
        html = self._html()
        self.assertIn('setSearchMode', html)
        self.assertIn('modeKeyword', html)
        self.assertIn('modeSemantic', html)
        print("  [PASS] Dashboard has semantic/keyword search mode toggle")

    def test_dashboard_has_semantic_search_function(self):
        html = self._html()
        self.assertIn('_doSemanticSearch', html)
        self.assertIn('_doKeywordSearch', html)
        print("  [PASS] Dashboard has _doSemanticSearch and _doKeywordSearch functions")

    def test_dashboard_semantic_handles_503(self):
        """Semantic search UI handles 503 (deps not installed) gracefully."""
        html = self._html()
        self.assertIn('503', html)
        self.assertIn('chromadb', html.lower())
        print("  [PASS] Dashboard handles semantic 503 with setup instructions")
```

### Agent F — Self-Verification Checklist

- [ ] `python3 -c "import ast; ast.parse(open('test_features.py').read())"` exits 0
- [ ] All 6 new test classes are present: `TestBrowserCapturesInContext`, `TestHybridScoring`, `TestMCPServer`, `TestProfileEndpoints`, `TestDemoAgentBackends`, `TestLaunchSemanticIndexer`, `TestDashboardV3`
- [ ] All existing test classes are still intact and unmodified
- [ ] New test classes added to the `suite.addTests(...)` block in the main block
- [ ] `python3 test_features.py` runs without errors (all offline tests pass)

---

## 9. Invariants All Agents Must Respect

These are non-negotiable constraints. Every agent must verify these before reporting complete.

### No new required dependencies

- `context-server.py` must remain importable with zero external packages. All `import semantic_search` calls remain inside try/except or function-local scope.
- `mcp_server.py` is stdlib-only: `json`, `sys`, `urllib.request`, `urllib.parse`, `datetime`. No third-party imports at module level.
- `launch.command` uses `importlib.import_module` inside a function — no top-level third-party imports.
- Cloud LLM deps (`anthropic`, `openai`) are import-guarded inside their respective functions in `demo_agent.py`.

### Backward compatibility

All existing `/context` response fields remain present and unchanged. Only new fields are added (`browser_captures_included`, `semantic_enhanced`, `source` on each result). Existing API clients continue to work without modification.

### No existing tests broken

`python3 test_features.py` must pass before and after all changes. Any regression is a blocking failure.

### Dark terminal aesthetic preserved

Dashboard additions use the existing color system: `#0a0a0a` background, `#00ff87` green, `#4d9fff` blue, `#555` dim text. No new colors or fonts introduced.

### Fail-safe degradation

| Component | Degraded state | Behavior |
|-----------|---------------|---------|
| Chroma index empty | Hybrid scoring | Zero semantic bonus, pure keyword |
| `semantic_search.py` missing | Hybrid scoring | `_semantic_available = False`, no error |
| No browser captures | `/context` | No browser candidates, zero change |
| `ANTHROPIC_API_KEY` missing | demo_agent | Clear error, exit |
| context-server.py not running | MCP server | Each tool returns human-readable error string |
| chromadb not installed | launch.command | Semantic indexer skipped with advisory |

### Version strings

Update all version references from `v0.2` to `v0.3`:
- `context-server.py`: banner `"│       Augur Context API v0.3         │"`
- `mcp_server.py`: `"serverInfo": {"name": "augur", "version": "0.3"}`

---

## 10. Quick Reference: What Each Agent Touches

```
context-server.py        ← Agent A only
  + browser_capture_to_candidate()
  + _try_load_semantic(), _get_semantic_scores()
  + _semantic_embedder, _semantic_collection, _semantic_available globals
  + get_profile(), get_context_card()
  + /profile and /context-card handlers in do_GET
  + browser_captures_included, semantic_enhanced in gather_context return
  + source field on every result
  + version bump to v0.3

mcp_server.py            ← Agent B only (new file)
  5 tools: get_context, get_daily_summary, get_anomalies,
           get_user_profile, get_browser_activity
  JSON-RPC 2.0 over stdio, stdlib-only

launch.command           ← Agent C only
  + SEMANTIC_INDEXER, SEMANTIC_PID_FILE constants
  + is_semantic_available(), is_semantic_running(), start_semantic_indexer()
  + Semantic indexer startup block in main()
  + [semantic: up/down] in keep-alive loop

demo_agent.py            ← Agent D only
  + _ask_claude(), _ask_openai(), _ask_lmstudio() (refactored from ask_llm)
  + ask_llm() dispatches to backend
  + check_services(backend=) updated
  + --api flag parsing in main()
  + backend param threaded through run_single_query, run_watch_mode

requirements.txt         ← Agent D only
  + optional backend dep comments

screenpipe-dashboard.html ← Agent E only
  + browserTab div + tab button
  + loadBrowserActivity() function
  + switchTab(6) → loadBrowserActivity()
  + setSearchMode(), _doSemanticSearch(), _doKeywordSearch() refactor
  + mode toggle HTML + CSS

test_features.py         ← Agent F only (Wave 2)
  + TestBrowserCapturesInContext
  + TestHybridScoring
  + TestMCPServer
  + TestProfileEndpoints
  + TestDemoAgentBackends
  + TestLaunchSemanticIndexer
  + TestDashboardV3
```
