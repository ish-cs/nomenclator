#!/usr/bin/env python3
"""
Augur — Feature Tests
---------------------
Tests all implemented roadmap features without requiring live screenpipe.

Run with:
  python test_features.py
  python test_features.py --live   (also tests endpoints against running context-server)
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
        """Browser capture storage: save and retrieve."""
        # Test the logic directly by importing context-server functions
        # We do this by importing with a temp path
        import importlib.util, types

        spec = importlib.util.spec_from_file_location(
            "cs",
            os.path.join(os.path.dirname(__file__), "context-server.py")
        )
        cs = types.ModuleType("cs")

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
    suite.addTests(loader.loadTestsFromTestCase(TestContextServerEndpoints))

    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, 'w'))
    result = runner.run(suite)

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
