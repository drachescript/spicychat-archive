import test from 'node:test';
import assert from 'node:assert/strict';
import {isVerifiedCoverage, chooseCoverage, loadCoverage} from '../assets/archive-coverage.js';

const recent = {
  schemaVersion: 2, complete: true, readOnly: true,
  generatedAt: '2026-10-23T11:03:38.478963Z',
  observedDiscoveryIds: 1428093, materializedIndexIds: 473307,
  storedBotFileIds: 473210, storedFileWithoutIndex: 0,
  indexedWithoutStoredFile: 97
};
const old = {...recent, generatedAt: '2026-10-23T10:05:33.683858Z',
  materializedIndexIds: 473278, storedFileWithoutIndex: 29};

test('accepts reconciled complete inventories only', () => {
  assert.equal(isVerifiedCoverage(recent), true);
  assert.equal(isVerifiedCoverage({...recent, complete: false}), false);
  assert.equal(isVerifiedCoverage({...recent, indexedWithoutStoredFile: 0}), false);
  assert.equal(isVerifiedCoverage({...recent, storedBotFileIds: '473210'}), false);
});

test('prefers newer saved snapshot to stale R2 audit', () => {
  const report = chooseCoverage(old, recent);
  assert.equal(report._source, 'site');
  assert.equal(report.storedFileWithoutIndex, 0);
});

test('uses current R2 report when newer', () => {
  const newer = {...recent, generatedAt: '2026-10-24T11:03:38Z'};
  assert.equal(chooseCoverage(newer, recent)._source, 'r2');
});

test('keeps saved-file totals when R2 is unavailable', async () => {
  const calls = [];
  const read = async url => {
    calls.push(url);
    if (url.includes('data.spicychatarchive')) throw new Error('CORS/404');
    return recent;
  };
  const report = await loadCoverage('https://data.spicychatarchive.drache.uk', read);
  assert.equal(report.storedBotFileIds, 473210);
  assert.equal(report._source, 'site');
  assert.equal(calls.length, 2);
});

test('rejects incomplete cached totals instead of displaying misleading numbers', async () => {
  const result = await loadCoverage('', async () => ({...recent, readOnly: false}));
  assert.equal(result, null);
});
