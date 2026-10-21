(() => {
  'use strict';
  const BASE = 'https://data.spicychatarchive.drache.uk/indexes/';
  const root = document.getElementById('tag-stats-app');
  const cache = new Map();
  let stats = null;
  let history = null;
  const fmt = n => Number(n || 0).toLocaleString('en-US');
  const pct = (n, all) => all ? (100 * n / all).toFixed(2) + '%' : '—';
  const normalize = text => String(text || '').normalize('NFKC').replace(/\s+/gu, ' ').trim().toLowerCase();
  const esc = text => String(text ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function hash(text) {
    let h = 2166136261;
    for (const byte of new TextEncoder().encode(text)) {
      h ^= byte;
      h = Math.imul(h, 16777619) >>> 0;
    }
    return (h & 15).toString(16).padStart(2, '0');
  }
  async function fetchJson(path) {
    const response = await fetch(BASE + path, { cache: 'no-cache' });
    if (!response.ok) throw Error('HTTP ' + response.status);
    return response.json();
  }
  async function getTag(text) {
    const key = normalize(text);
    if (!key) return null;
    const shard = hash(key);
    if (!cache.has(shard)) {
      cache.set(shard, fetchJson('tag-stats/' + shard + '.json').then(data => {
        if (data.schemaVersion !== 1 || !data.tags) throw Error('Invalid tag lookup index');
        return data.tags;
      }).catch(err => { cache.delete(shard); throw err; }));
    }
    const tags = await cache.get(shard);
    return tags[key] || null;
  }
  function button(tag, label) {
    return '<button type="button" class="tag-stat-choice" data-tag="' + esc(tag) +
      '">' + esc(label == null ? tag : label) + '</button>';
  }
  function ranked(rows, empty, count) {
    if (!rows?.length) return '<p class="filter-note">' + esc(empty) + '</p>';
    return '<div class="tag-stats-rank">' + rows.slice(0, count || 35).map((row, i) =>
      '<div class="tag-stats-rank-row"><span class="tag-rank-number">' + (i + 1) + '.</span>' +
      button(row.tag, row.tag) + '<b>' + fmt(row.count) + '</b></div>'
    ).join('') + '</div>';
  }
  function combinationRows(rows) {
    if (!rows?.length) return '<p class="filter-note">No repeated tag pairs in this index.</p>';
    return '<div class="tag-stats-pair-list">' + rows.slice(0, 35).map(pair =>
      '<div class="tag-stats-pair-row">' + button(pair.a, pair.a) +
      '<span class="tag-stat-pair-plus">+</span>' + button(pair.b, pair.b) +
      '<button type="button" class="tag-stat-compare-pair" data-compare-a="' + esc(pair.a) +
      '" data-compare-b="' + esc(pair.b) + '" title="Compare this pair">' +
      fmt(pair.count) + ' together</button></div>'
    ).join('') + '</div>';
  }
  function creatorRows(rows) {
    if (!rows?.length) return '<p class="filter-note">No creator breakdown available for this tag.</p>';
    return '<div class="tag-stats-rank">' + rows.map(row =>
      '<div class="tag-stats-rank-row"><span></span><a href="../creator/?name=' +
      encodeURIComponent(row.name) + '">' + esc(row.name) + '</a><b>' +
      fmt(row.count) + '</b></div>'
    ).join('') + '</div>';
  }
  function trending(key, label) {
    const place = document.getElementById('tag-trends');
    if (!place) return;
    const snapshots = Array.isArray(history?.snapshots) ? history.snapshots : [];
    if (snapshots.length < 2) {
      place.innerHTML = '<p class="filter-note">Trend history is just starting. The first daily snapshot has been recorded; comparisons will appear after at least two different days have been published.</p>';
      return;
    }
    const recent = snapshots.slice(-30);
    const points = recent.map(row => ({
      date: String(row.date || ''),
      count: Number(row.counts?.[key] || 0)
    }));
    const max = Math.max(1, ...points.map(p => p.count));
    const first = points[0], last = points[points.length - 1];
    const delta = last.count - first.count;
    place.innerHTML = '<p class="filter-note">' + esc(label) + ' · ' + esc(first.date) +
      ' to ' + esc(last.date) + ' · ' + (delta >= 0 ? '+' : '') + fmt(delta) +
      ' archived bots with this currently recorded tag. These are actual daily Archive snapshots.</p>' +
      '<div class="tag-stats-trend-chart" role="img" aria-label="Historical daily Archive tag counts for ' +
      esc(label) + '">' + points.map(point =>
        '<div class="tag-stats-trend-column" title="' + esc(point.date) + ': ' + fmt(point.count) +
        '"><div style="height:' + Math.max(2, Math.round(point.count / max * 100)) +
        '%"></div></div>'
      ).join('') + '</div>' +
      '<div class="tag-stats-trend-axis"><span>' + esc(first.date) + '</span><span>' +
      esc(last.date) + '</span></div>';
  }
  const previousShardCache = new Map();
  let previousRequest = 0;
  async function getFormerTagBots(text) {
    const key = normalize(text);
    if(!key)return [];
    const shard = hash(key);
    if(!previousShardCache.has(shard)) {
      previousShardCache.set(shard, fetchJson('historical-tags/' + shard + '.json')
        .then(result => {
          if(result.schemaVersion !== 1 || !result.tags)throw Error('Historic tag lookup index is not ready');
          return result.tags;
        }).catch(error => { previousShardCache.delete(shard); throw error; }));
    }
    return (await previousShardCache.get(shard))[key]?.bots || [];
  }
  async function showFormerBots(tag) {
    const root = document.getElementById('former-tag-results');
    if(!root)return;
    const request = ++previousRequest;
    root.textContent = 'Looking up former tag assignments…';
    try {
      const bots = await getFormerTagBots(tag);
      if(request !== previousRequest)return;
      if(!bots.length) {
        root.innerHTML = '<p class="filter-note">No removed tag assignments for <b>' + esc(tag) +
          '</b> have been recovered yet. Last-known tag matches are listed separately above.</p>';
        return;
      }
      root.innerHTML =
        '<p class="filter-note">' + fmt(bots.length) +
        ' bot IDs were recorded with this tag previously and do not currently have it in their last-known tag fields. Data is limited to changes observed by the Archive.</p>' +
        '<div class="tag-stats-compare-form"><input id="former-tag-search" type="search" placeholder="Filter by bot name or creator" aria-label="Filter previously tagged bots">' +
        '<select id="former-tag-status" aria-label="Formerly tagged bot status"><option value="all">All statuses</option><option value="public">Public</option><option value="deleted">Deleted</option><option value="unknown">Unknown</option></select></div>' +
        '<p id="former-tag-count" class="filter-note"></p><div id="former-tag-list" class="historic-bot-results"></div>' +
        '<button id="former-tag-more" type="button" class="secondary-button">Show more</button>';
      const list = root.querySelector('#former-tag-list');
      const search = root.querySelector('#former-tag-search');
      const status = root.querySelector('#former-tag-status');
      const more = root.querySelector('#former-tag-more');
      const count = root.querySelector('#former-tag-count');
      let visible = 80;
      const render = () => {
        const q = normalize(search.value);
        const type = status.value;
        const filtered = bots.filter(row =>
          (type === 'all' || String(row.status || 'unknown').toLowerCase() === type) &&
          (!q || [row.id,row.name,row.creator,row.title].join(' ').toLowerCase().includes(q)));
        count.textContent = fmt(filtered.length) + ' historical bot matches';
        list.innerHTML = filtered.slice(0,visible).map(row =>
          '<div class="historic-bot-hit"><a href="../bot/?id=' + encodeURIComponent(row.id) + '"><b>' +
          esc(row.name || row.id) + '</b></a><span>' + esc(row.title || 'Archived character') +
          '</span><small>' + esc(row.creator ? '@' + row.creator + ' · ' + (row.status || 'unknown') : (row.status || 'unknown')) +
          '</small></div>'
        ).join('');
        more.hidden = filtered.length <= visible;
      };
      search.addEventListener('input',()=>{visible=80;render();});
      status.addEventListener('change',()=>{visible=80;render();});
      more.addEventListener('click',()=>{visible+=80;render();});
      render();
    } catch(error) {
      if(request !== previousRequest)return;
      root.textContent = 'Former-tag lookup is not ready: ' + error.message;
    }
  }

  async function showTag(raw) {
    const name = String(raw || '').trim();
    const outcome = document.getElementById('tag-lookup-outcome');
    if (!name) { outcome.textContent = 'Enter a tag to check.'; return; }
    outcome.textContent = 'Checking…';
    void showFormerBots(name);
    try {
      const item = await getTag(name);
      const key = normalize(name);
      if (!item) {
        outcome.innerHTML = '<h3>0 archived matches</h3><p>No bots have the tag <b>' +
          esc(name) + '</b> in this indexed snapshot. This does not prove the tag is unofficial or unused on SpicyChat.</p>';
        trending(key, name);
        return;
      }
      const percent = pct(item.count, stats.coverage.indexedBots);
      const related = Object.entries(item.withTags || {})
        .sort((a, b) => b[1] - a[1]).slice(0, 12)
        .map(pair => '<span class="tag-stat-related">' + button(pair[0], pair[0]) +
          ' <b>' + fmt(pair[1]) + '</b></span>').join('');
      outcome.innerHTML =
        '<h3>' + esc(item.tag) + '</h3><div class="tag-stat-figures">' +
        '<div><b>' + fmt(item.count) + '</b><span>archived bots</span></div>' +
        '<div><b>' + esc(percent) + '</b><span>of indexed bot IDs</span></div>' +
        '<div><b>' + fmt(item.creatorCount) + '</b><span>distinct indexed creators</span></div></div>' +
        '<h4>Frequently paired tags</h4>' + (related || '<p class="filter-note">No observed combinations.</p>') +
        '<h4>Top creators using this tag</h4>' + creatorRows(item.topCreators);
      trending(key, item.tag);
    } catch (err) {
      outcome.textContent = 'Tag lookup failed: ' + err.message;
    }
  }
  async function compareTags(a, b) {
    const outcome = document.getElementById('tag-compare-outcome');
    const [keyA, keyB] = [normalize(a), normalize(b)];
    if (!keyA || !keyB) { outcome.textContent = 'Enter two tags to compare.'; return; }
    if (keyA === keyB) { outcome.textContent = 'Choose two different tags.'; return; }
    outcome.textContent = 'Comparing…';
    try {
      const [left, right] = await Promise.all([getTag(a), getTag(b)]);
      const nA = Number(left?.count || 0), nB = Number(right?.count || 0);
      const both = Number(left?.withTags?.[keyB] || 0);
      const union = nA + nB - both;
      outcome.innerHTML =
        '<div class="tag-stat-figures">' +
        '<div><b>' + fmt(nA) + '</b><span>' + esc(left?.tag || a) + ' total</span></div>' +
        '<div><b>' + fmt(nB) + '</b><span>' + esc(right?.tag || b) + ' total</span></div>' +
        '<div><b>' + fmt(both) + '</b><span>both tags together</span></div>' +
        '<div><b>' + fmt(nA - both) + '</b><span>first tag only</span></div>' +
        '<div><b>' + fmt(nB - both) + '</b><span>second tag only</span></div>' +
        '<div><b>' + pct(both, union) + '</b><span>overlap (of either)</span></div></div>' +
        '<p class="filter-note">Tag assignments on individual archived bots, not user preference or audience percentages.</p>';
    } catch (err) {
      outcome.textContent = 'Comparison failed: ' + err.message;
    }
  }
  async function main() {
    try {
      stats = await fetchJson('tag-stats.json');
      if (stats.schemaVersion !== 1 || !stats.coverage?.indexedBots) throw Error('No complete statistics');
    } catch {
      root.innerHTML = '<section class="section"><h2>Tag statistics are being prepared</h2>' +
        '<p>These counts appear after the Archive’s tag-statistics builder first publishes its snapshot. The builder runs alongside daily name statistics.</p>' +
        '<a class="secondary-button" href="https://github.com/drachescript/spicychat-archive/actions/workflows/name-trends.yml">View stats-builder workflow</a></section>';
      return;
    }
    try { history = await fetchJson('tag-stats-history.json'); } catch { history = null; }
    const coverage = stats.coverage;
    const updated = new Date(stats.generatedAt);
    const updatedLabel = Number.isNaN(updated.getTime()) ? 'Unknown' : updated.toLocaleString();
    root.innerHTML =
      '<p class="filter-note tag-stats-provenance">Snapshot: <b>' + esc(updatedLabel) +
      '</b>. Counting last-known tag fields, not words in titles or scenarios. A single bot can have multiple tags.</p>' +
      '<div class="stats-grid">' +
      '<div class="stat"><b>' + fmt(coverage.indexedBots) + '</b><span>bot records indexed</span></div>' +
      '<div class="stat"><b>' + fmt(coverage.taggedBots) + '</b><span>bots with recorded tags</span></div>' +
      '<div class="stat"><b>' + fmt(stats.distinctTags) + '</b><span>different recorded tags</span></div>' +
      '<div class="stat"><b>' + fmt(stats.oneUseTags) + '</b><span>tags on only one archived bot</span></div></div>' +
      '<datalist id="all-archive-tags">' + (stats.allTagLabels || []).map(tag =>
        '<option value="' + esc(tag) + '"></option>').join('') + '</datalist>' +
      '<div class="name-stats-grid">' +
      '<section class="table-card"><h2>How many bots use this tag?</h2>' +
      '<p class="filter-note">Exact tag label; capitalization and extra spaces are ignored. A term with zero matches may still appear in titles or descriptions.</p>' +
      '<form id="tag-lookup-form" class="name-check-form"><input id="tag-lookup-input" maxlength="100" required list="all-archive-tags" placeholder="Enter a tag" aria-label="Search archived tag usage">' +
      '<button type="submit" class="primary-button">Check tag</button></form>' +
      '<div id="tag-lookup-outcome" class="tag-stat-details">Pick a tag to see its usage, creators and common combinations.</div>' +
      '<h3>Previously tagged bots</h3><p class="filter-note">See bots that used this tag earlier, even if their creators removed it. Includes deleted bots when the Archive has recorded the assignment.</p>' +
      '<div id="former-tag-results" aria-live="polite">Search a tag above to browse its archived historical matches.</div></section>' +
      '<section class="table-card"><h2>Compare tags</h2><p class="filter-note">Compare exactly two tags across the same indexed Archive snapshot.</p>' +
      '<form id="tag-compare-form" class="tag-stats-compare-form"><input id="tag-compare-a" list="all-archive-tags" maxlength="100" required placeholder="First tag">' +
      '<input id="tag-compare-b" list="all-archive-tags" maxlength="100" required placeholder="Second tag">' +
      '<button type="submit" class="primary-button">Compare</button></form>' +
      '<div id="tag-compare-outcome" class="tag-stat-details">Enter two recorded tags to compare.</div></section></div>' +
      '<div class="name-stats-grid">' +
      '<section class="table-card"><h2>Most-used tags</h2><p class="filter-note">Ranked by the number of unique archived bot IDs carrying each tag.</p>' +
      ranked(stats.topTags, 'No tags recorded.', 45) + '</section>' +
      '<section class="table-card"><h2>Least-used tags</h2><p class="filter-note">Rare recorded tags first. A tag with 0 matches is not listed here; use the lookup above.</p>' +
      ranked(stats.rareTags, 'No tags recorded.', 45) + '</section></div>' +
      '<div class="name-stats-grid">' +
      '<section class="table-card"><h2>Common tag combinations</h2><p class="filter-note">Pairs of tag fields found on the same archived bot; choose a pair to compare it.</p>' +
      combinationRows(stats.topPairs) + '</section>' +
      '<section class="table-card"><h2>Observed tag trends</h2><p class="filter-note">Daily Archive snapshots start with this feature, not with the first bot creation date. To inspect a trend, check a tag above.</p>' +
      '<div id="tag-trends">Choose a tag to view its recorded trend.</div></section></div>' +
      '<p class="filter-note tag-stats-provenance">Coverage: ' + fmt(coverage.indexedBots) +
      ' bot IDs represented in creator indexes, ' + fmt(coverage.tagAssignments) +
      ' recorded tag assignments, ' + fmt(coverage.indexedCreators) +
      ' creator entries. This index can omit bots with no creator association. Tags are not a substitute for judging how common a kink is among users.</p>';

    root.addEventListener('click', event => {
      const pair = event.target.closest('[data-compare-a]');
      if (pair) {
        const a = pair.dataset.compareA, b = pair.dataset.compareB;
        document.getElementById('tag-compare-a').value = a;
        document.getElementById('tag-compare-b').value = b;
        void compareTags(a, b);
        return;
      }
      const target = event.target.closest('[data-tag]');
      if (target) {
        document.getElementById('tag-lookup-input').value = target.dataset.tag;
        void showTag(target.dataset.tag);
      }
    });
    document.getElementById('tag-lookup-form').addEventListener('submit', event => {
      event.preventDefault();
      void showTag(document.getElementById('tag-lookup-input').value);
    });
    document.getElementById('tag-compare-form').addEventListener('submit', event => {
      event.preventDefault();
      void compareTags(document.getElementById('tag-compare-a').value,
        document.getElementById('tag-compare-b').value);
    });
    const initial = new URLSearchParams(location.search).get('tag');
    if (initial) {
      document.getElementById('tag-lookup-input').value = initial.slice(0, 100);
      void showTag(initial.slice(0, 100));
    }
  }
  void main().catch(error => { root.textContent = 'Tag statistics could not load: ' + error.message; });
})();
