// Public profile evidence only; not a live attachment or deletion status.
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
export function relatedPublicObservations(payload, type, id) {
  if (payload?.schemaVersion !== 1 || !Array.isArray(payload.observations) || !uuid.test(id)) return [];
  return payload.observations.filter(row => {
    if (!row || !uuid.test(row.bot?.id || '') || !['rulebook','lorebook'].includes(row.book?.type)) return false;
    if (row.book.id !== null && !uuid.test(row.book.id || '')) return false;
    if (!row.bot.name || !row.book.name || !row.observedAt || !Array.isArray(row.evidence) || !row.evidence.length) return false;
    return type === 'bot' ? row.bot.id === id : row.book.type === type && row.book.id === id;
  });
}
function addText(parent, tag, text) {
  const el = document.createElement(tag);
  el.textContent = text;
  parent.append(el);
  return el;
}
function addLink(parent, type, entry) {
  const a = addText(parent, 'a', entry.name);
  a.href = '../' + type + '/?id=' + encodeURIComponent(entry.id);
}
async function showPublicRelationships() {
  const page = document.body.dataset.page;
  const id = (new URLSearchParams(location.search).get('id') || '').trim().toLowerCase();
  if (!['bot','rulebook','lorebook'].includes(page) || !uuid.test(id)) return;
  const app = document.getElementById('app');
  if (!app) return;
  const response = await fetch('../data/public-relationships.json');
  if (!response.ok) return;
  const rows = relatedPublicObservations(await response.json(), page, id);
  if (!rows.length) return;
  const section = document.createElement('section');
  section.className = 'table-card';
  addText(section, 'h2', 'Observed public attachments');
  addText(section, 'p', 'Historical public profile evidence. Links do not prove current attachment, public availability, or a saved full definition.');
  const list = document.createElement('ul');
  for (const row of rows) {
    const li = document.createElement('li');
    const entry = page === 'bot' ? row.book : row.bot;
    const type = page === 'bot' ? row.book.type : 'bot';
    addText(li, 'span', (type === 'bot' ? 'Bot' : type === 'rulebook' ? 'Rulebook' : 'Lorebook') + ': ');
    if (entry.id) {
      addLink(li, type, entry);
      const original = addText(li, 'a', ' · Original on SpicyChat');
      original.href = 'https://spicychat.ai/' + (type === 'bot' ? 'chatbot' : type) + '/' + encodeURIComponent(entry.id);
      original.target = '_blank';
      original.rel = 'noopener noreferrer';
    } else {
      addText(li, 'span', entry.name + ' (ID not yet verified)');
    }
    addText(li, 'span', ' — observed ' + row.observedAt.slice(0,10));
    list.append(li);
  }
  section.append(list);
  app.after(section);
}
if (typeof document !== 'undefined') showPublicRelationships().catch(() => {});
