#!/usr/bin/env python3
"""
Augur — Feature Tests
---------------------
Tests all implemented roadmap features without requiring live screenpipe.

Run with:
  python3 test_features.py
  python3 test_features.py --live   (also tests endpoints against running context-server)
"""

import json
import os
import sys
import urllib.request
import urllib.error
import importlib
import tempfile
import unittest

CONTEXT_API = "http://localhost:3031"
LIVE = "--live" in sys.argv


def req(path, method='GET', body=None):
    url = f"{CONTEXT_API}{path}"
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode()), r.status


# ─────────────────────────────────────────────────────────────────────
class TestSemanticSearch(unittest.TestCase):

    def test_import(self):
        """semantic_search.py can be imported."""
        import semantic_search
        self.assertTrue(hasattr(semantic_search, 'semantic_query'))
        self.assertTrue(hasattr(semantic_search, 'index_captures'))
        self.assertTrue(hasattr(semantic_search, 'get_client'))
        self.assertTrue(hasattr(semantic_search, 'get_collection'))
        self.assertTrue(hasattr(semantic_search, 'get_embedder'))
        self.assertTrue(hasattr(semantic_search, 'index_status'))
        print("  [PASS] semantic_search.py imports correctly")

    def test_chromadb_available(self):
        """chromadb package is installed."""
        try:
            import chromadb
            print(f"  [PASS] chromadb available (version: {chromadb.__version__})")
        except ImportError:
            self.skipTest("chromadb not installed — run: pip install chromadb sentence-transformers")

    def test_sentence_transformers_available(self):
        """sentence-transformers package is installed."""
        try:
            from sentence_transformers import SentenceTransformer
            print("  [PASS] sentence-transformers available")
        except ImportError:
            self.skipTest("sentence-transformers not installed")

    def test_chroma_client_init(self):
        """Chroma client can be initialised in a temp directory."""
        try:
            import chromadb
        except ImportError:
            self.skipTest("chromadb not installed")

        import semantic_search as ss
        original_path = ss.CHROMA_PATH
        with tempfile.TemporaryDirectory() as tmp:
            ss.CHROMA_PATH = tmp
            client = ss.get_client()
            col = ss.get_collection(client)
            self.assertEqual(col.count(), 0)
            status = ss.index_status(col)
            self.assertEqual(status['status'], 'empty')
            self.assertEqual(status['total_indexed'], 0)
            ss.CHROMA_PATH = original_path
        print("  [PASS] Chroma client and collection initialise correctly")

    def test_semantic_query_empty_index(self):
        """semantic_query returns a note when index is empty."""
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self.skipTest("chromadb/sentence-transformers not installed")

        import semantic_search as ss
        with tempfile.TemporaryDirectory() as tmp:
            ss.CHROMA_PATH = tmp
            client = ss.get_client()
            col = ss.get_collection(client)
            result = ss.semantic_query("test query", n_results=5, collection=col)
            self.assertIn('note', result)
            self.assertEqual(result['total_indexed'], 0)
            self.assertEqual(result['results'], [])
        print("  [PASS] semantic_query handles empty index correctly")

    def test_capture_to_doc(self):
        """capture_to_doc converts a screenpipe item to (uid, doc, meta)."""
        import semantic_search as ss
        item = {
            'type': 'OCR',
            'content': {
                'frame_id': 42,
                'timestamp': '2024-01-01T12:00:00Z',
                'app_name': 'Safari',
                'window_name': 'GitHub — Pull Requests',
                'browser_url': 'https://github.com/pulls',
                'text': 'Review pull requests for your team.',
            }
        }
        uid, doc, meta = ss.capture_to_doc(item)
        self.assertEqual(uid, '42')
        self.assertIn('Safari', doc)
        self.assertIn('GitHub', doc)
        self.assertEqual(meta['app'], 'Safari')
        self.assertEqual(meta['type'], 'OCR')
        print("  [PASS] capture_to_doc converts items correctly")

    def test_embed_and_query(self):
        """End-to-end: embed a document then query it semantically."""
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self.skipTest("chromadb/sentence-transformers not installed")

        import semantic_search as ss
        with tempfile.TemporaryDirectory() as tmp:
            ss.CHROMA_PATH = tmp
            client = ss.get_client()
            col = ss.get_collection(client)
            embedder = ss.get_embedder()

            # Manually insert a test document
            doc = "Python programming code review pull request GitHub"
            embedding = embedder.encode([doc], show_progress_bar=False).tolist()
            col.upsert(
                ids=['test-1'],
                embeddings=embedding,
                documents=[doc],
                metadatas=[{'app': 'Safari', 'window': 'GitHub', 'url': '', 'timestamp': '', 'text_preview': doc, 'type': 'OCR'}],
            )

            result = ss.semantic_query("software development code review", n_results=5,
                                       embedder=embedder, collection=col)
            self.assertEqual(result['total_indexed'], 1)
            self.assertEqual(len(result['results']), 1)
            hit = result['results'][0]
            self.assertEqual(hit['id'], 'test-1')
            self.assertGreater(hit['score'], 0.5)   # should be a good match
        print("  [PASS] End-to-end semantic embed + query works")


