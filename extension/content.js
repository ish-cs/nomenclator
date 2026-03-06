/**
 * Augur Browser Capture — Content Script
 * ----------------------------------------
 * Runs in every page. Tracks:
 *   - Time on page (reported on unload / visibility change)
 *   - Scroll depth
 *   - User text selections
 */

const pageStart = Date.now();
const pageUrl = location.href;
const pageDomain = location.hostname;
const pageTimestamp = new Date().toISOString();

let lastSelectedText = '';
let maxScrollDepth = 0;

// ── Scroll depth tracking ─────────────────────────────────────────
function updateScrollDepth() {
  const el = document.documentElement;
  const scrolled = el.scrollTop + el.clientHeight;
  const total = el.scrollHeight;
  if (total > 0) {
    maxScrollDepth = Math.max(maxScrollDepth, Math.round((scrolled / total) * 100));
  }
}
document.addEventListener('scroll', updateScrollDepth, { passive: true });

// ── Text selection ────────────────────────────────────────────────
let selectionTimer = null;
document.addEventListener('mouseup', () => {
  clearTimeout(selectionTimer);
  selectionTimer = setTimeout(() => {
    const sel = window.getSelection();
    const text = sel ? sel.toString().trim() : '';
    if (text && text !== lastSelectedText && text.length >= 10) {
      lastSelectedText = text;
      chrome.runtime.sendMessage({
        type: 'text_selected',
        url: pageUrl,
        domain: pageDomain,
        title: document.title,
        timestamp: new Date().toISOString(),
        text,
      });
    }
  }, 500);
});

// ── Page leave reporting ──────────────────────────────────────────
function reportPageLeave() {
  const time_on_page_s = Math.round((Date.now() - pageStart) / 1000);
  chrome.runtime.sendMessage({
    type: 'page_leave',
    url: pageUrl,
    domain: pageDomain,
    title: document.title,
    timestamp: pageTimestamp,
    time_on_page_s,
    scroll_depth_pct: maxScrollDepth,
    selected_text: lastSelectedText || null,
  });
}

// Report on visibility change (tab switch, window minimize)
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    reportPageLeave();
  }
});

// Report on page unload
window.addEventListener('pagehide', reportPageLeave);
