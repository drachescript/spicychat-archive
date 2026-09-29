(() => {
  'use strict';

  const page = document.body?.dataset?.page || 'browse';
  const TAG_PREFS_KEY = 'sca-tag-filters-v1';
  const HIDDEN_BOTS_KEY = 'sca-hidden-bots-v1';
  let statsPromise = null;
  let ageTimer = null;
  let queued = false;
  let hiddenPanelSignature = '';

  const textMap = new Map([
    ['Bots captured by the archive', "Bots I've saved so far"],
    ['Observed archive growth pace', "How fast it's growing right now"],
    ["Public bots reported by SpicyChat's index", 'Public bots SpicyChat is reporting'],
    ['Archive site age', 'Archive has been live for'],
    ['Captured in the last ~24h window', 'Added in the last 24h'],
    ['Captured in the last ~7d window', 'Added in the last 7d'],
    ['Confirmed deleted / archived', 'Bots confirmed gone / archived'],
    ['Current supported SpicyChat tags', 'Tags available in filters'],
    ['next adaptive discovery budget', 'next normal scan'],
    ['configured cap', 'largest manual test allowed'],
    ['discovery time ceiling', 'hard stop for discovery'],
    ['page after healthy run', 'automatic page increase'],
    ['new bots last scan', 'new bots in the last batch'],
    ['pages completed', 'pages finished'],
    ['archive storage', 'archive storage used'],
    ['R2 objects', 'files / objects in R2'],
    ['Class A this month', 'R2 writes this month'],
    ['Class B this month', 'R2 reads this month'],
    ['enriched last scan', 'bots enriched last batch'],
    ['images archived last scan', 'images saved last batch'],
  ]);

  const esc = (value = '') => String(value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));

  function getStats() {
    if (!statsPromise) {
      statsPromise = fetch('/data/stats.json', { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null)
        .catch(() => null);
    }
    return statsPromise;
  }

  function exactAge(start) {
    const started = new Date(start).getTime();
    if (!Number.isFinite(started)) return 'Unknown';
    let minutes = Math.max(0, Math.floor((Date.now() - started) / 60000));
    const days = Math.floor(minutes / 1440); minutes %= 1440;
    const hours = Math.floor(minutes / 60); minutes %= 60;
    const chunks = [];
    if (days) chunks.push(`${days}d`);
    chunks.push(`${String(hours).padStart(2, '0')}h`);
    chunks.push(`${String(minutes).padStart(2, '0')}m`);
    return chunks.join(' ');
  }

  function startExactAgeCounter() {
    if (page !== 'stats' || ageTimer) return;
    getStats().then(stats => {
      if (!stats?.startedAt) return;
      const update = () => {
        const label = [...document.querySelectorAll('.stat span')]
          .find(el => el.textContent.trim() === 'Archive has been live for');
        const value = label?.closest('.stat')?.querySelector('b');
        if (!value) return;
        value.id = 'archive-live-age';
        value.textContent = exactAge(stats.startedAt);
        value.title = `Started ${new Date(stats.startedAt).toLocaleString()}`;
      };
      update();
      ageTimer = setInterval(update, 60000);
    });
  }

  function humanizeCopy() {
    if (page === 'browse') {
      const hero = document.querySelector('.hero p');
      if (hero && !hero.dataset.humanized) {
        hero.textContent = "I'm building a public archive of SpicyChat bots so older versions and bots that disappear don't just vanish. Right now I'm focusing on finding as much of the public catalog as possible.";
        hero.dataset.humanized = '1';
      }
      const growth = document.querySelector('.growth-main span');
      if (growth) growth.textContent = ' at the pace it has been going so far';
      const results = document.querySelector('.results-head h2');
      if (results?.textContent.trim() === 'Public bots') results.textContent = 'Bots on SpicyChat right now';
    }

    if (page === 'deleted') {
      const hero = document.querySelector('.hero p');
      if (hero && !hero.dataset.humanized) {
        hero.textContent = "Bots only end up here after I can actually confirm they're gone from the public character API. I keep the last public version the archive saw.";
        hero.dataset.humanized = '1';
      }
      const results = document.querySelector('.results-head h2');
      if (results?.textContent.trim() === 'Deleted / archived') results.textContent = 'Bots confirmed gone';
    }

    if (page === 'stats') {
      const hero = document.querySelector('.hero p');
      if (hero && !hero.dataset.humanized) {
        hero.textContent = "Here's how fast the archive is growing, what the latest batches found, and how the crawler and storage are doing.";
        hero.dataset.humanized = '1';
      }
      document.querySelectorAll('.stat span, .recent-run span').forEach(el => {
        const replacement = textMap.get(el.textContent.trim());
        if (replacement) el.textContent = replacement;
      });
      document.querySelectorAll('.table-card h2').forEach(h => {
        const t = h.textContent.trim();
        if (t === 'Discovery right now') h.textContent = 'Crawler right now';
        else if (t === 'R2 / crawler') h.textContent = 'Storage + crawler';
        else if (t === 'Latest number updates') h.textContent = 'Latest batches';
        else if (t === 'Recent archive runs') h.textContent = 'Recent batches';
      });
      document.querySelectorAll('.stats-table th').forEach(th => {
        const t = th.textContent.trim();
        if (t === 'Archive Δ') th.textContent = 'Added';
        else if (t === 'New discovered') th.textContent = 'New bots';
        else if (t === 'Total runtime') th.textContent = 'Took';
      });
      startExactAgeCounter();
    }
  }

  function setupFilterToggle() {
    if (page !== 'browse' && page !== 'deleted') return;
    const layout = document.querySelector('.layout');
    const filters = document.querySelector('#filters');
    const results = document.querySelector('.results');
    const toolbar = results?.querySelector('.toolbar');
    if (!layout || !filters || !results || !toolbar) return;

    let button = document.querySelector('#archive-filter-toggle');
    if (!button) {
      button = document.createElement('button');
      button.id = 'archive-filter-toggle';
      button.type = 'button';
      button.className = 'secondary-button filter-panel-toggle';
      toolbar.before(button);
    }

    const stored = localStorage.getItem('sca-filters-open');
    const defaultOpen = window.matchMedia('(min-width: 981px)').matches;
    let open = stored == null ? defaultOpen : stored !== '0';

    const apply = () => {
      layout.classList.toggle('filters-collapsed', !open);
      filters.classList.toggle('open', open);
      button.textContent = open ? 'Hide filters' : 'Show filters';
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
      localStorage.setItem('sca-filters-open', open ? '1' : '0');
    };

    if (!button.dataset.bound) {
      button.addEventListener('click', () => { open = !open; apply(); });
      button.dataset.bound = '1';
    }
    apply();
  }

  function parseList(value) {
    return String(value || '').split(',').map(x => x.trim()).filter(Boolean);
  }

  function saveTagPreferences() {
    if (page !== 'browse' && page !== 'deleted') return;
    try {
      const params = new URLSearchParams(location.search);
      const include = parseList(params.get('include'));
      let exclude;
      if (!params.has('exclude')) exclude = ['NTR', 'Cheating'];
      else if (params.get('exclude') === 'none' || params.get('exclude') === '') exclude = [];
      else exclude = parseList(params.get('exclude'));
      const match = params.get('match') === 'any' ? 'any' : 'all';
      localStorage.setItem(TAG_PREFS_KEY, JSON.stringify({ include, exclude, match }));
    } catch {}
  }

  function setupTagPersistence() {
    if (page !== 'browse' && page !== 'deleted') return;
    const filters = document.querySelector('#filters');
    if (!filters || filters.dataset.prefsBound) return;
    const saveAfterApp = () => setTimeout(saveTagPreferences, 0);
    filters.addEventListener('click', saveAfterApp);
    filters.addEventListener('change', saveAfterApp);
    filters.dataset.prefsBound = '1';
    saveTagPreferences();
  }

  function readHiddenBots() {
    try {
      const value = JSON.parse(localStorage.getItem(HIDDEN_BOTS_KEY) || '{}');
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  }

  function writeHiddenBots(hidden) {
    try { localStorage.setItem(HIDDEN_BOTS_KEY, JSON.stringify(hidden)); } catch {}
  }

  function cardId(card) {
    const href = card?.querySelector('a.cardlink')?.getAttribute('href');
    if (!href) return '';
    try { return (new URL(href, location.href).searchParams.get('id') || '').toLowerCase(); }
    catch { return ''; }
  }

  function hideCard(card) {
    const id = cardId(card);
    if (!id) return;
    const hidden = readHiddenBots();
    hidden[id] = {
      id,
      name: card.querySelector('.body h3')?.textContent?.trim() || 'Unknown bot',
      creator: card.querySelector('.creator')?.textContent?.trim() || '',
      hiddenAt: new Date().toISOString(),
    };
    writeHiddenBots(hidden);
    card.remove();
    hiddenPanelSignature = '';
    renderHiddenManager();
    updateBrowseCount();
  }

  function restoreHidden(id) {
    const hidden = readHiddenBots();
    if (!hidden[id]) return;
    delete hidden[id];
    writeHiddenBots(hidden);
    location.reload();
  }

  function restoreAllHidden() {
    writeHiddenBots({});
    location.reload();
  }

  function renderHiddenManager() {
    if (page !== 'browse' && page !== 'deleted') return;
    const scroll = document.querySelector('#filters .filter-scroll');
    if (!scroll) return;

    const hidden = readHiddenBots();
    const rows = Object.values(hidden)
      .filter(row => row && row.id)
      .sort((a, b) => String(b.hiddenAt || '').localeCompare(String(a.hiddenAt || '')));
    const signature = JSON.stringify(rows.map(row => [row.id, row.name, row.creator]));
    if (signature === hiddenPanelSignature && document.querySelector('#local-hidden-section')) return;
    hiddenPanelSignature = signature;

    let section = document.querySelector('#local-hidden-section');
    if (!section) {
      section = document.createElement('section');
      section.id = 'local-hidden-section';
      section.className = 'filter-section local-hidden-section';
      scroll.append(section);
    }

    if (!rows.length) {
      section.innerHTML = '<h3>Hidden bots</h3><div class="filter-note">Nothing hidden on this browser.</div>';
      return;
    }

    const list = rows.slice(0, 200).map(row => `
      <div class="hidden-bot-row">
        <div class="hidden-bot-name"><b>${esc(row.name || 'Unknown bot')}</b>${row.creator ? `<span>${esc(row.creator)}</span>` : ''}</div>
        <button type="button" class="link-button restore-hidden-bot" data-hidden-id="${esc(row.id)}">Restore</button>
      </div>`).join('');
    const extra = rows.length > 200 ? `<div class="filter-note">Showing the newest 200 of ${rows.length.toLocaleString()} hidden bots. Restore all will clear the full list.</div>` : '';
    section.innerHTML = `
      <div class="hidden-bots-head"><h3>Hidden bots</h3><button type="button" class="link-button" id="restore-all-hidden">Restore all</button></div>
      <div class="filter-note">${rows.length.toLocaleString()} hidden on this browser. This never changes the public archive.</div>
      <details class="hidden-bots-details"><summary>Manage hidden bots</summary><div class="hidden-bot-list">${list}</div>${extra}</details>`;
  }

  function applyLocalHiding() {
    if (page !== 'browse' && page !== 'deleted') return;
    const hidden = readHiddenBots();
    document.querySelectorAll('#grid .card').forEach(card => {
      const id = cardId(card);
      if (!id) return;
      if (hidden[id]) {
        card.remove();
        return;
      }
      const art = card.querySelector('.art');
      if (!art || art.querySelector('.local-hide-bot')) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'local-hide-bot';
      button.textContent = '×';
      const name = card.querySelector('.body h3')?.textContent?.trim() || 'this bot';
      button.setAttribute('aria-label', `Hide ${name} on this browser`);
      button.title = 'Hide this bot on this browser';
      art.append(button);
    });
    renderHiddenManager();
  }

  function removeNonDeletedBadges() {
    if (page !== 'browse' && page !== 'deleted') return;
    document.querySelectorAll('#grid .badge:not(.deleted)').forEach(badge => badge.remove());
  }

  function updateBrowseCount() {
    if (page !== 'browse') return;
    const count = document.querySelector('#result-count');
    const grid = document.querySelector('#grid');
    if (!count || !grid) return;

    const raw = count.textContent.trim();
    const original = raw.match(/^([\d,]+)\s+matches\s+·\s+([\d,]+)\s+loaded$/i);
    if (original) {
      count.dataset.liveFound = original[1].replaceAll(',', '');
      count.dataset.loaded = original[2].replaceAll(',', '');
    }

    const visible = grid.querySelectorAll('.card').length;
    const hiddenCount = Object.keys(readHiddenBots()).length;
    getStats().then(stats => {
      if (!count.isConnected) return;
      const archiveTotal = Number(stats?.totalBots || 0);
      let text = `${visible.toLocaleString()} shown`;
      if (archiveTotal) text += ` · ${archiveTotal.toLocaleString()} saved in archive`;
      if (hiddenCount) text += ` · ${hiddenCount.toLocaleString()} hidden here`;
      if (count.textContent !== text) count.textContent = text;

      const liveFound = Number(count.dataset.liveFound || 0);
      if (liveFound && archiveTotal && liveFound !== archiveTotal) {
        count.title = `SpicyChat's live public index reports ${liveFound.toLocaleString()} matching bots. The archive has saved ${archiveTotal.toLocaleString()} so far.`;
      } else {
        count.removeAttribute('title');
      }
    });
  }

  function improveImages() {
    document.querySelectorAll('.card .art img').forEach(img => {
      img.decoding = 'async';
      img.fetchPriority = 'low';
    });
  }

  function bindGlobalActions() {
    if (document.documentElement.dataset.archiveLocalActionsBound) return;
    document.addEventListener('click', event => {
      const hide = event.target.closest('.local-hide-bot');
      if (hide) {
        event.preventDefault();
        event.stopPropagation();
        hideCard(hide.closest('.card'));
        return;
      }
      const restore = event.target.closest('.restore-hidden-bot');
      if (restore) {
        event.preventDefault();
        restoreHidden(restore.dataset.hiddenId || '');
        return;
      }
      if (event.target.closest('#restore-all-hidden')) {
        event.preventDefault();
        restoreAllHidden();
      }
    }, true);
    document.documentElement.dataset.archiveLocalActionsBound = '1';
  }

  function apply() {
    humanizeCopy();
    setupFilterToggle();
    setupTagPersistence();
    removeNonDeletedBadges();
    applyLocalHiding();
    improveImages();
    updateBrowseCount();
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      apply();
    });
  }

  bindGlobalActions();
  const observer = new MutationObserver(schedule);
  observer.observe(document.querySelector('#app') || document.body, { childList: true, subtree: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();
})();
