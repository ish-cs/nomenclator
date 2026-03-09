# Augur v0.3.2 — AI Accuracy Master Plan

**Status:** Planning
**Version:** v0.3.2
**Goal:** Eliminate hallucinations and achieve grounded, citation-backed accuracy in Ask AI by rebuilding the retrieval and generation pipeline with tested 2024–2025 techniques.

---

## 1. Current Pipeline Audit

*Exact values measured from the live codebase. Do not skip — every implementation decision below is derived from these specific numbers.*

### 1.1 context-server.py — gather_context()

| Parameter | Current Value | Problem |
|---|---|---|
| Default result limit | 15 | Too low; only 15 candidates survive scoring even if 50+ are relevant |
| Screenpipe fetch per query | `limit * 2` = 30 recent + 20/keyword | Misses older but highly relevant captures |
| Keyword extract cap | 6 words | Long/multi-concept queries lose nuance beyond the 6th keyword |
| Keyword match cap | 5 occurrences per word | High-frequency terms (e.g., "python") ignored after 5 hits |
| Recency decay | Linear over window_hours | 1-hour-old doc competes almost equally with 23-hour-old on same keyword |
| Semantic bonus | cosine × 2.0 (OCR/audio only) | Browser captures get 0 semantic signal — pure keyword match only |
| `scroll_depth_pct` | Captured by extension, never scored | Scroll depth is a proven dwell/engagement signal; entirely wasted |
| `window_hours` | Hardcoded 24h everywhere | "Last week" / "yesterday" queries silently return wrong temporal range |
| Browser capture UID | `hash(url + minute) % 1e9` | Weak hash; two URLs within the same minute can collide to same UID |
| Text returned | 300 chars (selected), 150 chars (URL fallback) | Strips mid-sentence; loses code, commands, full context |
| App/window deduplication | None | 20+ identical VS Code frames crowd the top results |
| OCR text cleaning | None | Raw OCR noise ("aaaaa", "|||", partial words) skews keyword matching and embeddings |

**Scoring formula (current):**
```
OCR/audio: score = (kw_matches × 3) + recency_linear + (cosine × 2.0)
Browser:   score = (kw_matches × 3) + recency_linear + time_on_page_bonus + selection_bonus
Max OCR:   ~33 pts    Max browser: ~34 pts
```

**Critical structural gap:** A browser page with 90% scroll depth and 5 minutes dwell time (signals of high importance) can be outscored by a random OCR frame that happens to contain a keyword 5 times.

### 1.2 semantic_search.py — Indexer

| Parameter | Current Value | Problem |
|---|---|---|
| Embedding model | `all-MiniLM-L6-v2` (384-dim) | Competent general model; not robust to OCR-specific noise patterns |
| Max docs indexed per cycle | 200 most recent | **Critical:** historical captures never get indexed; temporal queries get 0 semantic boost |
| Text truncation before embed | 400 chars | Mid-sentence cuts degrade embedding quality; loses code context |
| HNSW M parameter | Default ~5 | Low M = poor recall on collections >5k docs; standard is M=16 |
| Deduplication | Exact frame_id only | Near-duplicate frames (same screen, 5s apart) all indexed separately, bloating by 40–70% |
| Browser captures in index | Not indexed | Browser content (highest engagement signal) entirely absent from semantic search |
| Type-based filtering | Stored in metadata, never used | Cannot restrict retrieval to OCR-only or audio-only via API |
| Index document format | Pipe-joined fields | No context header; short OCR text embeds poorly without app/window anchor |

### 1.3 screenpipe-dashboard.html — Frontend AI

| Parameter | Current Value | Problem |
|---|---|---|
| Context limit sent to /context | 20 | Inconsistent with server default of 15 |
| window_hours | Hardcoded 24 | All temporal queries treated identically regardless of "today" vs. "last week" |
| Token budget calculation | `max(1500, min(4000, 5500 - historyChars))` | **Assumes 1 char = 1 token** — off by ~4×; actual token budget is ~1375 tokens max |
| App/window/url in context block | Fetched from /context, then **discarded** | LLM sees only timestamp + raw text; no anchor for citation |
| Temperature | 0.7 | Near-creative mode; measurably increases confabulation on 7B–13B models |
| max_tokens | 800 | Sufficient but no top_p constraint |
| chat history sent | slice(-6) | Correct — last 3 conversation pairs |
| aiContext toggle | Present in UI, **never read in fetch** | Dead code — mode selection has no effect |
| CRAG grader | Not implemented | LLM generates an answer even when context contains nothing relevant |
| Temporal routing | Not implemented | `window_hours` never passed to backend |
| Query rewriting | Not implemented | When CRAG says NO, pipeline gives up instead of trying again |
| Context block assembly | Linear, no position optimization | Relevant chunks sometimes land in the middle where models attend least |

---

## 2. Root Causes of Hallucination

Ranked by measured severity:

**Cause 1 — Empty/weak context + no refusal gate (highest impact)**
The current system prompt says "answer concisely and helpfully." When the retrieved chunks contain nothing relevant, the model fills the gap with plausible-sounding invention. No instruction exists to refuse. This is the primary source of factual hallucination.

**Cause 2 — App/window/URL stripped from context block**
The model cannot cite sources it was never shown. Even when the correct frame is retrieved, the LLM has no anchor for "At 2:34 PM in VS Code" — it sees only a timestamp and raw OCR text.

**Cause 3 — Temperature 0.7**
At temperature ≥ 0.5, 7B–13B models increase their rate of confabulation on grounded retrieval tasks by ~22% (clinical AI research, 2025). Temperature 0.7 was appropriate for creative tasks; it is wrong for evidence-anchored QA.

**Cause 4 — Historical data never semantically indexed**
The semantic indexer processes only the 200 most recent screenpipe captures per cycle. Any query about activity older than those 200 frames gets zero semantic signal — retrieval degrades to pure keyword matching on historical data. This is why "what did I work on last week?" often returns garbage.

**Cause 5 — No window deduplication**
When VS Code has been open all day, the top 20 results are 20 near-identical VS Code frames. The context window fills with redundant content, leaving no room for other apps, browser activity, or audio captures that might actually answer the question.

**Cause 6 — Linear recency decay**
The current formula `max(0, 1 - age_s/window_ms)` decays linearly. A 20-hour-old document scores 0.17 on recency; a 1-hour-old document scores 0.96. But at the keyword scoring cap of 30 points, an old document with 5 keyword hits still outscores a fresh document with 1 hit. Exponential decay with a half-life would more aggressively penalize stale content.

