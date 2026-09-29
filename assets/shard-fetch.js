(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  const shardCache = new Map();
  const indexCache = new Map();

  function botRequest(url, init) {
    try {
      const method = String(init?.method || 'GET').toUpperCase();
      if (method !== 'GET') return null;
      const parsed = new URL(typeof url === 'string' || url instanceof URL ? url : url.url, location.href);
      const match = parsed.pathname.match(/\/bots\/([0-9a-f_]{2})\/([^/]+)\.json$/i);
      if (!match) return null;
      const id = decodeURIComponent(match[2]).toLowerCase();
      return { parsed, prefix: match[1].toLowerCase(), id };
    } catch {
      return null;
    }
  }

  async function jsonFetch(url, cache) {
    if (cache.has(url)) return cache.get(url);
    const promise = nativeFetch(url, { cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    });
    cache.set(url, promise);
    try {
      return await promise;
    } catch (error) {
      cache.delete(url);
      throw error;
    }
  }

  window.fetch = async function shardAwareFetch(input, init = {}) {
    const response = await nativeFetch(input, init);
    if (response.ok || response.status !== 404) return response;

    const request = botRequest(input, init);
    if (!request) return response;

    const base = `${request.parsed.protocol}//${request.parsed.host}`;
    try {
      const indexUrl = `${base}/indexes/discovery/${encodeURIComponent(request.prefix)}.json`;
      const index = await jsonFetch(indexUrl, indexCache);
      const shardKey = index?.bots?.[request.id];
      if (!shardKey) return response;

      const shardUrl = `${base}/${String(shardKey).replace(/^\/+/, '')}`;
      const shard = await jsonFetch(shardUrl, shardCache);
      const record = shard?.records?.[request.id];
      if (!record) return response;

      return new Response(JSON.stringify(record), {
        status: 200,
        statusText: 'OK',
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-store',
          'X-SpicyChat-Archive-Storage': 'discovery-shard'
        }
      });
    } catch (error) {
      console.debug('Shard fallback unavailable for', request.id, error);
      return response;
    }
  };
})();
