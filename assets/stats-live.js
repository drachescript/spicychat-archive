(() => {
  'use strict';

  const page = document.body?.dataset?.page || 'browse';
  let dataPromise = null;
  let queued = false;

  const fmt = value => Math.round(Number(value || 0)).toLocaleString();
  const signed = value => {
    const n = Math.round(Number(value || 0));
    if (n > 0) return `+${n.toLocaleString()}`;
    if (n < 0) return `−${Math.abs(n).toLocaleString()}`;
    return '0';
  };
  const shortDate = value => {
    const date = new Date(value || 0);
    return Number.isNaN(date.getTime())
      ? 'Unknown'
      : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };
  const fullDate = value => {
    const date = new Date(value || 0);
    return Number.isNaN(date.getTime())
      ? 'Unknown'
      : date.toLocaleString(undefined, {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
          hour: 'numeric',
          minute: '2-digit'
        });
  };
  const archiveAdded = row => Number(row?.addedSincePrevious ?? row?.exploration?.new ?? 0) || 0;
  const discovered = row => Number(row?.exploration?.new ?? 0) || 0;
  const displayableArchiveRun = row =>
    row?.kind === 'archive-run' && (archiveAdded(row) > 0 || discovered(row) > 0);
  const displayArchiveRuns = stats => (stats?.runs || []).filter(displayableArchiveRun);

  async function fetchJson(path) {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function epoch(value) {
    const time = new Date(value || 0).getTime();
    return Number.isFinite(time) && time > 0 ? time / 1000 : null;
  }

  function derivePeriod(rows, hours) {
    if (rows.length < 2) return { hours, net: null, complete: false, spanHours: 0, baselineAt: null };
    const latest = rows.at(-1);
    const cutoff = latest.time - hours * 3600;
    const before = rows.slice(0, -1).filter(row => row.time <= cutoff);
    const baseline = before.at(-1) || rows[0];
    return {
      hours,
      net: latest.count - baseline.count,
      complete: baseline.time <= cutoff,
      spanHours: Math.max(0, (latest.time - baseline.time) / 3600),
      baselineAt: baseline.at
    };
  }

  function derivePublicGrowth(stats) {
    const rows = (stats?.runs || [])
      .filter(row => row?.kind === 'archive-run' && Number(row.publicIndexBots) > 0 && epoch(row.at))
      .map(row => ({ at: row.at, time: epoch(row.at), count: Number(row.publicIndexBots) }))
      .sort((a, b) => a.time - b.time);
    if (!rows.length) return null;

    const first = rows[0], latest = rows.at(-1);
    const observedHours = Math.max(0, (latest.time - first.time) / 3600);
    const observedNet = latest.count - first.count;
    const periods = {
      '24h': derivePeriod(rows, 24),
      '7d': derivePeriod(rows, 24 * 7),
      '30d': derivePeriod(rows, 24 * 30)
    };
    const paceWindow = periods['24h'].complete ? periods['24h'] : null;
    const averagePerDay = paceWindow?.spanHours > 0
      ? paceWindow.net / (paceWindow.spanHours / 24)
      : observedHours > 0 ? observedNet / (observedHours / 24) : 0;

    return {
      schemaVersion: 1,
      generatedAt: stats?.generatedAt || latest.at,
      sampleCount: rows.length,
      latestPublicIndexBots: latest.count,
      firstSampleAt: first.at,
      lastSampleAt: latest.at,
      observedHours,
      observedNet,
      averagePerDay,
      periods
    };
  }

  function loadData() {
    if (dataPromise) return dataPromise;
    dataPromise = Promise.all([
      fetchJson('/data/stats.json').catch(() => null),
      fetchJson('/data/public-growth.json').catch(() => null)
    ]).then(([stats, publicGrowth]) => ({
      stats,
      publicGrowth: publicGrowth || stats?.publicGrowth || derivePublicGrowth(stats)
    }));
    return dataPromise;
  }

  function setText(element, value) {
    if (element && element.textContent !== value) element.textContent = value;
  }

  function periodText(growth, key, fallbackLabel) {
    const period = growth?.periods?.[key];
    if (!period || period.net == null) return { value: '—', label: fallbackLabel };
    if (period.complete) return { value: signed(period.net), label: fallbackLabel };
    return {
      value: signed(growth.observedNet),
      label: "Net public bot change since I started tracking"
    };
  }

  function findCard(title) {
    return [...document.querySelectorAll('.table-card')]
      .find(card => card.querySelector('h2')?.textContent.trim() === title);
  }

  function patchBatchHistory(stats) {
    const recent = displayArchiveRuns(stats).slice(-10).reverse();
    if (page === 'stats') {
      const card = findCard('Recent batches') || findCard('Recent archive runs');
      const runTable = card?.querySelector('tbody');
      if (runTable) {
        const html = recent.length ? recent.map(row => `<tr><td>${fullDate(row.at)}</td><td>+${fmt(archiveAdded(row))}</td><td>${fmt(discovered(row))}</td><td>${fmt(row.exploration?.pagesCompleted)}/${fmt(row.exploration?.pageBudget)}</td><td>${row.runDurationSeconds ? `${Math.round(row.runDurationSeconds / 60)}m` : '—'}</td></tr>`).join('') : '<tr><td colspan="5">No batches with new bots yet.</td></tr>';
        if (runTable.innerHTML !== html) runTable.innerHTML = html;
      }
    }
    if (page === 'browse' || page === 'deleted') {
      const updates = document.querySelector('.growth-updates');
      if (updates) {
        const recentThree = displayArchiveRuns(stats).slice(-3).reverse();
        const html = recentThree.length ? recentThree.map(row => `<span>${shortDate(row.at)} <b>+${fmt(archiveAdded(row))}</b></span>`).join('') : '<span>Growth history starts when a batch archives new bots.</span>';
        if (updates.innerHTML !== html) updates.innerHTML = html;
      }
    }
  }

  function patchStats(stats, growth) {
    if (page !== 'stats' || !growth) return;
    const cards = [...document.querySelectorAll('.stats-grid .stat')];
    if (cards.length < 8) return;

    const pace = signed(growth.averagePerDay);
    setText(cards[1].querySelector('b'), `${pace}/day`);
    setText(cards[1].querySelector('span'), "How SpicyChat's public bot count is moving");
    cards[1].title = 'This follows SpicyChat’s reported public index, not how quickly I backfilled the archive.';

    const day = periodText(growth, '24h', 'Net public bot change in the last 24h');
    setText(cards[4].querySelector('b'), day.value);
    setText(cards[4].querySelector('span'), day.label);
    cards[4].title = 'Net change in SpicyChat’s reported public bot count.';

    const week = periodText(growth, '7d', 'Net public bot change in the last 7d');
    setText(cards[5].querySelector('b'), week.value);
    setText(cards[5].querySelector('span'), week.label);
    cards[5].title = growth.periods?.['7d']?.complete
      ? 'Net change in SpicyChat’s reported public bot count over the last seven days.'
      : `I only have ${Number(growth.observedHours || 0).toFixed(1)} hours of public-index history so far, so I’m showing the full tracked window instead of pretending it is seven days.`;

    // A manual 750/1000-page test should never make the page claim that is the
    // normal scheduled target. The checked-in base target is authoritative.
    const normalScan = [...document.querySelectorAll('.recent-run span')]
      .find(el => el.textContent.trim() === 'next normal scan');
    const normalValue = normalScan?.parentElement?.querySelector('b');
    const basePages = Number(stats?.adaptiveDiscovery?.basePages || 0);
    if (normalValue && basePages) setText(normalValue, `${fmt(basePages)} pages`);
  }

  function patchGrowthBanner(stats, growth) {
    if (!growth || (page !== 'browse' && page !== 'deleted')) return;
    const main = document.querySelector('.growth-main');
    if (!main) return;

    const day = growth.periods?.['24h'];
    const publicText = day?.net != null
      ? day.complete
        ? `SpicyChat public bots: ${signed(day.net)} in the last 24h`
        : `SpicyChat public bots: ${signed(growth.observedNet)} since I started tracking`
      : `SpicyChat public bots: ${fmt(growth.latestPublicIndexBots)}`;
    const latestAdded = Number(stats?.latestAdded || 0);
    const archiveText = latestAdded
      ? ` · I saved ${signed(latestAdded)} archive records in the latest batch`
      : '';

    const desired = `${publicText}${archiveText}`;
    if (main.dataset.publicGrowthText === desired) return;
    main.dataset.publicGrowthText = desired;
    main.replaceChildren();
    const strong = document.createElement('b');
    strong.textContent = publicText;
    const detail = document.createElement('span');
    detail.textContent = archiveText;
    main.append(strong, detail);
    main.title = 'The public-bot number comes from SpicyChat’s live index. Archive additions are shown separately so backfill work is not mistaken for site growth.';
  }

  async function apply() {
    const { stats, publicGrowth } = await loadData();
    if (!stats || !publicGrowth) return;
    patchBatchHistory(stats);
    patchStats(stats, publicGrowth);
    patchGrowthBanner(stats, publicGrowth);
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      apply().catch(() => {});
    });
  }

  const observer = new MutationObserver(schedule);
  observer.observe(document.querySelector('#app') || document.body, { childList: true, subtree: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();
})();