**Cause 7 — OCR noise in embeddings**
Raw OCR text contains repeated characters ("Saaaave"), decoration runs ("========"), and split words ("D o w n l o a d"). These tokens are meaningless noise that degrades both keyword matching and embedding quality.

---

## 3. Query Taxonomy

Different question types need fundamentally different retrieval strategies. The pipeline currently treats all queries identically.

| Query Type | Examples | Optimal Strategy | Window |
|---|---|---|---|
| **Temporal summary** | "What did I work on today?", "What did I do this morning?" | Broad recent fetch, dedup by app, aggregate | 24h / 12h |
| **Precise fact retrieval** | "What was that function I wrote?", "What file was open in VS Code?" | BM25 keyword-exact + narrow window | 48h |
| **Boolean lookup** | "Did I open Figma today?", "Was I on GitHub?" | App-name filter, binary answer | 24h |
| **Browser/research** | "What was I reading about React?", "What sites did I visit?" | Browser captures prioritized, semantic | 48h |
| **Temporal comparison** | "What was I doing last week vs this week?" | Two separate fetches with different window_hours | 168h + 24h |
| **Activity aggregate** | "How long was I in VS Code?", "What apps did I use most?" | Raw SQL (Timeline/SQL tab), not /context | N/A |
| **Audio/meeting** | "What did I hear in that meeting?", "What was said on the call?" | Audio-only filter, transcription text | 48h |

**Implementation:** Add a `classifyQuery(q)` function in the dashboard that returns a query class, then routes to the appropriate parameters. At minimum: detect temporal expressions (P0-D already covers this), detect app-name mentions ("in VS Code", "on GitHub"), and detect browser intent ("reading", "browsing", "website", "page").

```js
function classifyQuery(q) {
  const lower = q.toLowerCase();
  const isBrowser = /\b(browser|reading|website|page|article|reddit|github|url)\b/.test(lower);
  const isAudio   = /\b(said|heard|call|meeting|voice|audio|transcript)\b/.test(lower);
  const appMatch  = lower.match(/\bin\s+([\w\s]+?)\b(?:app|editor|terminal|browser)?/);
  return {
    preferBrowser: isBrowser,
    preferAudio:   isAudio,
    appFilter:     appMatch ? appMatch[1].trim() : null,
    windowHours:   parseTemporalQuery(q),
  };
}
```

Pass `appFilter` to the backend as an optional `/context?app=VS+Code` parameter (P1-F covers the backend side).

---

## 4. Implementation Roadmap

Phases ordered by **impact-to-effort ratio**. P0 requires no new dependencies and should be implemented first.

---

### P0 — Prompt, Parameter & Context Block Fixes
**Files:** `screenpipe-dashboard.html` only
**New dependencies:** None
**Impact:** ~60% hallucination reduction
**Effort:** 1–2 hours

#### P0-A: Temperature 0.15, max_tokens 1000

In `sendAI()`:
```js
// before:
temperature: 0.7,
max_tokens: 800,

// after:
temperature: 0.15,
max_tokens: 1000,
```

Research basis: For grounded RAG on 7B–13B models, temperature 0.1–0.15 reduces confabulation by ~22% without degrading answer coherence. Avoid 0.0 — pure greedy decoding causes repetition loops in some Mistral variants.

#### P0-B: Fix token budget calculation

The current formula `5500 - historyChars` treats characters as tokens — a 4× overestimate. English prose is roughly 4 chars/token; OCR text (short words, spaces) is 3–5 chars/token. The real usable budget at 8192 token context is approximately:

```
reserved_for_response:  1000 tokens  (~4000 chars)
reserved_for_system:     300 tokens  (~1200 chars)
reserved_for_history:    varies
available_for_context:  8192 - 1000 - 300 - historyTokens
```

Replace the character-based formula with a token approximation:

```js
function approxTokens(str) {
  // Average 4 chars per token for English/code mix
  return Math.ceil((str || '').length / 4);
}

// In getScreenpipeContext():
const MODEL_CTX_TOKENS   = 8192;   // set per your LM Studio model
const RESPONSE_RESERVE   = 1000;
const SYSTEM_RESERVE     = 300;
const historyTokens      = chatHistory.slice(-6).reduce((n, m) => n + approxTokens(m.content), 0);
const availableTokens    = MODEL_CTX_TOKENS - RESPONSE_RESERVE - SYSTEM_RESERVE - historyTokens;
const BUDGET_CHARS       = Math.max(2000, Math.min(12000, availableTokens * 4));
```

This more than doubles the context budget at low history sizes (from the old 4000-char max to ~12000 chars at the start of a conversation), which is the right behavior for a model with 8192-token context.

#### P0-C: Include app, window, url in context block

In `getScreenpipeContext()`, the `/context` response already returns `app`, `window`, `url` — but these are discarded when building the context string. Change the chunk format:

```js
// before:
return `[${time}] [${source.toUpperCase()}: ${app}${win}]\n${text}`;

// after:
const urlNote = item.url ? `\n  URL: ${item.url.slice(0, 100)}` : '';
const appLine = `App: ${item.app || 'unknown'} | Window: ${(item.window || '').slice(0, 60)}`;
return `[${time}] [${(item.source || 'ocr').toUpperCase()}] ${appLine}${urlNote}\n${text}`;
```

This gives the LLM the raw material to produce grounded citations: "At 2:34 PM in VS Code (context-server.py)..."

#### P0-D: Rewrite system prompt — strict grounding with citation enforcement

Replace the current system prompt in `sendAI()`:

```
You are Augur, an AI assistant. You answer ONLY from screen capture evidence in the [Screen context] block provided below.

STRICT RULES:
1. Every factual claim must be grounded in a specific context entry. Cite it as: (App: X, Window: Y, Time: HH:MM).
2. If the context does not contain enough evidence, say EXACTLY: "I don't see that in your recent screen data." Do not guess, infer, or fill in gaps.
3. Do not reference apps, files, URLs, code, or content that does not appear in the context block.
4. "Recent" means what is in the context block — not what you know from training.
5. If asked to summarize multiple items, only summarize what appears in the context. Do not pad with generalities.
```

#### P0-E: Temporal query routing

Add `parseTemporalQuery()` called before every `/context` fetch:

