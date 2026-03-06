#!/usr/bin/env python3
"""
Augur Context API Server
------------------------
Runs on localhost:3031. Provides context to AI agents from screenpipe data.

Endpoints:
  GET /health                        — status check
  GET /context?q=...&limit=N&window_hours=H  — ranked context for a query
  GET /summary?date=YYYY-MM-DD       — structured daily summary

Usage: python context-server.py
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────
SCREENPIPE_URL = "http://localhost:3030"
PORT = 3031
DEFAULT_LIMIT = 15
DEFAULT_WINDOW_HOURS = 24

STOP_WORDS = frozenset([
    'what','have','i','been','the','a','an','is','are','was','were',
    'do','did','can','tell','me','all','about','on','in','at','to',
    'for','of','and','or','my','your','any','some','this','that',
    'how','when','where','who','why','which','with','from','by',
    'as','it','its','be','has','had','will','would','could','should',
    'just','get','got','show','find','give','let','now','up','down',
    'not','no','yes','but','so','if','then','than','too','very',
])


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

    # Score: keyword_matches * 3 + recency
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_ms = window_hours * 3600
    for item in all_items:
        c = item.get('content', {})
        blob = ' '.join([
            c.get('text', '') or '',
            c.get('transcription', '') or '',
            c.get('app_name', '') or '',
            c.get('window_name', '') or '',
        ]).lower()
        kw_score = sum(
            min(blob.count(kw), 5) for kw in keywords
        )
        try:
            ts = datetime.fromisoformat(c.get('timestamp', '').replace('Z', '+00:00').replace('+00:00', ''))
            age_s = (now - ts).total_seconds()
        except Exception:
            age_s = window_ms
        recency = max(0.0, 1.0 - age_s / window_ms) if window_ms > 0 else 0.0
        item['_score'] = kw_score * 3 + recency

    all_items.sort(key=lambda x: x.get('_score', 0), reverse=True)
    top = all_items[:limit]

    results = []
    for item in top:
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
        })

    return {
        'query': query,
        'keywords': keywords,
        'total_candidates': len(all_items),
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

        else:
            self.send_json(404, {'error': 'not found', 'endpoints': ['/health', '/context', '/summary']})


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │       Augur Context API v0.1         │")
    print("  │       localhost:3031                 │")
    print("  └─────────────────────────────────────┘")
    print()
    sp = screenpipe_ok()
    icon = '  v' if sp else '  x'
    print(f"{icon}  screenpipe: {'connected' if sp else 'NOT reachable (start screenpipe first)'}")
    print()
    print("  Endpoints:")
    print("    GET /health")
    print("    GET /context?q=...&limit=15&window_hours=24")
    print("    GET /summary?date=YYYY-MM-DD")
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
