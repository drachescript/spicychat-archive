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

      const inventoryStatus = document.getElementById('snapshot-audit');
      if (inventoryStatus) {
        if (!base) {
          inventoryStatus.textContent = 'The complete stored-file audit is not available yet.';
        } else {
          try {
            const inventory = await json(base + '/indexes/discovery-id-audit.json');
            if (inventory.schemaVersion !== 2 || inventory.complete !== true || inventory.readOnly !== true)
              throw new Error('No complete published inventory');
            const stamped = new Date(inventory.generatedAt);
            const inventoryDate = !Number.isNaN(stamped.getTime()) ? stamped.toLocaleString() : 'unknown';
            // Each count is based on exact ID membership, not the unrelated
            // arithmetic difference between historical and public totals.
            const lines = [
              ['Stored bot JSON objects', inventory.storedBotFileIds],
              ['Observed IDs with saved bot JSON', inventory.observedWithStoredFile],
              ['Observed IDs without saved bot JSON', inventory.observedWithoutStoredFile],
              ['Saved bot JSON not in search index', inventory.observedWithoutIndexButWithStoredFile],
              ['Search-index IDs without saved bot JSON', inventory.indexedWithoutStoredFile]
            ];
            inventoryStatus.replaceChildren();
            const title = document.createElement('strong');
            title.textContent = 'Complete per-ID storage audit (' + inventoryDate + '): ';
            inventoryStatus.append(title);
            inventoryStatus.append(document.createTextNode(lines.map(([label, count]) => label + ': ' + fmt(count)).join(' · ')));
            inventoryStatus.append(document.createTextNode('. R2 file existence is not proof of live SpicyChat availability or of complete recoverable content. '));
            const link = document.createElement('a');
            link.href = base + '/indexes/discovery-id-audit.json';
            link.textContent = 'Full audit details';
            link.rel = 'noopener noreferrer';
            inventoryStatus.append(link);
          } catch {
            inventoryStatus.textContent = 'A complete per-ID stored-file audit has not published yet. Historical ID totals are not equivalent to recoverable bot records.';
          }
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