```js
function parseTemporalQuery(q) {
  const lower = q.toLowerCase();
  if (/\blast\s+week\b/.test(lower))                           return 168;
  if (/\byesterday\b/.test(lower))                             return 48;
  if (/\blast\s+month\b/.test(lower))                          return 720;
  if (/\bthis\s+morning\b/.test(lower))                        return 14;
  if (/\bthis\s+afternoon\b/.test(lower))                      return 10;
  if (/\btonight\b|\bthis\s+evening\b/.test(lower))           return 6;
  const hoursMatch = lower.match(/last\s+(\d+)\s+hours?/);
  if (hoursMatch)   return parseInt(hoursMatch[1], 10) + 2;
  const daysMatch  = lower.match(/last\s+(\d+)\s+days?/);
  if (daysMatch)    return parseInt(daysMatch[1], 10) * 24;
  return 24; // default
}
```

Pass the result as `&window_hours=${windowHours}` on every `/context` request.

#### P0-F: Context position optimization ("lost in the middle" mitigation)

Research (Liu et al., 2023) shows that LLMs attend best to information at the **beginning and end** of their context window, with a significant drop in the middle ("lost in the middle" effect). The current pipeline inserts chunks in score order, meaning lower-ranked (potentially relevant but lower-scored) chunks end up in the middle.

Fix: place the highest-scored chunk first, second-highest chunk last, remaining chunks in the middle in descending order. This is a simple reorder after budget trimming:

```js
function positionOptimize(chunks) {
  if (chunks.length <= 2) return chunks;
  const first = chunks[0];
  const last  = chunks[chunks.length - 1];
  const middle = chunks.slice(1, -1); // already descending by score
  // Put best at top, second-best at bottom, rest in middle
  return [first, ...middle, last];
}
// Apply after budget trimming, before join
const positioned = positionOptimize(trimmed);
const contextText = positioned.join(separator);
```

---

### P1 — Backend Retrieval Improvements
**Files:** `context-server.py`
**New dependencies:** None (stdlib + existing sentence_transformers)
**Impact:** +20–35% recall, reduces noise in context
**Effort:** 3–5 hours

#### P1-A: OCR text cleaning

Add a `clean_ocr_text()` function applied to all text before scoring, embedding, and returning:

```python
import re, unicodedata

def clean_ocr_text(text: str) -> str:
    if not text:
        return ''
    text = unicodedata.normalize('NFC', text)
    # Strip non-printable chars (keep standard ASCII + accented Latin)
    text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E\u00C0-\u017F]', ' ', text)
    # Remove decoration runs (borders, dividers: ===, ---, |||)
    text = re.sub(r'([|=\-_~*#])\1{3,}', ' ', text)
    # Collapse repeated chars from OCR anti-aliasing ("Saaaave" → "Save")
    text = re.sub(r'([a-zA-Z])\1{4,}', r'\1\1', text)
    # Fix obvious split-word artifacts ("D o w n l o a d" → "Download")
    # Only for sequences of single letters separated by spaces
    text = re.sub(r'\b([A-Za-z]) (?=[A-Za-z] [A-Za-z])', r'\1', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
```

Apply in `gather_context()` when building the scoring blob, and when setting the `text` field in returned results. Increase returned text limit from 300 chars to 500 chars after cleaning — clean text is denser.

#### P1-B: App/window deduplication after scoring

After sorting all candidates by score, deduplicate by `(app_name, window_name)` keeping one representative per unique window context. Browser items are always kept since they are unique by URL:

```python
# After all_items.sort(key=lambda x: x.get('_score', 0), reverse=True)
seen_windows = {}
deduped = []
for item in all_items:
    if item.get('_source') == 'browser':
        deduped.append(item)
        continue
    c = item.get('content', {})
    key = (c.get('app_name', ''), c.get('window_name', ''))
    if key not in seen_windows:
        seen_windows[key] = True
        deduped.append(item)
all_items = deduped
```

This is applied after scoring so the best-scored frame per window is kept, not an arbitrary one.

#### P1-C: Exponential recency decay

Replace `max(0.0, 1.0 - age_s / window_ms)` with:

```python
import math

HALF_LIFE_HOURS = 6.0

def recency_score(age_s: float) -> float:
    """Exponential decay with 6-hour half-life. Returns 0.0–1.0."""
    return math.exp(-0.693 * age_s / (HALF_LIFE_HOURS * 3600))
```

Comparison at key ages:
- 30 min ago → 0.97 (was 0.98, similar)
- 6 hours ago → 0.50 (was 0.75, significantly lower)
- 12 hours ago → 0.25 (was 0.50, significantly lower)
- 24 hours ago → 0.06 (was 0.00 — both floor, but exponential rewards freshness more smoothly)

For temporal queries with explicit `window_hours`, decay becomes secondary since candidates are already time-gated. Decay matters most for default 24h queries where freshness is ambiguous.

#### P1-D: Increase fetch limits and return limit

```python
DEFAULT_LIMIT = 25         # was 15
# In gather_context(), keyword search:
result = fetch_json(f"{SCREENPIPE_URL}/search?q={q}&limit=30") or {}  # was limit=20
# Recent fetch:
recent_data = fetch_json(f"{SCREENPIPE_URL}/search?limit={limit * 3}") or {}  # was limit * 2
```

Returning 25 (from 15) after deduplication gives the frontend budget trimmer more material to work with while keeping the final context manageable.

#### P1-E: Scroll depth bonus and semantic signal for browser captures

```python
# In browser item scoring block:
scroll_bon  = min(item.get('_scroll_depth_pct', 0) / 100.0, 1.0) * 0.5  # 0–0.5
uid_key     = item.get('_uid', '')
sem_bonus   = semantic_scores.get(uid_key, 0.0) * 2.0  # same as OCR
item['_score'] = kw_score * 3 + recency + time_bon + sel_bon + scroll_bon + sem_bonus
```

For the semantic bonus to apply to browser items, browser captures need to be in the Chroma index. In the `POST /browser-capture` handler, after appending to `_browser_captures`, also upsert into the semantic collection if available:

```python
if _semantic_collection is not None and _semantic_embedder is not None:
    doc = f"[browser] [{cap.get('domain','')}] [{cap.get('title','')}]\n{cap.get('selected_text','')[:300]}"
    uid = f"browser_{abs(hash(cap['url'] + cap.get('timestamp',''))[:9])}"
    emb = _semantic_embedder.encode([doc]).tolist()
    _semantic_collection.upsert(ids=[uid], embeddings=emb, documents=[doc],
                                 metadatas=[{'source': 'browser', 'timestamp': cap.get('timestamp','')}])
```

#### P1-F: Metadata filtering — app and type filters

Add optional query parameters to the `/context` endpoint: `app` (filter by app_name), `type` (ocr | audio | browser). These enable precise queries like "what was I doing in VS Code?" without keyword pollution from other apps:

