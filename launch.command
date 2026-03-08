#!/usr/bin/env python3
"""
Augur
-----
Double-click (or run) this script to:
  1. Clean up raw screenpipe files older than CLEANUP_DAYS
  2. Start screenpipe if it's not already running
  3. Start the Augur Context API server (port 3031)
  4. Start the Augur Semantic Indexer
  5. Warn you if LM Studio server isn't running
  6. Open the dashboard in your browser automatically
"""

import subprocess
import sys
import time
import webbrowser
import os
import urllib.request
import urllib.error
import threading
import importlib
from pathlib import Path
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk
from tkinter import scrolledtext

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
SEMANTIC_INDEXER  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic_search.py")
SEMANTIC_PID_FILE = os.path.expanduser("~/.screenpipe/semantic_indexer.pid")

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
        log(f"  [cleanup] freed {freed / 1e6:.1f} MB ({count} files older than {CLEANUP_DAYS} days)")


def is_semantic_available():
    """Check if chromadb and sentence_transformers are importable."""
    try:
        importlib.import_module('chromadb')
        importlib.import_module('sentence_transformers')
        return True
    except ImportError:
        return False


def is_semantic_running():
    """Check if the semantic indexer daemon is alive via PID file."""
    if not os.path.exists(SEMANTIC_PID_FILE):
        return False
    try:
        with open(SEMANTIC_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)   # signal 0 = existence check only
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        # PID file stale — clean it up
        try:
            os.unlink(SEMANTIC_PID_FILE)
        except Exception:
            pass
        return False


