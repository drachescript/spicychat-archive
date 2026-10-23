// Small, verified saved-file summary published with the website.
// Try the complete R2 report first, but never depend on it for the homepage.
export function isVerifiedCoverage(value) {
  if (!value || value.schemaVersion !== 2 || value.complete !== true || value.readOnly !== true) return false;
  if (!Number.isFinite(Date.parse(value.generatedAt || ''))) return false;
  for (const key of ['observedDiscoveryIds', 'materializedIndexIds', 'storedBotFileIds',
                     'storedFileWithoutIndex', 'indexedWithoutStoredFile']) {
    if (!Number.isSafeInteger(value[key]) || value[key] < 0) return false;
  }
  return value.storedBotFileIds <= value.observedDiscoveryIds &&
    value.storedFileWithoutIndex <= value.storedBotFileIds &&
    value.indexedWithoutStoredFile <= value.materializedIndexIds &&
    value.materializedIndexIds - value.indexedWithoutStoredFile ===
      value.storedBotFileIds - value.storedFileWithoutIndex;
}

export function chooseCoverage(remote, local) {
  const r = isVerifiedCoverage(remote) ? remote : null;
  const l = isVerifiedCoverage(local) ? local : null;
  if (r && (!l || Date.parse(r.generatedAt) >= Date.parse(l.generatedAt))) {
    return { ...r, _source: 'r2' };
  }
  return l ? { ...l, _source: 'site' } : null;
}

export async function loadCoverage(base, readJson) {
  const [remote, local] = await Promise.all([
    base ? readJson(base.replace(/\/$/, '') + '/indexes/discovery-id-audit.json').catch(() => null) : null,
    readJson('/data/archive-coverage.json').catch(() => null)
  ]);
  return chooseCoverage(remote, local);
}
