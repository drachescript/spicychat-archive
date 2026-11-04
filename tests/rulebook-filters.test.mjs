import test from 'node:test';
import assert from 'node:assert/strict';
import {parseTagList,matchesTagFilters,groupTags} from '../assets/rulebook-filters.mjs';

test('include any vs all and exclude always blocks any matching tag',()=>{
  const tags=['NTR','Cheating','Fantasy'];
  assert.equal(matchesTagFilters(tags,['NTR','Fantasy'],[],'all'),true);
  assert.equal(matchesTagFilters(tags,['NTR','Nonexistent'],[],'all'),false);
  assert.equal(matchesTagFilters(tags,['NTR','Nonexistent'],[],'any'),true);
  assert.equal(matchesTagFilters(tags,[],['NTR','Cheating'],'all'),false);
  assert.equal(matchesTagFilters(['Cheating'],[],['NTR','Cheating'],'all'),false);
  assert.equal(matchesTagFilters(['Romantic'],[],['NTR','Cheating'],'all'),true);
});
test('case-insensitive names, URL comma syntax, and deduplication',()=>{
  assert.deepEqual(parseTagList(' NTR,Cheating,ntr,,'),['NTR','Cheating']);
  assert.equal(matchesTagFilters(['Slow Burn'],['slow burn'],['ntr']),true);
  assert.equal(matchesTagFilters(['NTR'],['ntr'],['ntr']),false);
});
test('catalog groups and older uncategorized tags remain searchable',()=>{
  const groups=groupTags([{name:'Focus',tags:['Relationship','Relationship']}],
    ['Relationship','Retired Tag']);
  assert.deepEqual(groups,[{name:'Focus',tags:['Relationship']},{name:'Other',tags:['Retired Tag']}]);
});