def start_semantic_indexer():
    """Start semantic_search.py daemon. Returns (True, pid) or (False, reason)."""
    if not os.path.exists(SEMANTIC_INDEXER):
        return False, "semantic_search.py not found"
    if not is_semantic_available():
        return False, "deps missing (pip install chromadb sentence-transformers)"
    try:
        log_file = open(os.path.expanduser("~/.screenpipe/launcher.log"), "a")
        proc = subprocess.Popen(
            [sys.executable, SEMANTIC_INDEXER],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        os.makedirs(os.path.dirname(SEMANTIC_PID_FILE), exist_ok=True)
        with open(SEMANTIC_PID_FILE, 'w') as f:
            f.write(str(proc.pid))
        return True, proc.pid
    except Exception as e:
        return False, str(e)


# ── GUI globals ──────────────────────────────────────────────────────
root = None
log_widget = None
lbl_sp_status = None
lbl_ctx_status = None
lbl_sem_status = None
lbl_lm_status = None
btn_start = None
btn_stop = None


def _append_to_log(msg):
    """Must only be called from the main thread."""
    log_widget.config(state='normal')
    log_widget.insert(tk.END, msg + "\n")
    log_widget.see(tk.END)
    log_widget.config(state='disabled')


def log(msg):
    """Thread-safe log: schedules the append on the main thread."""
    root.after(0, lambda: _append_to_log(msg))


# ── Status polling ───────────────────────────────────────────────────
def poll_status():
    running_cfg = {"text": "● running", "fg": "#00c853"}
    stopped_cfg = {"text": "● stopped", "fg": "#ff3b3b"}

    sp  = is_port_open(SCREENPIPE_PORT)
    ctx = is_port_open(CONTEXT_PORT)
    sem = is_semantic_running()
    lm  = is_port_open(LM_STUDIO_PORT)

    lbl_sp_status.config(**(running_cfg if sp else stopped_cfg))
    lbl_ctx_status.config(**(running_cfg if ctx else stopped_cfg))
    lbl_sem_status.config(**(running_cfg if sem else stopped_cfg))
    lbl_lm_status.config(**(running_cfg if lm else stopped_cfg))

    root.after(5000, poll_status)


# ── Start Screenpipe button ──────────────────────────────────────────
def _start_screenpipe_thread():
    try:
        log_file = open(os.path.expanduser("~/.screenpipe/launcher.log"), "a")
        subprocess.Popen(
            [SCREENPIPE_BIN],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True
        )
    except Exception as e:
        log(f"  Failed to start screenpipe: {e}")
        root.after(0, lambda: btn_start.config(state='normal'))
        return

    log("  Waiting for screenpipe to initialize...")
    for _ in range(CHECK_RETRIES):
        time.sleep(RETRY_DELAY)
        if is_port_open(SCREENPIPE_PORT):
            log("  screenpipe started successfully")
            root.after(0, lambda: btn_start.config(state='normal'))
            return

    log("  screenpipe did not start in time")
    root.after(0, lambda: btn_start.config(state='normal'))


def on_start_screenpipe():
    btn_start.config(state='disabled')
    log("Starting screenpipe...")
    t = threading.Thread(target=_start_screenpipe_thread, daemon=True)
    t.start()


# ── Stop Screenpipe button ───────────────────────────────────────────
def on_stop_screenpipe():
    result = subprocess.run(['pkill', 'screenpipe'], capture_output=True, text=True)
    if result.returncode == 0:
        log("screenpipe stopped.")
    else:
        log(f"pkill screenpipe returned code {result.returncode} (may already be stopped).")


# ── Window close ─────────────────────────────────────────────────────
def on_close():
    log("Window closed. Services continue running in background.")
    root.after(100, root.destroy)


# ── Startup sequence (runs in background thread) ──────────────────────
def startup_sequence():
    log("Augur starting up...")

    # 0. Cleanup old raw files
    log("  Cleaning up old screenpipe files...")
    cleanup_old_files()

    # 1. Check if screenpipe binary exists
    if not os.path.exists(SCREENPIPE_BIN):
        log(f"  ERROR: screenpipe binary not found at {SCREENPIPE_BIN}")
        log("  To fix: make sure screenpipe is at ~/bin/screenpipe")
        log("  Or edit SCREENPIPE_BIN in this script.")
        return

    # 2. Check if screenpipe is already running
    if is_port_open(SCREENPIPE_PORT):
        log("  screenpipe already running on port 3030")
    else:
        log("  Starting screenpipe...")
        try:
            log_file = open(os.path.expanduser("~/.screenpipe/launcher.log"), "a")
            subprocess.Popen(
                [SCREENPIPE_BIN],
                stdout=log_file,
                stderr=log_file,
                start_new_session=True
            )
        except Exception as e:
            log(f"  Failed to start screenpipe: {e}")
            return

        log("  Waiting for screenpipe to initialize...")
        started = False
        for _ in range(CHECK_RETRIES):
            time.sleep(RETRY_DELAY)
            if is_port_open(SCREENPIPE_PORT):
                log("  screenpipe started successfully")
                started = True
                break
        if not started:
            log("  screenpipe did not start in time -- opening dashboard anyway")

    # 3. Start Context API server
    if is_port_open(CONTEXT_PORT):
        log("  Context API already running on port 3031")
    elif os.path.exists(CONTEXT_SERVER):
        log("  Starting Augur Context API (port 3031)...")
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
                log("  Context API started on port 3031")
            else:
                log("  Context API starting (may take a moment)")
        except Exception as e:
            log(f"  Failed to start Context API: {e}")
    else:
        log("  context-server.py not found -- skipping Context API")

    # 4. Start semantic indexer
    if is_semantic_running():
        log("  Semantic indexer already running")
    elif not is_semantic_available():
        log("  Semantic indexer not available (pip install chromadb sentence-transformers)")
        log("  To enable semantic context search:")
        log("    pip install chromadb sentence-transformers")
    elif not os.path.exists(SEMANTIC_INDEXER):
        log("  semantic_search.py not found -- skipping semantic indexer")
    else:
        log("  Starting Augur Semantic Indexer...")
        ok, info = start_semantic_indexer()
        if ok:
            time.sleep(1.5)
            if is_semantic_running():
                log(f"  Semantic indexer started (PID {info})")
            else:
                log("  Semantic indexer starting (model loading, may take a minute)")
        else:
            log(f"  Failed to start semantic indexer: {info}")

    # 5. Check LM Studio
    if is_port_open(LM_STUDIO_PORT):
        log("  LM Studio server running on port 1234")
    else:
        log("  LM Studio server NOT detected on port 1234")
        log("  To enable AI chat in the dashboard:")
        log("  1. Open LM Studio")
        log("  2. Go to Local Server tab")
        log("  3. Click 'Start Server'")
        log("  Dashboard will still open -- AI chat will work once server is running.")

    # 6. Check dashboard file
    if not os.path.exists(DASHBOARD_PATH):
        log(f"  ERROR: Dashboard file not found: {DASHBOARD_PATH}")
        log("  Make sure screenpipe-dashboard.html is in the same folder as this script.")
        return

    log("  Dashboard found")

    # 7. Open browser
    log("  Opening dashboard in browser...")
    time.sleep(0.5)
    webbrowser.open(f"file://{DASHBOARD_PATH}")
    log("  Dashboard opened in browser")
    log("  All systems go. Services are running in the background.")


# ── Main ─────────────────────────────────────────────────────────────
def main():
    global root, log_widget
    global lbl_sp_status, lbl_ctx_status, lbl_sem_status, lbl_lm_status
    global btn_start, btn_stop

    root = tk.Tk()
    root.title("Augur")
    root.resizable(False, False)
    root.configure(bg="#1e1e1e")

    # ── Header ──────────────────────────────────────────────────────
    frm_header = tk.Frame(root, bg="#1e1e1e", pady=16, padx=24)
    frm_header.pack(fill='x')

    tk.Label(
        frm_header,
        text="Augur",
        font=("Helvetica", 18, "bold"),
        bg="#1e1e1e",
        fg="#ffffff"
    ).pack(anchor='w')

    tk.Label(
        frm_header,
        text="personal context layer",
        font=("Helvetica", 11),
        bg="#1e1e1e",
        fg="#888888"
    ).pack(anchor='w')

    # ── Separator ───────────────────────────────────────────────────
    sep = ttk.Separator(root, orient='horizontal')
    sep.pack(fill='x', padx=24)

    # ── Status grid ─────────────────────────────────────────────────
    frm_status = tk.Frame(root, bg="#1e1e1e", pady=12, padx=24)
    frm_status.pack(fill='x')

    services = [
        ("screenpipe",       None),
        ("Context API",      None),
        ("Semantic Indexer", None),
        ("LM Studio",        None),
    ]

    status_labels = []
    for i, (name, _) in enumerate(services):
        tk.Label(
            frm_status,
            text=name,
            font=("Helvetica", 12),
            bg="#1e1e1e",
            fg="#cccccc",
            anchor='w',
            width=20
        ).grid(row=i, column=0, sticky='w', pady=3)

        lbl = tk.Label(
            frm_status,
            text="● stopped",
            font=("Helvetica", 12),
            bg="#1e1e1e",
            fg="#ff3b3b",
            anchor='e'
        )
        lbl.grid(row=i, column=1, sticky='e', pady=3)
        status_labels.append(lbl)

    frm_status.columnconfigure(0, weight=1)
    frm_status.columnconfigure(1, weight=1)

    lbl_sp_status  = status_labels[0]
    lbl_ctx_status = status_labels[1]
    lbl_sem_status = status_labels[2]
    lbl_lm_status  = status_labels[3]

    # ── Buttons ─────────────────────────────────────────────────────
    frm_buttons = tk.Frame(root, bg="#1e1e1e", pady=8, padx=24)
    frm_buttons.pack(fill='x')

    btn_start = tk.Button(
        frm_buttons,
        text="Start Screenpipe",
        command=on_start_screenpipe,
        font=("Helvetica", 11),
        bg="#2a2a2a",
        fg="#ffffff",
        activebackground="#3a3a3a",
        activeforeground="#ffffff",
        relief='flat',
        padx=12,
        pady=6,
        cursor='hand2'
    )
    btn_start.pack(side='left', padx=(0, 8))

    btn_stop = tk.Button(
        frm_buttons,
        text="Stop Screenpipe",
        command=on_stop_screenpipe,
        font=("Helvetica", 11),
        bg="#2a2a2a",
        fg="#ffffff",
        activebackground="#3a3a3a",
        activeforeground="#ffffff",
        relief='flat',
        padx=12,
        pady=6,
        cursor='hand2'
    )
    btn_stop.pack(side='left')

    # ── Log area ────────────────────────────────────────────────────
    frm_log = tk.Frame(root, bg="#1e1e1e", pady=8, padx=24)
    frm_log.pack(fill='both', expand=True)

    log_widget = scrolledtext.ScrolledText(
        frm_log,
        height=14,
        font=("Courier", 10),
        bg="#111111",
        fg="#aaaaaa",
        insertbackground="#aaaaaa",
        relief='flat',
        state='disabled',
        wrap='word',
        padx=8,
        pady=6
    )
    log_widget.pack(fill='both', expand=True)

    # ── Bottom padding ───────────────────────────────────────────────
    tk.Frame(root, bg="#1e1e1e", height=12).pack()

    # ── Window close handler ─────────────────────────────────────────
    root.protocol("WM_DELETE_WINDOW", on_close)

    # ── Initial status poll ──────────────────────────────────────────
    root.after(500, poll_status)

    # ── Startup sequence in background thread ────────────────────────
    t = threading.Thread(target=startup_sequence, daemon=True)
    t.start()

    root.mainloop()


if __name__ == "__main__":
    main()