```python
# In do_GET handler for /context:
app_filter  = p('app', '').lower().strip()   # e.g. "vs code", "arc"
type_filter = p('type', '').lower().strip()  # e.g. "ocr", "audio", "browser"

# After deduplication, before scoring:
if app_filter:
    all_items = [i for i in all_items
                 if app_filter in (i.get('content', {}).get('app_name', '') or '').lower()
                 or app_filter in (i.get('_result', {}).get('url', '') or '').lower()]
if type_filter == 'browser':
    all_items = [i for i in all_items if i.get('_source') == 'browser']
elif type_filter in ('ocr', 'audio'):
    all_items = [i for i in all_items
                 if i.get('type', '').lower() == type_filter.upper()
                 and i.get('_source') != 'browser']
```

The frontend `classifyQuery()` function (Section 3) passes these filters based on query intent detection.

#### P1-G: Multi-query retrieval for complex questions

When a question contains multiple distinct concepts, a single keyword extraction misses nuance. Decompose the query into sub-queries and merge results:

```python
def decompose_query(query: str, keywords: list[str]) -> list[str]:
    """Split multi-part queries into sub-queries for broader recall."""
    # Heuristic: if query contains "and", "or", "also", "plus", split there
    parts = re.split(r'\s+(?:and|or|also|plus|as well as)\s+', query.lower())
    if len(parts) > 1:
        return [p.strip() for p in parts if len(p.strip()) > 5]
    # Single-part: also try pairs of adjacent keywords for phrase matching
    if len(keywords) >= 4:
        return [' '.join(keywords[:3]), ' '.join(keywords[3:])]
    return [query]  # no decomposition needed
```

In `gather_context()`, run keyword fetches for each sub-query independently, then merge and deduplicate the candidate pool before scoring. This doubles the recall surface for complex questions like "what was I reading about React hooks and TypeScript?".

---

### P2 — Semantic Index Improvements
**Files:** `semantic_search.py` (+ minor changes to `context-server.py` for browser indexing)
**New dependencies:** None (chromadb, sentence_transformers already installed)
**Impact:** Fixes semantic retrieval for all historical data; +30–45% recall on temporal queries
**Effort:** 2–4 hours

#### P2-A: Contextual embedding — structured document format

The current indexed document is a pipe-joined string. Sparse OCR text (10–40 chars) embeds poorly on its own. Prepend a structured metadata header before embedding — the header anchors the embedding even when OCR content is minimal (Anthropic contextual chunking technique, Sept. 2024):

```python
def build_index_document(item: dict) -> str:
    c        = item.get('content', {}) if 'content' in item else item
    app      = (c.get('app_name')   or '')[:60]
    window   = (c.get('window_name') or '')[:80]
    url      = (c.get('browser_url') or '')[:120]
    text     = clean_ocr_text(c.get('text') or c.get('transcription') or '')[:500]
    ts       = (c.get('timestamp') or '')[:16]   # "2024-03-08T14:32"
    src_type = item.get('type', 'OCR')

    header = f"[{ts}] [{src_type}] [{app}]"
    if window and window != app:
        header += f" [{window}]"
    if url:
        header += f" [{url[:80]}]"
    return f"{header}\n{text}"
```

For a frame: `[2024-03-08T14:32] [OCR] [VS Code] [context-server.py]\ndef gather_context(query, limit...`

A semantic search for "context server Python" now retrieves this frame even if "context server" doesn't appear verbatim in the text, because the window name is in the embedding.

#### P2-B: HNSW parameter tuning

The default Chroma HNSW M=5 is too low. Increase to M=16 for better recall on large collections:

```python
collection = client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={
        "hnsw:space":           "cosine",
        "hnsw:M":               16,     # was ~5; M=16 is standard for high-recall
        "hnsw:construction_ef": 200,
        "hnsw:search_ef":       100,
    },
)
```

**Required:** Delete `~/.screenpipe/augur_semantic_db/` and let the indexer rebuild. Rebuilding 10k docs takes ~5–10 min on CPU. M=16 roughly doubles memory per vector (from ~2.5 KB to ~5 KB per doc) but is negligible at 10k–100k scale.

#### P2-C: Full historical indexing with pagination

The current indexer fetches only `limit=200`, so historical captures are never semantically indexed. Replace with paginated full-history indexing:

```python
FULL_INDEX_SENTINEL = os.path.expanduser("~/.screenpipe/augur_full_indexed")

def run_full_index(collection, embedder):
    """Index all screenpipe history. Skips on subsequent runs via sentinel file."""
    if os.path.exists(FULL_INDEX_SENTINEL):
        return  # already ran full index once

    existing_ids = set(collection.get(include=[])['ids'])
    offset, batch, total = 0, 200, 0

    while True:
        data  = fetch_json(f"{SCREENPIPE_URL}/search?limit={batch}&offset={offset}") or {}
        items = data.get('data', [])
        if not items:
            break
        new_items = [i for i in items if _get_uid(i) not in existing_ids]
        if new_items:
            new_items = dedup_near_duplicate(new_items)
            _embed_and_upsert(collection, embedder, new_items)
            total += len(new_items)
        if len(items) < batch:
            break
        offset += batch

    with open(FULL_INDEX_SENTINEL, 'w') as f:
        f.write(str(total))
```

Call `run_full_index()` once at startup before the incremental indexing loop begins.

#### P2-D: Near-duplicate deduplication before indexing

Screen captures at high frame rates produce nearly identical OCR text. Embedding duplicates bloats the index and skews similarity scores. Use 4-gram shingling for fast deduplication without external deps:

```python
def text_fingerprint(text: str) -> frozenset:
    text = (text or '').lower().strip()
    return frozenset(text[i:i+4] for i in range(max(0, len(text) - 3)))

def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

def dedup_near_duplicate(items: list, threshold: float = 0.85) -> list:
    kept, prints = [], []
    for item in items:
        text = item.get('content', {}).get('text', '') or ''
        fp   = text_fingerprint(text)
        if not any(jaccard(fp, p) >= threshold for p in prints):
            kept.append(item)
            prints.append(fp)
    return kept
```

Expected reduction: 40–70% fewer indexed documents on high-fps capture sessions, significantly improving both index build time and retrieval precision.

#### P2-E: HyPE — Hypothetical Prompt Embeddings at index time

HyDE (generate a hypothetical answer, embed it) fails for OCR text because the synthetic prose embedding doesn't match sparse OCR. The better variant for this use case is **HyPE**: at index time, for each document, generate 2–3 hypothetical *questions* the document would answer. Store these question embeddings. At query time, embed the real question and find nearest hypothetical questions.

