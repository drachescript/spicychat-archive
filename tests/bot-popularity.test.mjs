import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {recordBotView,shouldTrackBotView,validViewedBotId} from '../assets/bot-popularity.js';

const id = 'b376faf0-8c37-4b37-8861-8ca68cdd64b6';
const source = readFileSync(new URL('../worker/src/index.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const endpoint = 'https://spicychat-archive-import.example/api/analytics/bot-view';
const site = 'https://spicychatarchive.drache.uk';

function request(value=id, origin=site, type='text/plain') {
  return new Request(endpoint, {method:'POST',headers:{'Origin':origin,'Content-Type':type},body:value});
}
function storage() {
  const state = new Map();
  return {getItem:k=>state.get(k)||null,setItem:(k,v)=>state.set(k,v)};
}

test('worker records exactly the bot ID and no personal data, without R2 reads', async()=>{
  const events=[];
  const env={BOT_VIEWS_ANALYTICS:{writeDataPoint:row=>events.push(row)},
    ARCHIVE_BUCKET:{get:()=>{throw Error('R2 must not be accessed');}}};
  const res=await worker.fetch(request(), env);
  assert.equal(res.status,204);
  assert.equal(res.headers.get('access-control-allow-origin'),site);
  assert.deepEqual(events,[{indexes:[id],blobs:['bot-detail'],doubles:[1]}]);
});
test('worker rejects unknown/malformed/non-site requests and absent binding',async()=>{
  const events=[];
  const env={BOT_VIEWS_ANALYTICS:{writeDataPoint:row=>events.push(row)}};
  for(const [body,origin,type,status] of [
    [id,'https://evil.example','text/plain',403],
    ['wrong',site,'text/plain',400],
    [id,site,'application/json',415],
  ]){
    assert.equal((await worker.fetch(request(body,origin,type),env)).status,status);
  }
  assert.equal((await worker.fetch(request(),{})).status,503);
  assert.equal(events.length,0);
});
test('front-end de-duplicates per bot and respects privacy choices',async()=>{
  const s=storage();let calls=0;
  const fetchImpl=async (url, init)=>{
    calls++;
    assert.match(url,/\/api\/analytics\/bot-view$/);
    assert.equal(init.body,id);
    assert.equal(init.method,'POST');
    assert.equal(init.keepalive,true);
    return {ok:true};
  };
  assert.equal(validViewedBotId(id.toUpperCase()),id);
  assert.equal(validViewedBotId('bad-id'),'');
  assert.equal(await recordBotView(id,{storage:s,now:1000000,fetchImpl,navigator:{}}),true);
  assert.equal(await recordBotView(id,{storage:s,now:1000100,fetchImpl,navigator:{}}),false);
  assert.equal(calls,1);
  assert.equal(shouldTrackBotView(id,{storage:s,now:1000000+31*60000,navigator:{}}),true);
  assert.equal(shouldTrackBotView(id,{storage:s,now:1000000+31*60000,navigator:{doNotTrack:'1'}}),false);
  assert.equal(shouldTrackBotView(id,{storage:s,now:1000000+31*60000,navigator:{globalPrivacyControl:true}}),false);
});
test('failed telemetry never marks a page as measured',async()=>{
  const s=storage();
  assert.equal(await recordBotView(id,{storage:s,now:100000,fetchImpl:async()=>({ok:false}),navigator:{}}),false);
  assert.equal(s.getItem('sca-bot-view:'+id),null);
});
test('only successful record render triggers a view',()=>{
  const app=readFileSync(new URL('../assets/app.js',import.meta.url),'utf8');
  const start=app.indexOf('async function botPage()');
  const found=app.indexOf('  void recordBotView(record.id);',start);
  assert.ok(start>=0 && found>start);
  assert.ok(found>app.indexOf('app.innerHTML=`<article class="detail">',start));
  assert.ok(found>app.indexOf('catch(e){',start));
});
