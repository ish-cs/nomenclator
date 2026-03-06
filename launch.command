#!/usr/bin/env python3
"""
screenpipe launcher
-------------------
Double-click (or run) this script to:
  1. Clean up raw screenpipe files older than CLEANUP_DAYS
  2. Start screenpipe if it's not already running
  3. Start the Augur Context API server (port 3031)
  4. Warn you if LM Studio server isn't running
  5. Open the dashboard in your browser automatically
"""

import subprocess
import sys
import time
import webbrowser
import os
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────
SCREENPIPE_BIN   = os.path.expanduser("~/bin/screenpipe")
DASHBOARD_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenpipe-dashboard.html")
CONTEXT_SERVER   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context-server.py")
SCREENPIPE_PORT  = 3030
CONTEXT_PORT     = 3031
LM_STUDIO_PORT   = 1234
CHECK_RETRIES    = 10
RETRY_DELAY      = 1.5  # seconds
CLEANUP_DAYS     = 7    # delete raw screenpipe files older than this many days

# ── Helpers ─────────────────────────────────────────────────────────
def is_port_open(port):
    try:
        if port == SCREENPIPE_PORT:
            url = f"http://localhost:{port}/health"
        elif port == CONTEXT_PORT:
            url = f"http://localhost:{port}/health"
        else:
            url = f"http://localhost:{port}/v1/models"
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


def cleanup_old_files():
    data_dir = Path.home() / ".screenpipe" / "data"
    if not data_dir.exists():
        return
    cutoff = datetime.now() - timedelta(days=CLEANUP_DAYS)
    freed = 0
    count = 0
    for f in data_dir.glob("*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                freed += f.stat().st_size
                f.unlink()
                count += 1
        except Exception:
            pass
    if freed > 0:
        print(f"  [cleanup] freed {freed / 1e6:.1f} MB ({count} files older than {CLEANUP_DAYS} days)")

def print_status(msg, ok=True):
    icon = "✓" if ok else "✗"
    print(f"  {icon}  {msg}")

def print_header():
    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │         screenpipe launcher          │")
    print("  └─────────────────────────────────────┘")
    print()

# ── Main ─────────────────────────────────────────────────────────────
def main():
    print_header()

    # 0. Cleanup old raw files
    print("  ⟳  Cleaning up old screenpipe files...")
    cleanup_old_files()

    # 1. Check if screenpipe binary exists
    if not os.path.exists(SCREENPIPE_BIN):
        print_status(f"screenpipe binary not found at {SCREENPIPE_BIN}", ok=False)
        print()
        print("  To fix: make sure screenpipe is at ~/bin/screenpipe")
        print("  Or edit SCREENPIPE_BIN in this script.")
        input("\n  Press Enter to exit...")
        sys.exit(1)

    # 2. Check if screenpipe is already running
    if is_port_open(SCREENPIPE_PORT):
        print_status("screenpipe already running on port 3030")
    else:
        print("  ⟳  Starting screenpipe...")
        try:
            log_file = open(os.path.expanduser("~/.screenpipe/launcher.log"), "a")
            subprocess.Popen(
                [SCREENPIPE_BIN],
                stdout=log_file,
                stderr=log_file,
                start_new_session=True
            )
        except Exception as e:
            print_status(f"Failed to start screenpipe: {e}", ok=False)
            input("\n  Press Enter to exit...")
            sys.exit(1)

        # Wait for it to come up
        print("  ⟳  Waiting for screenpipe to initialize", end="", flush=True)
        for i in range(CHECK_RETRIES):
            time.sleep(RETRY_DELAY)
            print(".", end="", flush=True)
            if is_port_open(SCREENPIPE_PORT):
                print()
                print_status("screenpipe started successfully")
                break
        else:
            print()
            print_status("screenpipe didn't start in time -- opening dashboard anyway", ok=False)

    # 3. Start Context API server
    print()
    if is_port_open(CONTEXT_PORT):
        print_status("Context API already running on port 3031")
    elif os.path.exists(CONTEXT_SERVER):
        print("  ⟳  Starting Augur Context API (port 3031)...")
        try:
            log_file = open(os.path.expanduser("~/.screenpipe/launcher.log"), "a")
            subprocess.Popen(
                [sys.executable, CONTEXT_SERVER],
                stdout=log_file,
                stderr=log_file,
                start_new_session=True
            )
            time.sleep(1.5)
            if is_port_open(CONTEXT_PORT):
                print_status("Context API started on port 3031")
            else:
                print_status("Context API starting (may take a moment)", ok=True)
        except Exception as e:
            print_status(f"Failed to start Context API: {e}", ok=False)
    else:
        print_status("context-server.py not found — skipping Context API", ok=False)

    # 4. Check LM Studio
    print()
    if is_port_open(LM_STUDIO_PORT):
        print_status("LM Studio server running on port 1234")
    else:
        print_status("LM Studio server NOT detected on port 1234", ok=False)
        print()
        print("  To enable AI chat in the dashboard:")
        print("  1. Open LM Studio")
        print("  2. Go to Local Server tab")
        print("  3. Click 'Start Server'")
        print()
        print("  Dashboard will still open -- AI chat will work once server is running.")

    # 5. Check dashboard file
    print()
    if not os.path.exists(DASHBOARD_PATH):
        print_status(f"Dashboard file not found: {DASHBOARD_PATH}", ok=False)
        print("  Make sure screenpipe-dashboard.html is in the same folder as this script.")
        input("\n  Press Enter to exit...")
        sys.exit(1)

    print_status(f"Dashboard found")

    # 6. Open browser
    print()
    print("  ⟳  Opening dashboard in browser...")
    time.sleep(0.5)
    webbrowser.open(f"file://{DASHBOARD_PATH}")
    print_status("Dashboard opened in browser")

    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │  All systems go. Press Ctrl+C to    │")
    print("  │  stop this window (screenpipe keeps │")
    print("  │  running in the background).        │")
    print("  └─────────────────────────────────────┘")
    print()

    try:
        while True:
            time.sleep(10)
            sp = is_port_open(SCREENPIPE_PORT)
            ctx = is_port_open(CONTEXT_PORT)
            lm = is_port_open(LM_STUDIO_PORT)
            status = f"  [screenpipe: {'up' if sp else 'DOWN'}]  [context-api: {'up' if ctx else 'down'}]  [LM Studio: {'up' if lm else 'not running'}]"
            print(f"\r{status}    ", end="", flush=True)
    except KeyboardInterrupt:
        print("\n\n  Launcher closed. Screenpipe continues running in background.")
        print("  To stop screenpipe: run 'pkill screenpipe' in Terminal.")
        print()

if __name__ == "__main__":
    main()
