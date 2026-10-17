(() => {
  const fmt = value => {
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

      set('stat-bots-total', trackedBots);
      set('stat-bots-public', publicBots);
      set('stat-bots-deleted', deletedBots);
      set('stat-bots-verification-pending', pendingChecks);

      const reconcile = document.getElementById('snapshot-reconcile');
      if (reconcile) {
        reconcile.textContent =
          'The public count is reported by SpicyChat, while the Archive total ' +
          'counts every preserved bot ID, including unavailable history. ' +
          fmt(pendingChecks) + ' specific bot IDs are awaiting missing-status verification. ' +
          'These totals are different populations and should not be added together.';
      }

      const base = String(runtime.publicDataBaseUrl || '').replace(/\/$/, '');
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
