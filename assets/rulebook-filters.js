// Public Rulebook tag taxonomy, taken from SpicyChat's public Explore filters.
// Keep unknown/new tags available in an "Other" group.
export const RULEBOOK_TAG_GROUPS = {
  Focus: 'Relationship|Emotions & Mood|Time & Calendar|Slow Burn|Power Dynamics|Scenario-Driven|Stats & Leveling|Combat & Health|Money & Trade|Enemies & Rivals|Workplace & Authority|Survival|Factions & Reputation|Quests & Story Progress|Inventory & Items|Species & Creatures|Mystery & Clues'.split('|'),
  Genre: 'Romantic|Fantasy|Modern|Drama|Realism|Sci-Fi|Supernatural|Wholesome|Adventure|Horror|Magic|Action|Mystery|Fictional Media|Anime|School|Historical|Comedy|Crime|Gothic|Post-Apocalyptic|Mythological'.split('|'),
  Mechanics: 'RPG rules|Dice rolls|Points & scoring|Hidden stats|Random events|Choices & branches|Character creation|Turn-based|Skill checks|Countdown & deadlines|Mini-games'.split('|'),
  NSFW: 'Erotic Romance|Dominant|Submissive|Fetish|Corruption|Cheating|Impregnation|Switch|Bondage|NTR|Mind Control|Yandere'.split('|')
};
export const canonicalRulebookTag = value => String(value || '').trim().toLocaleLowerCase();
export function parseRulebookTags(value) {
  const input = Array.isArray(value) ? value : String(value || '').split(',');
  return [...new Set(input.map(canonicalRulebookTag).filter(Boolean))];
}
export function matchesRulebookTags(tags, included = [], excluded = [], match = 'any') {
  const actual = new Set(parseRulebookTags(tags));
  const includes = parseRulebookTags(included), excludes = parseRulebookTags(excluded);
  // An excluded tag ALWAYS removes a Rulebook; "all" applies to inclusion only.
  if (excludes.some(tag => actual.has(tag))) return false;
  if (!includes.length) return true;
  return match === 'all'
    ? includes.every(tag => actual.has(tag))
    : includes.some(tag => actual.has(tag));
}
export function toggleRulebookTag(included, excluded, tag, action) {
  const key = canonicalRulebookTag(tag);
  const include = new Set(parseRulebookTags(included)), exclude = new Set(parseRulebookTags(excluded));
  if (!key || !['include','exclude'].includes(action)) return {include:[...include],exclude:[...exclude]};
  const self = action === 'include' ? include : exclude;
  const other = action === 'include' ? exclude : include;
  if (self.has(key)) self.delete(key);
  else {self.add(key);other.delete(key);}
  return {include:[...include],exclude:[...exclude]};
}
export function groupedRulebookTags(tags) {
  const groups = new Map(Object.keys(RULEBOOK_TAG_GROUPS).map(k => [k, []]));
  groups.set('Other', []);
  const lookup = new Map(Object.entries(RULEBOOK_TAG_GROUPS)
    .flatMap(([g, values]) => values.map(tag => [canonicalRulebookTag(tag), g])));
  for (const tag of tags) {
    const group = lookup.get(canonicalRulebookTag(tag)) || 'Other';
    groups.get(group).push(tag);
  }
  for (const [group, values] of groups) {
    values.sort((a, b) => {
      const order = RULEBOOK_TAG_GROUPS[group];
      return order ? order.findIndex(x => canonicalRulebookTag(x) === canonicalRulebookTag(a))
        - order.findIndex(x => canonicalRulebookTag(x) === canonicalRulebookTag(b)) : a.localeCompare(b);
    });
  }
  return [...groups].filter(([,values]) => values.length);
}

export function rulebookCoverSeedUrl(id, seeds) {
  const key=String(id||'').trim().toLowerCase();
  const filename=seeds?.[key];
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(key) ||
      typeof filename!=='string' ||
      !/^[0-9a-f-]{36}\.(?:webp|png|jpg|jpeg)$/i.test(filename)) return '';
  return 'https://cdn.nd-api.com/avatars/'+filename+'?class=image500x650';
}
