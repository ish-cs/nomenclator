#!/usr/bin/env python3
"""
Augur Context API Server
------------------------
Runs on localhost:3031. Provides context to AI agents from screenpipe data.

Endpoints:
  GET /health                        — status check
  GET /context?q=...&limit=N&window_hours=H  — ranked context for a query
  GET /summary?date=YYYY-MM-DD       — structured daily summary
  GET /profile?days=7                — behavioral profile
  GET /context-card?days=7           — compact natural language profile card

Usage: python context-server.py
"""

import hashlib
import json
import math
import os
import re
import time
import unicodedata
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────
SCREENPIPE_URL = "http://localhost:3030"
PORT = 3031
DEFAULT_LIMIT = 25          # P1-D: was 15
DEFAULT_WINDOW_HOURS = 24

HALF_LIFE_HOURS = 6.0       # P1-C: exponential recency decay half-life

BROWSER_CAPTURES_FILE = os.path.expanduser("~/.screenpipe/browser_captures.json")
BROWSER_CAPTURES_MAX = 1000   # max entries stored

STOP_WORDS = frozenset([
    'what','have','i','been','the','a','an','is','are','was','were',
    'do','did','can','tell','me','all','about','on','in','at','to',
    'for','of','and','or','my','your','any','some','this','that',
    'how','when','where','who','why','which','with','from','by',
    'as','it','its','be','has','had','will','would','could','should',
    'just','get','got','show','find','give','let','now','up','down',
    'not','no','yes','but','so','if','then','than','too','very',
])

# ── Semantic cache (loaded once, reused across requests) ─────────────
_semantic_embedder   = None   # SentenceTransformer instance or None
_semantic_collection = None   # Chroma collection or None
_semantic_available  = None   # None = untested | True | False

# ── Cross-encoder grader (P3-B) ──────────────────────────────────────
_grader_model = None          # lazy-loaded CrossEncoder

# ── BM25 index (P4-A) ────────────────────────────────────────────────
_bm25_index    = None
_bm25_corpus   = []
_bm25_built_at = 0.0
BM25_REFRESH_SECS = 120


# ── Helpers ─────────────────────────────────────────────────────────
def extract_keywords(text):
    words = text.lower().replace("'", '').replace('"', '')
    words = ''.join(c if c.isalnum() or c == ' ' else ' ' for c in words)
    return [w for w in words.split() if len(w) > 2 and w not in STOP_WORDS][:6]


def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def post_json(url, payload):
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def screenpipe_ok():
    r = fetch_json(f"{SCREENPIPE_URL}/health")
    return r is not None and r.get('status') == 'healthy'


def cors_headers():
    return {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Content-Type': 'application/json',
    }


