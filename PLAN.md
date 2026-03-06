# Augur v0.3 — Implementation Plan

**Status:** Planning
**Based on:** v0.2 shipped (semantic search, browser extension, anomaly detection)
**Goal:** Close the loop between all capture sources; add MCP server for agent-native integration; add cloud LLM support; polish the product toward a demo-ready state.

---

## Overview of v0.3 Features

| # | Feature | Files Changed | Effort |
|---|---------|--------------|--------|
| 1 | Browser captures wired into `/context` ranking | `context-server.py` | Medium |
| 2 | Auto-start semantic indexer from `launch.command` | `launch.command` | Small |
| 3 | Claude + OpenAI API backends in `demo_agent.py` | `demo_agent.py`, `requirements.txt` | Medium |
| 4 | Hybrid context scoring (semantic + keyword blended) | `context-server.py` | Medium |
| 5 | MCP server (`mcp_server.py`) | New file | Large |
| 6 | `/profile` and `/context-card` endpoints | `context-server.py` | Small |
| 7 | Dashboard: browser captures tab | `screenpipe-dashboard.html` | Medium |
| 8 | Dashboard: semantic search mode toggle | `screenpipe-dashboard.html` | Small |
| 9 | Tests for all v0.3 features | `test_features.py` | Medium |
| 10 | README + PRODUCT.md updated | `README.md`, `DOCS/PRODUCT.md` | Small |

---

## Feature 1: Browser Extension Captures in `/context` Ranking

### Problem
Browser captures (URL, title, time on page, scroll depth, selected text) are stored in `~/.screenpipe/browser_captures.json` and returned by `/browser-captures`, but they are **completely ignored** when `/context` assembles ranked results. A user who spent 45 minutes reading a page about venture pricing has zero of that signal in context retrieval.

### Solution
Merge browser captures into the candidate pool inside `gather_context()` before scoring. Apply a modified scoring formula that rewards high dwell time and text selection.

### Implementation — `context-server.py`

**Step 1: Convert browser capture to scoreable candidate**

Add a helper function `browser_capture_to_candidate(cap)` that converts a browser capture dict to the same shape as a screenpipe item:

```python
def browser_capture_to_candidate(cap):
    """Convert a browser capture entry to a pseudo-item for scoring."""
    url = cap.get('url', '')
    domain = cap.get('domain', '')
    title = cap.get('title', '')
    selected = cap.get('selected_text', '') or ''
    ts = cap.get('timestamp', '') or cap.get('received_at', '')
    time_s = cap.get('time_on_page_s', 0) or 0
    scroll = cap.get('scroll_depth_pct', 0) or 0

    # Build searchable blob: title + domain + url + selected text
    blob = ' '.join(filter(None, [title, domain, url[:200], selected[:400]])).lower()

    # Unique ID: hash of url + minute-truncated timestamp to avoid collisions with frame IDs
    try:
        minute = ts[:16]  # "2026-03-05T14:23"
    except Exception:
        minute = ts
    uid = f"browser_{hash(url + minute) & 0xFFFFFFFF}"

    return {
        '_source': 'browser',
        '_blob': blob,
        '_timestamp': ts,
        '_uid': uid,
        '_time_on_page_s': time_s,
        '_has_selection': bool(selected and len(selected) > 10),
        '_result': {
            'frame_id': uid,
            'timestamp': ts,
            'app': 'Browser',
            'window': title[:120] if title else url[:120],
            'text': (selected[:300] if selected else title) or url,
            'url': url,
            'source': 'browser',
        }
    }
```

**Step 2: Modify `gather_context()` to include browser candidates**

At the start of `gather_context()`, after building `all_items` from screenpipe, also build `browser_candidates` from `_browser_captures`:

```python
# Pull browser captures within the window
browser_candidates = []
cutoff_ts = (datetime.now(timezone.utc) - timedelta(seconds=window_hours * 3600))
for cap in _browser_captures:
    ts_str = cap.get('timestamp') or cap.get('received_at', '')
    try:
        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        if ts.replace(tzinfo=None) >= cutoff_ts.replace(tzinfo=None):
            browser_candidates.append(browser_capture_to_candidate(cap))
    except Exception:
        pass  # skip unparseable timestamps
```

**Step 3: Deduplicate browser candidates by uid**

Browser UIDs (prefixed `browser_`) will never collide with screenpipe `frame_id` integers, so the existing seen-set deduplication works:

```python
for cand in browser_candidates:
    uid = cand['_uid']
    if uid not in seen:
        seen.add(uid)
        all_items.append(cand)  # tagged with _source='browser'
```

**Step 4: Unified scoring loop**

The scoring loop needs to handle both screenpipe items and browser candidates. Add a branch:

```python
for item in all_items:
    source = item.get('_source')

    if source == 'browser':
        blob = item['_blob']
        kw_score = sum(min(blob.count(kw), 5) for kw in keywords)
        try:
            ts = datetime.fromisoformat(
                item['_timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
            age_s = (now - ts).total_seconds()
        except Exception:
            age_s = window_ms
        recency = max(0.0, 1.0 - age_s / window_ms) if window_ms > 0 else 0.0
        # Time bonus: up to 1.0 for 5+ minutes (300s) of dwell time
        time_bonus = min(item['_time_on_page_s'] / 300.0, 1.0)
        # Selection bonus: +2.0 if user selected text (strong intent signal)
        selection_bonus = 2.0 if item['_has_selection'] else 0.0
        item['_score'] = kw_score * 3 + recency + time_bonus + selection_bonus
    else:
        # Existing OCR/audio scoring (unchanged)
        c = item.get('content', {})
        blob = ' '.join([...]).lower()
        ...
```

**Step 5: Extract result from browser candidates**

In the result-building loop, branch on `_source`:

