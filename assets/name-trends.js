(() => {
  'use strict';
  const ROOT = 'https://data.spicychatarchive.drache.uk/indexes/';
  const container = document.getElementById('name-trends-app');
  const shardCache = new Map();
  const fmt = count => Number(count || 0).toLocaleString();
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
  const normalize = value => String(value ?? '').normalize('NFKC').replace(/\s+/gu, ' ').trim().toLowerCase();
  const hash = key => {
    let h = 2166136261;
    for (const code of new TextEncoder().encode(key)) {
      h ^= code;
      h = Math.imul(h, 16777619) >>> 0;
    }
    return (h & 63).toString(16).padStart(2, '0');
  };
  async function fetchJson(path) {
    const response = await fetch(path, { cache: 'no-cache' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }
  async function findName(value) {
    const key = normalize(value);
    if (!key || key === 'unknown' || key === 'unknown bot') return { invalid: true };
    const bucket = hash(key);
    if (!shardCache.has(bucket)) {
      shardCache.set(bucket, fetchJson(ROOT + 'name-trends/' + bucket + '.json')
        .then(result => {
          if (result.schemaVersion !== 1 || !result.names) throw new Error('Name index is incomplete');
          return result.names;
        }).catch(error => { shardCache.delete(bucket); throw error; }));
    }
    const names = await shardCache.get(bucket);
    const found = names[key];
    return { count: found ? Number(found[1]) : 0, display: found ? String(found[0]) : value };
  }
  function statsTable(rows, emptyText, { link = false, limit = 25 } = {}) {
    if (!rows?.length) return '<p class="filter-note">' + esc(emptyText) + '</p>';
    return '<div class="name-rank-list">' + rows.slice(0, limit).map((row, i) =>
      '<div class="name-rank-item"><span class="name-rank-position">' + (i + 1) + '.</span>' +
      (link ? '<a href="../bots/?q=' + encodeURIComponent(row.value) + '" title="Search public bot listings (not an exact-name filter)">' + esc(row.value) + '</a>'
        : '<span>' + esc(row.value) + '</span>') +
      '<b>' + fmt(row.count) + '</b></div>'
    ).join('') + '</div>';
  }
  function themeRows(rows) {
    const filtered = (rows || []).filter(row => row.count > 0).sort((a, b) => b.count - a.count);
    if (!filtered.length) return '<p class="filter-note">No recognizable English title patterns in this snapshot.</p>';
    const largest = filtered[0].count;
    return '<div class="name-theme-list">' + filtered.map(row =>
      '<div class="name-theme"><div class="name-theme-head"><span>' + esc(row.label) + '</span><b>' + fmt(row.count) + '</b></div>' +
      '<div class="name-bar"><div style="width:' + Math.max(1, Math.round(row.count / largest * 100)) + '%"></div></div></div>'
    ).join('') + '</div>';
  }
  function nameIdeas() {
    const beginnings = ['Ael', 'Vael', 'Syr', 'Or', 'Rov', 'Thal', 'Ny', 'Iri', 'Cael', 'Zer', 'Ely', 'Vey', 'Mor', 'Syl', 'Kyr', 'Astra'];
    const endings = ['ith', 'ara', 'essa', 'orin', 'yra', 'evan', 'elis', 'endra', 'eth', 'wyn', 'ova', 'ielle', 'aris', 'arae'];
    const suggestions = new Set();
    for (let i = 0; i < 48; i++) {
      const first = beginnings[Math.floor(Math.random() * beginnings.length)];
      const last = endings[Math.floor(Math.random() * endings.length)];
      suggestions.add(first + last);
    }
    return [...suggestions].slice(0, 24);
  }
  async function showIdeas() {
    const el = document.getElementById('name-ideas');
    const button = document.getElementById('generate-names');
    if (!el) return;
    button.disabled = true;
    el.textContent = 'Checking generated name ideas against the archived name index…';
    try {
      const candidates = nameIdeas();
      // Limit in-flight requests so this never floods the public R2 endpoint.
      const found = [];
      for (let i = 0; i < candidates.length; i += 6) {
        const results = await Promise.all(candidates.slice(i, i + 6).map(async name => ({
          name, result: await findName(name)
        })));
        found.push(...results.filter(x => x.result.count === 0).map(x => x.name));
        if (found.length >= 10) break;
      }
      el.innerHTML = found.length
        ? '<div class="name-idea-list">' + found.slice(0, 10).map(name =>
          '<button type="button" data-suggest-name="' + esc(name) + '">' + esc(name) + '</button>'
        ).join('') + '</div><p class="filter-note">Generated ideas with 0 matches in this archived snapshot. Not guaranteed available or unused on SpicyChat.</p>'
        : '<p class="filter-note">No zero-match ideas in this attempt. Try generating another set.</p>';
      el.querySelectorAll('[data-suggest-name]').forEach(btn => btn.addEventListener('click', () => {
        document.getElementById('name-query').value = btn.dataset.suggestName;
        document.getElementById('check-name').requestSubmit();
      }));
    } catch (error) {
      el.textContent = 'Could not check suggestions: ' + error.message;
    } finally {
      button.disabled = false;
    }
  }
  async function main() {
    let stats;
    try {
      stats = await fetchJson(ROOT + 'name-trends.json');
      if (stats.schemaVersion !== 1 || !stats.coverage?.nameIndexedBots) throw new Error('Statistics not ready');
    } catch {
      container.innerHTML = '<section class="section"><h2>Statistics are being prepared</h2>' +
        '<p>There is no published names snapshot yet. The separate name-stats workflow can be started manually, and it then refreshes every day.</p>' +
        '<p><a class="secondary-button" href="https://github.com/drachescript/spicychat-archive/actions/workflows/name-trends.yml">Open name-stats workflow ↗</a></p></section>';
      return;
    }
    const c = stats.coverage;
    const generated = new Date(stats.generatedAt);
    const updated = Number.isNaN(generated.getTime()) ? 'Unknown' : generated.toLocaleString();
    container.innerHTML = '<div class="name-coverage">Last snapshot: <b>' + esc(updated) + '</b>. Names include archived/deleted records; title patterns use only bots with creator index data.</div>' +
      '<div class="stats-grid">' +
      '<div class="stat"><b>' + fmt(c.nameIndexedBots) + '</b><span>Archived bot records indexed for names</span></div>' +
      '<div class="stat"><b>' + fmt(stats.distinctNames) + '</b><span>Distinct normalized names</span></div>' +
      '<div class="stat"><b>' + fmt(stats.oneUseNames) + '</b><span>Names used by one archived bot</span></div>' +
      '<div class="stat"><b>' + fmt(c.titleIndexedBots) + '</b><span>Bot records checked for title trends</span></div>' +
      '</div>' +
      '<div class="name-stats-grid">' +
      '<section class="table-card"><h2>How many bots have this name?</h2>' +
      '<p class="filter-note">Exact name match, case-insensitive. This is a count from the Archive snapshot, not the public SpicyChat catalog.</p>' +
      '<form id="check-name" class="name-check-form"><input id="name-query" type="search" maxlength="100" placeholder="Try Luna, Alex, Dragon…" required aria-label="Check a bot name">' +
      '<button class="primary-button" type="submit">Check name</button></form><div class="name-check-result" id="name-result" aria-live="polite"></div>' +
      '<h3>Names not found in the Archive</h3><p class="filter-note">No name is globally “unused” forever. You can check your own idea, or generate a few fictional-name suggestions with zero matches right now.</p>' +
      '<button id="generate-names" class="secondary-button" type="button">Find zero-match name ideas</button><div id="name-ideas" aria-live="polite"></div></section>' +
      '<section class="table-card"><h2>Most-used bot names</h2><p class="filter-note">Each bot ID counts once. Case and extra spacing are ignored when matching names.</p>' +
      statsTable(stats.topNames, 'No names counted yet.', { link: true, limit: 40 }) + '</section></div>' +
      '<div class="name-stats-grid">' +
      '<section class="table-card"><h2>Repeated bot titles</h2><p class="filter-note">Exact repeated title text after capitalization and whitespace normalization. A repeated title alone does not establish copied content.</p>' +
      statsTable(stats.topRepeatedTitles, 'No repeated titles found.', { link: true, limit: 35 }) + '</section>' +
      '<section class="table-card"><h2>Situations mentioned in titles</h2><p class="filter-note">Simple English keyword matches, not AI reading or comparing actual scenario fields. One bot can match several situations.</p>' +
      themeRows(stats.titleSituations) + '</section></div>' +
      '<section class="table-card"><h2>Common tags on creator-indexed bots</h2><p class="filter-note">These reflect stored SpicyChat tags. Unlike the title-pattern groups, they are creator-selected labels.</p>' +
      statsTable(stats.topTagsFromCreatorIndex, 'No tag data available.', { limit: 30 }) + '</section>' +
      '<p class="filter-note name-coverage">Name counts only include the Archive\\'s currently indexed bot records, and names may be missing or old. The title/trend section covers the subset with creator metadata; non-English titles may not match the English theme patterns. All statistics update with the published daily snapshot.</p>';

    const form = document.getElementById('check-name');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const raw = document.getElementById('name-query').value.trim();
      const result = document.getElementById('name-result');
      result.textContent = 'Checking…';
      try {
        const row = await findName(raw);
        if (row.invalid) {
          result.textContent = 'Enter a meaningful name to check.';
          return;
        }
        if (row.count > 0) {
          result.innerHTML = '<b>' + fmt(row.count) + '</b> archived bot' + (row.count === 1 ? '' : 's') +
            ' with the name <b>' + esc(row.display) + '</b>.' +
            ' <a href="../bots/?q=' + encodeURIComponent(raw) + '">Search public bots ↗</a>';
        } else {
          result.innerHTML = '<b>0 matches</b> for <b>' + esc(raw) +
            '</b> in the indexed Archive. That does <em>not</em> guarantee the name is unused on SpicyChat.';
        }
      } catch (error) {
        result.textContent = 'Name lookup failed: ' + error.message;
      }
    });
    document.getElementById('generate-names').addEventListener('click', showIdeas);
  }
  main().catch(error => { container.textContent = 'Could not load name stats: ' + error.message; });
})();
