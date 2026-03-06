#!/usr/bin/env python3
"""
Augur Demo Agent
----------------
Proves the Context API works end-to-end.

Usage:
  python demo_agent.py "what have I been working on today?"
  python demo_agent.py "question" --api claude
  python demo_agent.py "question" --api openai
  python demo_agent.py --watch          # continuous mode, polls every 30s
  python demo_agent.py --watch --api claude
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────
CONTEXT_API  = "http://localhost:3031"
LM_STUDIO    = "http://localhost:1234"
CONTEXT_LIMIT = 15
WATCH_INTERVAL = 30  # seconds


# ── Helpers ─────────────────────────────────────────────────────────
def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        print(f"  [error] request failed: {e}")
        return None
    except Exception as e:
        print(f"  [error] {e}")
        return None


def post_json(url, payload):
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        print(f"  [error] LM Studio request failed: {e}")
        print("  Make sure LM Studio is running with the local server enabled.")
        return None
    except Exception as e:
        print(f"  [error] {e}")
        return None


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


def get_context(question):
    q = urllib.parse.quote(question)
    url = f"{CONTEXT_API}/context?q={q}&limit={CONTEXT_LIMIT}"
    result = fetch_json(url)
    if not result:
        return None, []
    return result, result.get('results', [])


def format_context_block(results):
    if not results:
        return "(no relevant screen captures found)"
    lines = []
    for r in results:
        ts = r.get('timestamp', '')
        try:
            t = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', ''))
            time_str = t.strftime('%H:%M')
        except Exception:
            time_str = ts[:16]
        app = r.get('app') or 'audio'
        window = r.get('window') or ''
        text = (r.get('text') or '').strip()[:200]
        lines.append(f"[{time_str}] [{app}{' / ' + window[:40] if window else ''}]\n{text}")
    return '\n\n---\n\n'.join(lines)


def ask_llm(question, context_block, model=None, backend='lmstudio'):
    """Dispatch to the selected LLM backend."""
    if backend == 'claude':
        return _ask_claude(question, context_block)
    elif backend in ('openai', 'gpt'):
        return _ask_openai(question, context_block)
    else:
        return _ask_lmstudio(question, context_block, model)


def _ask_lmstudio(question, context_block, model=None):
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


def run_single_query(question, model=None, backend='lmstudio'):
    print(f"  Query: {question}")
    print()

    print("  Fetching context from Augur...")
    ctx_data, results = get_context(question)
    if ctx_data is None:
        return

    keywords = ctx_data.get('keywords', [])
    total = ctx_data.get('total_candidates', 0)
    print(f"  Keywords: {keywords}")
    print(f"  Candidates: {total}  |  Using top {len(results)}")
    print()

    context_block = format_context_block(results)

    print("  Asking LM Studio...")
    print()
    answer = ask_llm(question, context_block, model, backend=backend)
    if answer:
        print("  " + "─" * 60)
        print()
        # Wrap and indent the answer
        for line in answer.strip().splitlines():
            print(f"  {line}")
        print()
        print("  " + "─" * 60)
    else:
        print("  [!] No answer received from LM Studio.")


def run_watch_mode(model=None, backend='lmstudio'):
    print("  Watch mode — polling every 30s. Press Ctrl+C to stop.")
    print()
    last_snapshot = None

    while True:
        now = datetime.now().strftime('%H:%M:%S')
        print(f"  [{now}] Checking for new activity...")

        ctx_data, results = get_context("what have I been working on")
        if results:
            # Build a fingerprint from top 5 frame IDs
            snapshot = tuple(r.get('frame_id') or r.get('timestamp') for r in results[:5])
            if snapshot != last_snapshot:
                if last_snapshot is not None:
                    print("  [!] Activity changed — summarizing...")
                    context_block = format_context_block(results)
                    answer = ask_llm("Briefly, what is the user currently doing based on recent screen captures?", context_block, model, backend=backend)
                    if answer:
                        print()
                        for line in answer.strip().splitlines():
                            print(f"  {line}")
                        print()
                last_snapshot = snapshot
            else:
                print("  No change detected.")
        else:
            print("  No context returned.")

        try:
            time.sleep(WATCH_INTERVAL)
        except KeyboardInterrupt:
            print("\n  Watch mode stopped.")
            break


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │         Augur Demo Agent             │")
    print("  └─────────────────────────────────────┘")
    print()

    args = sys.argv[1:]

    if not args:
        print("  Usage:")
        print('    python demo_agent.py "what have I been working on?"')
        print('    python demo_agent.py "question" --api claude')
        print('    python demo_agent.py "question" --api openai')
        print("    python demo_agent.py --watch")
        print("    python demo_agent.py --watch --api claude")
        print()
        sys.exit(0)

    # Parse --api flag
    api_backend = 'lmstudio'
    if '--api' in args:
        idx = args.index('--api')
        if idx + 1 < len(args):
            api_backend = args[idx + 1].lower()
            args = args[:idx] + args[idx + 2:]
        else:
            print("  [!] --api requires a value: claude, openai, or lmstudio")
            sys.exit(1)

    result = check_services(backend=api_backend)
    if result is False:
        sys.exit(1)
    _, model = result if isinstance(result, tuple) else (True, None)

    if args and args[0] == '--watch':
        run_watch_mode(model, backend=api_backend)
    else:
        question = ' '.join(args)
        run_single_query(question, model, backend=api_backend)


if __name__ == '__main__':
    main()
