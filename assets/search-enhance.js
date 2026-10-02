(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  const page = document.body?.dataset?.page || 'browse';
  const deletedPage = page === 'deleted';
  const snippets = new Map();
  const SAFE_EVERYTHING = ['name', 'title', 'tags', 'creator_username', 'character_id', 'type'];
  const RICH_FIELDS = ['persona', 'scenario', 'dialogue'];
  const FIELD_LABELS = {
    persona: 'Personality',
    scenario: 'Scenario',
    dialogue: 'Example Dialogue'
  };
  const FIELD_MAP = {
    persona: 'persona',
    scenario: 'scenario',
    dialogue: 'dialogue'
  };
  const richSupport = new Map(RICH_FIELDS.map(field => [field, null]));
  let capabilityProbe = null;
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

  function queryByFor(selection) {
    if (selection !== 'everything') return FIELD_MAP[selection] || SAFE_EVERYTHING.join(',');
    const rich = RICH_FIELDS.filter(field => richSupport.get(field) === true);
    return [...SAFE_EVERYTHING, ...rich].join(',');
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
          for (const candidate of ['persona', 'scenario', 'dialogue', 'title', 'name']) {
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
        if (!value) continue;
        snippets.set(id, { field, text: makeSnippet(value, q) });
      }
    }
  }

  function updateCapabilityUi() {
    const select = document.querySelector('#search-in');
    if (select) {
      for (const field of RICH_FIELDS) {
        const option = select.querySelector(`option[value="${field}"]`);
        if (!option) continue;
        const support = richSupport.get(field);
        const suffix = support === false ? (deletedPage ? ' (not indexed here)' : ' (not exposed)') : '';
        option.textContent = `${FIELD_LABELS[field] || field}${suffix}`;
        option.disabled = support === false;
      }
    }
    const supported = RICH_FIELDS.filter(field => richSupport.get(field) === true);
    const note = document.querySelector('#rich-search-note');
    if (note && !note.classList.contains('error-note')) {
      if (deletedPage) {
        note.textContent = 'The deleted list always searches its saved base fields. Personality, Scenario and Example Dialogue live in individual bot records and are not indexed across the full deleted list yet.';
      } else {
        note.textContent = supported.length
          ? `Extra-field search ready: ${supported.map(field => FIELD_LABELS[field] || field).join(', ')}. Everything still searches the normal bot fields too.`
          : 'Checking whether SpicyChat exposes Personality, Scenario or Example Dialogue to public text search…';
      }
    }
  }

  async function probeCapabilities(input, init, body) {
    if (deletedPage) {
      RICH_FIELDS.forEach(field => richSupport.set(field, false));
      updateCapabilityUi();
      return;
    }
    if (capabilityProbe) return capabilityProbe;
    capabilityProbe = (async () => {
      const base = body.searches?.[0];
      if (!base) return;
      const searches = RICH_FIELDS.map(field => ({
        ...base,
        q: 'the',
        query_by: field,
        page: 1,
        per_page: 1,
        include_fields: `character_id,${field}`
      }));
      try {
        const response = await nativeFetch(input, { ...init, body: JSON.stringify({ searches }) });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        RICH_FIELDS.forEach((field, index) => {
          const result = payload?.results?.[index];
          richSupport.set(field, Boolean(result && !result.error));
        });
      } catch {
        RICH_FIELDS.forEach(field => richSupport.set(field, false));
      }
      updateCapabilityUi();
      if (activeSearchIn === 'everything') retriggerSearch();
    })();
    return capabilityProbe;
  }

  window.fetch = async function archiveSearchFetch(input, init = {}) {
    if (!isTypesenseRequest(input, init)) return nativeFetch(input, init);
    const body = parseBody(init);
    if (!body) return nativeFetch(input, init);

    if (!capabilityProbe) queueMicrotask(() => probeCapabilities(input, init, body));

    const cloned = JSON.parse(JSON.stringify(body));
    const search = cloned.searches[0];
    if (!search) return nativeFetch(input, init);

    search.query_by = queryByFor(activeSearchIn);
    const selectedRich = RICH_FIELDS.filter(field => richSupport.get(field) === true || field === activeSearchIn);
    search.include_fields = [
      'character_id','name','title','tags','creator_username','avatar_url',
      'avatar_is_nsfw','is_nsfw','num_messages','num_messages_24h','rating_score',
      'createdAt','updatedAt', ...selectedRich
    ].join(',');

    if (activeSafety === 'sfw') search.filter_by = appendFilter(search.filter_by, 'is_nsfw:=false');
    if (activeSafety === 'nsfw') search.filter_by = appendFilter(search.filter_by, 'is_nsfw:=true');

    const response = await nativeFetch(input, { ...init, body: JSON.stringify(cloned) });
    if (!response.ok) return response;

    try {
      const payload = await response.clone().json();
      const error = payload?.results?.[0]?.error;
      if (error && RICH_FIELDS.includes(activeSearchIn)) {
        richSupport.set(activeSearchIn, false);
        updateCapabilityUi();
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
      <option value="persona">Personality</option>
      <option value="scenario">Scenario</option>
      <option value="dialogue">Example Dialogue</option>`;
    searchIn.value = activeSearchIn in FIELD_MAP || activeSearchIn === 'everything' ? activeSearchIn : 'everything';

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
    note.textContent = deletedPage
      ? 'Checking archived extra-field search support…'
      : 'Checking whether SpicyChat exposes Personality, Scenario or Example Dialogue to public text search…';
    toolbar.insertAdjacentElement('afterend', note);
    updateCapabilityUi();
  }

  function clearCapabilityMessage() {
    const note = document.querySelector('#rich-search-note');
    if (!note) return;
    note.classList.remove('error-note');
    updateCapabilityUi();
  }

  window.addEventListener('archive-search-capability-error', event => {
    const note = document.querySelector('#rich-search-note');
    if (!note) return;
    const field = event.detail?.field || 'selected field';
    const label = FIELD_LABELS[field] || field;
    note.classList.add('error-note');
    note.textContent = `SpicyChat's public search index does not expose ${label} as a searchable field. Nothing was silently substituted.`;
  });

  function renderSnippets() {
    for (const card of document.querySelectorAll('.card')) {
      const href = card.querySelector('a.cardlink')?.href;
      if (!href) continue;
      let id = '';
      try { id = new URL(href).searchParams.get('id')?.toLowerCase() || ''; } catch {}
      const existing = card.querySelector('.search-snippet');
      const snippet = snippets.get(id);
      if (!snippet?.text) {
        existing?.remove();
        continue;
      }
      if (existing) continue;
      const body = card.querySelector('.body');
      if (!body) continue;
      const box = document.createElement('div');
      box.className = 'search-snippet';
      const label = document.createElement('b');
      label.textContent = `${FIELD_LABELS[snippet.field] || (snippet.field === 'title' ? 'Title' : snippet.field === 'name' ? 'Name' : snippet.field)}: `;
      const text = document.createElement('span');
      text.textContent = snippet.text;
      box.append(label, text);
      body.insertBefore(box, body.querySelector('.taglist'));
    }
  }

  if (deletedPage) RICH_FIELDS.forEach(field => richSupport.set(field, false));

  const observer = new MutationObserver(() => {
    injectControls();
    renderSnippets();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', injectControls, { once: true });
  else injectControls();
})();
