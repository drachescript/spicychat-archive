import test from 'node:test';
import assert from 'node:assert/strict';
import {RULEBOOK_TAG_GROUPS, parseRulebookTags, matchesRulebookTags, toggleRulebookTag, groupedRulebookTags, rulebookCoverSeedUrl, hasSavedPublicRuleContent} from '../assets/rulebook-filters.js';

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

test('cover fallback is URL-only and validates filenames',()=>{
  const id='63416959-f266-593d-830d-37bc1c474ef9';
  const file='66f8c467-db24-4546-b36b-0e94d19d4f38.webp';
  assert.equal(rulebookCoverSeedUrl(id,{[id]:file}),
    'https://cdn.nd-api.com/avatars/'+file+'?class=image500x650');
  assert.equal(rulebookCoverSeedUrl(id,{[id]:'../../private.webp'}),'');
  assert.equal(rulebookCoverSeedUrl('invalid',{invalid:file}),'');
});

test('listing counts never masquerade as saved public Rulebook definitions',()=>{
  const doc={listing:{name:'War of Races',num_rules:173,cover_url:'https://cdn.nd-api.com/avatars/a.webp'}};
  assert.equal(hasSavedPublicRuleContent(doc),false);
  assert.equal(hasSavedPublicRuleContent({publicDefinition:{visibility:'private',
    meters:[{rules:[{instruction:'hidden'}]}]}}),false);
  assert.equal(hasSavedPublicRuleContent({publicDefinition:{visibility:'shared',
    meters:[{name:'Love'}]}}),false);
  assert.equal(hasSavedPublicRuleContent({publicDefinition:{visibility:'shared',
    meters:[{levels:[{name:'Friend',instruction:'Warm up slowly.'}]}]}}),true);
});
