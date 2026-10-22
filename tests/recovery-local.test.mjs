import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {buildOfflineDraft,sensitiveRecoveryContext,DRAFT_FIELDS} from '../assets/recovery-local.js';

test('offline suggestions are bounded to three real creator fields, not description',()=>{
 assert.deepEqual([...DRAFT_FIELDS],['personality','scenario','example_dialogue']);
 assert.equal(buildOfflineDraft('description',{name:'Alex',title:'Tea shop'}).text,'');
});

test('evidence matching produces a clearly labelled draft without replacing archived data',()=>{
 const evidence={name:'Lena',title:'Quiet bookshop',greeting:'*in the library, Lena waves with a playful smile*',scenario:''};
 const original=JSON.stringify(evidence);
 const draft=buildOfflineDraft('scenario',evidence);
 assert.match(draft.text,/a library/);
 assert.match(draft.text,/Lena/);
 assert.equal(draft.method,'offline-guided');
 assert.equal(draft.confidence,'low');
 assert.match(draft.reason,/invented|scaffold/i);
 assert.equal(JSON.stringify(evidence),original);
});

test('offline personality draft extracts only observed temperament words',()=>{
 const draft=buildOfflineDraft('personality',{name:'Rin',greeting:'Rin is curious, teasing and always thoughtful.'});
 assert.match(draft.text,/curious/);
 assert.match(draft.text,/playful/);
 assert.match(draft.text,/thoughtful/);
 assert.doesNotMatch(draft.text,/princess|dragon|soulmate/i);
});

test('explicit school-age ambiguity prevents both types of automatic suggestion',()=>{
 const risky={name:'Example',title:'Sibling story',greeting:'A character in high school with sexual themes and NSFW tags'};
 assert.equal(sensitiveRecoveryContext(risky).blocked,true);
 const result=buildOfflineDraft('scenario',risky);
 assert.equal(result.text,'');
 assert.match(result.reason,/age ambiguity/);
});

test('explicit family sexual context prevents automatic new content',()=>{
 const risky={name:'Sample',greeting:'An adopted sister discusses sexual themes.'};
 assert.equal(sensitiveRecoveryContext(risky).blocked,true);
 assert.equal(buildOfflineDraft('example_dialogue',risky).text,'');
});

test('existing archival fields cannot be overwritten by offline drafts',()=>{
 const result=buildOfflineDraft('personality',{name:'Rose',personality:'Kind.'});
 assert.equal(result.text,'');
 assert.match(result.reason,/already has archived text/);
});

test('no evidence yields no fake personality or scenario',()=>{
 const result=buildOfflineDraft('personality',{name:'Rose'});
 assert.equal(result.text,'');
 assert.match(result.reason,/No archived/);
});

test('confirmed deleted bot detail and Recovery Lab remove dead SpicyChat link',()=>{
 const app=readFileSync(new URL('../assets/app.js',import.meta.url),'utf8');
 const recovery=readFileSync(new URL('../assets/recovery.js',import.meta.url),'utf8');
 assert.match(app,/status==='deleted'\?/);
 assert.match(app,/Recover archived bot/);
 assert.match(app,/Confirmed deleted/);
 assert.match(recovery,/status==='deleted'\?/);
 assert.match(recovery,/original link removed/);
 assert.match(recovery,/Quick Draft \(offline\)/);
 assert.match(recovery,/data-suggest-browser/);
 assert.match(recovery,/data-draft-apply/);
 assert.match(recovery,/data-draft-dismiss/);
 assert.doesNotMatch(recovery,/Archived legacy metadata \(advanced\)/);
});

test('browser assistant stays explicit opt-in and never calls an external inference URL',()=>{
 const src=readFileSync(new URL('../assets/recovery-assistant.js',import.meta.url),'utf8');
 assert.match(src,/window\.LanguageModel/);
 assert.match(src,/model\.create\(\)/);
 assert.match(src,/state\.drafts\[key\]/);
 assert.match(src,/applyDraft\(key,text,source,edited\)/);
 assert.doesNotMatch(src,/fetch\(/);
 assert.doesNotMatch(src,/https?:\/\//);
});


test('changing imported evidence or selected message classes invalidates previews',()=>{
 const source=readFileSync(new URL('../assets/recovery.js',import.meta.url),'utf8');
 assert.match(source,/function invalidateRecoveryDrafts\(\)/);
 assert.match(source,/state\.drafts=\{\}/);
 assert.match(source,/state\.conversation=summarizeConversation\(messages,sourceName\);invalidateRecoveryDrafts\(\)/);
 assert.match(source,/x\.addEventListener\('change',\(\)=>\{invalidateRecoveryDrafts\(\)/);
 assert.match(source,/state\.conversation=null;invalidateRecoveryDrafts\(\)/);
});
