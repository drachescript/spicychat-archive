import test from 'node:test';
import assert from 'node:assert/strict';
import {searchShardForId,recordFromSearchIndex,recordWithoutArchive} from '../assets/recovery-index.js';

test('recovery lookup uses the same 64-shard hashing scheme',()=>{
  const id='0332e137-0ee7-47f9-91a9-4db09fb24f16';
  const value=searchShardForId(id);
  assert.match(value,/^[0-3][0-9a-f]$/);
  assert.equal(searchShardForId(id.toUpperCase()),value);
});

test('limited recovery preserves only old search evidence, never invents status',()=>{
  const id='0332e137-0ee7-47f9-91a9-4db09fb24f16';
  const record=recordFromSearchIndex(id,['Older name','Author',true,0,'bloom','2026-10-05T00:00:00Z']);
  assert.equal(record.lastKnown.name,'Older name');
  assert.equal(record.lastKnown.creator_username,'Author');
  assert.equal(record.status.current,'unknown');
  assert.equal(record._archiveCoverage,'search-only');
  assert.equal(record.lastSeenAt,'2026-10-05T00:00:00Z');
  assert.equal(record.firstSeenAt,undefined);
  assert.equal(recordFromSearchIndex(id,{}),null);
});

test('a missing file and missing index still allow local recovery without fake originals',()=>{
  const record=recordWithoutArchive('test-id');
  assert.deepEqual(record.lastKnown,{});
  assert.equal(record.status.current,'unknown');
  assert.equal(record._archiveCoverage,'id-only');
});