# ── P1-A: OCR text cleaning ──────────────────────────────────────────
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
    text = re.sub(r'\b([A-Za-z]) (?=[A-Za-z] [A-Za-z])', r'\1', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── P1-C: Exponential recency decay ─────────────────────────────────
def recency_score(age_s: float) -> float:
    """Exponential decay with 6-hour half-life. Returns 0.0–1.0."""
    return math.exp(-0.693 * age_s / (HALF_LIFE_HOURS * 3600))


# ── P1-G: Multi-query decomposition ─────────────────────────────────
def decompose_query(query: str, keywords: list) -> list:
    """Split multi-part queries into sub-queries for broader recall."""
    parts = re.split(r'\s+(?:and|or|also|plus|as well as)\s+', query.lower())
    if len(parts) > 1:
        return [p.strip() for p in parts if len(p.strip()) > 5]
    if len(keywords) >= 4:
        return [' '.join(keywords[:3]), ' '.join(keywords[3:])]
    return [query]


# ── Semantic helpers ─────────────────────────────────────────────────
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
        return {str(r.get('id', '')): r.get('score', 0.0) for r in result.get('results', []) if r.get('id')}
    except Exception as e:
        print(f'[semantic] scoring error: {e}', flush=True)
        return {}


# ── P3-B: Cross-encoder grader ───────────────────────────────────────
def _get_grader():
    global _grader_model
    if _grader_model is None:
        from sentence_transformers import CrossEncoder as _CE
        # 22M params, ~60ms/batch on CPU, no GPU needed
        _grader_model = _CE('cross-encoder/ms-marco-MiniLM-L-6-v2')
    return _grader_model


# ── P4-A: BM25 in-memory index ───────────────────────────────────────
def _ensure_bm25():
    global _bm25_index, _bm25_corpus, _bm25_built_at
    now = time.time()
    if _bm25_index and (now - _bm25_built_at) < BM25_REFRESH_SECS:
        return _bm25_index, _bm25_corpus

    data  = fetch_json(f"{SCREENPIPE_URL}/search?limit=500") or {}
    items = data.get('data', [])
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
        from rank_bm25 import BM25Okapi
        _bm25_index    = BM25Okapi([e['tokens'] for e in corpus])
        _bm25_corpus   = corpus
        _bm25_built_at = now

    return _bm25_index, _bm25_corpus


# ── P5-A: ColBERT via RAGatouille ────────────────────────────────────
_colbert_rag = None
_colbert_index_built = False
COLBERT_FRAME_THRESHOLD = 50000


def _get_colbert():
    global _colbert_rag, _colbert_index_built
    if _colbert_rag is not None:
        return _colbert_rag
    try:
        from ragatouille import RAGPretrainedModel
        _colbert_rag = RAGPretrainedModel.from_pretrained("colbert-ir/colbertv2.0")
    except Exception:
        _colbert_rag = False
    return _colbert_rag


def colbert_retrieve(query: str, docs: list[str], ids: list[str], top_k: int = 15) -> list[dict]:
    """Run ColBERT retrieval if available and frame count exceeds threshold."""
    rag = _get_colbert()
    if not rag:
        return []
    try:
        import tempfile, os
        index_path = os.path.expanduser("~/.screenpipe/colbert_index")
        global _colbert_index_built
        if not _colbert_index_built:
            rag.index(collection=docs, document_ids=ids,
                      index_name="augur", max_document_length=256,
                      overwrite_index=False)
            _colbert_index_built = True
        results = rag.search(query, k=top_k)
        return results
    except Exception:
        return []


# ── P4-B: RRF merge and hybrid retrieval ────────────────────────────
def rrf_merge(ranked_lists: list, k: int = 60) -> dict:
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

    uid_to_item = {str(i.get('content', {}).get('frame_id') or
                       i.get('content', {}).get('timestamp', '')): i
                   for i in existing_candidates}
    uid_to_bm25 = {e['uid']: e['item'] for e in corpus}

    result, seen = [], set()
    for uid in sorted(merged_scores, key=merged_scores.get, reverse=True):
        if uid in seen:
            continue
        item = uid_to_item.get(uid) or uid_to_bm25.get(uid)
        if item:
            result.append(item)
            seen.add(uid)
        if len(result) >= top_n:
            break
    return result


# ── P4-C: BM25 gate ──────────────────────────────────────────────────
def query_needs_bm25(keywords: list, query: str) -> bool:
    """BM25 adds most value for queries with specific technical terms."""
    specific = any(len(k) > 6 and not k.isdigit() for k in keywords)
    has_specific_extension = bool(re.search(r'\.\w{2,4}\b', query))
    return specific or has_specific_extension


# ── P5-D: Persistent query cache ─────────────────────────────────────
_ctx_cache = {}
CTX_CACHE_TTL = 60


def cached_gather_context(query, limit, window_hours, **kwargs):
    app_filter = kwargs.get('app_filter', '')
    type_filter = kwargs.get('type_filter', '')
    key = hashlib.md5(f"{query}|{limit}|{window_hours}|{app_filter}|{type_filter}".encode()).hexdigest()
    if key in _ctx_cache:
        ts, result = _ctx_cache[key]
        if time.time() - ts < CTX_CACHE_TTL:
            return result
    result = gather_context(query, limit, window_hours, **kwargs)
    _ctx_cache[key] = (time.time(), result)
    if len(_ctx_cache) > 100:
        oldest = min(_ctx_cache, key=lambda k: _ctx_cache[k][0])
        del _ctx_cache[oldest]
    return result


# ── Browser captures storage ─────────────────────────────────────────
_browser_captures = []   # in-memory list, loaded from file on start


def load_browser_captures():
    global _browser_captures
    try:
        if os.path.exists(BROWSER_CAPTURES_FILE):
            with open(BROWSER_CAPTURES_FILE, 'r') as f:
                _browser_captures = json.load(f)
    except Exception:
        _browser_captures = []


def save_browser_capture(entry):
    global _browser_captures
    entry['received_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    _browser_captures.append(entry)
    if len(_browser_captures) > BROWSER_CAPTURES_MAX:
        _browser_captures = _browser_captures[-BROWSER_CAPTURES_MAX:]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    _browser_captures = [c for c in _browser_captures if c.get('timestamp', '') >= cutoff]
    try:
        os.makedirs(os.path.dirname(BROWSER_CAPTURES_FILE), exist_ok=True)
        with open(BROWSER_CAPTURES_FILE, 'w') as f:
            json.dump(_browser_captures, f)
    except Exception:
        pass


def get_browser_captures(limit=50):
    return list(reversed(_browser_captures[-limit:]))


def browser_capture_to_candidate(cap):
    """Convert a browser capture entry into a pseudo-item compatible with gather_context scoring."""
    url    = cap.get('url', '') or ''
    domain = cap.get('domain', '') or ''
    title  = cap.get('title', '') or ''
    selected = cap.get('selected_text', '') or ''
    ts     = cap.get('timestamp') or cap.get('received_at', '') or ''
    time_s = int(cap.get('time_on_page_s') or 0)
    scroll = int(cap.get('scroll_depth_pct') or 0)

    # P1-A: apply clean_ocr_text to selected text in blob
    cleaned_selected = clean_ocr_text(selected)

    # Searchable blob: all text fields lowercased
    blob = ' '.join(filter(None, [
        title.lower(),
        domain.lower(),
        url[:200].lower(),
        cleaned_selected[:400].lower(),
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
            # P1-A: 500 chars (was 300), use cleaned text
            'text': (cleaned_selected[:500] if cleaned_selected else (title or url[:150])),
            'url': url,
            'source': 'browser',
        },
    }


# ── Anomaly detection ────────────────────────────────────────────────
def get_anomalies(days=7):
    today = datetime.now().strftime('%Y-%m-%d')

    today_data = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT app_name, COUNT(*) as cnt FROM frames "
            f"WHERE date(timestamp)='{today}' AND app_name IS NOT NULL AND app_name != '' "
            f"GROUP BY app_name ORDER BY cnt DESC"
        )
    }) or []

    hist_data = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': (
            f"SELECT app_name, date(timestamp) as day, COUNT(*) as cnt FROM frames "
            f"WHERE date(timestamp) < '{today}' "
            f"AND date(timestamp) >= date('{today}', '-{days} days') "
            f"AND app_name IS NOT NULL AND app_name != '' "
            f"GROUP BY app_name, day"
        )
    }) or []

    # Build per-app daily averages from history
    app_daily = defaultdict(list)
    for row in hist_data:
        app = row.get('app_name', '')
        if app:
            app_daily[app].append(int(row.get('cnt', 0)))

    app_avg = {app: sum(counts) / len(counts) for app, counts in app_daily.items() if counts}

    today_by_app = {row.get('app_name', ''): int(row.get('cnt', 0)) for row in today_data if row.get('app_name')}
    total_today = sum(today_by_app.values()) or 1

    anomalies = []
    all_apps = set(list(today_by_app.keys()) + list(app_avg.keys()))

    for app in all_apps:
        today_cnt = today_by_app.get(app, 0)
        avg_cnt = app_avg.get(app, 0)
        pct = round(today_cnt / total_today * 100)

        if avg_cnt == 0 and today_cnt >= 20:
            anomalies.append({
                'app': app, 'today': today_cnt, 'avg': 0,
                'ratio': None, 'type': 'new', 'pct_of_day': pct,
                'message': f"{app} appeared for the first time ({today_cnt} frames, {pct}% of today)",
            })
        elif avg_cnt > 0:
            ratio = today_cnt / avg_cnt
            if ratio >= 2.0 and today_cnt >= 20:
                anomalies.append({
                    'app': app, 'today': today_cnt, 'avg': round(avg_cnt, 1),
                    'ratio': round(ratio, 2), 'type': 'high', 'pct_of_day': pct,
                    'message': f"{app}: {today_cnt} frames today vs {avg_cnt:.0f} avg ({ratio:.1f}x more than usual)",
                })
            elif ratio <= 0.3 and avg_cnt >= 20:
                anomalies.append({
                    'app': app, 'today': today_cnt, 'avg': round(avg_cnt, 1),
                    'ratio': round(ratio, 2), 'type': 'low', 'pct_of_day': pct,
                    'message': f"{app}: only {today_cnt} frames today vs {avg_cnt:.0f} avg ({ratio:.2f}x of usual)",
                })

    anomalies.sort(key=lambda x: abs((x.get('ratio') or 3.0)), reverse=True)

    return {
        'date': today,
        'days_compared': days,
        'total_frames_today': total_today,
        'anomalies': anomalies,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }


# ── Context gathering ────────────────────────────────────────────────
def gather_context(query, limit, window_hours, app_filter='', type_filter=''):
    keywords = extract_keywords(query)

    # P1-G: decompose query into sub-queries for broader recall
    sub_queries = decompose_query(query, keywords)

    # P1-D: Recent fetch limit = limit * 3 (was limit * 2)
    recent_data = fetch_json(f"{SCREENPIPE_URL}/search?limit={limit * 3}") or {}
    recent_items = recent_data.get('data', [])

    # Fetch per-keyword search results for each sub-query
    # P1-D: keyword search limit = 30 (was 20)
    kw_items = []
    seen_kw = set()
    for sub_q in sub_queries:
        sub_keywords = extract_keywords(sub_q)
        if not sub_keywords:
            sub_keywords = keywords
        for kw in sub_keywords:
            if kw in seen_kw:
                continue
            seen_kw.add(kw)
            q = urllib.parse.quote(kw)
            result = fetch_json(f"{SCREENPIPE_URL}/search?q={q}&limit=30") or {}  # P1-D
            kw_items.extend(result.get('data', []))

    # Deduplicate by frame_id / timestamp
    seen = set()
    all_items = []
    for item in recent_items + kw_items:
        c = item.get('content', {})
        uid = c.get('frame_id') or c.get('timestamp')
        if uid and uid not in seen:
            seen.add(uid)
            all_items.append(item)

    # ── Merge browser captures ───────────────────────────────────────
    now_dt = datetime.now(timezone.utc)
    cutoff_dt = now_dt - timedelta(seconds=window_hours * 3600)
    for cap in _browser_captures:
        ts_str = cap.get('timestamp') or cap.get('received_at', '')
        try:
            cap_dt = datetime.fromisoformat(
                ts_str.replace('Z', '+00:00')
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

    # Attempt semantic bonus — transparent no-op if unavailable
    _try_load_semantic()
    semantic_scores = _get_semantic_scores(query, min(limit * 3, 60))
    semantic_active = bool(semantic_scores)

    # Score: keyword_matches * 3 + recency (+ semantic bonus for OCR/audio)
    now = datetime.now(timezone.utc)
    window_ms = window_hours * 3600
    for item in all_items:
        if item.get('_source') == 'browser':
            blob = item.get('_blob', '')
            kw_score = sum(min(blob.count(kw), 5) for kw in keywords)
            try:
                ts = datetime.fromisoformat(
                    item['_timestamp'].replace('Z', '+00:00'))
                age_s = (now - ts).total_seconds()
            except Exception:
                age_s = window_ms
            # P1-C: exponential recency decay (was linear)
            recency   = recency_score(age_s)
            time_bon  = min(item['_time_on_page_s'] / 300.0, 1.0)
            sel_bon   = 2.0 if item['_has_selection'] else 0.0
            # P1-E: scroll depth bonus and semantic signal for browser
            scroll_bon = min(item.get('_scroll_pct', 0) / 100.0, 1.0) * 0.5
            uid_key    = item.get('_uid', '')
            sem_bonus  = semantic_scores.get(uid_key, 0.0) * 2.0
            item['_score'] = kw_score * 3 + recency + time_bon + sel_bon + scroll_bon + sem_bonus
        else:
            # Existing OCR/audio scoring
            c = item.get('content', {})
            # P1-A: apply clean_ocr_text to scoring blob
            raw_text = c.get('text', '') or c.get('transcription', '') or ''
            cleaned_text = clean_ocr_text(raw_text)
            blob = ' '.join([
                cleaned_text,
                c.get('app_name', '') or '',
                c.get('window_name', '') or '',
            ]).lower()
            kw_score = sum(min(blob.count(kw), 5) for kw in keywords)
            try:
                ts = datetime.fromisoformat(
                    c.get('timestamp', '').replace('Z', '+00:00'))
                age_s = (now - ts).total_seconds()
            except Exception:
                age_s = window_ms
            # P1-C: exponential recency decay (was linear)
            recency = recency_score(age_s)
            # Semantic bonus: cosine similarity * 2.0 (range 0-2)
            uid_key = str(c.get('frame_id') or c.get('timestamp') or '')
            sem_bonus = semantic_scores.get(uid_key, 0.0) * 2.0
            item['_score'] = kw_score * 3 + recency + sem_bonus

    all_items.sort(key=lambda x: x.get('_score', 0), reverse=True)

    # P1-B: Deduplicate by (app_name, window_name), keeping best-scored per window.
    # Browser items always kept (unique by URL/timestamp).
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

    # P1-F: Apply app and type filters after deduplication
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

    # P4-C: Use hybrid BM25+vector retrieval only when query warrants it
    try:
        if query_needs_bm25(keywords, query):
            all_items = hybrid_retrieve(query, all_items, top_n=limit * 2)
    except Exception:
        pass  # BM25 unavailable or failed — degrade gracefully

    # P5-A: ColBERT retrieval — activates only when collection has >50k frames
    try:
        if _semantic_collection is not None and _semantic_collection.count() > COLBERT_FRAME_THRESHOLD:
            cb_docs, cb_ids = [], []
            for item in all_items:
                if item.get('_source') == 'browser':
                    cb_docs.append(item.get('_blob', ''))
                    cb_ids.append(item.get('_uid', ''))
                else:
                    c = item.get('content', {})
                    raw = c.get('text', '') or c.get('transcription', '') or ''
                    cb_docs.append(clean_ocr_text(raw)[:256])
                    cb_ids.append(str(c.get('frame_id') or c.get('timestamp', '')))
            if cb_docs:
                cb_results = colbert_retrieve(query, cb_docs, cb_ids, top_k=15)
                if cb_results:
                    cb_uid_order = [r.get('document_id', '') for r in cb_results if r.get('document_id')]
                    existing_order = [
                        (item.get('_uid') or str(item.get('content', {}).get('frame_id') or
                         item.get('content', {}).get('timestamp', '')))
                        for item in all_items
                    ]
                    merged_scores = rrf_merge([cb_uid_order, existing_order])
                    uid_to_item = {}
                    for item in all_items:
                        uid = (item.get('_uid') or str(item.get('content', {}).get('frame_id') or
                               item.get('content', {}).get('timestamp', '')))
                        uid_to_item[uid] = item
                    reranked, seen_cb = [], set()
                    for uid in sorted(merged_scores, key=merged_scores.get, reverse=True):
                        if uid in seen_cb:
                            continue
                        if uid in uid_to_item:
                            reranked.append(uid_to_item[uid])
                            seen_cb.add(uid)
                    if reranked:
                        all_items = reranked
    except Exception:
        pass  # ColBERT unavailable or failed — degrade gracefully

    top = all_items[:limit]

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
            # P1-A: apply clean_ocr_text and increase text limit 300→500
            raw_text = (c.get('text') if is_ocr else c.get('transcription')) or ''
            cleaned_text = clean_ocr_text(raw_text)
            results.append({
                'frame_id': c.get('frame_id'),
                'timestamp': c.get('timestamp'),
                'app': c.get('app_name') if is_ocr else None,
                'window': c.get('window_name') if is_ocr else None,
                'text': cleaned_text[:500],  # P1-A: 500 chars (was 300)
                'url': c.get('browser_url'),
                'score': round(item.get('_score', 0), 3),
                'source': 'ocr' if is_ocr else 'audio',
            })

    return {
        'query': query,
        'keywords': keywords,
        'total_candidates': len(all_items),
        'browser_captures_included': sum(
            1 for i in all_items if i.get('_source') == 'browser'
        ),
        'semantic_enhanced': semantic_active,
        'results': results,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }


# ── Daily summary ────────────────────────────────────────────────────
def get_summary(date_str):
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return None, 'invalid date format, use YYYY-MM-DD'

    top_apps = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': f"SELECT app_name, COUNT(*) as cnt FROM frames WHERE date(timestamp)='{date_str}' GROUP BY app_name ORDER BY cnt DESC LIMIT 10"
    }) or []

    hourly = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': f"SELECT strftime('%H', timestamp) as hr, COUNT(*) as cnt FROM frames WHERE date(timestamp)='{date_str}' GROUP BY hr ORDER BY cnt DESC LIMIT 5"
    }) or []

    total_frames = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': f"SELECT COUNT(*) as n FROM frames WHERE date(timestamp)='{date_str}'"
    }) or [{'n': 0}]

    audio_count = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': f"SELECT COUNT(*) as n FROM audio_transcriptions WHERE date(timestamp)='{date_str}'"
    }) or [{'n': 0}]

    url_count = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': f"SELECT COUNT(DISTINCT browser_url) as n FROM frames WHERE date(timestamp)='{date_str}' AND browser_url IS NOT NULL AND browser_url != ''"
    }) or [{'n': 0}]

    # Word frequency for topics
    sample_text = post_json(f"{SCREENPIPE_URL}/raw_sql", {
        'query': f"SELECT text FROM frames WHERE date(timestamp)='{date_str}' AND text IS NOT NULL LIMIT 200"
    }) or []
    word_freq = defaultdict(int)
    for row in sample_text:
        for w in (row.get('text', '') or '').lower().split():
            w = ''.join(c for c in w if c.isalpha())
            if len(w) > 3 and w not in STOP_WORDS:
                word_freq[w] += 1
    topics = [w for w, _ in sorted(word_freq.items(), key=lambda x: -x[1])[:20]]

    return {
        'date': date_str,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'profile': {
            'total_frames': total_frames[0].get('n', 0) if total_frames else 0,
            'top_apps': [{'app': r.get('app_name'), 'frames': r.get('cnt')} for r in top_apps],
            'active_hours': [int(r.get('hr', 0)) for r in hourly],
            'topics': topics,
            'urls_visited': url_count[0].get('n', 0) if url_count else 0,
            'audio_chunks': audio_count[0].get('n', 0) if audio_count else 0,
        }
    }, None