```python
for item in top:
    if item.get('_source') == 'browser':
        results.append({**item['_result'], 'score': round(item.get('_score', 0), 3)})
    else:
        # existing OCR/audio extraction (add 'source': 'ocr' or 'audio')
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

**Step 6: Update response metadata**

Add `browser_captures_included` count to the response object:

```python
return {
    'query': query,
    'keywords': keywords,
    'total_candidates': len(all_items),
    'browser_captures_included': len([i for i in all_items if i.get('_source') == 'browser']),
    'results': results,
    'generated_at': ...,
}
```

### Edge Cases

| Case | Handling |
|------|----------|
| No browser captures | `browser_candidates = []` — no-op, existing behavior unchanged |
| Browser capture with no timestamp | Skip (caught by `except Exception: pass`) |
| Same URL visited twice | Both included — different timestamps → different UIDs |
| Very long selected_text | Already capped at 2000 chars by extension; truncate to 300 in display |
| Browser capture outside window_hours | Filtered out by cutoff_ts comparison |
| Keyword match against URL with special chars | `blob.count(kw)` is substring match, safe for URLs |
| Browser capture from the future (clock skew) | `age_s` will be negative → `recency = max(0, ...)` → 1.0 (treated as fresh) |

---

## Feature 2: Auto-Start Semantic Indexer from `launch.command`

### Problem
`semantic_search.py` must be started manually. Users who don't know this get an empty semantic index and worse context quality.

### Solution
Add the semantic indexer as a 4th managed service in `launch.command`, after the Context API.

### Implementation — `launch.command`

**New constants at top:**

```python
SEMANTIC_INDEXER  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic_search.py")
SEMANTIC_PID_FILE = os.path.expanduser("~/.screenpipe/semantic_indexer.pid")
```

**New helper: `is_semantic_available()`**

```python
def is_semantic_available():
    """Returns True if chromadb and sentence-transformers are importable."""
    try:
        import importlib
        importlib.import_module('chromadb')
        importlib.import_module('sentence_transformers')
        return True
    except ImportError:
        return False
```

**New helper: `is_semantic_running()`**

```python
def is_semantic_running():
    """Check if the semantic indexer process is alive via PID file."""
    if not os.path.exists(SEMANTIC_PID_FILE):
        return False
    try:
        with open(SEMANTIC_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # signal 0 = check existence, does not kill
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False
```

**New helper: `start_semantic_indexer()`**

```python
def start_semantic_indexer():
    """Start semantic_search.py daemon and write PID to file."""
    if not os.path.exists(SEMANTIC_INDEXER):
        return False, "semantic_search.py not found"
    if not is_semantic_available():
        return False, "chromadb not installed (run: pip install chromadb sentence-transformers)"
    try:
        log_file = open(os.path.expanduser("~/.screenpipe/launcher.log"), "a")
        proc = subprocess.Popen(
            [sys.executable, SEMANTIC_INDEXER],   # daemon mode (no --index flag)
            stdout=log_file,
            stderr=log_file,
            start_new_session=True
        )
        with open(SEMANTIC_PID_FILE, 'w') as f:
            f.write(str(proc.pid))
        return True, proc.pid
    except Exception as e:
        return False, str(e)
```

**Insert in `main()` after Context API section:**

```python
# 4. Start semantic indexer
print()
if is_semantic_running():
    print_status("Semantic indexer already running")
elif not is_semantic_available():
    print_status("Semantic indexer: not available (pip install chromadb sentence-transformers)", ok=False)
    print()
    print("  To enable semantic search:")
    print("    pip install chromadb sentence-transformers")
elif os.path.exists(SEMANTIC_INDEXER):
    print("  ⟳  Starting Augur Semantic Indexer...")
    ok, info = start_semantic_indexer()
    if ok:
        time.sleep(2)  # give it time to start loading the model
        print_status(f"Semantic indexer started (PID {info})")
    else:
        print_status(f"Failed to start semantic indexer: {info}", ok=False)
else:
    print_status("semantic_search.py not found — skipping semantic indexer", ok=False)
```

**Update keep-alive loop status line:**

```python
sem = is_semantic_running()
status = (
    f"  [screenpipe: {'up' if sp else 'DOWN'}]"
    f"  [context-api: {'up' if ctx else 'down'}]"
    f"  [semantic: {'up' if sem else 'down'}]"
    f"  [LM Studio: {'up' if lm else 'not running'}]"
)
```

### Edge Cases

| Case | Handling |
|------|----------|
| chromadb not installed | Skip with advisory; status shows "not installed" |
| PID file exists but process crashed | `os.kill(pid, 0)` raises → returns False → restarts |
| semantic_search.py not found | Skip with clear error |
| First indexing run: model download | Model downloads to `~/.cache/` on first run; takes 1-3 min; launcher continues without blocking |
| User runs `launch.command` twice | `is_semantic_running()` returns True → skip, no double-start |

---

## Feature 3: Claude + OpenAI API Backends in `demo_agent.py`

### Problem
`demo_agent.py` hardcodes LM Studio. Investors and power users may want to use Claude or GPT-4 instead, especially for better quality answers in demos.

### Solution
Abstract the LLM call behind a backend selector. Add `--api claude|openai|lmstudio` flag.

### Implementation — `demo_agent.py`

**New CLI parsing:**

```python
# Parse --api flag
api_backend = 'lmstudio'  # default
if '--api' in args:
    idx = args.index('--api')
    if idx + 1 < len(args):
        api_backend = args[idx + 1].lower()
        args = args[:idx] + args[idx + 2:]  # remove flag + value from args
```

**New: LLM backend abstraction**

Replace the single `ask_llm()` with a dispatching function:

```python
def ask_llm(question, context_block, model=None, backend='lmstudio'):
    if backend == 'claude':
        return ask_claude(question, context_block)
    elif backend in ('openai', 'gpt'):
        return ask_openai(question, context_block)
    else:
        return ask_lmstudio(question, context_block, model)
```

**`ask_claude()` implementation:**

```python
def ask_claude(question, context_block):
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
    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": question}]
        )
        return msg.content[0].text
    except Exception as e:
        print(f"  [error] Claude API: {e}")
        return None
