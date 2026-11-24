// Exact UUIDs and supported SpicyChat/Archive bot links, not conversation IDs.
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
export function exactBotId(value) {
  const raw = String(value || '').trim();
  if (UUID.test(raw)) return raw.toLowerCase();
  let url;
  try { url = new URL(/^https?:\/\//i.test(raw) ? raw : 'https://' + raw); } catch { return ''; }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port) return '';
  let id = '';
  if (['spicychat.ai', 'www.spicychat.ai'].includes(url.hostname)) {
    const parts = url.pathname.split('/').filter(Boolean);
    const index = /^(?:chat|chatbot)$/i.test(parts[0] || '') ? 0
      : /^[a-z]{2}(?:-[a-z]{2})?$/i.test(parts[0] || '') && /^(?:chat|chatbot)$/i.test(parts[1] || '') ? 1 : -1;
    if (index >= 0) id = parts[index + 1] || '';
  } else if (url.hostname === 'spicychatarchive.drache.uk' && /^\/(?:bot|recovery)\/?$/.test(url.pathname)) {
    id = url.searchParams.get('id') || '';
  }
  return UUID.test(id) ? id.toLowerCase() : '';
}
export function bindExactBotLookup(input, {otherPage = ''} = {}) {
  if (!input) return;
  const box = document.createElement('div');
  box.className = 'filter-note exact-bot-lookup';
  box.hidden = true;
  box.style.margin = '8px 0';
  const note = document.createElement('p');
  note.textContent = 'Exact bot ID detected. The current listing and filters may hide it. Open the archived record directly, or try another listing.';
  const links = document.createElement('div');
  links.className = 'detail-actions';
  const direct = document.createElement('a');
  direct.className = 'secondary-button';
  direct.textContent = 'Open archive record by ID';
  const recovery = document.createElement('a');
  recovery.className = 'secondary-button';
  recovery.textContent = 'Open Recovery Lab';
  links.append(direct, recovery);
  let other;
  if (otherPage === 'bots' || otherPage === 'deleted') {
    other = document.createElement('a');
    other.className = 'secondary-button';
    other.textContent = otherPage === 'bots' ? 'Search public bots' : 'Search deleted bots';
    links.append(other);
  }
  box.append(note, links);
  (input.closest('.toolbar, .changes-toolbar') || input).after(box);
  function update() {
    const id = exactBotId(input.value);
    box.hidden = !id;
    if (!id) return;
    direct.href = '../bot/?id=' + encodeURIComponent(id);
    recovery.href = '../recovery/?id=' + encodeURIComponent(id);
    if (other) {
      const url = new URL('../' + otherPage + '/', location.href);
      url.searchParams.set('q', id);
      const excluded = new URLSearchParams(location.search).get('exclude');
      if (excluded !== null) url.searchParams.set('exclude', excluded);
      other.href = url.href;
    }
  }
  input.addEventListener('input', update);
  update();
}
