#!/usr/bin/env python3
"""
Augur Semantic Search
---------------------
Indexes screenpipe captures into a local Chroma vector store.
Provides meaning-based search beyond exact keyword matching.

Usage:
  python semantic_search.py                    # run indexer daemon (indexes every 5 min)
  python semantic_search.py --index            # index once and exit
  python semantic_search.py --query "text"     # run a semantic query
  python semantic_search.py --status           # show index status

Requires:
  pip install chromadb sentence-transformers
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SCREENPIPE_URL = "http://localhost:3030"
CHROMA_PATH = os.path.expanduser("~/.screenpipe/augur_semantic_db")
COLLECTION_NAME = "augur_captures"
INDEX_INTERVAL = 300   # seconds between indexing passes
BATCH_SIZE = 50        # captures per embedding batch
MODEL_NAME = "all-MiniLM-L6-v2"


# ── Helpers ─────────────────────────────────────────────────────────
def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# ── Chroma + embedder setup ─────────────────────────────────────────
def get_client():
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_PATH)


def get_collection(client):
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def get_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


# ── Capture → document conversion ───────────────────────────────────
def capture_to_doc(item):
    c = item.get('content', {})
    is_ocr = item.get('type') == 'OCR'
    text = (c.get('text') if is_ocr else c.get('transcription')) or ''
    app = c.get('app_name', '') or ''
    window = c.get('window_name', '') or ''
    url = c.get('browser_url', '') or ''
    ts = c.get('timestamp', '') or ''
    uid = str(c.get('frame_id') or ts or id(item))

    # Build document string: context fields + text
    doc = ' | '.join(filter(None, [app, window[:100], url[:200], text[:400]]))
    meta = {
        'type': item.get('type', ''),
        'app': app[:100],
        'window': window[:200],
        'url': url[:500],
        'timestamp': ts,
        'text_preview': text[:300],
    }
    return uid, doc, meta


# ── Indexing ─────────────────────────────────────────────────────────
def fetch_captures(limit=200):
    result = fetch_json(f"{SCREENPIPE_URL}/search?limit={limit}") or {}
    return result.get('data', [])


def index_captures(embedder, collection, limit=200):
    items = fetch_captures(limit)
    if not items:
        return 0

    # Get existing IDs to skip re-indexing
    try:
        existing = set(collection.get(include=[])['ids'])
    except Exception:
        existing = set()

    new_items = []
    for item in items:
        c = item.get('content', {})
        uid = str(c.get('frame_id') or c.get('timestamp') or '')
        if uid and uid not in existing:
            new_items.append(item)

    if not new_items:
        return 0

    ids, docs, metas = [], [], []
    for item in new_items:
        uid, doc, meta = capture_to_doc(item)
        if doc.strip() and len(doc) > 5:
            ids.append(uid)
            docs.append(doc)
            metas.append(meta)

    if not ids:
        return 0

    # Batch embed + upsert
    for i in range(0, len(ids), BATCH_SIZE):
        b_ids = ids[i:i + BATCH_SIZE]
        b_docs = docs[i:i + BATCH_SIZE]
        b_metas = metas[i:i + BATCH_SIZE]
        embeddings = embedder.encode(b_docs, show_progress_bar=False).tolist()
        collection.upsert(ids=b_ids, embeddings=embeddings, documents=b_docs, metadatas=b_metas)

    return len(ids)


# ── Querying ─────────────────────────────────────────────────────────
def semantic_query(query_text, n_results=15, embedder=None, collection=None):
    """
    Run a semantic similarity search against the indexed captures.
    Returns a dict with 'query', 'total_indexed', 'results'.
    """
    if embedder is None:
        embedder = get_embedder()
    if collection is None:
        client = get_client()
        collection = get_collection(client)

    total = collection.count()
    if total == 0:
        return {
            'query': query_text,
            'total_indexed': 0,
            'results': [],
            'note': 'Index is empty. Run: python semantic_search.py --index',
        }

    query_embedding = embedder.encode([query_text], show_progress_bar=False).tolist()
    n = min(n_results, total)

    try:
        raw = collection.query(
            query_embeddings=query_embedding,
            n_results=n,
            include=['documents', 'metadatas', 'distances'],
        )
    except Exception as e:
        return {'query': query_text, 'error': str(e), 'results': []}

    hits = []
    for uid, meta, dist in zip(
        raw.get('ids', [[]])[0],
        raw.get('metadatas', [[]])[0],
        raw.get('distances', [[]])[0],
    ):
        hits.append({
            'id': uid,
            'timestamp': meta.get('timestamp'),
            'app': meta.get('app'),
            'window': meta.get('window') or None,
            'url': meta.get('url') or None,
            'text': meta.get('text_preview'),
            'score': round(1.0 - dist, 4),  # cosine similarity (higher = better)
        })

    return {
        'query': query_text,
        'total_indexed': total,
        'results': hits,
    }


def index_status(collection):
    count = collection.count()
    return {
        'status': 'ready' if count > 0 else 'empty',
        'total_indexed': count,
        'db_path': CHROMA_PATH,
        'model': MODEL_NAME,
    }


# ── Daemon ───────────────────────────────────────────────────────────
def run_daemon(embedder, collection):
    print(f"  Semantic indexer running — indexing every {INDEX_INTERVAL}s")
    print(f"  DB: {CHROMA_PATH}")
    print()
    while True:
        ts = datetime.now().strftime('%H:%M:%S')
        try:
            n = index_captures(embedder, collection)
            total = collection.count()
            print(f"  [{ts}] Indexed {n} new captures. Total: {total}")
        except Exception as e:
            print(f"  [{ts}] Error: {e}")
        try:
            time.sleep(INDEX_INTERVAL)
        except KeyboardInterrupt:
            print("\n  Indexer stopped.")
            break


# ── Main ─────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │     Augur Semantic Search v0.1       │")
    print("  └─────────────────────────────────────┘")
    print()

    try:
        client = get_client()
        collection = get_collection(client)
    except ImportError:
        print("  chromadb not installed. Run:")
        print("    pip install chromadb sentence-transformers")
        sys.exit(1)

    if '--status' in args:
        print(json.dumps(index_status(collection), indent=2))
        return

    try:
        embedder = get_embedder()
    except ImportError:
        print("  sentence-transformers not installed. Run:")
        print("    pip install chromadb sentence-transformers")
        sys.exit(1)
    except Exception as e:
        print(f"  Failed to load embedder: {e}")
        sys.exit(1)

    if '--query' in args:
        idx = args.index('--query')
        if idx + 1 >= len(args):
            print("  Usage: python semantic_search.py --query 'your text here'")
            sys.exit(1)
        query_text = ' '.join(args[idx + 1:])
        result = semantic_query(query_text, embedder=embedder, collection=collection)
        print(json.dumps(result, indent=2))
        return

    if '--index' in args:
        print("  Indexing captures (one pass)...")
        n = index_captures(embedder, collection)
        total = collection.count()
        print(f"  Done. Indexed {n} new captures. Total in store: {total}")
        return

    # Default: daemon
    run_daemon(embedder, collection)


if __name__ == '__main__':
    main()