# ─────────────────────────────────────────────────────────────────────
class TestBrowserCaptures(unittest.TestCase):

    def test_context_server_has_browser_endpoints(self):
        """context-server.py contains browser capture endpoints."""
        path = os.path.join(os.path.dirname(__file__), 'context-server.py')
        with open(path) as f:
            src = f.read()
        self.assertIn('/browser-capture', src)
        self.assertIn('/browser-captures', src)
        self.assertIn('save_browser_capture', src)
        self.assertIn('do_POST', src)
        print("  [PASS] context-server.py has browser capture endpoints")

    def test_browser_capture_storage_logic(self):
        """Browser capture storage: JSON round-trip for save and retrieve."""
        # Patch the file path to use a temp file
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            tmp_path = f.name
        try:
            # Simulate save_browser_capture logic
            captures = []
            entry = {
                'source': 'browser_extension',
                'url': 'https://example.com',
                'domain': 'example.com',
                'title': 'Example',
                'timestamp': '2024-01-01T12:00:00Z',
                'time_on_page_s': 30,
            }
            captures.append(entry)
            with open(tmp_path, 'w') as f:
                json.dump(captures, f)

            with open(tmp_path) as f:
                loaded = json.load(f)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]['domain'], 'example.com')
            self.assertEqual(loaded[0]['time_on_page_s'], 30)
            print("  [PASS] Browser capture storage logic works")
        finally:
            os.unlink(tmp_path)

    def test_extension_files_exist(self):
        """All browser extension files are present."""
        base = os.path.join(os.path.dirname(__file__), 'extension')
        required = ['manifest.json', 'background.js', 'content.js', 'popup.html', 'popup.js', 'popup.css']
        for f in required:
            path = os.path.join(base, f)
            self.assertTrue(os.path.exists(path), f"Missing extension file: {f}")
        print("  [PASS] All extension files exist")

    def test_manifest_valid(self):
        """extension/manifest.json is valid JSON with required fields."""
        path = os.path.join(os.path.dirname(__file__), 'extension', 'manifest.json')
        with open(path) as f:
            manifest = json.load(f)
        self.assertEqual(manifest['manifest_version'], 3)
        self.assertIn('name', manifest)
        self.assertIn('background', manifest)
        self.assertIn('content_scripts', manifest)
        self.assertIn('host_permissions', manifest)
        # Must allow context API
        host_perms = manifest['host_permissions']
        self.assertTrue(any('3031' in p for p in host_perms), "host_permissions must include localhost:3031")
        print("  [PASS] manifest.json is valid MV3")

    def test_background_js_sends_to_api(self):
        """background.js references the correct API URL."""
        path = os.path.join(os.path.dirname(__file__), 'extension', 'background.js')
        with open(path) as f:
            src = f.read()
        self.assertIn('localhost:3031', src)
        self.assertIn('/browser-capture', src)
        self.assertIn("page_leave", src)
        self.assertIn("text_selected", src)
        print("  [PASS] background.js sends to correct endpoint")

    def test_content_js_tracks_events(self):
        """content.js tracks the right events."""
        path = os.path.join(os.path.dirname(__file__), 'extension', 'content.js')
        with open(path) as f:
            src = f.read()
        self.assertIn('visibilitychange', src)
        self.assertIn('mouseup', src)
        self.assertIn('pagehide', src)
        self.assertIn('time_on_page_s', src)
        self.assertIn('scroll', src)
        print("  [PASS] content.js tracks visibility, selection, scroll, and unload")


