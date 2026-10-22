import assert from 'node:assert/strict';
import { test } from 'node:test';
import {readFileSync} from 'node:fs';
import {
 recoveryReadiness,evidenceWarnings,conversationPreview,
 suggestionForField,fieldSuggestionPrompt,cardV2Payload,packCardPng
} from '../assets/recovery-tools.js';

test('readiness counts only actual creator fields; not unused Description',()=>{
 const fields=['name','title','greeting','personality','scenario','example_dialogue','tags','image'].map(key=>({key}));
 const found=recoveryReadiness({name:'Maria',title:'Archived',greeting:'hi',description:'Legacy data'},fields);
 assert.equal(found.complete,3);
 assert.equal(found.total,8);
 assert.ok(found.missing.includes('personality'));
 assert.ok(!found.missing.includes('description'));
 assert.ok(found.missingNarrative.includes('example_dialogue'));
});

test('age/setting and missing conversation warnings never silently settle uncertain details',()=>{
 const messages=evidenceWarnings({name:'Adult?',greeting:'at high school'},null).join('\n');
 assert.match(messages,/Age or school-setting/);
 assert.match(messages,/No conversation evidence/);
});
test('conversation identity preview warns for mismatched character names and preserves sample',()=>{
 const preview=conversationPreview([{role:'character',speaker:'Jane',text:'hello'},{role:'user',text:'hi'}],'Maria');
 assert.equal(preview.mismatch,true);
 assert.equal(preview.sample.length,2);
 assert.deepEqual(preview.names,['Jane']);
});

test('personality, scenario and dialogue suggestion prompts are labelled inventions',()=>{
 for(const key of ['personality','scenario','example_dialogue']){
  const idea=suggestionForField(key,{title:'Forest resident',greeting:'*she waves* hi'},null);
  assert.ok(idea.instruction.length>30);
  assert.ok(idea.clues.some(x=>x.includes('Forest')));
  const prompt=fieldSuggestionPrompt(key,{title:'Forest resident'},null);
  assert.match(prompt,/RECONSTRUCTION SUGGESTION/);
  assert.match(prompt,/not original archived wording/);
 }
});
test('Character Card V2 preserves original title and field provenance without manufacturing Description',()=>{
 const card=cardV2Payload({
  name:'Maria',title:'Forest resident',greeting:'Hi!',personality:'kind',
  scenario:'forest',example_dialogue:'hey',tags:['Fantasy'],image:'https://example.com/a.png'
 },{botId:'123',archiveBase:'latest',provenance:{personality:'reconstructed'},confidence:{personality:{level:'low'}}});
 assert.equal(card.data.description,'');
 assert.equal(card.data.first_mes,'Hi!');
 assert.equal(card.data.extensions.spicychat_archive_recovery.archived_title,'Forest resident');
 assert.equal(card.data.extensions.spicychat_archive_recovery.provenance.personality,'reconstructed');
 assert.equal(card.data.extensions.spicychat_archive_recovery.image_url,'https://example.com/a.png');
});

test('PNG character-card export inserts a chara tEXt chunk after IHDR and leaves image bytes intact',()=>{
 // A minimal 1px PNG sample, with a proper IHDR first.
 const original=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/uE8AAAAASUVORK5CYII=','base64');
 const png=packCardPng(new Uint8Array(original), Buffer.from('{"spec":"chara_card_v2"}').toString('base64'));
 assert.deepEqual(Buffer.from(png.slice(0,33)),original.subarray(0,33));
 assert.ok(Buffer.from(png).includes(Buffer.from('chara\0')));
 assert.ok(Buffer.from(png).includes(Buffer.from('tEXt')));
 assert.deepEqual(Buffer.from(png.slice(png.length-original.length+33)),original.subarray(33));
});

test('app preserves raw legacy description but does not expose normal description editor or suggestion',()=>{
 const app=readFileSync(new URL('../assets/recovery.js',import.meta.url),'utf8');
 assert.doesNotMatch(app,/key:'description',label:'Description'/);
 assert.match(app,/function legacyMetadata\(/);
 assert.doesNotMatch(app,/Archived legacy metadata \(advanced\)/);
 assert.doesNotMatch(app,/const missing=\['description','personality'/);
 assert.match(app,/refreshTabTitle\(/);
 assert.match(app,/buildAndShowPrompt\(\),'Prompt copied/);
 assert.match(app,/data-restore-original/);
 assert.match(app,/data-restore-generated/);
 assert.match(app,/data-clear-field/);
 assert.match(app,/recovery-ai-consent/);
 assert.match(app,/Remote AI endpoints must use HTTPS/);
 assert.match(app,/async function downloadArchivedPicture\(/);
 assert.match(app,/async function exportCharacterCardPng\(/);
});
