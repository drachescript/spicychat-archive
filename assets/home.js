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

      set('stat-bots-total', manifest.totalBots);
      set('stat-bots-public', manifest.activeBots);
      set('stat-bots-deleted', manifest.deletedBots);

      const base = String(runtime.publicDataBaseUrl || '').replace(/\/$/, '');
      if (base) {
        try {
          const lorebooks = await json(base + '/indexes/lorebooks.json');
          set('stat-lorebooks-total', lorebooks.totalArchived);
          set('stat-lorebooks-public', lorebooks.publicNow);
          set('stat-lorebooks-gone', lorebooks.notPublic);
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