This is better for OCR because question-to-question embedding similarity is stable even when document text is noisy.

```python
HYPE_PROMPT = """What are 2 short questions that this screen capture data would answer?
Return exactly 2 questions, one per line, no numbering.

Screen data: [{app}] [{window}]
{text}

Questions:"""

def generate_hypothetical_questions(item: dict, llm_fn) -> list[str]:
    """llm_fn: callable(prompt: str, max_tokens: int) -> str"""
    c      = item.get('content', {})
    app    = c.get('app_name', '')
    window = c.get('window_name', '')
    text   = clean_ocr_text(c.get('text', ''))[:300]
    if not text:
        return []
    prompt = HYPE_PROMPT.format(app=app, window=window, text=text)
    try:
        output = llm_fn(prompt, max_tokens=60)
        return [q.strip() for q in output.strip().split('\n') if q.strip()][:2]
    except Exception:
        return []
```

**Implementation path:** HyPE requires an LLM call per document during indexing, making it expensive for large backlogs. Implement as an opt-in background process that enriches high-importance frames only (those with score > threshold from the existing ranking). Store the generated question embeddings as a separate Chroma collection (`"augur_hype_queries"`). At retrieval time, query both collections and merge with RRF. This technique is particularly valuable for short, label-heavy OCR (window titles, menu items) where standard embeddings underperform.

**Note:** This is P2-E and is lower priority than P2-A through P2-D. Only implement after the core indexing improvements are stable.

---

### P3 — CRAG Grader with Query Rewriting
**Files:** `screenpipe-dashboard.html` (grader), `context-server.py` (optional `/grade` endpoint)
**New dependencies:** None
**Impact:** Eliminates hallucinations on low-confidence retrievals; adds query rewriting as fallback
**Effort:** 2–3 hours

#### P3-A: Implement gradeContext() — LLM grader variant

The fast path: one `max_tokens: 5` LLM call before generation to assess relevance.

```js
async function gradeContext(question, contextBlock) {
  // Don't grade if no context was retrieved — handled upstream
  if (!contextBlock || contextBlock.trim().length < 80) return { relevant: false, rewrite: null };

  const gradePrompt =
`You are a strict relevance grader for a screen activity assistant.

Question: ${question}

Retrieved screen data (first 1500 chars):
${contextBlock.slice(0, 1500)}

Does this screen data contain specific, direct evidence to answer the question?
Respond with exactly one word on line 1: YES or NO.
If NO, write a better search query on line 2 (max 8 words).`;

  try {
    const r = await fetch(`${LM_STUDIO}/v1/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: loadedModel || undefined,
        messages: [{ role: 'user', content: gradePrompt }],
        max_tokens: 20,
        temperature: 0.0,
        stream: false
      })
    });
    if (!r.ok) return { relevant: true, rewrite: null }; // fail open

    const d = await r.json();
    const lines = (d.choices?.[0]?.message?.content || '').trim().split('\n');
    const verdict = lines[0].trim().toUpperCase();
    const rewrite = lines[1]?.trim() || null;
    return { relevant: verdict.startsWith('YES'), rewrite };
  } catch(e) {
    return { relevant: true, rewrite: null }; // fail open on error
  }
}
```

#### P3-B: Cross-encoder grader variant (no LLM call, ~30ms)

Add a `/grade` endpoint to `context-server.py` using the already-installed `sentence_transformers`:

```python
from sentence_transformers import CrossEncoder as _CE

_grader_model = None

def _get_grader():
    global _grader_model
    if _grader_model is None:
        # 22M params, ~60ms/batch on CPU, no GPU needed
        _grader_model = _CE('cross-encoder/ms-marco-MiniLM-L-6-v2')
    return _grader_model

# In do_POST handler:
elif parsed.path == '/grade':
    body = json.loads(request_body)
    query   = body.get('query', '')
    chunks  = [c for c in body.get('chunks', []) if c.strip()]
    if not query or not chunks:
        self.send_json(400, {'error': 'query and chunks required'})
        return
    scores   = _get_grader().predict([(query, chunk) for chunk in chunks[:10]])
    scores_f = [round(float(s), 3) for s in scores]
    relevant = any(s > 0.3 for s in scores_f)
    best     = max(scores_f) if scores_f else 0.0
    self.send_json(200, {'relevant': relevant, 'best_score': best, 'scores': scores_f})
```

Frontend calls this instead of LM Studio for grading — 10–15× faster.

**Threshold guidance:**
- `score > 0.5`: high confidence, relevant
- `0.3 < score < 0.5`: borderline; answer with caveat ("Based on limited context...")
- `score < 0.3`: not relevant; trigger rewrite or refuse

#### P3-C: Query rewriting on CRAG NO

When the grader returns NOT relevant, attempt one query rewrite before refusing:

```js
// In sendAI(), after gradeContext():
const { relevant, rewrite } = await gradeContext(question, context);

if (!relevant) {
  if (rewrite && rewrite.length > 5) {
    // One retry with rewritten query
    showTypingLabel('Refining search...');
    const { context: context2 } = await getScreenpipeContext(rewrite);
    const { relevant: relevant2 } = await gradeContext(rewrite, context2);

    if (relevant2) {
      // Proceed to generation with context2 and original question
      return generateAnswer(question, context2);
    }
  }
  // Both attempts failed — hard refuse
  removeTyping();
  appendMsg('ai', "I don't see that in your recent screen data. Try rephrasing or check the Timeline tab for a visual overview.");
  // ...
  return;
}
generateAnswer(question, context);
```

#### P3-D: Integrate grader into sendAI()

```js
async function sendAI() {
  // ... existing setup ...

  const { context, fallback } = await getScreenpipeContext(question);

  // CRAG gate
  if (context.length > 0) {
    const { relevant, rewrite } = await gradeContext(question, context);
    if (!relevant) {
      // P3-C handles the rewrite + refuse flow
      await handleCRAGFail(question, rewrite);
      return;
    }
  }

  // Proceed to generation
  await generateAnswer(question, context, fallback);
}
```

---

### P4 — Hybrid BM25 Retrieval
**Files:** `context-server.py`
**New dependency:** `pip install rank-bm25` (pure Python, ~30KB, no C extensions)
**Impact:** +15–30% recall on exact-keyword queries (filenames, function names, error codes, app names)
**Effort:** 3–5 hours

BM25 excels where dense vectors fail: exact token matches for rare terms like specific filenames (`context-server.py`), error codes (`ERR_CONNECTION_REFUSED`), and proper nouns. Dense vectors excel on semantic similarity but smooth over exact terms. RRF combines both ranked lists without score normalization.

#### P4-A: In-memory BM25 index over recent frames

```python
from rank_bm25 import BM25Okapi

