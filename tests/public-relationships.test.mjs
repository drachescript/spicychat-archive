import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {relatedPublicObservations} from '../assets/public-relationships.js';
const data = JSON.parse(fs.readFileSync(new URL('../data/public-relationships.json', import.meta.url)));
test('public attachments are navigable both ways without inventing a Lorebook ID', () => {
  const bot = '75b9dbc8-ab04-422e-9943-e4d2339d102a';
  const book = 'c6c2ad82-e5c8-57e2-8666-d3b20924dad3';
  const edges = relatedPublicObservations(data, 'bot', bot);
  assert.equal(edges.length, 2);
  assert.equal(edges.find(x => x.book.type === 'rulebook').book.id, book);
  assert.equal(edges.find(x => x.book.type === 'lorebook').book.id, null);
  assert.deepEqual(relatedPublicObservations(data, 'rulebook', book).map(x => x.bot.id), [bot, 'ad9f3315-927c-44e1-a22f-d04089a11206']);
  assert.deepEqual(relatedPublicObservations(data, 'lorebook', 'unknown'), []);
  assert.deepEqual(relatedPublicObservations({schemaVersion:1, observations:[{bot:{id:bot,name:'Katie'},book:{type:'rulebook',id:'not-an-id',name:'bad'},observedAt:'2026-11-24',evidence:['bot-profile']}]}, 'bot', bot), []);
});
