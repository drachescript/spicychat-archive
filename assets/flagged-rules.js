// Public metadata triage only. No finding of illegality, image recognition,
// automated reporting, additional R2 reads, or changes to archived records.
// The image category deliberately supports MANUAL avatar inspection.
export const GUIDELINES_URL='https://docs.spicychat.ai/community-guidelines';
export const REVIEW_RULES=Object.freeze([
  {
    id:'photo',
    label:'Photorealistic / real-person avatar clues',
    section:'Community Guidelines §5: Respect for Personal Rights',
    terms:['photorealistic','hyperrealistic','real photo','deepfake','real person'],
    pattern:/\b(?:photo[\s-]?realistic|hyper[\s-]?realistic|real(?:[\s-]?life)?[\s-]?photo(?:graph)?|deep[\s-]?fake|real[\s-]?person|real[\s-]?actor|face[\s-]?swap)\b/i,
    note:'Only a text hint. Review the actual avatar; no computer vision, face matching, or identity detection is performed.'
  },
  {
    id:'underage',
    label:'Possible underage portrayal',
    section:'Community Guidelines §§1, 4, 6 and 8: Age restrictions',
    terms:['underage','minor','child','teenager','schoolgirl','schoolboy','17 years old','16 years old'],
    pattern:/\b(?:under[\s-]?age|minors?|child(?:ren)?|kids?|teen(?:age|ager|agers)?|school[\s-]?(?:girl|boy)|middle[\s-]?school(?:er|ers)?|(?:[1-9]|1[0-7])[\s-]?(?:years?[\s-]?old|yr[\s-]?old|y[\s-]?o))\b/i,
    note:'An age-related word does not establish the character’s age or whether its content is sexual. An adult “aged-up” portrayal needs context.'
  },
  {
    id:'exploitation',
    label:'Possible serious exploitation',
    section:'Community Guidelines §§4 and 6: Prohibited topics and exploitation',
    terms:['child sexual abuse','child exploitation','sex trafficking','human trafficking','CSAM'],
    pattern:/\b(?:child(?:ren)?[\s-]+sexual[\s-]+abuse|child[\s-]+(?:sexual[\s-]+)?exploitation|sex[\s-]+trafficking|human[\s-]+trafficking|csam|sexual[\s-]+exploitation[\s-]+of[\s-]+(?:a[\s-]+)?minor|groom(?:ing)?[\s-]+(?:a[\s-]+)?minor)\b/i,
    note:'May be a critical or fictional discussion. Do not treat a keyword match as evidence of illegal conduct.'
  }
]);
const trim=v=>String(v??'').replace(/\s+/g,' ').trim();
export function validBotId(value){
  const id=trim(value).toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)?id:'';
}
export function parseBotId(value){
  const raw=trim(value),direct=validBotId(raw);
  if(direct)return direct;
  try {
    const url=new URL(raw);
    if(!['http:','https:'].includes(url.protocol))return '';
    if(!['spicychat.ai','www.spicychat.ai','spicychatarchive.drache.uk'].includes(url.hostname.toLowerCase()))return '';
    const q=validBotId(url.searchParams.get('id'));
    if(q)return q;
    return validBotId(url.pathname.split('/').filter(Boolean).at(-1));
  } catch{return '';}
}
export function normalizeReviewBot(doc,source='public') {
  const id=validBotId(doc?.character_id||doc?.id);
  if(!id)return null;
  const restored=source==='restored'&&Number(doc?.restoreCount||0)>0&&Boolean(doc?.restoredAt);
  if(source==='restored'&&!restored)return null;
  const tags=Array.isArray(doc.tags)?doc.tags.filter(x=>typeof x==='string').slice(0,24):[];
  return {id,name:trim(doc.name||'Unknown bot'),title:trim(doc.title),
    creator:trim(doc.creator_username||doc.creator),tags,
    status:source==='restored'?trim(doc.status).toLowerCase():'public',
    source,
    image:source==='restored'?trim(doc.avatarFallback||doc.avatar):trim(doc.avatar_url),
    nsfw:doc.is_nsfw===true||doc.isNsfw===true,
    messages:Number(doc.num_messages??doc.messages??0)||0,
    restoredAt:source==='restored'?trim(doc.restoredAt):''
  };
}
export function reviewSignals(bot,category='all') {
  if(!bot)return [];
  const fields=[['Name',bot.name],['Title',bot.title],['Tags',(bot.tags||[]).join(' ')]];
  const results=[];
  for(const rule of REVIEW_RULES) {
    if(category!=='all'&&category!==rule.id)continue;
    const hit=fields.find(([,v])=>rule.pattern.test(String(v||'')));
    if(hit)results.push({id:rule.id,label:rule.label,section:rule.section,
      field:hit[0],matched:(String(hit[1]).match(rule.pattern)||[])[0]?.slice(0,70)||'',note:rule.note});
  }
  return results;
}
export function discoveryTerms(category='all'){
  return [...new Set(REVIEW_RULES.filter(x=>category==='all'||x.id===category)
    .flatMap(x=>x.terms))].slice(0,20);
}