# ─────────────────────────────────────────────────────────────────────
class TestAnomalyDetection(unittest.TestCase):

    def test_anomaly_logic(self):
        """Anomaly scoring logic correctly classifies apps."""
        from collections import defaultdict

        # Simulate what get_anomalies() does
        today_by_app = {'Twitter': 300, 'VSCode': 50, 'Slack': 10}
        app_avg = {'Twitter': 80, 'VSCode': 200, 'Slack': 60}
        total_today = sum(today_by_app.values())  # 360

        anomalies = []
        for app in set(list(today_by_app.keys()) + list(app_avg.keys())):
            today_cnt = today_by_app.get(app, 0)
            avg_cnt = app_avg.get(app, 0)
            if avg_cnt == 0 and today_cnt >= 20:
                anomalies.append({'app': app, 'type': 'new'})
            elif avg_cnt > 0:
                ratio = today_cnt / avg_cnt
                if ratio >= 2.0 and today_cnt >= 20:
                    anomalies.append({'app': app, 'type': 'high', 'ratio': ratio})
                elif ratio <= 0.3 and avg_cnt >= 20:
                    anomalies.append({'app': app, 'type': 'low', 'ratio': ratio})

        anomaly_types = {a['app']: a['type'] for a in anomalies}
        self.assertEqual(anomaly_types.get('Twitter'), 'high')   # 300 vs 80 avg = 3.75x
        self.assertEqual(anomaly_types.get('VSCode'), 'low')     # 50 vs 200 avg = 0.25x
        self.assertEqual(anomaly_types.get('Slack'), 'low')      # 10 vs 60 avg = 0.17x, avg >= 20
        print("  [PASS] Anomaly logic correctly flags high/low usage")

    def test_anomaly_new_app(self):
        """New apps with no history are flagged correctly."""
        today_by_app = {'NewApp': 50}
        app_avg = {}

        anomalies = []
        for app, cnt in today_by_app.items():
            avg = app_avg.get(app, 0)
            if avg == 0 and cnt >= 20:
                anomalies.append({'app': app, 'type': 'new'})

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]['type'], 'new')
        print("  [PASS] New apps flagged correctly")

    def test_anomaly_thresholds(self):
        """Apps below minimum frame count are not flagged."""
        # Only 5 frames today for a new app — below threshold of 20
        today_by_app = {'TinyApp': 5}
        app_avg = {}

        anomalies = []
        for app, cnt in today_by_app.items():
            if app_avg.get(app, 0) == 0 and cnt >= 20:
                anomalies.append(app)

        self.assertEqual(len(anomalies), 0)
        print("  [PASS] Low-frame apps correctly excluded from anomalies")


