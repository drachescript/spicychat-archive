import assert from 'node:assert/strict';
import {test} from 'node:test';
import {REVIEW_RULES, validBotId, parseBotId, normalizeReviewBot, reviewSignals, discoveryTerms} from '../assets/flagged-rules.js';
const id='c0c8beed-9afd-457b-bda0-c142a6805b0c';
const bot=(name,title='',tags=[])=>normalizeReviewBot({character_id:id,name,title,tags});
test('only UUIDs and recognized archive links are accepted',()=>{
 assert.equal(validBotId(id),id);
 assert.equal(parseBotId(id),id);
 assert.equal(parseBotId('https://spicychatarchive.drache.uk/bot/?id='+id),id);
 assert.equal(parseBotId('https://spicychat.ai/chatbot/'+id),id);
 assert.equal(parseBotId('https://evil.example/bot/?id='+id),'');
 assert.equal(parseBotId('bad id'),'');
});
test('only present public or previously verified restored metadata is eligible',()=>{
 const pub=normalizeReviewBot({character_id:id,name:'A bot'});
 assert.equal(pub.status,'public');
 assert.equal(normalizeReviewBot({id,status:'deleted',restoreCount:1},'restored'),null);
 const restored=normalizeReviewBot({id,status:'deleted',restoreCount:1,restoredAt:'2026-11-07T00:00:00Z'},'restored');
 assert.equal(restored.status,'deleted');
 assert.equal(restored.source,'restored');
 assert.equal(normalizeReviewBot({id:'invalid'}),null);
});
test('image text hints do not assert actual facial identity',()=>{
 assert.deepEqual(reviewSignals(bot('Photorealistic portrait')).map(x=>x.id),['photo']);
 assert.equal(reviewSignals(bot('A fantasy character')).length,0);
 assert.match(REVIEW_RULES[0].note,/no computer vision/i);
});
test('youthful cues are reviewed but adult statements do not trigger by themselves',()=>{
 assert.deepEqual(reviewSignals(bot('Adult college student')).map(x=>x.id),[]);
 assert.deepEqual(reviewSignals(bot('16 years old')).map(x=>x.id),['underage']);
 assert.deepEqual(reviewSignals(bot('Underage')).map(x=>x.id),['underage']);
 assert.deepEqual(reviewSignals(bot('Kidnapper')).map(x=>x.id),[]);
 assert.deepEqual(reviewSignals(bot('18 years old')).map(x=>x.id),[]);
});
test('underage review ignores parenting, updates, age disclaimers, and negations',()=>{
 const ordinary=[
  'You cannot bear his child so his secretary did',
  'A father with three children',
  'Minor update and new lorebook',
  'He punishes even minor mistakes',
  'His child is the heir',
  'She is NOT UNDERAGE',
  'No underage content',
  "They're 19, not underage",
  'Teenager character aged up to 19',
  'Rebellious teenager age=18',
  'All characters are 18+ schoolgirls',
  'A teenager who is explicitly 20 years old',
 ];
 for(const title of ordinary){
   assert.deepEqual(reviewSignals(bot('Ordinary Adult',title)),[],title);
 }
 for(const title of ['a minor', '16 years old', 'Underage character', 'teenager schoolboy']){
   assert.deepEqual(reviewSignals(bot('Age cue',title)).map(x=>x.id),['underage'],title);
 }
});
test('fictional violence and consensual adult content are outside these focus filters',()=>{
 for(const title of ['Assassin in the city','Warrior with a sword','Consensual romance','High fantasy war']) {
   assert.deepEqual(reviewSignals(bot(title)),[],title);
 }
 assert.deepEqual(reviewSignals(bot('Human trafficking investigation')).map(x=>x.id),['exploitation']);
 assert.equal(REVIEW_RULES.length,3);
 assert.ok(REVIEW_RULES.every(x=>x.note&&x.section));
 assert.ok(discoveryTerms().length<=20);
 assert.deepEqual(discoveryTerms('unrecognized'),[]);
});
