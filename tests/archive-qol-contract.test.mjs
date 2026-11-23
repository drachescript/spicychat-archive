// Static Archive contract consumed by QoL v0.2.86+ and v0.2.87+.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {evaluateBlockPreset} from '../assets/block-preset-matcher.js';
const load = path => JSON.parse(fs.readFileSync(new URL('../'+path,import.meta.url),'utf8'));
const c=load('data/block-presets/catalog.json');
const p=load('data/block-presets/manifest.json');
const m=load('data/community-tags/manifest.json');
const tax=load('data/community-tags/taxonomy.json');
const aliases=load('data/community-tags/aliases.json');
const rules=load('data/community-tags/rules.json');

test('blocking preset catalog matches the manifest used by QoL',()=>{
  assert.equal(p.contract,'spicychat-archive-block-presets-v1');
  assert.equal(c.contract,p.contract);
  assert.equal(c.schemaVersion,1);
  assert.equal(p.catalogVersion,c.catalogVersion);
  assert.equal(p.catalog,'https://spicychatarchive.drache.uk/data/block-presets/catalog.json');
  assert.ok(c.presets.length<=500&&c.characters.length<=5000&&c.groups.length<=1000);
  assert.equal(p.catalogVersion,3);
  assert.equal(p.counts.franchises,31);
  assert.equal(p.counts.characters,187);
  assert.ok(c.presets.every(x=>x.enabledByDefault===false));
  assert.ok(c.presets.some(x=>x.id==='franchise:call-of-duty'));
  assert.ok(c.characters.some(x=>x.id==='cod-ghost'));
  assert.ok(c.presets.some(x=>x.id==='theme:ntr'));
});
test('community lookup and taxonomy stay in the supported v1 shape',()=>{
  assert.equal(m.contract,'spicychat-archive-community-tags-v1');
  assert.equal(m.schemaVersion,1);
  assert.equal(m.live.batchSize,80);
  assert.equal(m.live.manifest,'https://data.spicychatarchive.drache.uk/indexes/community-tags/manifest.json');
  assert.equal(m.live.lookup,'https://spicychat-archive-import.dragongraf.workers.dev/api/community-tags/lookup');
  assert.ok(tax.tags.length>300 && tax.tags.length<1200);
  assert.equal(tax.schemaVersion,1);
  assert.equal(aliases.schemaVersion,1);
  assert.equal(rules.schemaVersion,1);
  assert.ok(rules.rules.every(x=>x.confidence==='candidate'));
  const ids=new Set(tax.tags.map(x=>x.id));
  for(const [id,values] of Object.entries(aliases.byTagId)) {
    assert.ok(ids.has(id),'Alias targets unknown tag: '+id);
    assert.ok(Array.isArray(values));
  }
});
test('QoL and Archive preview keep ambiguous and multi-character titles',()=>{
  const preset='franchise:call-of-duty';
  const check=(title,mode)=>evaluateBlockPreset(c,preset,title,{mode});
  for(const title of ['Ghost','Soap dishes','Gas price','The Theory'])
    assert.equal(check(title,'all').hide,false,title);
  assert.equal(check('Simon Riley','keep-female').hide,true);
  assert.equal(check('Kate Laswell','keep-female').hide,false);
  assert.equal(check('John Price and Simon Riley','keep-female').hide,false);
  assert.equal(check('Kate Laswell and Farah Karim','keep-male').hide,false);
  assert.equal(check('Female OC Simon Riley','keep-female').hide,false);
  assert.equal(check('Task Force 141','keep-female').hide,false);
  assert.equal(check('Task Force 141','all').hide,true);
  assert.equal(check('König','all').hide,true);
});
