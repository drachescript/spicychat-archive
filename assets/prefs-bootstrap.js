(() => {
  'use strict';

  const STORAGE_KEY = 'sca-tag-filters-v1';
  const params = new URLSearchParams(location.search);

  // A shared/bookmarked URL should always win. Local preferences are only used
  // when the page was opened without an explicit tag-filter state.
  if (params.has('include') || params.has('exclude') || params.has('match')) return;

  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (!saved || typeof saved !== 'object') return;

    const include = Array.isArray(saved.include) ? saved.include.filter(Boolean) : [];
    const exclude = Array.isArray(saved.exclude) ? saved.exclude.filter(Boolean) : null;
    const match = saved.match === 'any' ? 'any' : 'all';

    if (include.length) params.set('include', include.join(','));
    if (exclude) params.set('exclude', exclude.length ? exclude.join(',') : 'none');
    params.set('match', match);

    const url = new URL(location.href);
    url.search = params.toString();
    history.replaceState(null, '', url);
  } catch {
    // Broken/blocked localStorage should never stop the archive from loading.
  }
})();