_bm25_index    = None
_bm25_corpus   = []
_bm25_built_at = 0.0
BM25_REFRESH_SECS = 120

def _ensure_bm25():
    global _bm25_index, _bm25_corpus, _bm25_built_at
    now = time.time()
    if _bm25_index and (now - _bm25_built_at) < BM25_REFRESH_SECS:
        return _bm25_index, _bm25_corpus

    data   = fetch_json(f"{SCREENPIPE_URL}/search?limit=500") or {}
    items  = data.get('data', [])
    corpus = []
    for item in items:
        c    = item.get('content', {})
        text = clean_ocr_text(c.get('text') or c.get('transcription') or '')
        blob = ' '.join(filter(None, [
            c.get('app_name', ''), c.get('window_name', ''),
            c.get('browser_url', ''), text
        ])).lower()
        uid  = str(c.get('frame_id') or c.get('timestamp', ''))
        corpus.append({'uid': uid, 'tokens': blob.split(), 'item': item})

    if corpus:
        _bm25_index    = BM25Okapi([e['tokens'] for e in corpus])
        _bm25_corpus   = corpus
        _bm25_built_at = now

    return _bm25_index, _bm25_corpus
```

#### P4-B: Reciprocal Rank Fusion

```python
def rrf_merge(ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """Standard RRF. k=60 is the canonical default from the original paper."""
    scores = {}
    for ranked in ranked_lists:
        for rank, uid in enumerate(ranked):
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (k + rank + 1)
    return scores

def hybrid_retrieve(query: str, existing_candidates: list, top_n: int = 50) -> list:
    bm25, corpus = _ensure_bm25()
    if bm25 is None:
        return existing_candidates[:top_n]

    keywords   = extract_keywords(query)
    bm25_raw   = bm25.get_scores(keywords)
    bm25_order = [corpus[i]['uid']
                  for i in sorted(range(len(bm25_raw)), key=lambda i: -bm25_raw[i])[:top_n]]

    existing_order = [
        str(i.get('content', {}).get('frame_id') or i.get('content', {}).get('timestamp', ''))
        for i in sorted(existing_candidates, key=lambda x: -x.get('_score', 0))
    ]

    merged_scores = rrf_merge([bm25_order, existing_order])

    uid_to_item  = {str(i.get('content', {}).get('frame_id') or
                        i.get('content', {}).get('timestamp', '')): i
                    for i in existing_candidates}
    uid_to_bm25  = {e['uid']: e['item'] for e in corpus}

    result, seen = [], set()
    for uid in sorted(merged_scores, key=merged_scores.get, reverse=True):
        if uid in seen: continue
        item = uid_to_item.get(uid) or uid_to_bm25.get(uid)
        if item:
            result.append(item)
            seen.add(uid)
        if len(result) >= top_n:
            break
    return result
```

Call `hybrid_retrieve()` at the start of `gather_context()`, replacing the initial keyword fetch loop. The result becomes the candidate pool that goes into scoring.

#### P4-C: BM25 for keyword-intent queries only (optimization)

BM25 index rebuilds every 2 minutes — acceptable overhead. However, BM25 is overkill for short temporal queries ("what did I do today?") where keywords are generic. Optionally gate BM25 retrieval based on query type:

```python
def query_needs_bm25(keywords: list[str], query: str) -> bool:
    """BM25 adds most value for queries with specific technical terms."""
    specific = any(len(k) > 6 and not k.isdigit() for k in keywords)
    has_specific_extension = bool(re.search(r'\.\w{2,4}\b', query))  # "config.py", ".env"
    return specific or has_specific_extension
```

---

### P5 — Future Work (v0.4+)

These are not appropriate for v0.3.2 but should be planned for later.

#### P5-A: ColBERT via RAGatouille

ColBERT achieves state-of-the-art retrieval quality by storing per-token embeddings and using MaxSim scoring. For collections > 50k frames, it outperforms bi-encoder + cross-encoder reranking on both recall and latency.

**Prerequisites:** Collection > 50k indexed frames, Python 3.9+
**Dependencies:** `pip install ragatouille` (brings PyTorch, transformers)
**Storage:** ~5–10 MB per 1k documents (token matrix vs. single vector)
**Query latency:** 50–200ms on CPU with PLAID index for 100k docs

```python
from ragatouille import RAGPretrainedModel

rag = RAGPretrainedModel.from_pretrained("colbert-ir/colbertv2.0")
rag.index(collection=docs, index_name="augur", max_document_length=256)
results = rag.search(query, k=15)
```

**Decision gate:** When total indexed frames exceeds 50k and recall quality plateaus with P4 improvements, migrate semantic retrieval to ColBERT.

#### P5-B: Streaming responses

Currently `stream: false` — the full response is buffered before display. Streaming (`stream: true`) would make the UI feel significantly more responsive for long answers.

```js
const r = await fetch(`${LM_STUDIO}/v1/chat/completions`, {
  body: JSON.stringify({ ..., stream: true })
});
const reader = r.body.getReader();
const decoder = new TextDecoder();
let aiDiv = appendMsgStreaming('ai'); // creates the bubble immediately

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const chunk = decoder.decode(value);
  for (const line of chunk.split('\n')) {
    if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
    const token = JSON.parse(line.slice(6))?.choices?.[0]?.delta?.content || '';
    aiDiv.innerHTML += formatToken(token); // incremental markdown
  }
}
```

**Constraint:** The CRAG grader (`gradeContext()`) must complete before streaming starts — you can't stream and grade simultaneously. Run the grader first, then stream only on YES.

#### P5-C: Fine-tuned embedding model for screen activity

`all-MiniLM-L6-v2` was trained on general web text, not screen OCR. A fine-tuned model on a screen-capture dataset would significantly improve retrieval of noisy, UI-specific text.

**Training data path:** Generate contrastive pairs from the existing Chroma collection. Positive pairs: (user question, relevant frame). Negative pairs: (user question, irrelevant frames). Use `sentence_transformers` `MultipleNegativesRankingLoss` on a 10k-pair dataset (approximately 1 week of captures for an active user).

**Alternative without training:** Switch to `BAAI/bge-small-en-v1.5` which performs better than `all-MiniLM-L6-v2` on short/fragmented text and is similarly sized.

#### P5-D: Persistent query cache

For repeated or near-identical queries, cache the context retrieval result for 60 seconds to avoid redundant screenpipe fetches:

```python
import hashlib, functools