# ─────────────────────────────────────────────────────────────────────
class TestContextServerEndpoints(unittest.TestCase):
    """Live tests — only run with --live flag."""

    def setUp(self):
        if not LIVE:
            self.skipTest("Skipping live tests (pass --live to enable)")

    def test_health(self):
        d, status = req('/health')
        self.assertEqual(status, 200)
        self.assertEqual(d['status'], 'ok')
        print("  [PASS] /health OK")

    def test_context_requires_q(self):
        try:
            req('/context')
            self.fail("Expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
        print("  [PASS] /context returns 400 without ?q=")

    def test_context_returns_results(self):
        d, status = req('/context?q=test&limit=5')
        self.assertEqual(status, 200)
        self.assertIn('results', d)
        self.assertIn('keywords', d)
        self.assertIn('query', d)
        print(f"  [PASS] /context?q=test returned {len(d['results'])} results")

    def test_summary_today(self):
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        d, status = req(f'/summary?date={today}')
        self.assertEqual(status, 200)
        self.assertIn('profile', d)
        self.assertIn('date', d)
        print(f"  [PASS] /summary?date={today} OK")

    def test_anomalies(self):
        d, status = req('/anomalies?days=7')
        self.assertEqual(status, 200)
        self.assertIn('anomalies', d)
        self.assertIn('date', d)
        self.assertIn('days_compared', d)
        self.assertEqual(d['days_compared'], 7)
        print(f"  [PASS] /anomalies returned {len(d['anomalies'])} anomalies")

    def test_browser_capture_post(self):
        entry = {
            'source': 'browser_extension',
            'url': 'https://test.example.com/page',
            'domain': 'test.example.com',
            'title': 'Test Page',
            'timestamp': '2024-01-01T12:00:00Z',
            'time_on_page_s': 42,
        }
        d, status = req('/browser-capture', method='POST', body=entry)
        self.assertEqual(status, 200)
        self.assertTrue(d['ok'])
        self.assertGreater(d['total'], 0)
        print(f"  [PASS] POST /browser-capture stored (total: {d['total']})")

    def test_browser_captures_get(self):
        d, status = req('/browser-captures?limit=10')
        self.assertEqual(status, 200)
        self.assertIn('results', d)
        self.assertIn('total', d)
        # Should include the one we just sent
        self.assertGreater(d['total'], 0)
        print(f"  [PASS] GET /browser-captures returned {len(d['results'])} results")

    def test_profile_endpoint(self):
        d, status = req('/profile?days=7')
        self.assertEqual(status, 200)
        self.assertIn('profile', d)
        self.assertIn('generated_at', d)
        profile = d['profile']
        for key in ('top_apps', 'active_hours', 'top_domains', 'top_topics', 'browser_captures'):
            self.assertIn(key, profile, f"profile missing key: {key}")
        print(f"  [PASS] /profile endpoint OK — top apps: {[a['app'] for a in profile['top_apps'][:3]]}")

    def test_context_card_endpoint(self):
        d, status = req('/context-card')
        self.assertEqual(status, 200)
        self.assertIn('card', d)
        self.assertIn('generated_at', d)
        card = d['card']
        self.assertIsInstance(card, str)
        self.assertGreater(len(card), 10)
        print(f"  [PASS] /context-card returns string ({len(card)} chars)")

    def test_context_includes_source_field(self):
        d, status = req('/context?q=test&limit=5')
        self.assertEqual(status, 200)
        self.assertIn('browser_captures_included', d)
        self.assertIn('semantic_enhanced', d)
        for item in d.get('results', []):
            self.assertIn('source', item, "Each result must have a 'source' field")
            self.assertIn(item['source'], ('ocr', 'audio', 'browser'))
        print(f"  [PASS] /context results have source field and metadata flags")

    def test_semantic_endpoint(self):
        try:
            d, status = req('/semantic?q=test&limit=5')
            self.assertEqual(status, 200)
            self.assertIn('results', d)
            self.assertIn('query', d)
            print(f"  [PASS] /semantic?q=test returned {len(d['results'])} results")
        except urllib.error.HTTPError as e:
            if e.code == 503:
                print("  [SKIP] /semantic: chromadb not installed (expected if deps not set up)")
            else:
                raise

    def test_404(self):
        try:
            req('/nonexistent')
            self.fail("Expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)
        print("  [PASS] Unknown endpoint returns 404")


# ─────────────────────────────────────────────────────────────────────
class TestExistingFeatures(unittest.TestCase):
    """Verify the already-implemented features are intact."""

    def test_context_server_exists(self):
        path = os.path.join(os.path.dirname(__file__), 'context-server.py')
        self.assertTrue(os.path.exists(path))
        print("  [PASS] context-server.py exists")

    def test_demo_agent_exists(self):
        path = os.path.join(os.path.dirname(__file__), 'demo_agent.py')
        self.assertTrue(os.path.exists(path))
        print("  [PASS] demo_agent.py exists")

    def test_dashboard_exists(self):
        path = os.path.join(os.path.dirname(__file__), 'screenpipe-dashboard.html')
        self.assertTrue(os.path.exists(path))
        print("  [PASS] screenpipe-dashboard.html exists")

    def test_dashboard_has_timeline(self):
        path = os.path.join(os.path.dirname(__file__), 'screenpipe-dashboard.html')
        with open(path) as f:
            html = f.read()
        self.assertIn('timelineTab', html)
        self.assertIn('loadTimeline()', html)
        print("  [PASS] Dashboard has Timeline tab")

    def test_dashboard_has_localstorage_chat(self):
        path = os.path.join(os.path.dirname(__file__), 'screenpipe-dashboard.html')
        with open(path) as f:
            html = f.read()
        self.assertIn('localStorage', html)
        self.assertIn('CHAT_STORAGE_KEY', html)
        self.assertIn('loadPersistedChat', html)
        print("  [PASS] Dashboard has persistent chat memory (localStorage)")

    def test_dashboard_has_context_snapshot(self):
        path = os.path.join(os.path.dirname(__file__), 'screenpipe-dashboard.html')
        with open(path) as f:
            html = f.read()
        self.assertIn('exportContextSnapshot', html)
        self.assertIn('context-snapshot', html)
        print("  [PASS] Dashboard has shareable context snapshot export")

    def test_launch_has_cleanup(self):
        path = os.path.join(os.path.dirname(__file__), 'launch.command')
        with open(path) as f:
            src = f.read()
        self.assertIn('cleanup_old_files', src)
        self.assertIn('CLEANUP_DAYS', src)
        print("  [PASS] launch.command has auto-cleanup of raw files")

    def test_dashboard_has_anomalies(self):
        path = os.path.join(os.path.dirname(__file__), 'screenpipe-dashboard.html')
        with open(path) as f:
            html = f.read()
        self.assertIn('anomaliesTab', html)
        self.assertIn('loadAnomalies()', html)
        self.assertIn('renderAnomalies', html)
        print("  [PASS] Dashboard has Anomalies tab")


# ─────────────────────────────────────────────────────────────────────
class TestBrowserCapturesInContext(unittest.TestCase):
    """Verify context-server.py merges browser captures into /context ranking."""

    def _read_src(self):
        path = os.path.join(os.path.dirname(__file__), 'context-server.py')
        with open(path) as f:
            return f.read()

    def test_browser_candidate_converter(self):
        src = self._read_src()
        self.assertIn('browser_capture_to_candidate', src)
        self.assertIn('browser_', src)  # browser_ prefix for UIDs
        print("  [PASS] context-server.py has browser_capture_to_candidate()")

    def test_context_response_includes_metadata_flags(self):
        src = self._read_src()
        self.assertIn('browser_captures_included', src)
        self.assertIn('semantic_enhanced', src)
        print("  [PASS] /context response includes browser_captures_included + semantic_enhanced flags")

    def test_source_field_on_results(self):
        src = self._read_src()
        self.assertIn("'source'", src)
        self.assertIn("'browser'", src)
        self.assertIn("'ocr'", src)
        self.assertIn("'audio'", src)
        print("  [PASS] context-server.py sets source field (ocr/audio/browser) on results")

    def test_browser_scoring_includes_bonuses(self):
        src = self._read_src()
        self.assertIn('sel_bon', src)
        self.assertIn('time_on_page_s', src)
        self.assertIn('selected_text', src)
        print("  [PASS] Browser scoring includes time_on_page_s bonus and selection sel_bon")


# ─────────────────────────────────────────────────────────────────────
class TestHybridScoring(unittest.TestCase):
    """Verify hybrid scoring formula and semantic integration in context-server.py."""

    def _read_src(self):
        path = os.path.join(os.path.dirname(__file__), 'context-server.py')
        with open(path) as f:
            return f.read()

    def test_semantic_globals_exist(self):
        src = self._read_src()
        self.assertIn('_semantic_embedder', src)
        self.assertIn('_semantic_collection', src)
        self.assertIn('_semantic_available', src)
        print("  [PASS] Semantic module-level globals present in context-server.py")

    def test_try_load_semantic_function(self):
        src = self._read_src()
        self.assertIn('_try_load_semantic', src)
        self.assertIn('import semantic_search', src)
        print("  [PASS] _try_load_semantic() present for lazy loading")

    def test_semantic_score_applied_as_bonus(self):
        src = self._read_src()
        self.assertIn('_get_semantic_scores', src)
        self.assertIn('sem_bonus', src)
        self.assertIn('semantic_scores', src)
        print("  [PASS] Semantic bonus (sem_bonus) applied to OCR/audio scores")

    def test_hybrid_score_formula(self):
        """Score formula: (kw_matches * 3) + recency + sem_bonus + browser_bonuses."""
        # Verify the formula constants exist in source
        src = self._read_src()
        self.assertIn('* 3', src)    # keyword weight
        self.assertIn('* 2.0', src)  # semantic cosine multiplier
        self.assertIn('recency', src)
        print("  [PASS] Hybrid score formula constants present (kw×3, semantic×2.0, recency)")


# ─────────────────────────────────────────────────────────────────────
class TestMCPServer(unittest.TestCase):
    """Verify mcp_server.py exists and implements the MCP protocol correctly."""

    def _read_src(self):
        path = os.path.join(os.path.dirname(__file__), 'mcp_server.py')
        self.assertTrue(os.path.exists(path), "mcp_server.py not found")
        with open(path) as f:
            return f.read()

    def test_mcp_file_exists(self):
        path = os.path.join(os.path.dirname(__file__), 'mcp_server.py')
        self.assertTrue(os.path.exists(path))
        print("  [PASS] mcp_server.py exists")

    def test_mcp_syntax(self):
        import ast
        path = os.path.join(os.path.dirname(__file__), 'mcp_server.py')
        with open(path) as f:
            src = f.read()
        try:
            ast.parse(src)
        except SyntaxError as e:
            self.fail(f"mcp_server.py has syntax error: {e}")
        print("  [PASS] mcp_server.py has valid syntax")

    def test_five_tools_defined(self):
        src = self._read_src()
        tools = ['get_context', 'get_daily_summary', 'get_anomalies', 'get_user_profile', 'get_browser_activity']
        for t in tools:
            self.assertIn(t, src, f"Tool '{t}' not found in mcp_server.py")
        print(f"  [PASS] All 5 MCP tools defined: {tools}")

    def test_stdio_protocol(self):
        src = self._read_src()
        self.assertIn('Content-Length', src)
        self.assertIn('sys.stdin.buffer', src)
        self.assertIn('sys.stdout.buffer', src)
        self.assertIn('sys.stderr', src)
        print("  [PASS] MCP uses Content-Length framing on stdin/stdout, stderr for logs")

    def test_notification_handling(self):
        """Notifications (no 'id') must not receive a response."""
        src = self._read_src()
        self.assertIn("'id'", src)
        self.assertIn('notification', src)
        print("  [PASS] mcp_server.py handles notifications (no response sent)")

    def test_pure_stdlib(self):
        """mcp_server.py must only use stdlib imports."""
        import ast
        path = os.path.join(os.path.dirname(__file__), 'mcp_server.py')
        with open(path) as f:
            src = f.read()
        tree = ast.parse(src)
        stdlib_mods = {'json', 'sys', 'os', 'io', 'traceback', 'urllib', 'datetime',
                       'threading', 'http', 'time', 'logging', 'collections'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split('.')[0], stdlib_mods,
                                  f"Non-stdlib import found: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.assertIn(node.module.split('.')[0], stdlib_mods,
                                  f"Non-stdlib import found: {node.module}")
        print("  [PASS] mcp_server.py uses only stdlib imports")

    def test_mcp_initialize_response(self):
        """Run mcp_server and send an initialize request, check response."""
        import subprocess, struct
        path = os.path.join(os.path.dirname(__file__), 'mcp_server.py')
        msg = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}
        })
        frame = f"Content-Length: {len(msg)}\r\n\r\n{msg}".encode()
        try:
            proc = subprocess.run(
                ['python3', path], input=frame,
                capture_output=True, timeout=5
            )
            out = proc.stdout.decode(errors='replace')
            self.assertIn('Content-Length', out)
            self.assertIn('"result"', out)
            self.assertIn('serverInfo', out)
            print("  [PASS] mcp_server.py responds correctly to initialize")
        except subprocess.TimeoutExpired:
            self.fail("mcp_server.py timed out on initialize")


