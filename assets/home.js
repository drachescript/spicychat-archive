import {loadCoverage} from './archive-coverage.js';

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
      const [inventory, lorebooks] = await Promise.all([
        loadCoverage(base, json),
        base ? json(base + '/indexes/lorebooks.json').catch(() => null) : null
      ]);

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
          (Number.isFinite(gap) ? fmt(Math.abs(gap)) + ' is the difference when we compare old Archive IDs with SpicyChat’s public count and confirmed-gone bots. ' : '') +
          'It is not a list of missing bots. ' +
          (pending ? fmt(pending) + ' specific IDs are waiting for availability checks.' :
            'No individual bot IDs are currently waiting for availability checks.');
      }

      const auditBox = document.getElementById('snapshot-audit');
      if (auditBox) {
        auditBox.replaceChildren();
        if (inventory) {
          const checked = new Date(inventory.generatedAt).toLocaleString();
          auditBox.append(document.createTextNode(
            'Last full file check (' + checked + '): ' +
            fmt(inventory.storedBotFileIds) + ' bot files saved, ' +
            fmt(inventory.materializedIndexIds) + ' search entries. ' +
            fmt(inventory.storedFileWithoutIndex) + ' saved files missing from search, ' +
            fmt(inventory.indexedWithoutStoredFile) + ' older search entries without a saved file. ' +
            'This only checks Archive storage, not whether bots are public now. '
          ));
          const link = document.createElement('a');
          link.href = inventory._source === 'r2'
            ? base + '/indexes/discovery-id-audit.json'
            : '/data/archive-coverage.json';
          link.textContent = 'See the numbers';
          link.rel = 'noopener noreferrer';
          auditBox.append(link);
        } else {
          auditBox.textContent = 'The saved-file totals could not be loaded. Other Archive counts are still available.';
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
