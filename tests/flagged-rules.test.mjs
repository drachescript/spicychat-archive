import assert from 'node:assert/strict';
import {test} from 'node:test';
import { REVIEW_RULES, validBotId, parseBotId, normalizeReviewBot, reviewSignals, discoveryTerms } from '../assets/flagged-rules.js';

const id='c0c8beed-9afd-457b-bda0-c142a6805b0c';
test('only validated UUIDs and known bot link hosts are accepted',()=>{
 assert.equal(validBotId(id),id);
 assert.equal(parseBotId(id),id);
 assert.equal(parseBotId('https://spicychatarchive.drache.uk/bot/?id='+id),id);
 assert.equal(parseBotId('https://spicychat.ai/chatbot/'+id),id);
 assert.equal(parseBotId('https://evil.example/bot/?id='+id),'');
 assert.equal(parseBotId('../bot/?id='+id),'');
 assert.equal(parseBotId('oops'),'');
});
test('normalizes only public and verified-restoration candidates',()=>{
 const raw={character_id:id,name:'A bot',title:'Photorealistic portrait',is_nsfw:false,avatar_url:'https://cdn.nd-api.com/a.jpg'};
 assert.equal(normalizeReviewBot(raw)?.status,'public');
 assert.equal(normalizeReviewBot({...raw,status:'private'},'restored'),null);
 const restored=normalizeReviewBot({...raw,status:'deleted',restoreCount:1,avatarFallback:'https://cdn.nd-api.com/a.jpg'},'restored');
 assert.equal(restored?.status,'deleted');
 assert.equal(restored?.source,'restored');
 assert.equal(restored?.image,'https://cdn.nd-api.com/a.jpg');
 assert.equal(normalizeReviewBot({...raw,character_id:'bad'}),null);
});
test('signals are suggestive and based on explicit public metadata',()=>{
 const bot=normalizeReviewBot({character_id:id,name:'Real Photo Muse',title:'Photorealistic portrait',tags:['Portrait'],is_nsfw:false});
 assert.deepEqual(reviewSignals(bot).map(s=>s.id),['image']);
 assert.equal(reviewSignals(bot)[0].field,'Name');
 assert.deepEqual(reviewSignals(bot,'family'),[]);
 assert.deepEqual(reviewSignals(normalizeReviewBot({character_id:id,name:'Normal',title:'Nothing explicit',tags:[]})),[]);
});
test('NSFW category only checks possible SFW label mismatches',()=>{
 const sfw=normalizeReviewBot({character_id:id,name:'NSFW bot',is_nsfw:false});
 const nsfw=normalizeReviewBot({character_id:id,name:'NSFW bot',is_nsfw:true});
 assert.ok(reviewSignals(sfw).some(s=>s.id==='rating'));
 assert.ok(!reviewSignals(nsfw).some(s=>s.id==='rating'));
});
test('patterns require context rather than asserting violations',()=>{
 const bot=normalizeReviewBot({character_id:id,title:'High school reunion among adults',tags:['Romance']});
 assert.equal(reviewSignals(bot,'age').length,1);
 assert.equal(reviewSignals(bot,'consent').length,0);
 assert.equal(reviewSignals(bot,'family').length,0);
 assert.ok(REVIEW_RULES.every(x=>x.note && x.section && x.terms.length));
 assert.ok(discoveryTerms().length<=18);
 assert.deepEqual(discoveryTerms('bogus'),[]);
});
