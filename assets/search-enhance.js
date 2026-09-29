(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  const snippets = new Map();
  const FIELD_MAP = {
    everything: 'name,title,tags,creator_username,character_id,type,description,greeting,scenario',
    name: 'name,title',
    creator: 'creator_username',
    tags: 'tags',
    greeting: 'greeting',
    description: 'description',
    scenario: 'scenario'
  };
  const RICH_FIELDS = new Set(['greeting', 'description', 'scenario']);
  let activeQuery = '';
  let activeSearchIn = new URLSearchParams(location.search).get('search_in') || 'everything';
  let activeSafety = new URLSearchParams(location.search).get('safety') || 'all';

  function isTypesenseRequest(input, init) {
    try {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
      const method = String(init?.method || 'GET').toUpperCase();
      return method === 'POST' && /\/multi_search(?:$|\?)/.test(url.pathname);
    } catch {
      return false;
    }
  }

  function parseBody(init) {
    try {
      if (!init?.body || typeof init.body !== 'string') return null;
      const parsed = JSON.parse(init.body);
      return parsed && Array.isArray(parsed.searches) ? parsed : null;
    } catch {
      return null;
    }
  }

  function appendFilter(existing, clause) {
    const value = String(existing || '').trim();
    return value ? `${value} && ${clause}` : clause;
  }

  function cleanQuery(value) {
    return String(value || '').trim().replace(/^"|"$/g, '').trim();
  }

  function makeSnippet(text, query, max = 220) {
    const source = String(text || '').replace(/\s+/g, ' ').trim();
    if (!source) return '';
    const needle = cleanQuery(query).toLowerCase();
    let at = needle ? source.toLowerCase().indexOf(needle) : -1;
    if (at < 0 && needle) {
      for (const token of needle.split(/\s+/).filter(x => x.length > 2)) {
        at = source.toLowerCase().indexOf(token);
        if (at >= 0) break;
      }
    }
    if (at < 0) at = 0;
    const start = Math.max(0, at - Math.floor(max * 0.35));
    const end = Math.min(source.length, start + max);
    return `${start ? '…' : ''}${source.slice(start, end)}${end < source.length ? '…' : ''}`;
  }

  function rememberSnippets(payload, search) {
    const q = String(search?.q || '').trim();
    if (!q || q === '*') return;
    const requested = activeSearchIn;
    for (const result of payload?.results || []) {
      for (const hit of result?.hits || []) {
        const doc = hit?.document || {};
        const id = String(doc.character_id || doc.id || '').toLowerCase();
        if (!id) continue;
        let field = requested;
        let value = '';
        if (requested === 'everything') {
          for (const candidate of ['greeting', 'description', 'scenario', 'title', 'name']) {
            const candidateValue = doc[candidate];
            if (candidateValue && String(candidateValue).toLowerCase().includes(cleanQuery(q).toLowerCase())) {
              field = candidate;
              value = candidateValue;
              break;
            }
          }
        } else {
          value = doc[requested];
        }
        if (!value && requested === 'name') value = doc.name || doc.title;
        if (!value && requested === 'creator') value = doc.creator_username;
        if (!value && requested === 'tags') value = Array.isArray(doc.tags) ? doc.tags.join(', ') : doc.tags;
        if (!value) continue;
        snippets.set(id, { field, text: makeSnippet(value, q) });
      }
    }
  }

  window.fetch = async function archiveSearchFetch(input, init = {}) {
    if (!isTypesenseRequest(input, init)) return nativeFetch(input, init);
    const body = parseBody(init);
    if (!body) return nativeFetch(input, init);

    const cloned = JSON.parse(JSON.stringify(body));
    const search = cloned.searches[0];
    if (!search) return nativeFetch(input, init);

    activeQuery = String(search.q || '');
    const queryBy = FIELD_MAP[activeSearchIn] || FIELD_MAP.everything;
    search.query_by = queryBy;
    search.include_fields = [
      'character_id','name','title','tags','creator_username','avatar_url',
      'avatar_is_nsfw','is_nsfw','num_messages','num_messages_24h','rating_score',
      'createdAt','updatedAt','description','greeting','scenario'
    ].join(',');

    if (activeSafety === 'sfw') search.filter_by = appendFilter(search.filter_by, 'is_nsfw:=false');
    if (activeSafety === 'nsfw') search.filter_by = appendFilter(search.filter_by, 'is_nsfw:=true');

    const nextInit = { ...init, body: JSON.stringify(cloned) };
    const response = await nativeFetch(input, nextInit);
    if (!response.ok) return response;

    try {
      const payload = await response.clone().json();
      const error = payload?.results?.[0]?.error;
      if (error && (RICH_FIELDS.has(activeSearchIn) || activeSearchIn === 'everything')) {
        window.dispatchEvent(new CustomEvent('archive-search-capability-error', { detail: { field: activeSearchIn, error } }));
        return response;
      }
      rememberSnippets(payload, search);
      queueMicrotask(renderSnippets);
    } catch {}
    return response;
  };

  function updateUrl() {
    const url = new URL(location.href);
    if (activeSearchIn === 'everything') url.searchParams.delete('search_in');
    else url.searchParams.set('search_in', activeSearchIn);
    if (activeSafety === 'all') url.searchParams.delete('safety');
    else url.searchParams.set('safety', activeSafety);
    history.replaceState(null, '', url);
  }

  function retriggerSearch() {
    updateUrl();
    const input = document.querySelector('#search');
    if (!input) return;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function injectControls() {
    const toolbar = document.querySelector('.toolbar');
    if (!toolbar || toolbar.dataset.richSearchEnhanced) return;
    toolbar.dataset.richSearchEnhanced = '1';
    toolbar.classList.add('archive-search-toolbar');

    const searchIn = document.createElement('select');
    searchIn.id = 'search-in';
    searchIn.setAttribute('aria-label', 'Search in');
    searchIn.innerHTML = `
      <option value="everything">Everything</option>
      <option value="name">Name / title</option>
      <option value="creator">Creator</option>
      <option value="tags">Tags</option>
      <option value="greeting">Greeting</option>
      <option value="description">Description</option>
      <option value="scenario">Scenario</option>`;
    searchIn.value = FIELD_MAP[activeSearchIn] ? activeSearchIn : 'everything';

    const safety = document.createElement('select');
    safety.id = 'safety-filter';
    safety.setAttribute('aria-label', 'SFW or NSFW');
    safety.innerHTML = `
      <option value="all">SFW + NSFW</option>
      <option value="sfw">SFW only</option>
      <option value="nsfw">NSFW only</option>`;
    safety.value = ['all','sfw','nsfw'].includes(activeSafety) ? activeSafety : 'all';

    searchIn.addEventListener('change', () => {
      activeSearchIn = searchIn.value;
      snippets.clear();
      clearCapabilityMessage();
      retriggerSearch();
    });
    safety.addEventListener('change', () => {
      activeSafety = safety.value;
      snippets.clear();
      clearCapabilityMessage();
      retriggerSearch();
    });

    toolbar.append(searchIn, safety);

    const note = document.createElement('div');
    note.id = 'rich-search-note';
    note.className = 'rich-search-note';
    note.innerHTML = 'Search bot text directly. Put a remembered phrase in quotes for a tighter match.';
    toolbar.insertAdjacentElement('afterend', note);
  }

  function clearCapabilityMessage() {
    const note = document.querySelector('#rich-search-note');
    if (!note) return;
    note.classList.remove('error-note');
    note.textContent = 'Search bot text directly. Put a remembered phrase in quotes for a tighter match.';
  }

  window.addEventListener('archive-search-capability-error', event => {
    const note = document.querySelector('#rich-search-note');
    if (!note) return;
    const field = event.detail?.field || 'selected field';
    note.classList.add('error-note');
    note.textContent = `SpicyChat's public search index did not accept ${field} search on this request. The archive kept the error visible rather than silently searching a different field.`;
  });

  function renderSnippets() {
    for (const card of document.querySelectorAll('.card')) {
      if (card.querySelector('.search-snippet')) continue;
      const href = card.querySelector('a.cardlink')?.href;
      if (!href) continue;
      let id = '';
      try { id = new URL(href).searchParams.get('id')?.toLowerCase() || ''; } catch {}
      const snippet = snippets.get(id);
      if (!snippet?.text) continue;
      const body = card.querySelector('.body');
      if (!body) continue;
      const box = document.createElement('div');
      box.className = 'search-snippet';
      const label = document.createElement('b');
      label.textContent = `${snippet.field}: `;
      const text = document.createElement('span');
      text.textContent = snippet.text;
      box.append(label, text);
      body.insertBefore(box, body.querySelector('.taglist'));
    }
  }

  const observer = new MutationObserver(() => {
    injectControls();
    renderSnippets();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', injectControls, { once: true });
  else injectControls();
})();
