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
    terms:['underage','minor','teenager','schoolgirl','schoolboy','17 years old','16 years old'],
    // A child being born, "my child", and "minor update" say nothing
    // about the roleplayed bot's age. Require a more specific age cue.
    pattern:/\b(?:under[\s-]?age|(?:a|the)[\s-]+minor|minor[\s-]+(?:character|student|girl|boy)|teen(?:age|ager|agers)?|school[\s-]?(?:girl|boy)|middle[\s-]?school(?:er|ers)?|(?:[1-9]|1[0-7])[\s-]?(?:years?[\s-]?old|yr[\s-]?old|y[\s-]?o))\b/i,
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
// Conservative textual triage; explicit *adult* and *negated* phrases
// should not manufacture a minor-related allegation.
function ageSignalContradicted(fieldText, matchedText, at){
  const text=String(fieldText||''),matched=matchedText.toLowerCase();
  const before=text.slice(Math.max(0,at-70),at).toLowerCase();
  const after=text.slice(at+matchedText.length,at+matchedText.length+65).toLowerCase();
  const explicitChildAge=/\b(?:[1-9]|1[0-7])[\s-]?(?:years?[\s-]?old|yr[\s-]?old|y[\s-]?o)\b/i.test(text);
  const explicitAdult=/\b(?:18|19|2[0-9]|3[0-9])\s*(?:\+(?!\d)|years?\s*old\b|yr\s*old\b|y\s*o\b)|\bage\s*[:=]\s*(?:18|19|2[0-9]|3[0-9])\b|\ball\s+(?:characters|chars|people)\s+(?:are\s+)?(?:18\+|adults?)\b|\baged?[\s-]+up\s+to\s+(?:18|19|2[0-9])\b/i.test(text);
  if(!explicitChildAge&&explicitAdult)return true;
  if(/under[\s-]?age|minor/.test(matched)){
    if(/\b(?:no|not|never|without|isn['’]?t|aren['’]?t|is\s+not|are\s+not|was\s+not|don['’]?t|doesn['’]?t)\s+(?:\w+[\s-]+){0,4}$/.test(before))return true;
    if(/\b(?:how|why)\s+(?:is|was)\s+(?:this|it|he|she)\s*$/i.test(before))return true;
    if(/\b(?:content|activity|characters?)\s+(?:is|are)\s+not\s*$/i.test(before))return true;
    if(/^(?:\s*(?:stuff|content|bots?)\s*(?:is|are)\s+not\s+underage)/i.test(after))return true;
  }
  return false;
}
export function reviewSignals(bot,category='all') {
  if(!bot)return [];
  const fields=[['Name',bot.name],['Title',bot.title],['Tags',(bot.tags||[]).join(' ')]];
  const results=[];
  for(const rule of REVIEW_RULES) {
    if(category!=='all'&&category!==rule.id)continue;
    let found=null;
    for(const [field,text] of fields){
      const value=String(text||'');
      const expression=new RegExp(rule.pattern.source,rule.pattern.flags.replace('g','')+'g');
      for(const hit of value.matchAll(expression)){
        if(rule.id==='underage'&&ageSignalContradicted(value,hit[0],hit.index??0))continue;
        found={id:rule.id,label:rule.label,section:rule.section,
          field,matched:hit[0].slice(0,70),note:rule.note};
        break;
      }
      if(found)break;
    }
    if(found)results.push(found);
  }
  return results;
}
export function discoveryTerms(category='all'){
  return [...new Set(REVIEW_RULES.filter(x=>category==='all'||x.id===category)
    .flatMap(x=>x.terms))].slice(0,20);
}
