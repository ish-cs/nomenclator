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

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────
SCREENPIPE_URL = "http://localhost:3030"
PORT = 3031
DEFAULT_LIMIT = 15
DEFAULT_WINDOW_HOURS = 24

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
        return {str(r['id']): r['score'] for r in result.get('results', [])}
    except Exception:
        return {}


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
def gather_context(query, limit, window_hours):
    keywords = extract_keywords(query)

    # Fetch recent captures
    recent_data = fetch_json(f"{SCREENPIPE_URL}/search?limit={limit * 2}") or {}
    recent_items = recent_data.get('data', [])

    # Fetch per-keyword search results
    kw_items = []
    for kw in keywords:
        q = urllib.parse.quote(kw)
        result = fetch_json(f"{SCREENPIPE_URL}/search?q={q}&limit=20") or {}
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

    # Attempt semantic bonus — transparent no-op if unavailable
    _try_load_semantic()
    semantic_scores = _get_semantic_scores(query, min(limit * 3, 60))
    semantic_active = bool(semantic_scores)

    # Score: keyword_matches * 3 + recency (+ semantic bonus for OCR/audio)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_ms = window_hours * 3600
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
            # Existing OCR/audio scoring
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
            # Semantic bonus: cosine similarity * 2.0 (range 0-2)
            uid_key = str(c.get('frame_id') or c.get('timestamp') or '')
            sem_bonus = semantic_scores.get(uid_key, 0.0) * 2.0
            item['_score'] = kw_score * 3 + recency + sem_bonus

    all_items.sort(key=lambda x: x.get('_score', 0), reverse=True)
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
            if not query:
                self.send_json(400, {'error': 'q parameter required'})
                return
            result = gather_context(query, limit, window_hours)
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
                'POST /browser-capture',
            ]})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/browser-capture':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                entry = json.loads(body.decode())
                save_browser_capture(entry)
                self.send_json(200, {'ok': True, 'total': len(_browser_captures)})
            except Exception as e:
                self.send_json(400, {'error': str(e)})
        else:
            self.send_json(404, {'error': 'not found'})


# ── Main ─────────────────────────────────────────────────────────────
def main():
    load_browser_captures()

    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │       Augur Context API v0.3         │")
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

    # Attempt to warm up semantic scoring
    _try_load_semantic()
    if _semantic_available:
        print("  v  semantic scoring: active (hybrid mode)")
    else:
        print("  -  semantic scoring: inactive (index empty or deps missing)")

    print()
    print("  Endpoints:")
    print("    GET  /health")
    print("    GET  /context?q=...&limit=15&window_hours=24")
    print("    GET  /summary?date=YYYY-MM-DD")
    print("    GET  /anomalies?days=7")
    print("    GET  /semantic?q=...&limit=15")
    print("    GET  /browser-captures?limit=50")
    print("    GET  /profile?days=7")
    print("    GET  /context-card?days=7")
    print("    POST /browser-capture  (JSON body)")
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
