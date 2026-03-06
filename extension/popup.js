const CONTEXT_API = 'http://localhost:3031';

async function init() {
  // Check API health
  let apiOk = false;
  try {
    const r = await fetch(`${CONTEXT_API}/health`, { signal: AbortSignal.timeout(2000) });
    const d = await r.json();
    apiOk = d.status === 'ok';
  } catch (_) {}

  const dot = document.getElementById('statusDot');
  const apiBadge = document.getElementById('apiBadge');
  dot.className = 'dot ' + (apiOk ? 'online' : 'offline');
  apiBadge.textContent = apiOk ? 'online' : 'offline';
  apiBadge.className = 'badge ' + (apiOk ? 'ok' : 'err');

  if (!apiOk) {
    document.getElementById('captures').innerHTML =
      '<div class="empty">Context API offline.<br>Start context-server.py first.</div>';
    document.getElementById('countBadge').textContent = '—';
    return;
  }

  // Load browser captures
  try {
    const r = await fetch(`${CONTEXT_API}/browser-captures?limit=20`);
    const d = await r.json();
    const items = d.results || [];

    document.getElementById('countBadge').textContent = d.total || 0;
    document.getElementById('countBadge').className = 'badge ok';

    const today = new Date().toISOString().slice(0, 10);
    const todayItems = items.filter(i => (i.timestamp || '').startsWith(today));

    if (!items.length) {
      document.getElementById('captures').innerHTML =
        '<div class="empty">No captures yet.<br>Browse some pages!</div>';
      return;
    }

    const html = items.slice(0, 10).map(item => {
      const domain = item.domain || new URL(item.url || 'http://unknown').hostname;
      const title = (item.title || item.url || '').slice(0, 50);
      const time = item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : '';
      const extras = [];
      if (item.time_on_page_s) extras.push(`${item.time_on_page_s}s`);
      if (item.selected_text) extras.push('selected text');
      return `
        <div class="capture-item" title="${(item.url || '').replace(/"/g, '&quot;')}">
          <div class="capture-domain">${domain}</div>
          <div class="capture-title">${title}</div>
          <div class="capture-meta">${time}${extras.length ? ' · ' + extras.join(' · ') : ''}</div>
        </div>`;
    }).join('');

    document.getElementById('captures').innerHTML = html;
  } catch (e) {
    document.getElementById('captures').innerHTML =
      `<div class="empty">Error: ${e.message}</div>`;
  }
}

init();
