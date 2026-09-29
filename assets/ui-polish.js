(() => {
  'use strict';

  const page = document.body?.dataset?.page || 'browse';
  let statsPromise = null;
  let ageTimer = null;
  let queued = false;

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
    let seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const days = Math.floor(seconds / 86400); seconds %= 86400;
    const hours = Math.floor(seconds / 3600); seconds %= 3600;
    const minutes = Math.floor(seconds / 60); seconds %= 60;
    const chunks = [];
    if (days) chunks.push(`${days}d`);
    chunks.push(`${String(hours).padStart(2, '0')}h`);
    chunks.push(`${String(minutes).padStart(2, '0')}m`);
    chunks.push(`${String(seconds).padStart(2, '0')}s`);
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
      ageTimer = setInterval(update, 1000);
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

  function improveImages() {
    document.querySelectorAll('.card .art img').forEach(img => {
      img.decoding = 'async';
      img.fetchPriority = 'low';
    });
  }

  function apply() {
    humanizeCopy();
    setupFilterToggle();
    improveImages();
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      apply();
    });
  }

  const observer = new MutationObserver(schedule);
  observer.observe(document.querySelector('#app') || document.body, { childList: true, subtree: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();
})();
