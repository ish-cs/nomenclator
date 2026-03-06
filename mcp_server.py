#!/usr/bin/env python3
"""
Augur MCP Server
----------------
Exposes Augur context tools via the Model Context Protocol (MCP).
Works with Claude Desktop, Cursor, Zed, and any MCP-compatible AI client.

Setup — Claude Desktop (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "augur": {
        "command": "python3",
        "args": ["/absolute/path/to/mcp_server.py"]
      }
    }
  }

Requires context-server.py running on localhost:3031.
No additional dependencies — pure Python stdlib.
"""

import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime

CONTEXT_API = "http://localhost:3031"


# ── I/O layer ────────────────────────────────────────────────────────

def _read_message():
    """Read one JSON-RPC message from stdin (Content-Length framed)."""
    headers = {}
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw or raw in (b'\r\n', b'\n'):
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
    """Write one JSON-RPC message to stdout (Content-Length framed)."""
    body   = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    header = f'Content-Length: {len(body)}\r\n\r\n'.encode('utf-8')
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def _log(msg):
    """Log to stderr — stdout is reserved for the MCP protocol."""
    print(f"[augur-mcp] {msg}", file=sys.stderr, flush=True)


# ── Context API call ─────────────────────────────────────────────────

def _call_api(path):
    """Call the local Context API. Returns parsed JSON or None on failure."""
    try:
        with urllib.request.urlopen(f"{CONTEXT_API}{path}", timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        _log(f"API error {path}: {e}")
        return None


# ── Tool definitions ─────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_context",
        "description": (
            "Get ranked, relevant screen captures for a natural language query. "
            "Returns what the user has been doing, reading, or working on based on "
            "passive screen capture data. Use this to understand the user's context "
            "before responding. Includes OCR captures, audio transcriptions, and "
            "browser activity."
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
            "Includes top apps used, peak active hours, unique URLs visited, "
            "audio transcription count, and key topic keywords extracted from OCR."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format. Defaults to today if omitted."
                }
            }
        }
    },
    {
        "name": "get_anomalies",
        "description": (
            "Detect behavioral anomalies — apps used significantly more or less than usual, "
            "or entirely new apps appearing for the first time. Compares today's activity "
            "against a rolling N-day baseline."
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
            "Get a behavioral profile of the user over the last N days. "
            "Includes top apps with hours, peak active hours heatmap, most visited domains, "
            "and topic keywords from OCR text. Use this to personalize responses without "
            "asking the user to describe themselves."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Days of history to include (default: 7)",
                    "default": 7
                }
            }
        }
    },
    {
        "name": "get_browser_activity",
        "description": (
            "Get recent browser captures: URLs visited, time spent on each page, "
            "scroll depth, and any text the user explicitly selected. "
            "Requires the Augur Chrome extension to be installed."
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


# ── Tool handlers ────────────────────────────────────────────────────

def _tool_get_context(args):
    query        = args.get('query', '')
    limit        = int(args.get('limit', 10))
    window_hours = int(args.get('window_hours', 24))
    if not query:
        return "Error: 'query' argument is required."

    q    = urllib.parse.quote(query)
    data = _call_api(f"/context?q={q}&limit={limit}&window_hours={window_hours}")
    if data is None:
        return (
            "Could not reach Context API at localhost:3031. "
            "Make sure context-server.py is running: python3 context-server.py"
        )

    results = data.get('results', [])
    if not results:
        return f"No screen context found for: {query}"

    sem = " [semantic+keyword]" if data.get('semantic_enhanced') else " [keyword]"
    browser_n = data.get('browser_captures_included', 0)
    header = (
        f"Screen context for: \"{query}\"\n"
        f"Mode:{sem} | {len(results)} results"
        + (f" | {browser_n} from browser" if browser_n else "")
        + "\n"
    )

    lines = [header]
    for r in results:
        ts = r.get('timestamp', '')
        try:
            t = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', ''))
            time_str = t.strftime('%H:%M')
        except Exception:
            time_str = ts[:16]

        app     = r.get('app') or r.get('source') or 'unknown'
        window  = r.get('window') or ''
        text    = (r.get('text') or '').strip()[:300]
        url     = r.get('url') or ''
        src_tag = f" [{r.get('source', 'ocr')}]"

        line = f"[{time_str}] {app}"
        if window:
            line += f" / {window[:50]}"
        line += src_tag
        lines.append(line)
        if url:
            lines.append(f"  URL: {url[:120]}")
        if text:
            lines.append(f"  {text[:200]}")
        lines.append("")

    return '\n'.join(lines)[:4000]  # hard cap to avoid overwhelming LLM context


def _tool_get_daily_summary(args):
    date = args.get('date') or datetime.now().strftime('%Y-%m-%d')
    data = _call_api(f"/summary?date={date}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    p = data.get('profile', {})
    lines = [f"Daily summary for {date}:"]
    lines.append(f"  Frames captured: {p.get('total_frames', 0)}")
    lines.append(f"  URLs visited: {p.get('urls_visited', 0)}")
    lines.append(f"  Audio chunks: {p.get('audio_chunks', 0)}")

    apps = p.get('top_apps', [])
    if apps:
        lines.append("  Top apps: " + ", ".join(
            f"{a['app']} ({a['frames']} frames)" for a in apps[:5]
        ))

    hours = p.get('active_hours', [])
    if hours:
        lines.append("  Most active hours: " + ", ".join(f"{h}:00" for h in hours[:4]))

    topics = p.get('topics', [])
    if topics:
        lines.append(f"  Key topics: {', '.join(topics[:15])}")

    return '\n'.join(lines)


def _tool_get_anomalies(args):
    days = int(args.get('days', 7))
    data = _call_api(f"/anomalies?days={days}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    anomalies = data.get('anomalies', [])
    if not anomalies:
        return (
            f"No behavioral anomalies detected today "
            f"(compared against {days}-day baseline)."
        )

    lines = [f"Behavioral anomalies (vs {days}-day baseline) — {data.get('date', '')}:"]
    for a in anomalies[:10]:
        lines.append(f"  {a.get('message', str(a))}")
    return '\n'.join(lines)


def _tool_get_user_profile(args):
    days = int(args.get('days', 7))
    data = _call_api(f"/profile?days={days}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    p = data.get('profile', {})
    dr = data.get('date_range', {})
    lines = [f"User profile ({dr.get('start', '')} → {dr.get('end', '')}):"]
    lines.append(f"  Total frames: {p.get('total_frames', 0)}")

    apps = p.get('top_apps', [])
    if apps:
        lines.append("  Top apps: " + ", ".join(
            f"{a['app']} ({a.get('hours', 0):.1f}h)" for a in apps[:5]
        ))

    heatmap = p.get('active_hours', {}).get('heatmap', [])
    if heatmap and max(heatmap, default=0) > 0:
        peak_hours = sorted(range(24), key=lambda h: -heatmap[h])[:3]
        lines.append("  Peak hours: " + ", ".join(f"{h}:00" for h in peak_hours))

    domains = p.get('top_domains', [])
    if domains:
        lines.append("  Top domains: " + ", ".join(d['domain'] for d in domains[:5]))

    topics = p.get('top_topics', [])
    if topics:
        lines.append(f"  Key topics: {', '.join(topics[:15])}")

    return '\n'.join(lines)


def _tool_get_browser_activity(args):
    limit = int(args.get('limit', 20))
    data  = _call_api(f"/browser-captures?limit={limit}")
    if data is None:
        return "Could not reach Context API at localhost:3031."

    results = data.get('results', [])
    if not results:
        return (
            "No browser activity captured. "
            "Install the Augur Chrome extension to capture browser activity: "
            "load extension/ as an unpacked extension in chrome://extensions"
        )

    lines = [f"Recent browser activity ({len(results)} captures, {data.get('total', 0)} total):"]
    for r in results[:20]:
        title  = r.get('title', '') or ''
        url    = r.get('url', '') or ''
        time_s = r.get('time_on_page_s')
        scroll = r.get('scroll_depth_pct')
        sel    = (r.get('selected_text') or '').strip()

        line = f"  {title[:80] or url[:80]}"
        meta = []
        if time_s is not None:
            meta.append(f"{time_s}s")
        if scroll is not None:
            meta.append(f"scroll {scroll}%")
        if meta:
            line += f" ({', '.join(meta)})"
        lines.append(line)

        if sel:
            lines.append(f"    Selected: \"{sel[:150]}\"")

    return '\n'.join(lines)[:3000]


# ── Dispatch ─────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    'get_context':          _tool_get_context,
    'get_daily_summary':    _tool_get_daily_summary,
    'get_anomalies':        _tool_get_anomalies,
    'get_user_profile':     _tool_get_user_profile,
    'get_browser_activity': _tool_get_browser_activity,
}


def _handle(msg):
    """
    Process one JSON-RPC message.
    Returns a response dict, or None for notifications (no id field).
    """
    method = msg.get('method', '')
    msg_id = msg.get('id')        # None for notifications
    params = msg.get('params') or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    # Notifications have no 'id' — must not respond
    if msg_id is None:
        return None

    if method == 'initialize':
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "augur", "version": "0.3"},
        })

    elif method == 'tools/list':
        return ok({"tools": TOOLS})

    elif method == 'tools/call':
        name      = params.get('name', '')
        arguments = params.get('arguments') or {}
        handler   = TOOL_HANDLERS.get(name)
        if handler is None:
            return err(-32601, f"Unknown tool: {name}")
        try:
            text = handler(arguments)
            return ok({"content": [{"type": "text", "text": text}]})
        except Exception as e:
            _log(f"Tool '{name}' raised: {e}")
            return ok({"content": [{"type": "text", "text": f"Tool error: {e}"}]})

    elif method == 'ping':
        return ok({})

    else:
        return err(-32601, f"Method not found: {method}")


# ── Main loop ────────────────────────────────────────────────────────

def main():
    _log("Augur MCP Server v0.3 — waiting for client")
    _log(f"Context API: {CONTEXT_API}")
    try:
        while True:
            msg = _read_message()
            if msg is None:
                break
            _log(f"<- {msg.get('method', '?')} id={msg.get('id')}")
            response = _handle(msg)
            if response is not None:
                _write_message(response)
    except (EOFError, BrokenPipeError, KeyboardInterrupt):
        _log("MCP server stopped.")
    except Exception as e:
        _log(f"Fatal: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
