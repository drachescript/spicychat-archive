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
      let inventory = null;
      if (base) {
        try {
          const report = await json(base + '/indexes/discovery-id-audit.json');
          if (report.schemaVersion === 2 && report.complete === true && report.readOnly === true) inventory = report;
        } catch { /* Keep the status report as a fallback. */ }
      }
      set('stat-bots-indexed', inventory?.materializedIndexIds ?? audit?.materializedBotIndexCount ?? null);
      set('stat-bots-count-gap', Number.isFinite(gap) ? Math.abs(gap) : null);
      set('stat-bots-total', trackedBots);
      set('stat-bots-public', publicBots);
      set('stat-bots-deleted', deletedBots);
      set('stat-bots-verification-pending', pendingChecks);

      const reconcile = document.getElementById('snapshot-reconcile');
      if (reconcile) {
         const gapText = Number.isFinite(gap) ? fmt(Math.abs(gap)) : 'Some';
        const source = inventory || audit;
        const indexed = inventory?.materializedIndexIds ?? audit?.materializedBotIndexCount;
        const indexedAt = inventory?.materializedIndexGeneratedAt || audit?.materializedIndexGeneratedAt;
        reconcile.textContent =
          gapText + ' is the difference when we compare the Archive’s old ID total with ' +
          'SpicyChat’s current public total and bots confirmed gone. ' +
          'It does not identify missing or deleted bots. ' +
          fmt(pendingChecks) + ' individual bot IDs are currently queued for status checks; ' +
          'that is not a check of every recorded ID. ' +
          (Number.isInteger(indexed) ? fmt(indexed) + ' records are searchable in the saved Archive index' +
            (indexedAt ? ' (checked ' + new Date(indexedAt).toLocaleString() + ')' : '') + '. ' : '') +
          'Seeing a bot in the past does not prove it is still public or has been deleted.';
      }

      const inventoryStatus = document.getElementById('snapshot-audit');
      if (inventoryStatus) {
        if (inventory) {
          const items = [
            fmt(inventory.storedBotFileIds) + ' bot files saved',
            fmt(inventory.observedWithoutStoredFile) + ' IDs seen but not saved',
            fmt(inventory.storedFileWithoutIndex) + ' saved files missing from search',
            fmt(inventory.indexedWithoutStoredFile) + ' search entries without a saved file'
          ];
          inventoryStatus.replaceChildren();
          inventoryStatus.append(document.createTextNode('File check: ' + items.join(' · ') + '. Not having a saved file does not mean a bot is deleted. '));
          const link = document.createElement('a');
          link.href = base + '/indexes/discovery-id-audit.json';
          link.textContent = 'Audit details';
          link.rel = 'noopener noreferrer';
          inventoryStatus.append(link);
        } else {
          inventoryStatus.textContent = 'The complete file check is not available yet.';
        }
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