```

**`ask_openai()` implementation:**

```python
def ask_openai(question, context_block):
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
    client = openai.OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            temperature=0.6,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": question}
            ]
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"  [error] OpenAI API: {e}")
        return None
```

**`ask_lmstudio()` — refactored from existing `ask_llm()`:**

```python
def ask_lmstudio(question, context_block, model=None):
    system = (
        "You are a helpful AI assistant with access to screenpipe screen capture data.\n"
        "Answer the user's question based on the context below. Be specific, concise, and direct.\n\n"
        f"Screen capture context ({len(context_block.splitlines())} lines):\n\n"
        f"{context_block}\n\nAnswer the question using this data."
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
```

**Updated `check_services()`:**

```python
def check_services(backend='lmstudio'):
    print("  Checking services...")

    ctx = fetch_json(f"{CONTEXT_API}/health")
    if ctx is None:
        print("  [x] Context API not reachable at localhost:3031")
        return False
    print(f"  [v] Context API  — localhost:3031")

    screenpipe_ok = ctx.get('screenpipe', False)
    print(f"  {'[v]' if screenpipe_ok else '[!]'} screenpipe")

    if backend == 'lmstudio':
        lm = fetch_json(f"{LM_STUDIO}/v1/models")
        if lm is None:
            print("  [x] LM Studio not reachable at localhost:1234")
            return False
        models = [m for m in lm.get('data', []) if 'embed' not in m.get('id', '').lower()]
        model_name = models[0]['id'] if models else 'unknown'
        print(f"  [v] LM Studio    — {model_name}")
        print()
        return True, model_name

    elif backend == 'claude':
        key = os.environ.get('ANTHROPIC_API_KEY')
        if not key:
            print("  [x] ANTHROPIC_API_KEY not set — export ANTHROPIC_API_KEY=sk-ant-...")
            return False
        print(f"  [v] Claude API   — claude-opus-4-6")
        print()
        return True, 'claude-opus-4-6'

    elif backend in ('openai', 'gpt'):
        key = os.environ.get('OPENAI_API_KEY')
        if not key:
            print("  [x] OPENAI_API_KEY not set — export OPENAI_API_KEY=sk-...")
            return False
        model = os.environ.get('OPENAI_MODEL', 'gpt-4o')
        print(f"  [v] OpenAI API   — {model}")
        print()
        return True, model

    return False
```

**Updated usage printout:**

```
Usage:
  python demo_agent.py "what have I been working on?"
  python demo_agent.py "question" --api claude
  python demo_agent.py "question" --api openai
  python demo_agent.py --watch
  python demo_agent.py --watch --api claude
```

**`requirements.txt` additions:**

```
# Optional LLM backends (instead of LM Studio):
# pip install anthropic     → Claude API
# pip install openai        → OpenAI API
```

### Edge Cases

| Case | Handling |
|------|----------|
| `--api` flag but SDK not installed | Friendly error with install command |
| API key set but invalid | SDK raises exception → caught, friendly error |
| `--watch` + cloud API | Each poll calls API; user warned it costs API credits |
| `--api` without value | Falls back to lmstudio (safe default) |
| Unknown backend name | Falls through to lmstudio with warning |

---

## Feature 4: Hybrid Context Scoring (Semantic + Keyword Blended)

### Problem
`/context` only uses keyword matching. If the user asks "what was I researching about pricing models?" and they were on a page about "SaaS monetization strategies," keyword search may miss it entirely.

### Solution
When the Chroma index is populated and the embedder is loaded, blend a semantic similarity bonus into the existing score formula. Transparent — no API change needed.

### Implementation — `context-server.py`

**New module-level cached globals:**

```python
_semantic_embedder = None      # loaded once on first hybrid query
_semantic_collection = None    # loaded once on first hybrid query
_semantic_available = None     # None = untested, True/False after first attempt
```

**New helper: `_try_load_semantic()`**

```python
def _try_load_semantic():
    """Attempt to load Chroma collection and embedder. Caches result globally."""
    global _semantic_embedder, _semantic_collection, _semantic_available
    if _semantic_available is not None:
        return _semantic_available  # already tested

    try:
        import semantic_search as ss
        client = ss.get_client()
        col = ss.get_collection(client)
        if col.count() == 0:
            _semantic_available = False  # index empty — skip
            return False
        embedder = ss.get_embedder()
        _semantic_collection = col
        _semantic_embedder = embedder
        _semantic_available = True
        print("  [semantic] hybrid scoring active")
        return True
    except Exception:
        _semantic_available = False
        return False
```

Called once from `main()` at startup (async-style: fires and caches). Also re-attempted lazily on the first `/context` request that needs it.

**New helper: `_get_semantic_scores(query, n)`**

```python
def _get_semantic_scores(query, n):
    """Returns {uid_str: similarity_score} for top-n semantic matches. Empty dict if unavailable."""
    if not _semantic_available or _semantic_embedder is None or _semantic_collection is None:
        return {}
    try:
        import semantic_search as ss
        result = ss.semantic_query(
            query, n_results=n,
            embedder=_semantic_embedder,
            collection=_semantic_collection
        )
        return {str(r['id']): r['score'] for r in result.get('results', [])}
    except Exception:
        return {}
```

**Modify `gather_context()`:**

After building `all_items` and before the scoring loop, fetch semantic scores:

```python
# Attempt semantic bonus (transparent — empty dict if unavailable)
_try_load_semantic()
semantic_scores = _get_semantic_scores(query, limit * 3)
semantic_enhanced = bool(semantic_scores)
```

Inside the scoring loop, add semantic bonus:

```python
# For OCR items:
uid_key = str(c.get('frame_id') or c.get('timestamp') or '')
semantic_bonus = semantic_scores.get(uid_key, 0.0) * 2.0  # 0.0–2.0 bonus
item['_score'] = kw_score * 3 + recency + semantic_bonus
```

**Add to response:**

```python
return {
    ...
    'semantic_enhanced': semantic_enhanced,
    ...
}
```

### Performance Considerations

- Embedder loads take 2–10 seconds on first call — do this in `main()` so it's warm before first request
- Chroma query on 10k+ items takes ~50–200ms — acceptable for the context use case
- If semantic query takes >500ms (rare), it still completes; no timeout needed
- Embedder is cached globally — subsequent requests are fast (~20–50ms per query)
- If semantic_search module is missing or Chroma empty → zero overhead, `{}` returned instantly

### Edge Cases

| Case | Handling |
|------|----------|
| Chroma index empty | `_semantic_available = False`, no bonus applied |
| Embedder load fails | Exception caught → `_semantic_available = False` |
| Screenpipe frame_id not in semantic index | Missing key → `.get(uid, 0.0)` → 0.0 bonus |
| Browser candidates in hybrid mode | Browser captures have `browser_XXXX` UIDs, won't match Chroma frame IDs — score bonus = 0, that's correct |
| Second startup (model already cached) | Model loads from `~/.cache/` in <1s |

---

## Feature 5: MCP Server (`mcp_server.py`)

### Why This Is the Biggest v0.3 Feature

MCP (Model Context Protocol) is the standard for local tool servers that AI agents can call natively. Claude Desktop, Cursor, Zed, and other editors support MCP. Shipping an MCP server means:
- Claude Desktop users get automatic access to their screen context with **zero extra code**
- No need for an agent to implement HTTP calls — just configure `~/.claude/claude_desktop_config.json`
- Augur becomes a first-class context source for any MCP-compatible agent

### MCP Protocol Overview

MCP uses JSON-RPC 2.0 over stdio with `Content-Length` framing (same as Language Server Protocol). Messages look like:

```
Content-Length: 87\r\n
\r\n
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
```

Server responds with the same framing.

**Message flow:**
1. Client sends `initialize` → server responds with capabilities
2. Client sends `tools/list` → server responds with tool definitions
3. Client sends `tools/call` with `name` + `arguments` → server executes, responds with result
4. All other requests → 404-style error

### Implementation — `mcp_server.py` (new file, ~350 lines)

**File header and imports:**
```python
#!/usr/bin/env python3
"""
Augur MCP Server
----------------
Exposes Augur context tools via the Model Context Protocol (MCP).
Any MCP-compatible AI agent (Claude Desktop, Cursor, etc.) can use this.

Usage:
  python mcp_server.py

Configure in Claude Desktop (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "augur": {
        "command": "python3",
        "args": ["/absolute/path/to/mcp_server.py"]
      }
    }
  }

Requires the Augur Context API to be running (python3 context-server.py).
"""

import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime
```

**Protocol I/O layer:**

```python
CONTEXT_API = "http://localhost:3031"

def _read_message():
    """Read one JSON-RPC message from stdin using Content-Length framing."""
    headers = {}
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw or raw == b'\r\n':
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
    """Write one JSON-RPC message to stdout with Content-Length framing."""
    body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    header = f'Content-Length: {len(body)}\r\n\r\n'.encode('utf-8')
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def _log(msg):
    """Log to stderr (stdout is reserved for the MCP protocol)."""
    print(f"[augur-mcp] {msg}", file=sys.stderr, flush=True)
```

**Context API call helper:**

```python
def _call_api(path):
    """Call the local Context API. Returns parsed JSON or None."""
    try:
        url = f"{CONTEXT_API}{path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        _log(f"API error {path}: {e}")
        return None
```

**Tool definitions (for `tools/list` response):**

```python
TOOLS = [
    {
        "name": "get_context",
        "description": (
            "Get ranked, relevant screen captures for a natural language query. "
            "Returns what the user has been doing, reading, or working on, based on "
            "passive screen capture data. Use this to understand user context before responding."
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
            "Includes top apps used, active hours, visited URLs count, and key topics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (default: today)"
                }
            }
        }
    },
    {
        "name": "get_anomalies",
        "description": (
            "Detect behavioral anomalies — apps used significantly more or less than usual, "
            "or new apps appearing. Compares today's activity against recent history."
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
            "Get a compact behavioral profile of the user for the last N days. "
            "Includes top apps, active hours, most visited domains, and topic keywords. "
            "Useful for personalizing agent responses without needing the user to explain themselves."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Days of history to include in profile (default: 7)",
                    "default": 7
                }
            }
        }
    },
    {
        "name": "get_browser_activity",
        "description": (
            "Get recent browser captures: URLs visited, time spent, scroll depth, "
            "and any text the user selected. Complements OCR screen data with intent signals."
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
```

**Tool execution handlers:**

```python
def _tool_get_context(args):
    query = args.get('query', '')
    limit = args.get('limit', 10)
    window_hours = args.get('window_hours', 24)
    if not query:
        return "Error: query is required."
    q = urllib.parse.quote(query)
    data = _call_api(f"/context?q={q}&limit={limit}&window_hours={window_hours}")
    if data is None:
        return "Context API not reachable. Make sure context-server.py is running (python3 context-server.py)."
    results = data.get('results', [])
    if not results:
        return f"No screen context found for: {query}"
    lines = [f"Screen context for: {query}\n(semantic: {'yes' if data.get('semantic_enhanced') else 'no'}, {len(results)} results)\n"]
    for r in results:
        ts = r.get('timestamp', '')
        try:
            t = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', ''))
            time_str = t.strftime('%H:%M')
        except Exception:
            time_str = ts[:16]
        app = r.get('app') or r.get('source') or 'unknown'
        window = r.get('window') or ''
        text = (r.get('text') or '').strip()[:300]
        url = r.get('url', '')
        source_tag = f" [{r.get('source', 'ocr')}]" if r.get('source') else ''
        lines.append(f"[{time_str}] {app}{(' / ' + window[:50]) if window else ''}{source_tag}")
        if url:
            lines.append(f"  URL: {url[:120]}")
        if text:
            lines.append(f"  {text[:200]}")
        lines.append('')
    return '\n'.join(lines)[:4000]  # hard cap for LLM context


def _tool_get_daily_summary(args):
    date = args.get('date', datetime.now().strftime('%Y-%m-%d'))
    data = _call_api(f"/summary?date={date}")
    if data is None:
        return "Context API not reachable."
    p = data.get('profile', {})
    lines = [f"Daily summary for {date}:"]
    lines.append(f"  Total frames: {p.get('total_frames', 0)}")
    lines.append(f"  URLs visited: {p.get('urls_visited', 0)}")
    lines.append(f"  Audio chunks: {p.get('audio_chunks', 0)}")
    apps = p.get('top_apps', [])
    if apps:
        lines.append("  Top apps: " + ", ".join(f"{a['app']} ({a['frames']} frames)" for a in apps[:5]))
    hours = p.get('active_hours', [])
    if hours:
        lines.append(f"  Most active hours: {', '.join(str(h) + ':00' for h in hours[:3])}")
    topics = p.get('topics', [])
    if topics:
        lines.append(f"  Key topics: {', '.join(topics[:15])}")
    return '\n'.join(lines)


def _tool_get_anomalies(args):
    days = args.get('days', 7)
    data = _call_api(f"/anomalies?days={days}")
    if data is None:
        return "Context API not reachable."
    anomalies = data.get('anomalies', [])
    if not anomalies:
        return f"No behavioral anomalies detected (vs {days}-day baseline)."
    lines = [f"Behavioral anomalies (vs {days}-day baseline):"]
    for a in anomalies[:10]:
        lines.append(f"  {a.get('message', str(a))}")
    return '\n'.join(lines)


def _tool_get_user_profile(args):
    days = args.get('days', 7)
    data = _call_api(f"/profile?days={days}")
    if data is None:
        return "Context API not reachable."
    p = data.get('profile', {})
    lines = [f"User profile ({days}-day window):"]
    apps = p.get('top_apps', [])
    if apps:
        lines.append("  Top apps: " + ", ".join(f"{a['app']} ({a.get('hours', 0):.1f}h)" for a in apps[:5]))
    domains = p.get('top_domains', [])
    if domains:
        lines.append("  Top domains: " + ", ".join(d['domain'] for d in domains[:5]))
    topics = p.get('top_topics', [])
    if topics:
        lines.append(f"  Key topics: {', '.join(topics[:15])}")
    hours = p.get('active_hours', {}).get('heatmap', [])
    if hours:
        peak = hours.index(max(hours)) if max(hours) > 0 else None
        if peak is not None:
            lines.append(f"  Peak hour: {peak}:00")
    return '\n'.join(lines)


def _tool_get_browser_activity(args):
    limit = args.get('limit', 20)
    data = _call_api(f"/browser-captures?limit={limit}")
    if data is None:
        return "Context API not reachable."
    results = data.get('results', [])
    if not results:
        return "No browser activity captured. Make sure the Augur Chrome extension is installed."
    lines = [f"Recent browser activity ({len(results)} captures):"]
    for r in results[:20]:
        title = r.get('title', '')
        url = r.get('url', '')
        time_s = r.get('time_on_page_s')
        scroll = r.get('scroll_depth_pct')
        sel = r.get('selected_text', '')
        line = f"  {title or url[:80]}"
        if time_s is not None:
            line += f" ({time_s}s)"
        if scroll is not None:
            line += f" scroll:{scroll}%"
        lines.append(line)
        if sel:
            lines.append(f"    Selected: {sel[:150]}")
    return '\n'.join(lines)[:3000]
```

**Main dispatch loop:**

```python
TOOL_HANDLERS = {
    'get_context': _tool_get_context,
    'get_daily_summary': _tool_get_daily_summary,
    'get_anomalies': _tool_get_anomalies,
    'get_user_profile': _tool_get_user_profile,
    'get_browser_activity': _tool_get_browser_activity,
}


def _handle(msg):
    """Process one JSON-RPC message and return the response dict."""
    method = msg.get('method', '')
    msg_id = msg.get('id')
    params = msg.get('params', {})

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if method == 'initialize':
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "augur", "version": "0.3"},
        })

    elif method == 'notifications/initialized':
        return None  # notification — no response needed

    elif method == 'tools/list':
        return ok({"tools": TOOLS})

    elif method == 'tools/call':
        name = params.get('name', '')
        arguments = params.get('arguments', {})
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return err(-32601, f"Unknown tool: {name}")
        try:
            text = handler(arguments)
            return ok({"content": [{"type": "text", "text": text}]})
        except Exception as e:
            _log(f"Tool {name} error: {e}")
            return ok({"content": [{"type": "text", "text": f"Tool error: {e}"}]})

    elif method == 'ping':
        return ok({})

    else:
        if msg_id is not None:
            return err(-32601, f"Method not found: {method}")
        return None  # unknown notification — ignore


