import assert from 'node:assert/strict';
import test from 'node:test';
import worker from '../worker/src/index.js';

const ID='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const snapshot='0123456789abcdefabcd';
const data={
  'indexes/community-tags/manifest.json': {
    schemaVersion:1, snapshot, shardPrefix:'indexes/community-tags/snapshots/'+snapshot,
    generatedAt:'2026-11-17T01:00:00Z', complete:true,
    coverage:{indexedBots:100,signalBots:1}
  },
  ['indexes/community-tags/snapshots/'+snapshot+'/a.json']:{
    schemaVersion:1,snapshot,bots:{[ID]:{status:'deleted',signals:[
      {tagId:'cheating',kind:'rule-candidate',confidence:'candidate'}
    ]}}
  }
};
const calls=[];
const env={ARCHIVE_BUCKET:{async get(key){
  calls.push(key);
  const doc=data[key];
  if(!doc)return null;
  return {async arrayBuffer(){return new TextEncoder().encode(JSON.stringify(doc)).buffer;}};
}}};
function request(ids){
  return new Request('https://test.local/api/community-tags/lookup',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
}
test('bounded lookup returns sparse evidence without claiming safety',async()=>{
  calls.length=0;
  const response=await worker.fetch(request([ID]),env);
  assert.equal(response.status,200);
  const body=await response.json();
  assert.equal(body.ok,true);
  assert.equal(body.matches[ID].status,'deleted');
  assert.equal(body.matches[ID].signals[0].confidence,'candidate');
  assert.deepEqual(calls,['indexes/community-tags/manifest.json',
    'indexes/community-tags/snapshots/'+snapshot+'/a.json']);
});
test('unknown bot remains unknown',async()=>{
  const unknown='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaabbbb';
  const body=await (await worker.fetch(request([unknown]),env)).json();
  assert.equal(body.ok,true);
  assert.equal(body.matched,0);
  assert.equal(Object.hasOwn(body.matches,unknown),false);
  assert.match(body.note,/UNKNOWN/);
});
test('invalid, unbounded and unavailable lookups fail closed',async()=>{
  assert.equal((await worker.fetch(request(['../../private']),env)).status,400);
  assert.equal((await worker.fetch(request(Array(81).fill(ID)),env)).status,400);
  const stale={ARCHIVE_BUCKET:{async get(){return null;}}};
  assert.equal((await worker.fetch(request([ID]),stale)).status,503);
});
test('manifest can be inspected without querying individual bot IDs',async()=>{
  const res=await worker.fetch(new Request('https://test.local/api/community-tags/manifest'),env);
  const body=await res.json();
  assert.equal(res.status,200);
  assert.equal(body.snapshot,snapshot);
});
