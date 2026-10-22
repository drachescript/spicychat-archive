// Lightweight, deterministic evidence-only recovery helper.
// It never downloads models, calls a network endpoint, or claims invented text is recovered.
const safeTraitWords = [
  ['curious',/\bcurious|inquisitive|asks questions?\b/i],
  ['playful',/\bplayful|teasing|joking|silly|goofy\b/i],
  ['reserved',/\breserved|quiet|shy|introverted\b/i],
  ['direct',/\bblunt|straightforward|direct\b/i],
  ['protective',/\bprotective|looks after|worries about\b/i],
  ['patient',/\bpatient|calm|gentle\b/i],
  ['energetic',/\benergetic|excitable|hyper|bouncy\b/i],
  ['wry',/\bsarcastic|dry humor|deadpan\b/i],
  ['confident',/\bconfident|self-assured\b/i],
  ['thoughtful',/\bthoughtful|considerate|observant\b/i],
  ['determined',/\bdetermined|stubborn|persistent\b/i],
];
const knownSettings = [
  ['a library',/\blibrary\b/i],['a café',/\bcaf[eé]|coffee shop\b/i],
  ['a park',/\bpark\b/i],['a marketplace',/\bmarket|bazaar\b/i],
  ['an apartment',/\bapartment|flat\b/i],['a house',/\bhouse|home\b/i],
  ['a forest',/\bforest|woods\b/i],['a castle',/\bcastle\b/i],
  ['an office',/\boffice\b/i],['a village',/\bvillage\b/i],
  ['a tavern',/\btavern|inn\b/i],['a spaceship',/\bspaceship|starship\b/i],
];
export const DRAFT_FIELDS = Object.freeze(['personality','scenario','example_dialogue']);

export function sensitiveRecoveryContext(evidence) {
  const text=['name','title','greeting','personality','scenario','example_dialogue']
    .map(key=>String(evidence?.[key]||'')).join(' ');
  const ageUnclear=/\b(?:high school|middle school|secondary school|schoolgirl|schoolboy|underage|minor|child|young teen)\b|\b(?:1[0-7]|[0-9])\s*(?:years? old|y\/o)\b/i.test(text);
  const sexual=/\b(?:nsfw|sexual|sex|naked|nude|erotic|pregnan|impregnat|lactat|breasts?|tits?|fetish|lust|bedroom|thighs?|crotch)\b/i.test(text);
  const family=/\b(?:sister|brother|step[- ]?family|stepsister|stepbrother|adopted daughter|adopted son|mother|father)\b/i.test(text);
  return {
    blocked:Boolean((ageUnclear&&sexual)||(family&&sexual)),
    reason:(ageUnclear&&sexual)
      ?'The archived material combines school or age ambiguity with sexual themes. Automatic content generation is disabled for this record; review the original fields manually.'
      :(family&&sexual)
      ?'The archived material combines family relationships with sexual themes. Automatic draft generation is disabled for this record.'
      :'',
  };
}

export function buildOfflineDraft(field,evidence,conversation=null,style='faithful') {
  if(!DRAFT_FIELDS.includes(field))return {text:'',reason:'Not a supported reconstruction field.'};
  if(String(evidence?.[field]||'').trim())return {text:'',reason:'This field already has archived text. Restore or edit the original instead.'};
  const sensitivity=sensitiveRecoveryContext(evidence);
  if(sensitivity.blocked)return {text:'',reason:sensitivity.reason};
  const archive=[evidence?.greeting,evidence?.personality,evidence?.title].filter(Boolean).join('\n');
  const messageSample=(conversation?.messages||[]).filter(m=>m.role==='character'&&!m.ooc&&!m.regen)
    .slice(0,10).map(m=>String(m.text||'')).join('\n');
  const input=(archive+'\n'+messageSample).slice(0,12000);
  if(!input.trim())return {text:'',reason:'No archived greeting, personality, title or usable conversation evidence was found.'};
  const foundTraits=safeTraitWords.filter(([,re])=>re.test(input)).map(([name])=>name).slice(0,5);
  const where=knownSettings.find(([,re])=>re.test(input))?.[0];
  const name=String(evidence?.name||'{{char}}').trim().slice(0,70);
  const traitPhrase=foundTraits.length?foundTraits.join(', '):'not clearly established by the surviving evidence';
  let text='';
  if(field==='personality'){
    text=(foundTraits.length
      ? name+' appears '+traitPhrase+' in the surviving archived dialogue or character notes. '
      : name+'’s defining personality traits are not clearly documented in the saved evidence. ')+
      'Keep their speech consistent with the original greeting. Let motivations, relationships and boundaries develop through interaction rather than inventing them as historical facts.';
    if(style==='expanded')text+=' Show these qualities through choices and conversational reactions while allowing {{user}} to determine their own response.';
  } else if(field==='scenario'){
    text=(where?'The scene is set around '+where+', a location mentioned in the archived material. ':'The setting is not reliably established by the surviving records. ')+
      name+' and {{user}} begin from the situation established by the saved greeting. Continue naturally without adding unsupported events, identities, ages or relationship history. {{user}} chooses their own words, actions and decisions.';
    if(style==='expanded')text+=' Introduce only details that follow from the archived context; leave open questions for the interaction to resolve.';
  } else {
    text='{{char}}: "There is more to this conversation than we have established so far."\n'+
      '{{user}}: "[User reply]"\n'+
      '{{char}}: "We can work it out as we go."';
    if(foundTraits.length)text='[Illustrative exchange; observed traits: '+foundTraits.join(', ')+']\n'+text;
  }
  return {
    text,
    reason:foundTraits.length||where?'Guided draft using limited matching words and settings from observed text; wording is invented.':'Generic scaffold only; the surviving evidence is insufficient for character-specific conclusions.',
    method:'offline-guided',
    confidence:'low',
  };
}
