import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

// Load the deployed Worker source unchanged, without needing Wrangler or R2
// secrets in CI. The Worker only touches these mocks for the bot text route.
const source = readFileSync(new URL('../worker/src/index.js', import.meta.url), 'utf8');
const { default: worker } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const id = 'b376faf0-8c37-4b37-8861-8ca68cdd64b6';
const uri = 'https://spicychat-archive-import.example/api/archive/bot/' + id + '.txt';

function harness(record) {
  const cache = new Map();
  let reads = 0;
  globalThis.caches = { default: {
    async match(request) { return cache.get(request.url)?.clone() || null; },
    async put(request, response) { cache.set(request.url, response.clone()); },
  }};
  const env = { ARCHIVE_BUCKET: {
    async get(key) {
      reads += 1;
      assert.equal(key, 'bots/b3/' + id + '.json');
      if (!record) return null;
      const raw = new TextEncoder().encode(JSON.stringify(record));
      return { arrayBuffer: async () => raw.buffer };
    }
  }};
  return { env, readCount: () => reads };
}

const publicRecord = {
  id,
  status: { current: 'deleted' },
  lastSeenAt: '2026-11-08T00:00:00Z',
  availabilityHistory: [
    { status: 'public', from: '2026-09-29T00:00:00Z', source: 'typesense' },
    { status: 'deleted', from: '2026-11-08T00:00:00Z', source: 'character-api:repeated-404' }
  ],
  lastKnown: {
    name: 'Changed private name',
    persona: 'QoL-only private content must not be included',
    privateData: 'SECRET_EXTENSION_DATA'
  },
  current: {
    'character-api': {
      name: 'Yui Kimura',
      creator_username: 'dragongraf1312',
      title: 'The Mechanic She Trusts',
      greeting: 'Hello from public archive',
      persona: 'Public personality',
      scenario: 'Public scenario',
      dialogue: 'Public dialogue'
    },
    'qol-bot-status-import': { persona: 'PRIVATE_OLD_PERSONA' },
  }
};

test('reads one verified public bot on demand and reuses cache', async () => {
  const { env, readCount } = harness(publicRecord);
  const first = await worker.fetch(new Request(uri), env);
  assert.equal(first.status, 200);
  assert.match(first.headers.get('content-type'), /text\/plain/);
  assert.equal(first.headers.get('x-robots-tag'), 'noindex, nofollow');
  const text = await first.text();
  for (const expected of ['Yui Kimura', 'The Mechanic She Trusts', 'Public personality', 'Public scenario', 'Public dialogue', 'dragongraf1312']) {
    assert.ok(text.includes(expected), 'missing ' + expected);
  }
  assert.ok(!text.includes('PRIVATE_OLD_PERSONA'));
  assert.ok(!text.includes('SECRET_EXTENSION_DATA'));
  assert.ok(!text.includes('Changed private name'));
  assert.equal(readCount(), 1);

  const second = await worker.fetch(new Request(uri), env);
  assert.equal(second.status, 200);
  assert.equal(readCount(), 1, 'cached requests must not read R2 again');
  assert.equal(await second.text(), text);
});

test('unverified QoL-only imports are not public', async () => {
  const { env, readCount } = harness({
    ...publicRecord,
    status: { current: 'unverified-import' },
    availabilityHistory: [{ status: 'unverified-import', source: 'qol-bot-status-import' }]
  });
  const response = await worker.fetch(new Request(uri), env);
  assert.equal(response.status, 404);
  assert.ok(!(await response.text()).includes('Public personality'));
  assert.equal(readCount(), 1);
});

test('private records and fake public claims do not pass verification', async () => {
  for (const [status, source] of [['private', 'typesense'], ['deleted', 'qol-bot-status-import']]) {
    const { env } = harness({
      ...publicRecord,
      status: { current: status },
      availabilityHistory: [{ status: 'public', source }]
    });
    const response = await worker.fetch(new Request(uri), env);
    assert.equal(response.status, 404);
  }
});

test('invalid IDs and unknown IDs are handled without scans', async () => {
  const { env, readCount } = harness(null);
  const invalid = await worker.fetch(new Request('https://spicychat-archive-import.example/api/archive/bot/not-a-uuid.txt'), env);
  assert.equal(invalid.status, 400);
  assert.equal(readCount(), 0);
  const missing = await worker.fetch(new Request(uri), env);
  assert.equal(missing.status, 404);
  assert.equal(readCount(), 1);
  const missingCached = await worker.fetch(new Request(uri), env);
  assert.equal(missingCached.status, 404);
  assert.equal(readCount(), 1);
});