def main():
    _log("Augur MCP Server starting — waiting for client...")
    try:
        while True:
            msg = _read_message()
            if msg is None:
                break
            _log(f"<- {msg.get('method', '?')} (id={msg.get('id')})")
            response = _handle(msg)
            if response is not None:
                _log(f"-> {response.get('result', {}).get('serverInfo', response.get('result', {}).get('tools', '...'))}")
                _write_message(response)
    except (EOFError, KeyboardInterrupt):
        _log("MCP server stopped.")
    except Exception as e:
        _log(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
```

### Claude Desktop Configuration

Add to `~/.claude/claude_desktop_config.json`:
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

After adding, restart Claude Desktop. The tools appear automatically.

### Edge Cases

| Case | Handling |
|------|----------|
| Context API not running | Each tool returns a human-readable error string, not a crash |
| screenpipe not running | Context API returns empty results → tool returns "no context found" |
| MCP client sends unknown method (notification) | If no `id`, ignore silently; if has `id`, return -32601 |
| Tool returns very long text | Hard-capped at 4000 chars per tool result |
| Client disconnects mid-session | EOFError caught in main loop → clean exit |
| `notifications/initialized` message | Must NOT send a response (notifications have no `id`) |
| Empty `arguments` | Each handler uses `.get(key, default)` — safe |

---

## Feature 6: `/profile` and `/context-card` Endpoints

### `/profile?days=N`

**File:** `context-server.py`

New function `get_profile(days)`:

```python
def get_profile(days=7):
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    top_apps = post_json(SCREENPIPE_URL + '/raw_sql', {
        'query': (
            f"SELECT app_name, COUNT(*) as frames FROM frames "
            f"WHERE date(timestamp) >= '{start}' AND app_name IS NOT NULL "
            f"GROUP BY app_name ORDER BY frames DESC LIMIT 10"
        )
    }) or []

    hourly = post_json(SCREENPIPE_URL + '/raw_sql', {
        'query': (
            f"SELECT strftime('%H', timestamp) as hr, COUNT(*) as cnt FROM frames "
            f"WHERE date(timestamp) >= '{start}' GROUP BY hr"
        )
    }) or []

    domains = post_json(SCREENPIPE_URL + '/raw_sql', {
        'query': (
            f"SELECT browser_url, COUNT(*) as visits FROM frames "
            f"WHERE date(timestamp) >= '{start}' AND browser_url IS NOT NULL "
            f"AND browser_url != '' GROUP BY browser_url ORDER BY visits DESC LIMIT 50"
        )
    }) or []

    sample_text = post_json(SCREENPIPE_URL + '/raw_sql', {
        'query': (
            f"SELECT text FROM frames WHERE date(timestamp) >= '{start}' "
            f"AND text IS NOT NULL LIMIT 500"
        )
    }) or []

    total_frames = post_json(SCREENPIPE_URL + '/raw_sql', {
        'query': f"SELECT COUNT(*) as n FROM frames WHERE date(timestamp) >= '{start}'"
    }) or [{'n': 0}]

    audio_count = post_json(SCREENPIPE_URL + '/raw_sql', {
        'query': (
            f"SELECT COUNT(*) as n FROM audio_transcriptions "
            f"WHERE date(timestamp) >= '{start}'"
        )
    }) or [{'n': 0}]

    # Process top apps with estimated hours (frames / avg_fps * 3600)
    # screenpipe captures ~1 frame/sec, so frames ≈ seconds of activity
    app_list = []
    for r in top_apps:
        frames = int(r.get('frames', 0))
        hours = round(frames / 3600, 2)  # approximate
        app_list.append({'app': r.get('app_name'), 'frames': frames, 'hours': hours})

    # Hourly heatmap: 24-slot array, counts per hour
    heatmap = [0] * 24
    for r in hourly:
        try:
            heatmap[int(r.get('hr', 0))] = int(r.get('cnt', 0))
        except Exception:
            pass

    # Domain frequency from URLs
    from urllib.parse import urlparse
    domain_counts = defaultdict(int)
    for r in domains:
        url = r.get('browser_url', '')
        try:
            domain = urlparse(url).netloc
            if domain:
                domain_counts[domain] += int(r.get('visits', 0))
        except Exception:
            pass
    top_domains = [
        {'domain': d, 'visits': c}
        for d, c in sorted(domain_counts.items(), key=lambda x: -x[1])[:10]
    ]

    # Topic extraction (same as summary but over longer window)
    word_freq = defaultdict(int)
    for row in sample_text:
        for w in (row.get('text', '') or '').lower().split():
            w = ''.join(c for c in w if c.isalpha())
            if len(w) > 3 and w not in STOP_WORDS:
                word_freq[w] += 1
    top_topics = [w for w, _ in sorted(word_freq.items(), key=lambda x: -x[1])[:25]]

    # Browser captures count
    recent_browser = len([
        c for c in _browser_captures
        if c.get('timestamp', '') >= start
    ])

    return {
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'window_days': days,
        'date_range': {'start': start, 'end': end},
        'profile': {
            'total_frames': total_frames[0].get('n', 0) if total_frames else 0,
            'total_audio_chunks': audio_count[0].get('n', 0) if audio_count else 0,
            'browser_captures': recent_browser,
            'top_apps': app_list,
            'active_hours': {'heatmap': heatmap},
            'top_domains': top_domains,
            'top_topics': top_topics,
        }
    }
```

**Add handler in `do_GET`:**

```python
elif parsed.path == '/profile':
    days = int(p('days', 7))
    self.send_json(200, get_profile(days))
```

### `/context-card`

Ultra-compact natural language profile, ~300–500 chars. Designed to prepend to any LLM system prompt.

**New function `get_context_card(days)`:**

```python
def get_context_card(days=7):
    prof = get_profile(days)['profile']
    apps = prof.get('top_apps', [])
    topics = prof.get('top_topics', [])
    domains = prof.get('top_domains', [])
    heatmap = prof.get('active_hours', {}).get('heatmap', [])

    # Peak hours
    if heatmap and max(heatmap) > 0:
        sorted_hours = sorted(range(24), key=lambda h: -heatmap[h])
        peak_hours = ', '.join(f"{h}:00" for h in sorted_hours[:3])
    else:
        peak_hours = 'unknown'

    top_app_names = ', '.join(a['app'] for a in apps[:3] if a.get('app'))
    top_topic_words = ', '.join(topics[:8])
    top_domain_names = ', '.join(d['domain'] for d in domains[:3])

    card = (
        f"User profile (last {days} days): "
        f"Primarily uses {top_app_names}. "
        f"Most active at {peak_hours}. "
    )
    if top_topic_words:
        card += f"Recent topics: {top_topic_words}. "
    if top_domain_names:
        card += f"Frequent sites: {top_domain_names}."

    return {
        'card': card.strip(),
        'chars': len(card),
        'window_days': days,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
```

**Add handler:**

```python
elif parsed.path == '/context-card':
    days = int(p('days', 7))
    self.send_json(200, get_context_card(days))
```

**Update `/health` endpoints list and startup print:**

```python
print("    GET  /profile?days=7")
print("    GET  /context-card?days=7")
```

---

## Feature 7: Dashboard — Browser Captures Tab

### Implementation — `screenpipe-dashboard.html`

**New tab button** (insert after Anomalies tab button in the tab bar):

```html
<button class="tab-btn" onclick="switchTab(6)" id="tab-btn-6">Browser</button>
```

(Tab index 6 — Anomalies moves to... actually, keep Anomalies at 5, Browser at 6.)

**New tab content panel:**

```html
<div id="browserTab" class="tab-content" style="display:none">
  <div class="section-header">
    <span>Browser Activity</span>
    <button onclick="loadBrowserActivity()" class="btn-small">Refresh</button>
  </div>
  <div id="browserList">
    <div class="placeholder">Loading browser activity...</div>
  </div>
</div>
```

**New JS function `loadBrowserActivity()`:**

```javascript
async function loadBrowserActivity() {
  const el = document.getElementById('browserList');
  el.innerHTML = '<div class="placeholder">Loading...</div>';
  try {
    const resp = await fetch('http://localhost:3031/browser-captures?limit=100');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const results = data.results || [];
    if (!results.length) {
      el.innerHTML = '<div class="placeholder">No browser captures yet.<br>Install the Augur Chrome extension to capture browser activity.</div>';
      return;
    }
    el.innerHTML = results.map(r => {
      const timeAgo = r.timestamp ? relativeTime(r.timestamp) : '';
      const timeOnPage = r.time_on_page_s ? `${r.time_on_page_s}s` : '';
      const scroll = r.scroll_depth_pct != null ? `↓${r.scroll_depth_pct}%` : '';
      const selected = r.selected_text ? `<div class="capture-selected">"${escHtml(r.selected_text.slice(0, 200))}"</div>` : '';
      const meta = [timeOnPage, scroll].filter(Boolean).join(' · ');
      return `
        <div class="capture-card browser-card">
          <div class="capture-header">
            <span class="badge badge-browser">browser</span>
            <span class="capture-app">${escHtml(r.domain || '')}</span>
            <span class="capture-time">${timeAgo}</span>
          </div>
          <div class="capture-title">
            <a href="${escHtml(r.url || '')}" target="_blank">${escHtml(r.title || r.url || '')}</a>
          </div>
          ${meta ? `<div class="capture-meta">${meta}</div>` : ''}
          ${selected}
        </div>
      `;
    }).join('');
  } catch(e) {
    el.innerHTML = `<div class="error">Could not load browser captures: ${e.message}<br>Make sure context-server.py is running.</div>`;
  }
}
```

**Load on tab switch** — add `case 6: loadBrowserActivity(); break;` to the `switchTab` function.

**CSS additions:**

```css
.browser-card { border-left: 2px solid #4d9fff; }
.capture-title a { color: #4d9fff; text-decoration: none; }
.capture-title a:hover { text-decoration: underline; }
.capture-selected { font-style: italic; color: #888; margin-top: 6px; font-size: 12px; }
.badge-browser { background: rgba(77,159,255,0.15); color: #4d9fff; }
.capture-meta { font-size: 11px; color: #555; margin-top: 4px; }
```

---

## Feature 8: Dashboard — Semantic Search Mode Toggle

### Implementation — `screenpipe-dashboard.html`

**In the Search tab header**, add a mode toggle after the search input:

```html
<div class="search-mode-toggle">
  <button id="searchModeKeyword" class="mode-btn active" onclick="setSearchMode('keyword')">Keyword</button>
  <button id="searchModeSemanticBtn" class="mode-btn" onclick="setSearchMode('semantic')">Semantic</button>
</div>
```

**New JS state and function:**

```javascript
let _searchMode = 'keyword';

function setSearchMode(mode) {
  _searchMode = mode;
  document.getElementById('searchModeKeyword').classList.toggle('active', mode === 'keyword');
  document.getElementById('searchModeSemanticBtn').classList.toggle('active', mode === 'semantic');
}
```

**Modify `doSearch()` to branch on mode:**

```javascript
async function doSearch() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;

  if (_searchMode === 'semantic') {
    await doSemanticSearch(q);
  } else {
    await doKeywordSearch(q);
  }
}

async function doSemanticSearch(q) {
  const el = document.getElementById('searchResults');
  el.innerHTML = '<div class="placeholder">Running semantic search...</div>';
  try {
    const resp = await fetch(`http://localhost:3031/semantic?q=${encodeURIComponent(q)}&limit=20`);
    if (resp.status === 503) {
      el.innerHTML = '<div class="error">Semantic search not available.<br>Run: pip install chromadb sentence-transformers<br>Then: python semantic_search.py --index</div>';
      return;
    }
    const data = await resp.json();
    const results = data.results || [];
    if (data.note) {
      el.innerHTML = `<div class="placeholder">${data.note}</div>`;
      return;
    }
    el.innerHTML = `<div class="result-count">${results.length} semantic matches (${data.total_indexed} indexed)</div>` +
      results.map(r => {
        const sim = r.score ? `<span class="sim-score">${(r.score * 100).toFixed(0)}% match</span>` : '';
        return `
          <div class="result-card">
            <div class="result-header">
              <span class="badge badge-ocr">OCR</span>
              <span class="capture-app">${escHtml(r.app || '')}</span>
              ${sim}
            </div>
            ${r.url ? `<div class="result-url">${escHtml(r.url)}</div>` : ''}
            <div class="result-text">${escHtml((r.text || '').slice(0, 300))}</div>
          </div>
        `;
      }).join('');
  } catch(e) {
    el.innerHTML = `<div class="error">Semantic search error: ${e.message}</div>`;
  }
}
```

**CSS for toggle:**

```css
.search-mode-toggle { display: flex; gap: 4px; margin-bottom: 12px; }
.mode-btn { padding: 4px 12px; border: 1px solid #2a2a2a; background: #111; color: #555; cursor: pointer; border-radius: 3px; font-size: 12px; }
.mode-btn.active { border-color: #00ff87; color: #00ff87; background: rgba(0,255,135,0.05); }
.sim-score { font-size: 11px; color: #00ff87; margin-left: auto; }
```

---

## Feature 9: Tests for v0.3

### New test classes in `test_features.py`

**`TestBrowserCapturesInContext`:**

```python
class TestBrowserCapturesInContext(unittest.TestCase):

    def test_browser_candidate_conversion(self):
        """browser_capture_to_candidate produces correct shape."""
        import importlib.util
        # ... import context-server and call browser_capture_to_candidate
        cap = {
            'url': 'https://example.com/article',
            'domain': 'example.com',
            'title': 'Example Article',
            'timestamp': '2026-03-05T14:00:00Z',
            'time_on_page_s': 120,
            'scroll_depth_pct': 75,
            'selected_text': 'This is selected text from the article.',
        }
        # cand = browser_capture_to_candidate(cap)
        # self.assertEqual(cand['_source'], 'browser')
        # self.assertTrue(cand['_has_selection'])
        # self.assertIn('example.com', cand['_blob'])

    def test_context_response_has_source_field(self):
        """Live: /context results include 'source' field."""
        # live-only test

    def test_browser_captures_appear_in_context_for_matching_query(self):
        """Live: if browser capture matches query keywords, it appears in /context results."""
        # live-only test
```

**`TestMCPServer`:**

```python
class TestMCPServer(unittest.TestCase):

    def test_mcp_server_exists(self):
        path = os.path.join(os.path.dirname(__file__), 'mcp_server.py')
        self.assertTrue(os.path.exists(path))

    def test_mcp_server_importable(self):
        import mcp_server
        self.assertTrue(hasattr(mcp_server, '_handle'))
        self.assertTrue(hasattr(mcp_server, 'TOOLS'))
        self.assertEqual(len(mcp_server.TOOLS), 5)

    def test_mcp_initialize_response(self):
        import mcp_server
        msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
        resp = mcp_server._handle(msg)
        self.assertEqual(resp['result']['serverInfo']['name'], 'augur')
        self.assertIn('capabilities', resp['result'])

    def test_mcp_tools_list(self):
        import mcp_server
        msg = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}
        resp = mcp_server._handle(msg)
        tools = resp['result']['tools']
        names = [t['name'] for t in tools]
        self.assertIn('get_context', names)
        self.assertIn('get_daily_summary', names)
        self.assertIn('get_anomalies', names)
        self.assertIn('get_user_profile', names)
        self.assertIn('get_browser_activity', names)

    def test_mcp_unknown_method_returns_error(self):
        import mcp_server
        msg = {'jsonrpc': '2.0', 'id': 3, 'method': 'unknown/method', 'params': {}}
        resp = mcp_server._handle(msg)
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32601)

    def test_mcp_notification_returns_none(self):
        import mcp_server
        msg = {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}
        resp = mcp_server._handle(msg)
        self.assertIsNone(resp)
```

**`TestProfileEndpoint` (live mode):**

```python
class TestProfileEndpoint(unittest.TestCase):
    def setUp(self):
        if not LIVE:
            self.skipTest("live only")

    def test_profile_response_shape(self):
        d, status = req('/profile?days=7')
        self.assertEqual(status, 200)
        self.assertIn('profile', d)
        self.assertIn('top_apps', d['profile'])
        self.assertIn('active_hours', d['profile'])
        self.assertIn('top_domains', d['profile'])

    def test_context_card_response_shape(self):
        d, status = req('/context-card?days=7')
        self.assertEqual(status, 200)
        self.assertIn('card', d)
        self.assertIsInstance(d['card'], str)
        self.assertGreater(len(d['card']), 0)
        self.assertLessEqual(len(d['card']), 600)
```

---

## Implementation Sequence

Build in this order to minimize regressions:

```
Phase A — Core API (no new deps, no UI changes)
  1. Feature 6: /profile + /context-card endpoints   [~1h]
  2. Feature 1: Browser captures in /context          [~2h]
  3. Feature 4: Hybrid scoring                        [~2h]

Phase B — New services
  4. Feature 2: Auto-start semantic indexer           [~1h]
  5. Feature 5: MCP server                            [~3h]
  6. Feature 3: Claude/OpenAI in demo_agent           [~2h]

Phase C — Dashboard UI
  7. Feature 7: Browser captures tab                  [~2h]
  8. Feature 8: Semantic search mode toggle           [~1h]

Phase D — Tests + Docs
  9. Feature 9: v0.3 tests                            [~2h]
  10. README + PRODUCT.md                             [~30m]
```

---

## Invariants to Preserve

- **Zero new required dependencies.** All new deps are optional (anthropic, openai — only needed if user wants cloud LLM). MCP server is stdlib-only.
- **context-server.py stays pure stdlib.** semantic_search is imported dynamically, never at module level.
- **Backward compatibility.** `/context` response shape is additive only (new fields, no removed fields). All existing clients continue to work.
- **Fail-safe degradation.** If semantic index is empty, hybrid scoring = keyword only. If browser extension not installed, `/context` = OCR-only. If cloud API key missing, demo_agent falls back to LM Studio.
- **No auth, no cloud, no tracking.** Everything local. MCP server only calls localhost:3031.

---

## Files Changed in v0.3

| File | Change Type |
|------|-------------|
| `context-server.py` | Modified (browser in context, hybrid scoring, /profile, /context-card) |
| `launch.command` | Modified (semantic indexer auto-start) |
| `demo_agent.py` | Modified (Claude/OpenAI backends) |
| `mcp_server.py` | New file |
| `screenpipe-dashboard.html` | Modified (browser tab, semantic search toggle) |
| `test_features.py` | Modified (new test classes) |
| `requirements.txt` | Modified (optional dep comments) |
| `README.md` | Modified (v0.2 shipped, v0.3 roadmap, MCP setup) |
| `DOCS/PRODUCT.md` | Modified (v0.3 section, new endpoints, architecture) |
| `DOCS/CLAUDE.md` | Modified (updated file structure, next things) |
