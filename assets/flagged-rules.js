// Candidate signals are only search hints, NOT moderation decisions or legal findings.
// Match public title/name/tags. No private chats, identity recognition, image AI,
// automated crawling, or modification of Archive records.
export const GUIDELINES_URL = 'https://docs.spicychat.ai/community-guidelines';

export const REVIEW_RULES = Object.freeze([
  {
    id: 'image', label: 'Real-person / realistic imagery', section: '5. Respect for Personal Rights',
    terms: ['photorealistic', 'real photo'],
    pattern: /\b(?:photo[\s-]?realistic|hyper[\s-]?realistic|real(?:[- ]life)?\s+photo(?:graph)?|face[\s-]?claim|deep[\s-]?fake|real[- ]person|real[- ]life\s+actor)\b/i,
    note: 'Text suggests an avatar or character may require inspection. It does NOT recognize a person or inspect the pixels.'
  },
  {
    id: 'age', label: 'Age / youthful presentation', section: '1, 4 and 8. Age and underage content',
    terms: ['underage', 'high school'],
    pattern: /\b(?:underage|minors?|high[\s-]?school(?:er|ers)?|middle[\s-]?school(?:er|ers)?|teen(?:age|ager|agers)?|(?:[0-9]|1[0-7])[\s-]?years?[\s-]?old)\b/i,
    note: 'A school setting or a youth-related word does not establish the character’s age.'
  },
  {
    id: 'consent', label: 'Consent concerns', section: '4. Prohibited Topics',
    terms: ['nonconsensual', 'without consent'],
    pattern: /\b(?:non[\s-]?consensual|without\s+consent|sexual\s+assault|rape|forced\s+(?:intimacy|sex|marriage))\b/i,
    note: 'Mentions of a subject are not proof that the bot promotes it.'
  },
  {
    id: 'family', label: 'Family relationships / incest', section: '4 and 6. Prohibited Topics and Specific Content',
    terms: ['incest', 'stepsister'],
    pattern: /\b(?:incest(?:uous)?|step[\s-]?(?:brother|sister|mother|father|son|daughter)|(?:mother|father)[\s-]+and[\s-]+(?:son|daughter)|(?:brother|sister)[\s-]+and[\s-]+(?:sister|brother))\b/i,
    note: 'A family-related mention may be nonsexual and needs context.'
  },
  {
    id: 'hate', label: 'Hateful or extremist themes', section: '3, 4 and 6. Unacceptable Content',
    terms: ['white supremacist', 'hate speech'],
    pattern: /\b(?:white\s+supremac(?:y|ist|ists)|racial\s+supremac(?:y|ist)|hate\s+speech|ethnic\s+cleansing|nazi\s+propaganda)\b/i,
    note: 'A critical depiction is not the same as promotion or glorification.'
  },
  {
    id: 'harm', label: 'Self-harm / criminal activity themes', section: '3 and 6. Unacceptable Content',
    terms: ['self harm', 'trafficking'],
    pattern: /\b(?:self[\s-]?harm|suicid(?:e|al)|human\s+traffick(?:ing|er)|sex\s+traffick(?:ing|er)|terrorist\s+propaganda|war\s+criminal|drug\s+traffick(?:ing|er))\b/i,
    note: 'A fictional crime story or a prevention-oriented discussion is not inherently illegal or glorifying.'
  },
  {
    id: 'violence', label: 'Extreme violence references', section: '6. Specific Content Guidelines',
    terms: ['gore', 'snuff'],
    pattern: /\b(?:gore|snuff|extreme\s+violence|sexual\s+torture)\b/i,
    note: 'The guideline distinguishes violence alone from combining it with sexual scenarios.'
  },
  {
    id: 'promotion', label: 'External links / promotions', section: '9. Advertisement and Promotion',
    terms: ['discord.gg', 'patreon'],
    pattern: /(?:https?:\/\/|discord\.gg\/|patreon\.com\/|onlyfans\.com\/|t\.me\/|(?:buy|subscribe)\s+my\s+(?:content|page))/i,
    note: 'An external link or promotion needs review in its original context.'
  },
  {
    id: 'rating', label: 'Possible NSFW labeling mismatch', section: '2. Content and Chatbot Creation',
    terms: ['NSFW', 'explicit'],
    pattern: /\b(?:nsfw|sexually\s+explicit|explicit\s+sexual|pornograph(?:y|ic))\b/i,
    requiresSfw: true,
    note: 'Only shown when listing metadata marks the bot SFW; this is not proof of incorrect classification.'
  }
]);

const normalize = value => String(value ?? '').replace(/\s+/g, ' ').trim();
export function validBotId(value) {
  const id=normalize(value).toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)?id:'';
}
export function parseBotId(value) {
  const raw=normalize(value);
  const id=validBotId(raw);
  if(id)return id;
  try {
    const url = new URL(raw);
    if(!['https:', 'http:'].includes(url.protocol))return '';
    if(!['spicychat.ai','www.spicychat.ai','spicychatarchive.drache.uk'].includes(url.hostname.toLowerCase()))return '';
    const fromQuery=validBotId(url.searchParams.get('id'));
    if(fromQuery)return fromQuery;
    const parts=url.pathname.split('/').filter(Boolean);
    return validBotId(parts.at(-1));
  } catch{return '';}
}
export function normalizeReviewBot(doc, source='public') {
  const id=validBotId(doc?.character_id||doc?.id);
  if(!id)return null;
  const restored=source==='restored' && Number(doc?.restoreCount||0)>0;
  // The restored index contains API-verified restorations only.
  if(source==='restored' && !restored)return null;
  const tags=Array.isArray(doc.tags)?doc.tags.filter(v=>typeof v==='string').slice(0,24):[];
  return {
    id,
    name: normalize(doc.name||'Unknown bot'),
    title: normalize(doc.title),
    creator: normalize(doc.creator_username||doc.creator),
    tags,
    status:source==='restored'?String(doc.status||'unknown').toLowerCase():'public',
    source,
    image:source==='restored'?normalize(doc.avatarFallback||''):normalize(doc.avatar_url||''),
    nsfw:doc.is_nsfw===true||doc.isNsfw===true,
    messages:Number(doc.num_messages??doc.messages??0)||0,
    restoredAt:source==='restored'?String(doc.restoredAt||''):''
  };
}
export function reviewSignals(bot, category='all') {
  if(!bot)return [];
  const fields=[['Name', bot.name],['Title', bot.title],['Tags', (bot.tags||[]).join(' ')]];
  const rules=REVIEW_RULES.filter(r=>category==='all'||r.id===category);
  const signals=[];
  for(const rule of rules){
    if(rule.requiresSfw && bot.nsfw)continue;
    const match=fields.find(([_,value])=>rule.pattern.test(String(value||'')));
    if(!match)continue;
    signals.push({ id:rule.id,label:rule.label,section:rule.section,
      field:match[0], matched:String(match[1]).match(rule.pattern)?.[0]?.slice(0,70)||'', note:rule.note });
  }
  return signals;
}
export function discoveryTerms(category='all') {
  // Fixed, bounded discovery budget. Queries search PUBLIC name/title/tags only.
  const matches=REVIEW_RULES.filter(r=>category==='all'||r.id===category);
  return [...new Set(matches.flatMap(r=>r.terms))].slice(0,18);
}