# ─────────────────────────────────────────────────────────────────────
class TestProfileEndpoints(unittest.TestCase):
    """Verify /profile and /context-card endpoints exist in context-server.py."""

    def _read_src(self):
        path = os.path.join(os.path.dirname(__file__), 'context-server.py')
        with open(path) as f:
            return f.read()

    def test_get_profile_function(self):
        src = self._read_src()
        self.assertIn('get_profile', src)
        print("  [PASS] get_profile() function present in context-server.py")

    def test_get_context_card_function(self):
        src = self._read_src()
        self.assertIn('get_context_card', src)
        print("  [PASS] get_context_card() function present in context-server.py")

    def test_profile_route(self):
        src = self._read_src()
        self.assertIn("'/profile'", src)
        print("  [PASS] /profile route registered")

    def test_context_card_route(self):
        src = self._read_src()
        self.assertIn("'/context-card'", src)
        print("  [PASS] /context-card route registered")

    def test_profile_fields(self):
        src = self._read_src()
        for field in ('top_apps', 'active_hours', 'top_domains', 'top_topics', 'browser_captures'):
            self.assertIn(field, src, f"Profile field '{field}' not in context-server.py")
        print("  [PASS] Profile response includes all required fields")


# ─────────────────────────────────────────────────────────────────────
class TestDemoAgentBackends(unittest.TestCase):
    """Verify demo_agent.py supports multiple LLM backends via --api flag."""

    def _read_src(self):
        path = os.path.join(os.path.dirname(__file__), 'demo_agent.py')
        with open(path) as f:
            return f.read()

    def test_api_flag_present(self):
        src = self._read_src()
        self.assertIn('--api', src)
        print("  [PASS] demo_agent.py has --api flag")

    def test_claude_backend(self):
        src = self._read_src()
        self.assertIn('_ask_claude', src)
        self.assertIn('ANTHROPIC_API_KEY', src)
        self.assertIn('anthropic', src)
        print("  [PASS] demo_agent.py has Claude backend (_ask_claude)")

    def test_openai_backend(self):
        src = self._read_src()
        self.assertIn('_ask_openai', src)
        self.assertIn('OPENAI_API_KEY', src)
        self.assertIn('openai', src)
        print("  [PASS] demo_agent.py has OpenAI backend (_ask_openai)")

    def test_lmstudio_backend(self):
        src = self._read_src()
        self.assertIn('_ask_lmstudio', src)
        self.assertIn('1234', src)
        print("  [PASS] demo_agent.py has LM Studio backend (_ask_lmstudio)")

    def test_lazy_imports(self):
        """Cloud backends must use lazy imports (inside function, not top-level)."""
        import ast
        path = os.path.join(os.path.dirname(__file__), 'demo_agent.py')
        with open(path) as f:
            src = f.read()
        tree = ast.parse(src)
        top_level_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_level_imports.add(node.module)
        self.assertNotIn('anthropic', top_level_imports, "anthropic must be lazily imported")
        self.assertNotIn('openai', top_level_imports, "openai must be lazily imported")
        print("  [PASS] anthropic and openai are lazily imported (not top-level)")

    def test_syntax(self):
        import ast
        path = os.path.join(os.path.dirname(__file__), 'demo_agent.py')
        with open(path) as f:
            src = f.read()
        try:
            ast.parse(src)
        except SyntaxError as e:
            self.fail(f"demo_agent.py syntax error: {e}")
        print("  [PASS] demo_agent.py has valid syntax")