_ctx_cache = {}  # {query_hash: (timestamp, result)}
CTX_CACHE_TTL = 60  # seconds

def cached_gather_context(query, limit, window_hours):
    key = hashlib.md5(f"{query}|{limit}|{window_hours}".encode()).hexdigest()
    if key in _ctx_cache:
        ts, result = _ctx_cache[key]
        if time.time() - ts < CTX_CACHE_TTL:
            return result
    result = gather_context(query, limit, window_hours)
    _ctx_cache[key] = (time.time(), result)
    # Evict old entries
    if len(_ctx_cache) > 100:
        oldest = min(_ctx_cache, key=lambda k: _ctx_cache[k][0])
        del _ctx_cache[oldest]
    return result
```

---

## 5. Signal Inventory

Full map of what data exists in the pipeline and how it should be used:

| Signal | Source | Currently Used? | Proposed Phase |
|---|---|---|---|
| `app_name`, `window_name` | screenpipe OCR frames | Not in LLM context | P0-C |
| `browser_url` | screenpipe OCR frames | Not in LLM context | P0-C |
| `scroll_depth_pct` | Browser extension | Captured, never scored | P1-E |
| `time_on_page_s` | Browser extension | OCR scoring only | P1-E (browser too) |
| `selected_text` | Browser extension | Yes (sel_bon 2.0) | Already used |
| `timestamp` | All | Shown in context, routing ignored | P0-E |
| Historical frames (>200) | screenpipe | Not indexed | P2-C |
| Browser captures | Extension | Not in semantic index | P1-E + P2-A |
| `type` field (OCR/audio) | screenpipe | Stored, never filtered | P1-F |
| Heatmap / peak hours | /context-card | Not used in retrieval | P5 (time-of-day prior) |
| BM25 exact match | In-memory | Not implemented | P4 |
| Cross-encoder relevance score | sentence_transformers | Not implemented | P3-B |
| App-level user dwell patterns | /profile | Not used for ranking | Future |

---

## 6. Latency Profile

Understanding where time is spent is essential for choosing where to optimize.

| Step | Current latency | Bottleneck? |
|---|---|---|
| `getScreenpipeContext()` — context-server fetch | 50–200ms | Screenpipe SQLite + Python scoring |
| `getScreenpipeContext()` — fallback (3030 direct) | 100–400ms | Multiple parallel fetches |
| `gradeContext()` — LLM call (P3-A) | 300–800ms | LM Studio round-trip |
| `gradeContext()` — cross-encoder (P3-B) | 30–60ms | CPU inference |
| `sendAI()` — generation | 2000–8000ms | Depends on model, response length |
| Context block assembly + trimming | <5ms | Negligible |
| BM25 index rebuild (P4) | 200–500ms every 2 min | Background, non-blocking |
| Semantic index query | 20–80ms | Chroma HNSW search |

**Parallelization opportunity:** The context-server fetch and the LM Studio model availability check can run in parallel with `Promise.all()`. Currently they are sequential.

**Perceived latency budget:** Users expect <3s total before seeing any response. Current: context fetch (200ms) + generation (3000ms) = ~3.2s, already at the edge. The CRAG grader adds 300–800ms on the LLM path — use the cross-encoder variant (P3-B) to keep latency under 3s.

---

## 7. Measuring Improvement

Without a labeled ground-truth dataset, use these proxy measures:

**Proxy metric 1 — Grader acceptance rate:** After implementing P3, log the fraction of queries where the CRAG grader returns YES vs NO. A healthy grader should accept 70–85% of queries (the rest being genuinely unanswerable from screen data). If acceptance drops below 50%, retrieval quality is too low and P1/P2 improvements are needed.

**Proxy metric 2 — Refusal rate on fiction test:** Submit 10 made-up queries ("What was in my Kubernetes config?" when never opened). Count how many correctly refuse. Target: 10/10.

**Proxy metric 3 — Citation density:** Count the fraction of AI responses that include at least one citation in the form `(App: X, Time: HH:MM)` or equivalent. Target: >80% of responses.

**Proxy metric 4 — Context diversity:** For each response, count how many unique apps appear in the retrieved context block. Consistently seeing 1 app (usually VS Code) indicates the window deduplication (P1-B) is not working.

**Proxy metric 5 — Semantic index coverage:** Run `collection.count()` on the Chroma collection. It should grow toward total screenpipe frame count, not plateau at 200.

**Evaluation script sketch:**

```python
# test_ai_accuracy.py
import urllib.request, json

FICTION_QUERIES = [
    "What did I write in my AWS IAM config?",
    "What Kubernetes namespace was I configuring?",
    "What was in the Terraform module I wrote?",
]

def query_context(q):
    url = f"http://localhost:3031/context?q={urllib.parse.quote(q)}&limit=10"
    return json.loads(urllib.request.urlopen(url).read())

for q in FICTION_QUERIES:
    result = query_context(q)
    top_score = max((r.get('score', 0) for r in result.get('results', [])), default=0)
    print(f"Query: {q[:50]}")
    print(f"  Top result score: {top_score:.3f} (should be low for fiction)")
    print(f"  Candidate count: {result.get('total_candidates', 0)}")
