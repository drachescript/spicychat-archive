(() => {
  const fmt = value => {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString() : '—';
  };
  const set = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = fmt(value);
  };
  const status = document.getElementById('snapshot-status');
  const foot = status?.closest('.snapshot-foot');

  async function json(url) {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  async function load() {
    try {
      const [manifest, runtime] = await Promise.all([
        json('/data/manifest.json'),
        json('/data/runtime.json')
      ]);

      const trackedBots = Number(manifest.totalBots) || 0;
      const publicBots = Number(manifest.publicIndexBots ?? manifest.activeBots) || 0;
      const deletedBots = Number(manifest.deletedBots) || 0;
      const published = manifest.reconciliation || {};
      // The previously displayed 2k+ "unreconciled" value was an arithmetic
      // gap between a sitewide *live* public count and distinct archived bot
      // IDs. It is NOT a list of specific unverified IDs or deletion candidates.
      // Use the actual queued per-ID missing checks instead.
      const pendingChecks = Math.max(0, Number(published.pendingVerificationBots ?? manifest.missingBots) || 0);

      const gap = Number(published.countGap);
      const base = String(runtime.publicDataBaseUrl || '').replace(/\/$/, '');
      let audit = null;
      if (base) {
        try {
          audit = await json(base + '/indexes/bot-status-audit.json');
          if (audit.schemaVersion !== 1) audit = null;
        } catch { /* Audit may not have published yet. */ }
      }
      set('stat-bots-indexed', audit?.materializedBotIndexCount ?? null);
      set('stat-bots-count-gap', Number.isFinite(gap) ? Math.abs(gap) : null);
      set('stat-bots-total', trackedBots);
      set('stat-bots-public', publicBots);
      set('stat-bots-deleted', deletedBots);
      set('stat-bots-verification-pending', pendingChecks);

      const reconcile = document.getElementById('snapshot-reconcile');
      if (reconcile) {
        const delta = Number.isFinite(gap) ? fmt(Math.abs(gap)) : 'an unknown number';
        const indexed = audit?.materializedBotIndexCount;
        reconcile.textContent =
          delta + ' is an arithmetic gap between historical Archive IDs, ' +
          'SpicyChat\'s current public count and confirmed-gone records — not ' +
          'a list of identifiable missing bots. ' +
          fmt(pendingChecks) + ' bot IDs are queued for explicit verification, ' +
          'but this is not an exhaustive audit of all recorded IDs. ' +
          (Number.isInteger(indexed)
            ? fmt(indexed) + ' bot records are represented by the materialized index (snapshot ' +
              String(audit.materializedIndexGeneratedAt || 'date unknown') + '). '
            : 'The independent materialized-record audit has not published yet. ') +
          'An old observation is not proof of present availability or deletion.';
      }

      if (base) {
        try {
          const lorebooks = await json(base + '/indexes/lorebooks.json');
          const lorebookTotal = Number(lorebooks.totalArchived) || 0;
          const lorebookPublic = Number(lorebooks.publicNow) || 0;
          const lorebookGone = Number(lorebooks.notPublic) || 0;
          const lorebookGap = Math.max(0, lorebookTotal - lorebookPublic - lorebookGone);
          set('stat-lorebooks-total', lorebookTotal);
          set('stat-lorebooks-public', lorebookPublic);
          set('stat-lorebooks-gone', lorebookGone);
          set('stat-lorebooks-unreconciled', lorebookGap);
        } catch {
          // Keep bot totals usable even if the separate Lorebook index is unavailable.
        }
      }

      if (status) {
        const when = manifest.generatedAt ? new Date(manifest.generatedAt) : null;
        status.textContent = when && !Number.isNaN(when.getTime())
          ? 'Bot index updated ' + when.toLocaleString()
          : 'Current archive data loaded';
      }
    } catch {
      if (status) status.textContent = 'Archive totals are temporarily unavailable';
      foot?.classList.add('error');
    }
  }

  void load();
})();
