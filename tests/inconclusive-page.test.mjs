import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {normalizeReviewCase,validBotId,reviewSourceDiagnostic} from '../assets/inconclusive-records.js';

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