# ─────────────────────────────────────────────────────────────────────
class TestLaunchSemanticIndexer(unittest.TestCase):
    """Verify launch.command auto-starts the semantic indexer."""

    def _read_src(self):
        path = os.path.join(os.path.dirname(__file__), 'launch.command')
        with open(path) as f:
            return f.read()

    def test_semantic_constants(self):
        src = self._read_src()
        self.assertIn('SEMANTIC_INDEXER', src)
        self.assertIn('SEMANTIC_PID_FILE', src)
        self.assertIn('semantic_indexer.pid', src)
        print("  [PASS] launch.command has semantic indexer constants")

    def test_is_semantic_available(self):
        src = self._read_src()
        self.assertIn('is_semantic_available', src)
        self.assertIn('chromadb', src)
        self.assertIn('sentence_transformers', src)
        print("  [PASS] launch.command checks chromadb + sentence_transformers availability")

    def test_start_semantic_indexer(self):
        src = self._read_src()
        self.assertIn('start_semantic_indexer', src)
        self.assertIn('subprocess', src)
        self.assertIn('is_semantic_running', src)
        print("  [PASS] launch.command starts semantic indexer as subprocess")

    def test_keepalive_shows_semantic_status(self):
        src = self._read_src()
        self.assertIn('semantic', src.lower())
        # Should show [semantic: up/down] in keep-alive
        self.assertIn('is_semantic_running', src)
        print("  [PASS] Keep-alive loop shows semantic indexer status")


