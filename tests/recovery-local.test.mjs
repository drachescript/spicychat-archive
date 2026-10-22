import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {
 buildOfflineDraft,sensitiveRecoveryContext,safeRecoveryClues,
 safeRecoveryEvidencePrompt,validateSafeDraft,DRAFT_FIELDS
} from '../assets/recovery-local.js';
import {fieldSuggestionPrompt,suggestionForField} from '../assets/recovery-tools.js';

const kotohaLike={
 name:'Kotoha',
 title:'Your adopted sister wants your attention',
 greeting:'Kotoha is your adopted sister in high school. She is bored and asks, "Hey, {{user}}, wanna watch something?" The archived original also has NSFW sexual themes.',
 personality:'She is very hype, silly, excitable, sweet and teasing. An explicit anatomy reference was stored in the original.',
 tags:['NSFW'],
 scenario:'',
 example_dialogue:'',
};

test('drafts cover only creator fields and never legacy description',()=>{
 assert.deepEqual([...DRAFT_FIELDS],['personality','scenario','example_dialogue']);
 assert.equal(buildOfflineDraft('description',{name:'Alex',title:'Tea shop'}).text,'');
});

test('preserves real archived fields instead of regenerating them',()=>{
 const result=buildOfflineDraft('personality',kotohaLike);
 assert.equal(result.text,'');
 assert.match(result.reason,/already has archived text/);
});

test('Kotoha-like records can reconstruct NON-explicit scenario from their saved opening',()=>{
 const before=JSON.stringify(kotohaLike);
 const risk=sensitiveRecoveryContext(kotohaLike);
 assert.equal(risk.restricted,true);
 assert.equal(risk.blocked,false);
 const draft=buildOfflineDraft('scenario',kotohaLike);
 assert.equal(draft.method,'offline-guided');
 assert.equal(draft.confidence,'low');
 assert.match(draft.text,/Kotoha/);
 assert.match(draft.text,/adopted sister/);
 assert.match(draft.text,/watch something/);
 assert.match(draft.text,/playful|energetic/);
 assert.doesNotMatch(draft.text,/\b(?:nsfw|sex|anatomy|erotic|breast|romance|dating|lover)\b/i);
 assert.match(draft.reason,/not the original creator text/);
 assert.equal(JSON.stringify(kotohaLike),before,'no archival field may be altered');
});

test('Kotoha-like records can suggest in-character everyday dialogue without sexualization',()=>{
 const draft=buildOfflineDraft('example_dialogue',kotohaLike);
 assert.match(draft.text,/{{char}}:/);
 assert.match(draft.text,/{{user}}:/);
 assert.match(draft.text,/bored/);
 assert.match(draft.text,/watch something/);
 assert.equal(validateSafeDraft('example_dialogue',draft.text,kotohaLike).ok,true);
});

test('AI gets only derived non-explicit clues, never the archived sexual source',()=>{
 const clues=safeRecoveryClues(kotohaLike);
 assert.equal(clues.relationship,'adopted sister');
 assert.equal(clues.watchSomething,true);
 assert.equal(clues.bored,true);
 assert.ok(clues.traits.includes('energetic'));
 const summary=safeRecoveryEvidencePrompt(kotohaLike);
 const prompt=fieldSuggestionPrompt('scenario',kotohaLike);
 assert.match(summary,/Observed voice\/personality indicators/);
 assert.match(prompt,/NON-EXPLICIT ONLY/);
 assert.match(prompt,/watching something together/);
 assert.doesNotMatch(prompt,/NSFW|sexual themes|anatomy reference/i);
 const uiEvidence=suggestionForField('scenario',kotohaLike).clues.join('\n');
 assert.doesNotMatch(uiEvidence,/NSFW|anatomy reference/i);
});

test('draft acceptance filter rejects sexual or romantic model output for risky archived context',()=>{
 for(const input of [
  'They begin a sexual relationship.',
  'Her younger brother becomes her boyfriend.',
  'The scene focuses on intimate caressing.',
 ]) {
  const result=validateSafeDraft('scenario',input,kotohaLike);
  assert.equal(result.ok,false,input);
 }
 assert.equal(validateSafeDraft('scenario',
  'Kotoha is bored and suggests a movie; {{user}} decides how to respond.',kotohaLike).ok,true);
});

test('the unrestricted archive still supports normal evidence-guided personality drafting',()=>{
 const evidence={name:'Rin',greeting:'Rin is curious, teasing and thoughtful.'};
 const draft=buildOfflineDraft('personality',evidence);
 assert.match(draft.text,/curious/);
 assert.match(draft.text,/playful/);
 assert.match(draft.text,/thoughtful/);
 assert.equal(sensitiveRecoveryContext(evidence).restricted,false);
});
test('greeting location is inferred only when it is actually mentioned',()=>{
 const draft=buildOfflineDraft('scenario',{name:'Lena',title:'Bookshop friend',greeting:'*in the library, Lena waves with a playful smile*'});
 assert.match(draft.text,/a library/);
 assert.equal(draft.confidence,'low');
});
test('no actual evidence yields no fabricated personality or scenario',()=>{
 const draft=buildOfflineDraft('personality',{name:'Rose'});
 assert.equal(draft.text,'');
 assert.match(draft.reason,/No archived/);
});
test('source evidence remains usable for non-sexual school stories without triggering restrictions',()=>{
 const safe={name:'Maya',greeting:'Maya attends high school and is shy.'};
 assert.equal(sensitiveRecoveryContext(safe).restricted,false);
 assert.ok(buildOfflineDraft('personality',safe).text);
});
test('confirmed deleted bot detail and Recovery Lab omit the dead SpicyChat link',()=>{
 const app=readFileSync(new URL('../assets/app.js',import.meta.url),'utf8');
 const recovery=readFileSync(new URL('../assets/recovery.js',import.meta.url),'utf8');
 assert.match(app,/status==='deleted'\?/);
 assert.match(app,/Recover archived bot/);
 assert.match(recovery,/status==='deleted'\?/);
 assert.match(recovery,/original link removed/);
 assert.match(recovery,/data-suggest-quick/);
 assert.match(recovery,/data-suggest-browser/);
 assert.match(recovery,/data-draft-apply/);
 assert.match(recovery,/data-draft-dismiss/);
 assert.doesNotMatch(recovery,/Archived legacy metadata \(advanced\)/);
});
test('browser assistant remains local, explicit opt-in and validates suggestions',()=>{
 const code=readFileSync(new URL('../assets/recovery-assistant.js',import.meta.url),'utf8');
 assert.match(code,/window\.LanguageModel/);
 assert.match(code,/model\.create\(\)/);
 assert.match(code,/validateSafeDraft\(key,draft,state\.archiveEvidence\)/);
 assert.match(code,/validateSafeDraft\(key,text,state\.archiveEvidence\)/);
 assert.doesNotMatch(code,/fetch\(/);
});
test('changing conversation selection invalidates unaccepted drafts',()=>{
 const source=readFileSync(new URL('../assets/recovery.js',import.meta.url),'utf8');
 assert.match(source,/function invalidateRecoveryDrafts\(\)/);
 assert.match(source,/state\.drafts=\{\}/);
 assert.match(source,/state\.conversation=summarizeConversation\(messages,sourceName\);invalidateRecoveryDrafts\(\)/);
 assert.match(source,/x\.addEventListener\('change',\(\)=>\{invalidateRecoveryDrafts\(\)/);
 assert.match(source,/state\.conversation=null;invalidateRecoveryDrafts\(\)/);
});
