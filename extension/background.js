/**
 * Augur Browser Capture — Background Service Worker
 * --------------------------------------------------
 * Listens for tab changes and content script messages.
 * Sends captures to the Augur Context API at localhost:3031.
 */

const CONTEXT_API = 'http://localhost:3031';
const MIN_TIME_ON_PAGE = 5;   // seconds — ignore pages visited too briefly
const DEBOUNCE_MS = 2000;     // wait 2s after navigation before sending

let pendingCapture = null;

// ── Send a capture to the context API ──────────────────────────────
async function sendCapture(entry) {
  try {
    await fetch(`${CONTEXT_API}/browser-capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry),
    });
    console.log('[Augur] Sent capture:', entry.url);
  } catch (e) {
    // Context API not running — fail silently
    console.warn('[Augur] Could not reach Context API:', e.message);
  }
}

// ── Build a capture entry from a tab ───────────────────────────────
function buildCapture(tab, extras = {}) {
  const url = tab.url || '';
  // Skip internal browser pages
  if (!url || url.startsWith('chrome://') || url.startsWith('about:') || url.startsWith('chrome-extension://')) {
    return null;
  }
  let domain = '';
  try { domain = new URL(url).hostname; } catch (_) {}
  return {
    source: 'browser_extension',
    url,
    domain,
    title: tab.title || '',
    timestamp: new Date().toISOString(),
    ...extras,
  };
}

// ── Tab activated (user switches to a tab) ─────────────────────────
chrome.tabs.onActivated.addListener(({ tabId }) => {
  chrome.tabs.get(tabId, (tab) => {
    if (chrome.runtime.lastError) return;
    const capture = buildCapture(tab);
    if (capture) {
      // Use debounce — user may be rapidly switching tabs
      clearTimeout(pendingCapture);
      pendingCapture = setTimeout(() => sendCapture(capture), DEBOUNCE_MS);
    }
  });
});

// ── Tab updated (navigation within same tab) ───────────────────────
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;
  const capture = buildCapture(tab);
  if (capture) {
    clearTimeout(pendingCapture);
    pendingCapture = setTimeout(() => sendCapture(capture), DEBOUNCE_MS);
  }
});

// ── Messages from content.js ───────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type === 'page_leave') {
    // User is leaving a page — record time spent if significant
    if (msg.time_on_page_s < MIN_TIME_ON_PAGE) return;
    const capture = {
      source: 'browser_extension',
      url: msg.url,
      domain: msg.domain,
      title: msg.title,
      timestamp: msg.timestamp,
      time_on_page_s: msg.time_on_page_s,
      selected_text: msg.selected_text || null,
      scroll_depth_pct: msg.scroll_depth_pct || 0,
    };
    sendCapture(capture);
  }

  if (msg.type === 'text_selected') {
    // User selected text — send immediately as context signal
    if (!msg.text || msg.text.trim().length < 10) return;
    const capture = {
      source: 'browser_extension',
      url: msg.url,
      domain: msg.domain,
      title: msg.title,
      timestamp: msg.timestamp,
      selected_text: msg.text.trim().slice(0, 2000),
      event: 'text_selected',
    };
    sendCapture(capture);
  }
});