# ── Profile endpoints ─────────────────────────────────────────────────
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


def get_context_card(days=7):
    """
    Ultra-compact natural language profile (~300-500 chars).
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


# ── HTTP Handler ─────────────────────────────────────────────────────
class ContextHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        ts = datetime.now().strftime('%H:%M:%S')
        print(f"  [{ts}] {fmt % args}")

    def send_json(self, code, payload):
        body = json.dumps(payload, indent=2).encode()
        self.send_response(code)
        for k, v in cors_headers().items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in cors_headers().items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        def p(key, default=None):
            vals = params.get(key)
            return vals[0] if vals else default

        if parsed.path == '/health':
            self.send_json(200, {
                'status': 'ok',
                'screenpipe': screenpipe_ok(),
                'port': PORT,
            })

        elif parsed.path == '/context':
            query = p('q', '')
            limit = int(p('limit', DEFAULT_LIMIT))
            window_hours = int(p('window_hours', DEFAULT_WINDOW_HOURS))
            # P1-F: parse app and type filter params
            app_filter  = (p('app', '') or '').lower().strip()
            type_filter = (p('type', '') or '').lower().strip()
            if not query:
                self.send_json(400, {'error': 'q parameter required'})
                return
            if len(query) > 1000:
                query = query[:1000]
            result = cached_gather_context(query, limit, window_hours,
                                           app_filter=app_filter, type_filter=type_filter)
            self.send_json(200, result)

        elif parsed.path == '/summary':
            date_str = p('date', datetime.now().strftime('%Y-%m-%d'))
            result, err = get_summary(date_str)
            if err:
                self.send_json(400, {'error': err})
            else:
                self.send_json(200, result)

        elif parsed.path == '/anomalies':
            days = int(p('days', 7))
            self.send_json(200, get_anomalies(days))

        elif parsed.path == '/semantic':
            query = p('q', '')
            limit = int(p('limit', DEFAULT_LIMIT))
            if not query:
                self.send_json(400, {'error': 'q parameter required'})
                return
            try:
                import semantic_search as ss
                client = ss.get_client()
                col = ss.get_collection(client)
                embedder = ss.get_embedder()
                result = ss.semantic_query(query, n_results=limit, embedder=embedder, collection=col)
                self.send_json(200, result)
            except ImportError:
                self.send_json(503, {
                    'error': 'Semantic search not available.',
                    'fix': 'pip install chromadb sentence-transformers',
                })
            except Exception as e:
                self.send_json(500, {'error': str(e)})

        elif parsed.path == '/browser-captures':
            limit = int(p('limit', 50))
            self.send_json(200, {
                'total': len(_browser_captures),
                'results': get_browser_captures(limit),
            })

        elif parsed.path == '/profile':
            days = int(p('days', 7))
            self.send_json(200, get_profile(days))

        elif parsed.path == '/context-card':
            days = int(p('days', 7))
            self.send_json(200, get_context_card(days))

        else:
            self.send_json(404, {'error': 'not found', 'endpoints': [
                '/health', '/context', '/summary', '/anomalies',
                '/semantic', '/browser-captures', '/profile', '/context-card',
                'POST /browser-capture', 'POST /grade',
            ]})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/browser-capture':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                entry = json.loads(body.decode())
                save_browser_capture(entry)

                # P1-E: upsert browser capture into semantic collection if available
                _try_load_semantic()
                if _semantic_collection is not None and _semantic_embedder is not None:
                    try:
                        cap = entry
                        doc = (
                            f"[browser] [{cap.get('domain', '')}] [{cap.get('title', '')}]\n"
                            f"{cap.get('selected_text', '')[:300]}"
                        )
                        ts  = cap.get('timestamp', '')
                        url = cap.get('url', '')
                        uid = f"browser_{abs(hash(url + ts)) % (10 ** 9)}"
                        emb = _semantic_embedder.encode([doc]).tolist()
                        _semantic_collection.upsert(
                            ids=[uid],
                            embeddings=emb,
                            documents=[doc],
                            metadatas=[{'source': 'browser', 'timestamp': ts}],
                        )
                    except Exception:
                        pass  # semantic upsert is best-effort

                self.send_json(200, {'ok': True, 'total': len(_browser_captures)})
            except Exception as e:
                self.send_json(400, {'error': str(e)})

        # P3-B: Cross-encoder grader endpoint
        elif parsed.path == '/grade':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                payload = json.loads(body.decode())
                query  = payload.get('query', '')
                chunks = [c for c in payload.get('chunks', []) if c.strip()]
                if not query or not chunks:
                    self.send_json(400, {'error': 'query and chunks required'})
                    return
                grader   = _get_grader()
                scores   = grader.predict([(query, chunk) for chunk in chunks[:10]])
                scores_f = [round(float(s), 3) for s in scores]
                relevant = any(s > 0.3 for s in scores_f)
                best     = max(scores_f) if scores_f else 0.0
                self.send_json(200, {'relevant': relevant, 'best_score': best, 'scores': scores_f})
            except ImportError:
                self.send_json(503, {
                    'error': 'sentence_transformers not available.',
                    'fix': 'pip install sentence-transformers',
                })
            except Exception as e:
                self.send_json(500, {'error': str(e)})

        else:
            self.send_json(404, {'error': 'not found'})


# ── Main ─────────────────────────────────────────────────────────────
def main():
    load_browser_captures()

    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │       Augur Context API v0.3.2       │")
    print("  │       localhost:3031                 │")
    print("  └─────────────────────────────────────┘")
    print()
    sp = screenpipe_ok()
    icon = '  v' if sp else '  x'
    print(f"{icon}  screenpipe: {'connected' if sp else 'NOT reachable (start screenpipe first)'}")
    print(f"  v  browser captures loaded: {len(_browser_captures)}")

    try:
        import chromadb  # noqa
        import sentence_transformers  # noqa
        print("  v  semantic search: available")
    except ImportError:
        print("  -  semantic search: not available (pip install chromadb sentence-transformers)")

    try:
        import rank_bm25  # noqa
        print("  v  BM25 hybrid retrieval: available")
    except ImportError:
        print("  -  BM25 hybrid retrieval: not available (pip install rank-bm25)")

    # Attempt to warm up semantic scoring
    _try_load_semantic()
    if _semantic_available:
        print("  v  semantic scoring: active (hybrid mode)")
    else:
        print("  -  semantic scoring: inactive (index empty or deps missing)")

    print()
    print("  Endpoints:")
    print("    GET  /health")
    print(f"    GET  /context?q=...&limit={DEFAULT_LIMIT}&window_hours=24")
    print("    GET  /summary?date=YYYY-MM-DD")
    print("    GET  /anomalies?days=7")
    print("    GET  /semantic?q=...&limit=25")
    print("    GET  /browser-captures?limit=50")
    print("    GET  /profile?days=7")
    print("    GET  /context-card?days=7")
    print("    POST /browser-capture  (JSON body)")
    print("    POST /grade  (JSON body: {query, chunks})")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    class ReuseHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReuseHTTPServer(('localhost', PORT), ContextHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Context API stopped.")
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