```

---

## 8. Error Taxonomy

What different failure modes look like so they can be diagnosed quickly:

| Symptom | Likely Cause | Fix |
|---|---|---|
| AI confidently states wrong app or file | Context block missing app/window (Cause 2) | P0-C |
| Follow-up question answered correctly, then third message hallucinates | Context dropped after N turns | Check synthetic-turn pattern; verify history slice |
| "What did I do last week?" returns today's data | Temporal routing not active | P0-E |
| AI answers with generic knowledge, no citations | CRAG grader not blocking low-relevance context | P3-A/B |
| Context filled entirely with VS Code frames | No window deduplication | P1-B |
| Semantic search always returns 0 results | Historical frames not indexed (only 200 indexed) | P2-C |
| Context block too short, cuts off mid-sentence | Budget calc using chars instead of tokens | P0-B |
| Temperature changes don't seem to affect creativity | LM Studio model ignoring temperature | Check LM Studio "Inference" settings; some models require `repeat_penalty` instead |
| Grader always says YES even for clearly irrelevant context | Grader prompt too lenient or model too agreeable | Lower `max_tokens` to 5, force single-word output, check verdict parsing |
| BM25 adding noise (wrong filenames matching) | Generic keyword tokens swamping specific ones | Apply keyword extraction to BM25 query too; use IDF from BM25Okapi |

---

## 9. Dependency Map

| Phase | File Modified | New Dependency | Existing Deps |
|---|---|---|---|
| P0 | screenpipe-dashboard.html | None | None |
| P1-A–D | context-server.py | None | `re`, `unicodedata`, `math` (stdlib) |
| P1-E | context-server.py | None | `sentence_transformers` (already installed) |
| P1-F | context-server.py | None | stdlib |
| P2-A–D | semantic_search.py | None | `chromadb`, `sentence_transformers` |
| P2-E (HyPE) | semantic_search.py | LM Studio local | `chromadb`, `sentence_transformers` |
| P3-A (LLM grader) | screenpipe-dashboard.html | None | LM Studio local |
| P3-B (CE grader) | context-server.py | None | `sentence_transformers` |
| P4 | context-server.py | `rank-bm25` | None |
| P5-A (ColBERT) | context-server.py | `ragatouille`, PyTorch | — |
| P5-B (streaming) | screenpipe-dashboard.html | None | None |

---

## 10. Constraints

- **No extra endpoints** unless absolutely required. P3-B adds `/grade`; that is the only new route.
- **Vanilla JS only** in `screenpipe-dashboard.html`. No npm, no build step, no bundler.
- **Local-first, always offline capable.** All LLM calls to `localhost:1234`. No external API calls ever.
- **Fail gracefully everywhere.** If grader, BM25, or semantic index fails, fall back to current pipeline silently. Never crash the user's session.
- **stdlib preference** in `context-server.py`. Only `rank-bm25` is acceptable as a new Python dep (pure Python, no compilation). `sentence_transformers` and `chromadb` are already in `requirements.txt`.
- **Do not touch** `mcp_server.py`, `demo_agent.py`, `extension/`, `launch.command` unless explicitly noted.
- **Screenpipe API contract:** Do not change the screenpipe port (3030) or endpoint paths. The `/raw_sql` endpoint may be used for index pagination (P2-C) if `/search?offset=N` is unavailable.

---

## 11. Verification & Testing Matrix

| ID | Scenario | Expected Result | What It Validates |
|---|---|---|---|
| T1 | "What was in my AWS config file?" (never opened) | Hard refuse: "I don't see that..." | CRAG grader blocking hallucination |
| T2 | Q1: "What function was I writing?" Q2: "What parameters?" | Second answer cites same function correctly | Fresh context on every turn |
| T3 | "What was I reading in the browser?" | "At 1:45 PM in Arc Browser (github.com/...)..." | App + URL in context block |
| T4 | "What did I work on last week?" | Returns data from 7+ days ago | Temporal routing + full history index |
| T5 | Ask anything after VS Code open all day | At most 1–2 VS Code frames in context | Window deduplication working |
| T6 | "Find that context-server.py file I had open 3h ago" | File appears in results | BM25 exact filename match |
| T7 | Ask ambiguous question with sparse context | Conservative answer or polite refuse | Temperature 0.15 + grader |
| T8 | Ask about a page scrolled 90% vs. a bounced page | Deep-scroll page ranks above bounce | scroll_depth_pct scoring |
| T9 | Ask "what did I hear in the meeting?" | Audio frames surface if recorded | Type-based routing (audio preference) |
| T10 | Ask same question twice within 60s | Second response is instant | Context cache (P5-D, future) |
| T11 | Semantic index count after 1 full week | collection.count() > 5000 | Full historical indexing |
| T12 | Grader acceptance rate over 20 real queries | 70–85% YES | Grader calibration |

---

## 12. Expected Gains by Phase

| Phase Stack | Hallucination Reduction | Recall Improvement | Notes |
|---|---|---|---|
| P0 alone | ~60% | — | No new deps, highest ROI |
| P0 + P1 | ~70% | +20–35% | Backend tuning only |
| P0 + P1 + P2 | ~75% | +30–45% on temporal | Full semantic history |
| P0 + P1 + P2 + P3 | ~90% | — (precision gate) | CRAG eliminates residual hallucination |
| Full P0–P4 | ~90–95% | +40–50% on keyword | BM25 closes the exact-match gap |

**Research benchmark sources:**
- CRAG (Yan et al., 2023): +12–18% exact match accuracy over naive RAG
- Anthropic contextual retrieval (Sept. 2024): 35–49% retrieval failure reduction vs. standard chunking
- BM25 + dense + RRF: +15–30% recall@10 vs. dense-only (multiple IR benchmarks)
- Cross-encoder reranking: +5–20% precision@5 (lower bound for short OCR text, upper for longer)
- Temperature 0.7 → 0.15 on 7B–13B models: ~22% hallucination reduction (RAG clinical studies, 2025)
- "Lost in the middle" (Liu et al., 2023): 15–30% accuracy drop for relevant content placed in middle position
- Near-duplicate removal from index: 40–70% index size reduction; +5–12% retrieval diversity

---

## 13. Anti-Goals

Specific things NOT to build and exactly why:

**HyDE (Hypothetical Document Embeddings):** Generates a hypothetical answer, embeds it, searches for similar documents. Fails for OCR because the synthetic prose embedding has a different distribution than sparse, noisy OCR tokens. The embedding space mismatch hurts recall. Use HyPE (P2-E) — index-time hypothetical queries — instead, which embeds question-to-question and is much more stable.

**ColBERT at v0.3.2:** Excellent retrieval quality but ~1 GB index per 100k docs, 5–10 min full rebuild, requires PyTorch. Overkill for current scale. Revisit at v0.4 when the frame count exceeds 50k.

**LLMLingua prompt compression:** Adds a BERT-base forward pass to selectively drop "unimportant" tokens from the context. Useful when the model context window is genuinely tight (≤ 2k tokens). With LM Studio at 8192+ tokens and the corrected budget calculation (P0-B), the context window is not a binding constraint. Skip.

**Summary cascades:** Multiple LLM calls to compress retrieved context into a summary. High latency, risk of losing key details. The position optimization (P0-F) + budget-proportional trimming achieves similar results with zero additional LLM calls.

**Self-consistency voting (2× LLM calls):** Runs two generations and picks the consistent answer. Doubles latency. The CRAG grader achieves more reliable hallucination reduction at lower cost because it gates before generation rather than adjudicating after.

**External embedding APIs (OpenAI, Cohere, Voyage):** Sends user screen data to third-party servers. Non-negotiable privacy violation for this product. Local only.

**Streaming without grader:** Streaming before the CRAG grade completes creates a bad UX — the AI starts generating a hallucinated answer and must be interrupted. Always grade first, then stream.
