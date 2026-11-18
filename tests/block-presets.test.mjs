import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {normalizePresetText,evaluateBlockPreset} from '../assets/block-preset-matcher.js';
const catalog=JSON.parse(fs.readFileSync(new URL('../data/block-presets/catalog.json',import.meta.url),'utf8'));
const cod='franchise:call-of-duty', atl='franchise:avatar-the-last-airbender';
const check=(id,title,mode)=>evaluateBlockPreset(catalog,id,title,{mode});
test('opt-in only and no theory keyword',()=>{
  assert.equal(catalog.characters.length,42);
  assert.ok(catalog.presets.every(p=>p.enabledByDefault===false));
  assert.ok(!catalog.presets.some(p=>JSON.stringify(p).toLowerCase().includes('theory')));
});
test('male/female character exceptions and individual keeps',()=>{
  assert.equal(check(cod,'Farah Karim','keep-female').hide,false);
  assert.equal(check(cod,'Farah Karim','keep-male').hide,true);
  assert.equal(check(cod,'Simon "Ghost" Riley','keep-female').hide,true);
  assert.equal(check(cod,'Simon "Ghost" Riley','keep-male').hide,false);
  assert.equal(check('franchise:resident-evil','Leon Kennedy','keep-female').hide,true);
  assert.equal(check('franchise:resident-evil','Ada Wong','keep-female').hide,false);
  assert.equal(evaluateBlockPreset(catalog,cod,'Ghost Riley',{mode:'custom',keepCharacterIds:['cod-ghost']}).hide,false);
});
test('unknown, groups and gender variants stay visible in gender filters',()=>{
  assert.equal(check(cod,'Call of Duty female OC','keep-female').hide,false);
  assert.equal(check(cod,'Call of Duty female OC','all').hide,true);
  assert.equal(check(cod,'TaskForce 141','keep-female').hide,false);
  assert.equal(check(cod,'Ghost and Soap','keep-female').hide,false);
  assert.equal(check(cod,'Ghost and Soap','all').hide,true);
  assert.equal(check(cod,'Female Ghost, Call of Duty','keep-female').hide,false);
  assert.equal(check(atl,'Katara and Zuko','keep-female').hide,false);
  assert.equal(check(atl,'Katara and Zuko','keep-male').hide,false);
});
test('ambiguous nicknames do not hide unrelated titles',()=>{
  for(const title of ['Soap dishes','Gazebo sale','Ghost stories','Gas price','The Theory','BBC news','a marvel at nature'])
    for(const p of catalog.presets.filter(p=>p.kind==='franchise'))
      assert.equal(check(p.id,title,'all').hide,false,p.id+' / '+title);
  assert.equal(check(cod,'Ghost','all').hide,false);
  assert.equal(check(cod,'Soap','all').hide,false);
  assert.equal(check(cod,'Call of Duty: Soap','keep-female').hide,true);
});
test('accents, spacing, franchises, themes',()=>{
  assert.equal(normalizePresetText('König'),'konig');
  assert.equal(normalizePresetText('TaskForce 141'),'task force 141');
  for(const title of ['König','Konig','Ghost Simon','TF141','TF-141'])
    assert.equal(check(cod,title,'all').hide,true,title);
  assert.equal(check('franchise:pacific-rim','Pacififc Rim','all').hide,true);
  assert.equal(check('franchise:jujutsu-kaisen','JJK','all').hide,true);
  assert.equal(check('franchise:my-hero-academia','MHA','all').hide,true);
  assert.equal(check(atl,'Zuko','keep-female').hide,true);
  assert.equal(check('theme:ntr','NTR romance','all').hide,true);
  assert.equal(check('theme:omegaverse','Enigma','all').hide,false);
  assert.equal(check('theme:omegaverse','User Omega','all').hide,true);
});