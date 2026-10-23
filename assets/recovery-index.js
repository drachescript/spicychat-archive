// What Recovery Lab can show when the full saved bot JSON is missing.
// Search-index metadata is historical evidence, not a complete character card.
export function searchShardForId(id) {
  let hash = 2166136261;
  for (const byte of new TextEncoder().encode(String(id || '').toLowerCase())) {
    hash ^= byte;
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return (hash & 63).toString(16).padStart(2, '0');
}

export function recordFromSearchIndex(id, row) {
  if (!Array.isArray(row) || row.length < 2) return null;
  const name = typeof row[0] === 'string' ? row[0].trim() : '';
  const creator = typeof row[1] === 'string' ? row[1].trim() : '';
  const lastSeenAt = typeof row[5] === 'string' ? row[5] : null;
  return {
    id,
    lastKnown: { ...(name ? { name } : {}), ...(creator ? { creator_username: creator } : {}) },
    lastSeenAt,
    status: { current: 'unknown' },
    _archiveCoverage: 'search-only'
  };
}

export function recordWithoutArchive(id) {
  return { id, lastKnown: {}, status: { current: 'unknown' }, _archiveCoverage: 'id-only' };
}
