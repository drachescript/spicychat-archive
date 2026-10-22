import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {buildOfflineExpansion,EXPAND_FIELDS,validateSafeDraft} from '../assets/recovery-local.js';
import {fieldExpansionPrompt} from '../assets/recovery-tools.js';

const kotohaLike={
 name:'Kotoha',title:'Your adopted sister wants company',
 greeting:'Kotoha is your adopted sister in high school. She is bored and suggests watching something. NSFW sexual themes appear in the archived original.',
 personality:'She is playful, hype, silly and excitable, and her archived personality mentions explicit anatomy.',
 scenario:'',example_dialogue:''
};
test('existing personality expands without overwriting source or generating sexual content',()=>{
 const before=JSON.stringify(kotohaLike);
 const draft=buildOfflineExpansion('personality',kotohaLike);
 assert.equal(draft.kind,'expand');
 assert.equal(draft.confidence,'low');
 assert.match(draft.text,/playful|energetic/);
 assert.match(draft.text,/watch|company|teasing/);
 assert.ok(!draft.text.includes(kotohaLike.personality));
 assert.equal(validateSafeDraft('personality',draft.text,kotohaLike).ok,true);
 assert.equal(JSON.stringify(kotohaLike),before);
});
test('an existing greeting, scenario and dialogue can get independent additions',()=>{
 for(const field of ['greeting','scenario','example_dialogue']){
  const existing={...kotohaLike,[field]:field==='example_dialogue'?'{{char}}: "hello"':'Existing archived field.'};
  const draft=buildOfflineExpansion(field,existing);
  assert.ok(draft.text.length>20,field);
  assert.equal(draft.kind,'expand');
  assert.equal(validateSafeDraft(field,draft.text,kotohaLike).ok,true);
 }
});
test('expansions reject empty fields and metadata including description',()=>{
 assert.deepEqual([...EXPAND_FIELDS],['personality','greeting','scenario','example_dialogue']);
 assert.equal(buildOfflineExpansion('scenario',kotohaLike).text,'');
 assert.equal(buildOfflineExpansion('description',{description:'something'}).text,'');
});
test('AI expansion prompt for risky archive passes only safe observations, not original explicit wording',()=>{
 const prompt=fieldExpansionPrompt('personality',kotohaLike);
 assert.match(prompt,/ADDITION/);
 assert.match(prompt,/NON-EXPLICIT ONLY/);
 assert.match(prompt,/playful/);
 assert.doesNotMatch(prompt,/NSFW sexual themes appear in the archived original/);
 assert.doesNotMatch(prompt,/personality mentions explicit anatomy/);
});
test('unrestricted AI expansion includes existing personality as reference',()=>{
 const prompt=fieldExpansionPrompt('personality',{name:'Ari',personality:'Talkative and adventurous'});
 assert.match(prompt,/Existing field text: Talkative and adventurous/);
 assert.match(prompt,/DO NOT return the original text/);
});
test('recovery app displays expansion controls, accepts only with explicit append action',()=>{
 const source=readFileSync(new URL('../assets/recovery.js',import.meta.url),'utf8');
 assert.match(source,/data-expand-quick/);
 assert.match(source,/data-expand-browser/);
 assert.match(source,/data-expand-copy/);
 assert.match(source,/data-expand-ai/);
 assert.match(source,/Append to edited field/);
 assert.match(source,/if\(kind==='expand'\)/);
 assert.match(source,/state\.provenance\[key\]='modified'/);
 assert.match(source,/state\.drafts\[targetField\]/);
 assert.match(source,/fieldExpansionPrompt\(/);
});
test('browser AI feature-gate errors disable only browser buttons, never offline Quick Expand',()=>{
 const source=readFileSync(new URL('../assets/recovery-assistant.js',import.meta.url),'utf8');
 assert.match(source,/feature flag/);
 assert.match(source,/browserModelBlockedReason/);
 assert.match(source,/removeBrokenBrowserButtons\(\)/);
 assert.match(source,/\[data-suggest-browser\],\[data-expand-browser\]/);
 assert.doesNotMatch(source,/remove\(\).*data-expand-quick/);
 assert.match(source,/buildOfflineExpansion\(/);
 assert.match(source,/validateSafeDraft\(field,text,state\.archiveEvidence\)/);
});
