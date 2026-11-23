import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {normalizePresetText,evaluateBlockPreset} from '../assets/block-preset-matcher.js';
const catalog=JSON.parse(fs.readFileSync(new URL('../data/block-presets/catalog.json',import.meta.url),'utf8'));
const cod='franchise:call-of-duty', atl='franchise:avatar-the-last-airbender';
const check=(id,title,mode)=>evaluateBlockPreset(catalog,id,title,{mode});
test('opt-in only and no theory keyword',()=>{
  assert.equal(catalog.characters.length,187);
  assert.equal(catalog.presets.filter(p=>p.kind==='franchise').length,31);
  assert.equal(catalog.catalogVersion,3);
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
  assert.equal(check(cod,'Simon Riley and John Price','keep-female').hide,false);
  assert.equal(check(cod,'Farah Karim and Kate Laswell','keep-male').hide,false);
  assert.equal(check(cod,'Female OC: Simon Riley','keep-female').hide,false);
  assert.equal(check(cod,'Male OC: Farah Karim','keep-male').hide,false);
  assert.equal(check(cod,'Simon Riley','keep-female').hide,true);
});
test('ambiguous nicknames do not hide unrelated titles',()=>{
  for(const title of ['Soap dishes','Gazebo sale','Ghost stories','Gas price','The Theory','BBC news','a marvel at nature'])
    for(const p of catalog.presets.filter(p=>p.kind==='franchise'))
      assert.equal(check(p.id,title,'all').hide,false,p.id+' / '+title);
  assert.equal(check(cod,'Ghost','all').hide,false);
  assert.equal(check(cod,'Soap','all').hide,false);
  assert.equal(check(cod,'Call of Duty: Soap','keep-female').hide,true);
});
test('expanded franchises support both gender exceptions without guessing unrelated names',()=>{
  const check=(franchise,title,mode)=>evaluateBlockPreset(catalog,'franchise:'+franchise,title,{mode});
  const examples=[
    ['genshin-impact','Zhongli Genshin','keep-female',true],
    ['genshin-impact','Hu Tao','keep-female',false],
    ['honkai-star-rail','Honkai Star Rail: March 7th','keep-male',true],
    ['naruto','Sasuke Uchiha','keep-female',true],
    ['one-piece','Nico Robin','keep-male',true],
    ['demon-slayer','Nezuko Kamado','keep-female',false],
    ['chainsaw-man','Makima Chainsaw Man','keep-male',true],
    ['attack-on-titan','Mikasa Ackerman','keep-male',true],
    ['dragon-ball','Son Goku','keep-female',true],
    ['five-nights-at-freddys','William Afton','keep-female',true],
    ['harry-potter','Hermione Granger','keep-female',false],
    ['star-wars','Ahsoka Tano','keep-male',true],
    ['pokemon','Ash Ketchum','keep-female',true],
    ['baldurs-gate-3','Shadowheart','keep-male',true],
    ['cyberpunk-2077','Judy Alvarez','keep-male',true],
    ['the-witcher','Geralt of Rivia','keep-female',true],
    ['overwatch','Genji Shimada','keep-female',true],
    ['final-fantasy-vii','Tifa Lockhart','keep-female',false],
    ['the-legend-of-zelda','Ganondorf','keep-female',true],
    ['league-of-legends','Caitlyn Kiramman','keep-male',true],
    ['hazbin-hotel','Charlie Morningstar','keep-male',true],
    ['helluva-boss','Loona Helluva Boss','keep-male',true],
    ['undertale','Toriel Undertale','keep-female',false],
    ['death-note','Light Yagami','keep-female',true]
  ];
  for (const [f,t,mode,want] of examples) assert.equal(check(f,t,mode).hide,want,f+'/'+t);
  for (const title of ['March 7th party','Power outage','A link to the library','Viktor fixes the PC','One piece of clothing']){
    // Unrelated titles must not be mistaken for high-confidence characters.
    for (const f of ['honkai-star-rail','chainsaw-man','the-legend-of-zelda','league-of-legends'])
      assert.equal(check(f,title,'all').hide,false,f+'/'+title);
  }
  assert.equal(check('helluva-boss','Helluva Boss original character','all').hide,true);
  assert.equal(check('hazbin-hotel','Helluva Boss original character','all').hide,false);
  assert.equal(check('league-of-legends','Jinx','all').hide,false);
  assert.equal(check('league-of-legends','League of Legends: Jinx','keep-male').hide,true);
  assert.equal(check('naruto','Naruto Team 7','keep-female').hide,false);
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