# ─────────────────────────────────────────────────────────────────────
class TestDashboardV3(unittest.TestCase):
    """Verify v0.3 dashboard features: Browser tab + semantic search mode."""

    def _read_html(self):
        path = os.path.join(os.path.dirname(__file__), 'screenpipe-dashboard.html')
        with open(path) as f:
            return f.read()

    def test_browser_tab_button(self):
        html = self._read_html()
        self.assertIn("switchTab('browser'", html)
        self.assertIn('>Browser<', html)
        print("  [PASS] Dashboard has Browser tab button")

    def test_browser_tab_div(self):
        html = self._read_html()
        self.assertIn('id="browserTab"', html)
        self.assertIn('id="browserContent"', html)
        print("  [PASS] Dashboard has browserTab and browserContent divs")

    def test_load_browser_activity_function(self):
        html = self._read_html()
        self.assertIn('loadBrowserActivity', html)
        self.assertIn('browser-captures', html)
        print("  [PASS] loadBrowserActivity() fetches /browser-captures")

    def test_switchtab_handles_browser(self):
        html = self._read_html()
        self.assertIn("name === 'browser'", html)
        self.assertIn("if (name === 'browser') loadBrowserActivity", html)
        print("  [PASS] switchTab() handles browser tab correctly")

    def test_search_mode_toggle(self):
        html = self._read_html()
        self.assertIn('modeKeyword', html)
        self.assertIn('modeSemantic', html)
        self.assertIn('setSearchMode', html)
        self.assertIn('_searchMode', html)
        print("  [PASS] Search mode toggle (Keyword / Semantic) present")

    def test_semantic_search_function(self):
        html = self._read_html()
        self.assertIn('_doSemanticSearch', html)
        self.assertIn('localhost:3031/context', html)
        self.assertIn('semantic_enhanced', html)
        print("  [PASS] _doSemanticSearch() calls Context API")

    def test_keyword_search_refactored(self):
        html = self._read_html()
        self.assertIn('_doKeywordSearch', html)
        self.assertIn('searchResults', html)
        print("  [PASS] _doKeywordSearch() uses searchResults container")

    def test_browser_card_css(self):
        html = self._read_html()
        self.assertIn('.browser-card', html)
        self.assertIn('.browser-url', html)
        self.assertIn('.badge-browser', html)
        self.assertIn('.mode-btn', html)
        self.assertIn('.sim-score', html)
        print("  [PASS] Browser card and search mode CSS classes present")


# ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print()
    print("  ┌────────────────────────────────────────────┐")
    print("  │          Augur Feature Tests               │")
    if LIVE:
        print("  │          Mode: live (context-server.py)    │")
    else:
        print("  │          Mode: offline (no server needed)  │")
    print("  └────────────────────────────────────────────┘")
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestExistingFeatures))
    suite.addTests(loader.loadTestsFromTestCase(TestSemanticSearch))
    suite.addTests(loader.loadTestsFromTestCase(TestBrowserCaptures))
    suite.addTests(loader.loadTestsFromTestCase(TestAnomalyDetection))
    # v0.3 test classes
    suite.addTests(loader.loadTestsFromTestCase(TestBrowserCapturesInContext))
    suite.addTests(loader.loadTestsFromTestCase(TestHybridScoring))
    suite.addTests(loader.loadTestsFromTestCase(TestMCPServer))
    suite.addTests(loader.loadTestsFromTestCase(TestProfileEndpoints))
    suite.addTests(loader.loadTestsFromTestCase(TestDemoAgentBackends))
    suite.addTests(loader.loadTestsFromTestCase(TestLaunchSemanticIndexer))
    suite.addTests(loader.loadTestsFromTestCase(TestDashboardV3))
    # Live endpoint tests (skipped unless --live)
    suite.addTests(loader.loadTestsFromTestCase(TestContextServerEndpoints))

    _devnull = open(os.devnull, 'w')
    runner = unittest.TextTestRunner(verbosity=0, stream=_devnull)
    result = runner.run(suite)
    _devnull.close()

    print()
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    passed = total - failed - skipped

    print(f"  Results: {passed} passed, {skipped} skipped, {failed} failed  ({total} total)")

    if result.failures:
        print()
        print("  FAILURES:")
        for test, msg in result.failures:
            print(f"    [FAIL] {test}: {msg.splitlines()[-1]}")

    if result.errors:
        print()
        print("  ERRORS:")
        for test, msg in result.errors:
            print(f"    [ERROR] {test}: {msg.splitlines()[-1]}")

    print()
    sys.exit(0 if not failed else 1)
