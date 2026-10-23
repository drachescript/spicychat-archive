(() => {
  const fmt = value => {
    if (value == null || value === '') return '—';
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
        json('/data/manifest.json'), json('/data/runtime.json')
      ]);
      const base = String(runtime.publicDataBaseUrl || '').replace(/\/$/, '');
      const [rawInventory, lorebooks] = await Promise.all([
        base ? json(base + '/indexes/discovery-id-audit.json').catch(() => null) : null,
        base ? json(base + '/indexes/lorebooks.json').catch(() => null) : null
      ]);
      const inventory = rawInventory?.schemaVersion === 2 &&
        rawInventory.complete === true && rawInventory.readOnly === true ? rawInventory : null;

      set('stat-bots-total', manifest.totalBots);
      set('stat-bots-saved', inventory?.storedBotFileIds);
      set('stat-bots-public', manifest.publicIndexBots ?? manifest.activeBots);
      set('stat-bots-deleted', manifest.deletedBots);
      set('stat-lorebooks-total', lorebooks?.totalArchived);
      set('stat-lorebooks-gone', lorebooks?.notPublic);

      const published = manifest.reconciliation || {};
      const pending = Math.max(0, Number(published.pendingVerificationBots ?? manifest.missingBots) || 0);
      const gap = Number(published.countGap);
      const reconcile = document.getElementById('snapshot-reconcile');
      if (reconcile) {
        reconcile.textContent =
          (Number.isFinite(gap) ? fmt(Math.abs(gap)) + ' is the difference between our old bot ID total, SpicyChat’s public count and confirmed-gone bots. ' : '') +
          'It is not a list of missing bots. ' + fmt(pending) + ' IDs are queued for individual status checks. ' +
          'An old sighting does not tell us whether a bot is still public or deleted.';
      }

      const auditBox = document.getElementById('snapshot-audit');
      if (auditBox) {
        auditBox.replaceChildren();
        if (inventory) {
          auditBox.append(document.createTextNode(
            'Last file check: ' + fmt(inventory.observedDiscoveryIds) + ' IDs seen, ' +
            fmt(inventory.storedBotFileIds) + ' saved bot files, ' +
            fmt(inventory.materializedIndexIds) + ' search entries. ' +
            fmt(inventory.storedFileWithoutIndex) + ' saved files are missing from search; ' +
            fmt(inventory.indexedWithoutStoredFile) + ' search entries have no saved file. ' +
            'Neither result proves that a bot was deleted. '
          ));
          const link = document.createElement('a');
          link.href = base + '/indexes/discovery-id-audit.json';
          link.textContent = 'See the full check';
          link.rel = 'noopener noreferrer';
          auditBox.append(link);
        } else {
          auditBox.textContent = 'A complete saved-file check is not available right now.';
        }
      }

      if (status) {
        const when = new Date(manifest.generatedAt || '');
        status.textContent = Number.isNaN(when.getTime())
          ? 'Archive data loaded'
          : 'Bot counts updated ' + when.toLocaleString();
      }
    } catch {
      if (status) status.textContent = 'Archive counts could not be loaded';
      foot?.classList.add('error');
    }
  }
  void load();
})();
