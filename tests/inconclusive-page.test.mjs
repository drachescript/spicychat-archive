import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {normalizeReviewCase,validBotId,reviewSourceDiagnostic} from '../assets/inconclusive-records.js';
import {archivedBotRecordUrl,resolveArchivedAvatar,archivedBotPreview} from '../assets/inconclusive-metadata.js';

const readJson=path=>JSON.parse(readFileSync(new URL(path,import.meta.url),'utf8'));
test('generated review index keeps all published records renderable',()=>{
  const index=readJson('../data/inconclusive.json');
  const source=index.cases;
  assert.ok(Array.isArray(source));
  const normalized=source.map(normalizeReviewCase).filter(Boolean);
  assert.equal(normalized.length,index.caseCount,
    reviewSourceDiagnostic('Index',source,normalized.length));
  assert.ok(normalized.every(row=>row.archiveUrl.endsWith(row.id)));
  assert.ok(normalized.every(row=>row.spicychatUrl.endsWith(row.id)));
});
test('prefilled correction JSON is usable as a fallback review source',()=>{
  const corrections=readJson('../data/inconclusive-corrections.json');
  const source=corrections.corrections;
  assert.ok(Array.isArray(source));
  const normalized=source.map(normalizeReviewCase).filter(Boolean);
  assert.equal(normalized.length,source.length,
    reviewSourceDiagnostic('Corrections',source,normalized.length));
});
test('parser handles alternate ID sources and restores missing categories',()=>{
  const id='0004d9d6-473d-4c4c-b536-d25e184d5cec';
  assert.equal(validBotId(' '+id.toUpperCase()+' '),id);
  assert.equal(normalizeReviewCase({id,category:'awaiting-confirmation',httpStatus:404}).id,id);
  const fromUrl=normalizeReviewCase({spicychatUrl:'https://spicychat.ai/chatbot/'+id,httpStatus:404});
  assert.equal(fromUrl?.id,id);
  assert.equal(fromUrl?.category,'awaiting-confirmation');
  assert.equal(normalizeReviewCase({id,httpStatus:503}).category,'api-error');
  assert.equal(normalizeReviewCase({id,httpStatus:200}).category,'empty-response');
  assert.equal(normalizeReviewCase({id:'invalid',spicychatUrl:'javascript:alert(1)'}),null);
  assert.equal(normalizeReviewCase({id,archiveUrl:'https://evil.example/bot/'+id}).archiveUrl,
    'https://spicychatarchive.drache.uk/bot/?id='+id);
});

test('inconclusive cards use the archived bot name and archived avatar when saved',()=>{
  const id='0004d9d6-473d-4c4c-b536-d25e184d5cec';
  const base='https://data.spicychatarchive.drache.uk';
  assert.equal(archivedBotRecordUrl(id),base+'/bots/00/'+id+'.json');
  assert.equal(archivedBotRecordUrl('wrong-id'),'');
  const preview=archivedBotPreview({
    id,
    lastKnown:{
      name:'A saved character',title:'A saved character · archived',
      creator_username:'example_creator',
      avatar_url:'avatars/original.png',
      is_nsfw:true
    },
    avatarArchive:{r2Key:'media/00/saved-image.webp'}
  });
  assert.equal(preview.name,'A saved character');
  assert.equal(preview.subtitle,'A saved character · archived');
  assert.equal(preview.creator,'example_creator');
  assert.equal(preview.image,base+'/media/00/saved-image.webp');
  assert.equal(preview.fallbackImage,'https://cdn.nd-api.com/avatars/original.png?class=avatar256x256');
});
test('unknown or unsafe image paths never become invented bot metadata',()=>{
  assert.equal(archivedBotPreview({lastKnown:{}}).name,'');
  assert.equal(archivedBotPreview({lastKnown:{}}).image,'');
  assert.equal(archivedBotPreview({lastKnown:{name:'Unknown bot'}}).name,'');
  assert.equal(resolveArchivedAvatar('javascript:alert(1)'),'');
  assert.equal(resolveArchivedAvatar('http://bad.example/a.png'),'');
  assert.equal(resolveArchivedAvatar('https://example.com/a.jpg'),'https://example.com/a.jpg');
  assert.equal(archivedBotPreview(null),null);
});
test('only visible review cards request historical previews, with a concurrency cap',()=>{
  const code=readFileSync(new URL('../assets/inconclusive.js',import.meta.url),'utf8');
  assert.match(code,/IntersectionObserver/);
  assert.match(code,/PREVIEW_CONCURRENCY=3/);
  assert.match(code,/\.observe\(el\)/);
  assert.match(code,/ic-bot-name/);
  assert.match(code,/ic-bot-avatar/);
  assert.match(code,/Archive bot/);
  assert.match(code,/SpicyChat bot/);
});
