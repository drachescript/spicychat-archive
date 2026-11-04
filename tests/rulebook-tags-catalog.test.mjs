import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {RULEBOOK_TAG_GROUPS} from '../assets/rulebook-filters.js';
const taxonomy = JSON.parse(readFileSync(new URL('../rulebook-tags.json', import.meta.url), 'utf8'));
test('published Rulebook taxonomy matches browser filter groups', () => {
  assert.equal(taxonomy.schemaVersion, 1);
  assert.deepEqual(taxonomy.groups.map(group => group.name), Object.keys(RULEBOOK_TAG_GROUPS));
  for(const group of taxonomy.groups) assert.deepEqual(group.tags, RULEBOOK_TAG_GROUPS[group.name]);
  const all=taxonomy.groups.flatMap(group => group.tags);
  assert.equal(all.length, 62);
  assert.equal(new Set(all.map(tag => tag.toLowerCase())).size, all.length);
});
