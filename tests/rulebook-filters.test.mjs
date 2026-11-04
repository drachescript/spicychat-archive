import test from 'node:test';
import assert from 'node:assert/strict';
import {RULEBOOK_TAG_GROUPS, parseRulebookTags, matchesRulebookTags, toggleRulebookTag, groupedRulebookTags} from '../assets/rulebook-filters.js';

test('tag groups mirror the public Rulebook Explore categories', () => {
  assert.equal(Object.values(RULEBOOK_TAG_GROUPS).reduce((n, tags) => n + tags.length, 0), 62);
  assert.deepEqual(groupedRulebookTags(['Fantasy','Cheating','Mystery & Clues','New Tag']).map(x=>x[0]),
                   ['Focus','Genre','NSFW','Other']);
});
test('include any/all and exclude any remain independent', () => {
  const tags = ['Romantic','Relationship','Cheating'];
  assert.equal(matchesRulebookTags(tags,['Romantic','Fantasy'],[],'any'), true);
  assert.equal(matchesRulebookTags(tags,['Romantic','Fantasy'],[],'all'), false);
  assert.equal(matchesRulebookTags(tags,['romantic','RELATIONSHIP'],[],'all'), true);
  assert.equal(matchesRulebookTags(tags,[],['NTR','cheating'],'all'), false);
  assert.equal(matchesRulebookTags(tags,[],['NTR'],'any'), true);
  assert.equal(matchesRulebookTags(tags,['romantic'],['Cheating'],'any'), false);
  assert.equal(matchesRulebookTags(tags,[],[]),true);
});
test('switching tag include/exclude is exclusive and reversible', () => {
  const a=toggleRulebookTag([],[],'NTR','exclude');
  assert.deepEqual(a,{include:[],exclude:['ntr']});
  const b=toggleRulebookTag(a.include,a.exclude,'ntr','include');
  assert.deepEqual(b,{include:['ntr'],exclude:[]});
  const c=toggleRulebookTag(b.include,b.exclude,'NTR','include');
  assert.deepEqual(c,{include:[],exclude:[]});
  assert.deepEqual(parseRulebookTags('  NTR, ntr, Cheating, '),['ntr','cheating']);
});
