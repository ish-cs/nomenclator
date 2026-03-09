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
import re
import sys
import time
import unicodedata
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SCREENPIPE_URL = "http://localhost:3030"
CHROMA_PATH = os.path.expanduser("~/.screenpipe/augur_semantic_db")
COLLECTION_NAME = "augur_captures"
INDEX_INTERVAL = 300   # seconds between indexing passes
BATCH_SIZE = 50        # captures per embedding batch
MODEL_NAME = "BAAI/bge-small-en-v1.5"
# NOTE: changing this model requires deleting ~/.screenpipe/augur_semantic_db/ and rebuilding

# P2-C: sentinel file marks that full historical index has been run
FULL_INDEX_SENTINEL = os.path.expanduser("~/.screenpipe/augur_full_indexed")


# ── Helpers ─────────────────────────────────────────────────────────
def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# ── P2-A: OCR text cleaning ──────────────────────────────────────────
def clean_ocr_text(text: str) -> str:
    """Strip non-printable chars, decoration runs, repeated chars, split-word artifacts."""
    if not text:
        return ''
    # Strip non-printable / control characters (keep newlines and tabs)
    text = ''.join(
        ch for ch in text
        if unicodedata.category(ch)[0] != 'C' or ch in ('\n', '\t', ' ')
    )
    # Remove runs of 3+ identical non-alphanumeric characters (decoration runs)
    text = re.sub(r'([^a-zA-Z0-9\s])\1{2,}', '', text)
    # Remove lines that are purely non-word characters (separators, borders)
    lines = text.splitlines()
    lines = [ln for ln in lines if re.search(r'[a-zA-Z0-9]', ln)]
    text = ' '.join(lines)
    # Collapse multiple spaces
    text = re.sub(r' {2,}', ' ', text).strip()
    return text


# ── P2-A: Build structured embedding document ───────────────────────
def build_index_document(item: dict) -> str:
    """Prepend structured metadata header before embedding text."""
    c        = item.get('content', {}) if 'content' in item else item
    app      = (c.get('app_name')    or '')[:60]
    window   = (c.get('window_name') or '')[:80]
    url      = (c.get('browser_url') or '')[:120]
    text     = clean_ocr_text(c.get('text') or c.get('transcription') or '')[:500]
    ts       = (c.get('timestamp')   or '')[:16]   # "2024-03-08T14:32"
    src_type = item.get('type', 'OCR')

    header = f"[{ts}] [{src_type}] [{app}]"
    if window and window != app:
        header += f" [{window}]"
    if url:
        header += f" [{url[:80]}]"
    return f"{header}\n{text}"


# ── P2-D: Near-duplicate deduplication ──────────────────────────────
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


# ── Chroma + embedder setup ─────────────────────────────────────────
def get_client():
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_PATH)


def get_collection(client):
    # P2-B: HNSW parameter tuning for better recall
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "hnsw:space":           "cosine",
            "hnsw:M":               16,
            "hnsw:construction_ef": 200,
            "hnsw:search_ef":       100,
        },
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

    # P2-A: use structured document format
    doc = build_index_document(item)
    meta = {
        'type': item.get('type', ''),
        'app': app[:100],
        'window': window[:200],
        'url': url[:500],
        'timestamp': ts,
        'text_preview': text[:300],
    }
    return uid, doc, meta


def _get_uid(item):
    """Extract the ID used for dedup checks."""
    c = item.get('content', {})
    return str(c.get('frame_id') or c.get('timestamp') or '')


def _embed_and_upsert(collection, embedder, items):
    """Convert items to docs and upsert into collection in batches."""
    ids, docs, metas = [], [], []
    for item in items:
        uid, doc, meta = capture_to_doc(item)
        if doc.strip() and len(doc) > 5:
            ids.append(uid)
            docs.append(doc)
            metas.append(meta)

    if not ids:
        return 0

    for i in range(0, len(ids), BATCH_SIZE):
        b_ids   = ids[i:i + BATCH_SIZE]
        b_docs  = docs[i:i + BATCH_SIZE]
        b_metas = metas[i:i + BATCH_SIZE]
        embeddings = embedder.encode(b_docs, show_progress_bar=False).tolist()
        collection.upsert(ids=b_ids, embeddings=embeddings, documents=b_docs, metadatas=b_metas)

    return len(ids)


# ── P2-C: Full historical indexing ──────────────────────────────────
def run_full_index(collection, embedder):
    """Index all screenpipe history. Skips on subsequent runs via sentinel file."""
    if os.path.exists(FULL_INDEX_SENTINEL):
        return  # already ran full index once

    print("  [full-index] Starting full historical index...")
    try:
        existing_ids = set(collection.get(include=[])['ids'])
    except Exception:
        existing_ids = set()

    offset, batch, total = 0, 200, 0

    while True:
        data  = fetch_json(f"{SCREENPIPE_URL}/search?limit={batch}&offset={offset}") or {}
        items = data.get('data', [])
        if not items:
            break
        new_items = [i for i in items if _get_uid(i) not in existing_ids]
        if new_items:
            new_items = dedup_near_duplicate(new_items)
            n = _embed_and_upsert(collection, embedder, new_items)
            total += n
            for i in new_items:
                existing_ids.add(_get_uid(i))
        print(f"  [full-index] offset={offset} items={len(items)} new={len(new_items)} total_indexed={total}")
        if len(items) < batch:
            break
        offset += batch

    with open(FULL_INDEX_SENTINEL, 'w') as f:
        f.write(str(total))
    print(f"  [full-index] Done. Indexed {total} historical captures.")


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

    # P2-D: deduplicate near-duplicates before indexing
    new_items = dedup_near_duplicate(new_items)

    return _embed_and_upsert(collection, embedder, new_items)


# ── Querying ─────────────────────────────────────────────────────────
def semantic_query(query_text, n_results=15, embedder=None, collection=None):
    """
    Run a semantic similarity search against the indexed captures.
    Returns a dict with 'query', 'total_indexed', 'results'.
    """
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

    if embedder is None:
        embedder = get_embedder()

    # BGE models require a query prefix for retrieval tasks (documents do not need it)
    bge_query = f"Represent this sentence: {query_text}"
    query_embedding = embedder.encode([bge_query], show_progress_bar=False).tolist()
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
        # P2-C: run full historical index first if not done
        run_full_index(collection, embedder)
        n = index_captures(embedder, collection)
        total = collection.count()
        print(f"  Done. Indexed {n} new captures. Total in store: {total}")
        return

    # Default: daemon
    # P2-C: run full historical index once before entering incremental loop
    run_full_index(collection, embedder)
    run_daemon(embedder, collection)


if __name__ == '__main__':
    main()
