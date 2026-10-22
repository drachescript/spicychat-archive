// Offline Recovery Lab evidence hints and non-explicit scaffold drafts.
// No network, model, or Archive bot mutations; drafts remain user-editable.
export const DRAFT_FIELDS = Object.freeze(['personality','scenario','example_dialogue']);

const traits = [
 ['curious',/\bcurious|inquisitive|asks questions?\b/i],
 ['playful',/\bplayful|teasing|pesters?|joking|silly|goofy\b/i],
 ['reserved',/\breserved|quiet|shy|introverted\b/i],
 ['direct',/\bblunt|straightforward|direct\b/i],
 ['protective',/\bprotective|looks after|worries about\b/i],
 ['patient',/\bpatient|calm|gentle\b/i],
 ['energetic',/\benergetic|excitable|hyper|bouncy|hype\b/i],
 ['wry',/\bsarcastic|dry humor|deadpan\b/i],
 ['confident',/\bconfident|self-assured\b/i],
 ['thoughtful',/\bthoughtful|considerate|observant|sweet\b/i],
 ['determined',/\bdetermined|stubborn|persistent\b/i],
];
const places = [
 ['a library',/\blibrary\b/i],['a café',/\bcaf[eé]|coffee shop\b/i],
 ['a park',/\bpark\b/i],['a marketplace',/\bmarket|bazaar\b/i],
 ['an apartment',/\bapartment|flat\b/i],['a house',/\bhouse|home\b/i],
 ['a forest',/\bforest|woods\b/i],['a castle',/\bcastle\b/i],
 ['an office',/\boffice\b/i],['a village',/\bvillage\b/i],
 ['a tavern',/\btavern|inn\b/i],['a spaceship',/\bspaceship|starship\b/i],
];
const sexualSignals = /\b(?:nsfw|sexual|sex|sexy|naked|nude|erotic|porn|pregnan\w*|impregnat\w*|lactat\w*|breasts?|boobs?|tits?|fetish|lust|seduc\w*|orgasm\w*|thighs?|crotch|cameltoe|horny|intimat\w*|masturbat\w*|fuck\w*)\b/i;
const romanceSignals = /\b(?:lover|romanc\w*|seduc\w*|make out|kiss\w*|crush on|dating|boyfriend|girlfriend|soulmate|in love|flirt\w*)\b/i;
const ageSignals = /\b(?:high school|middle school|secondary school|schoolgirl|schoolboy|underage|minor|child|young teen)\b|\b(?:1[0-7]|[0-9])\s*(?:years? old|y\/o)\b/i;
const familySignals = /\b(?:sister|brother|step[- ]?family|stepsister|stepbrother|adopted daughter|adopted son|adopted sister|adopted brother|mother|father)\b/i;

export function sensitiveRecoveryContext(evidence) {
 const text=['name','title','greeting','personality','scenario','example_dialogue']
   .map(key=>String(evidence?.[key]||'')).join(' ');
 const ageUnclear=ageSignals.test(text);
 const family=familySignals.test(text);
 const sexual=sexualSignals.test(text);
 const restricted=sexual&&(ageUnclear||family);
 return {
  // Historical records remain viewable and missing fields can be rebuilt as
  // *non-explicit* everyday scenes; never generate sexualized youth/incest.
  blocked:false, restricted, ageUnclear, family,
  reason:restricted
   ?'The archived record combines sexual themes with school/age ambiguity or family relationships. Suggestions will use only safe character traits and everyday scene cues, not sexual or romantic content.'
   :'',
 };
}

function safeName(input){
 const clean=String(input||'{{char}}').trim().replace(/[\r\n<>]/g,' ').slice(0,70);
 return clean||'{{char}}';
}

export function safeRecoveryClues(evidence,conversation=null) {
 const name=safeName(evidence?.name);
 const previous=[evidence?.greeting,evidence?.personality,evidence?.title].filter(Boolean).join('\n');
 const characterMessages=(conversation?.messages||[])
   .filter(row=>row?.role==='character'&&!row.ooc&&!row.regen)
   .slice(0,12).map(row=>String(row.text||'')).join('\n');
 const source=(previous+'\n'+characterMessages).slice(0,16000);
 const opening=String(evidence?.greeting||'').slice(0,4000);
 const foundTraits=traits.filter(([,pattern])=>pattern.test(source)).map(([name])=>name).slice(0,5);
 const location=places.find(([,pattern])=>pattern.test(opening))?.[0]||null;
 const lowered=source.toLowerCase();
 const relationship=/\badopted sister|adoptive sister\b/i.test(source)?'adopted sister':
   /\badopted brother|adoptive brother\b/i.test(source)?'adopted brother':
   /\bstep[- ]?sister\b/i.test(source)?'stepsister':
   /\bstep[- ]?brother\b/i.test(source)?'stepbrother':
   /\broommate\b/i.test(source)?'roommate':
   /\b(?:best friend|close friend)\b/i.test(source)?'friend':null;
 const watchSomething=/\bwatch something|watch a (?:movie|show|film)|watch tv\b/i.test(opening);
 const bored=/\bbore+d+\b|\bbored\b/i.test(opening);
 const tease=/\bpesters?|teas\w*|joking|silly\b/i.test(source);
 const school=/\bhigh school|middle school|secondary school\b/i.test(source);
 // Only compact, non-explicit observations reach the AI prompt for risky bots.
 return {name,traits:foundTraits,location,relationship,watchSomething,bored,tease,school,
  hasContext:!!source.trim(),hasConversation:!!characterMessages.trim(),
  contextType:characterMessages?'archive plus selected character messages':'archived fields'};
}

export function safeRecoveryEvidencePrompt(evidence,conversation=null) {
 const facts=safeRecoveryClues(evidence,conversation);
 const info=[
  'Archived name: '+facts.name,
  facts.relationship?'Established non-romantic relationship to {{user}}: '+facts.relationship:'',
  facts.traits.length?'Observed voice/personality indicators: '+facts.traits.join(', '):'',
  facts.location?'Location mentioned in the archived greeting: '+facts.location:'',
  facts.bored?'Greeting cue: the character is bored and seeking company':'',
  facts.watchSomething?'Greeting cue: the character suggests watching something together':'',
  facts.tease?'Dialogue manner: lively or teasing':'',
  facts.school?'School is mentioned in the source, but no age or current scene location should be inferred':'',
  'Sources: '+facts.contextType,
 ].filter(Boolean);
 return info.join('\n');
}

export function validateSafeDraft(field,text,evidence) {
 if(!DRAFT_FIELDS.includes(field)&&field!=='greeting')return {ok:false,reason:'Not a supported narrative field.'};
 const content=String(text||'').trim();
 if(!content)return {ok:false,reason:'The model returned no text.'};
 if(content.length>12000)return {ok:false,reason:'Draft is too large; shorten it before use.'};
 const risk=sensitiveRecoveryContext(evidence);
 if(risk.restricted&&(sexualSignals.test(content)||romanceSignals.test(content)))
   return {ok:false,reason:'The suggestion included sexual or romantic material inappropriate for this archived context. No field was changed.'};
 return {ok:true,reason:''};
}

export function buildOfflineDraft(field,evidence,conversation=null,style='faithful') {
 if(!DRAFT_FIELDS.includes(field))return {text:'',reason:'Not a supported reconstruction field.'};
 if(String(evidence?.[field]||'').trim())
   return {text:'',reason:'This field already has archived text. Restore or edit the original instead.'};
 const facts=safeRecoveryClues(evidence,conversation);
 if(!facts.hasContext)return {text:'',reason:'No archived greeting, personality, title or usable conversation evidence was found.'};
 const name=facts.name;
 const relationship=facts.relationship
   ?name+' is {{user}}’s '+facts.relationship+'. '
   :'';
 const manner=facts.traits.length
   ?name+' comes across as '+facts.traits.join(', ')+'. '
   :'';
 let text='';
 if(field==='personality'){
  text=manner||name+'’s personality is only lightly established by the surviving records. ';
  text+='Build on the tone of the archived greeting. Let motivations and personal history emerge through interaction instead of inventing them as recovered facts.';
  if(style==='expanded')text+=' Respond consistently to other characters while leaving {{user}} in control of their own decisions.';
 }else if(field==='scenario'){
  text=relationship;
  if(facts.location)text+='The greeting mentions '+facts.location+'. ';
  if(facts.watchSomething)text+='In the saved opening, '+name+' wants to watch something together with {{user}}. ';
  else if(facts.bored)text+='In the saved opening, '+name+' is bored and seeking company. ';
  if(manner)text+=manner;
  if(!text)text='The surviving opening establishes a conversation between '+name+' and {{user}}, but the specific setting is not documented. ';
  text+='Continue with ordinary conversation and character-consistent actions. {{user}} decides what to say and do; do not invent an age or unsupported shared history.';
  if(style==='expanded')text+=' Treat unrecorded details as open possibilities, not historical fact.';
 }else{
  if(facts.watchSomething)text='{{char}}: "hey, {{user}}, i’m bored. wanna watch something?"\n'+
    '{{user}}: "[Your response]"\n'+
    '{{char}}: "c’mon, help me pick something good!"';
  else if(facts.bored)text='{{char}}: "hey, {{user}}, i’m bored. got any ideas?"\n'+
    '{{user}}: "[Your response]"\n'+
    '{{char}}: "okay, i’m listening!"';
  else if(facts.location)text='{{char}}: "hey, {{user}}. what do you think of this place?"\n'+
    '{{user}}: "[Your response]"\n'+
    '{{char}}: "tell me what’s on your mind."';
  else text='{{char}}: "hey, {{user}}. got a minute?"\n'+
    '{{user}}: "[Your response]"\n'+
    '{{char}}: "sure, what’s on your mind?"';
 }
 const review=validateSafeDraft(field,text,evidence);
 if(!review.ok)return {text:'',reason:review.reason};
 return {text,reason:'Proposed wording built from '+facts.contextType+'; it is not the original creator text. '+
  (sensitiveRecoveryContext(evidence).restricted?'Only non-explicit everyday interactions are used.':''),
  method:'offline-guided',confidence:'low'};
}


// Expand an existing (possibly archived) narrative field with a separate,
// non-destructive ADDITION. Do not copy or modify the archived text itself.
// Acceptance is a separate user action that marks the editor's field modified.
export const EXPAND_FIELDS = Object.freeze(['personality','greeting','scenario','example_dialogue']);

export function buildOfflineExpansion(field,evidence,conversation=null) {
 if(!EXPAND_FIELDS.includes(field))return {text:'',reason:'Not an expandable narrative field.'};
 if(!String(evidence?.[field]||'').trim())
  return {text:'',reason:'This field is empty; use Quick Draft to create it first.'};
 const facts=safeRecoveryClues(evidence,conversation);
 if(!facts.hasContext)return {text:'',reason:'Not enough saved character evidence to expand this field.'};
 const name=facts.name;
 const characteristic=facts.traits.length?facts.traits.join(', '):'the behavior established by the archived greeting';
 let addition='';
 if(field==='personality') {
  const parts=[
   'In ordinary conversation, '+name+' shows '+characteristic+' tendencies through how they respond to others.',
   facts.bored?name+' frequently seeks out something to do and likes having company when restless.':'',
   facts.tease?name+' tends toward light teasing and quick, lively remarks.':'',
   facts.watchSomething?name+' readily suggests shared activities, such as picking something to watch.':'',
   'Keep this characterization consistent with the archived dialogue and allow {{user}} to decide their own actions and replies.'
  ];
  addition=parts.filter(Boolean).join(' ');
 } else if(field==='scenario') {
  addition=(facts.watchSomething?name+' has suggested watching something together, leaving the choice of what to watch open to {{user}}. ':
   facts.bored?name+' is looking for a way to pass the time with {{user}}. ':'')+
   (facts.location?'The original opening refers to '+facts.location+'. ':'')+
   (facts.tease?'Their banter can stay lighthearted and energetic. ':'')+
   'The scene can progress through dialogue and small everyday choices without assuming what {{user}} says or does.';
 } else if(field==='greeting') {
  addition=facts.watchSomething
   ?'*'+name+' flips through the available shows with obvious impatience, glancing over for a suggestion.* "okay, your pick first!"'
   :facts.bored
   ?'*'+name+' drums their fingers, looking for something to do.* "so, any ideas?"'
   :'*'+name+' pauses, leaving room for {{user}} to answer before continuing.*';
 } else {
  addition=facts.watchSomething
   ?'{{char}}: "you pick the first show, and i’ll pick the next one!"\n{{user}}: "[Your response]"\n{{char}}: "deal! let’s see what you choose."'
   :facts.bored
   ?'{{char}}: "got any ideas for today?"\n{{user}}: "[Your response]"\n{{char}}: "oh, tell me more!"'
   :'{{char}}: "what would you like to do next?"\n{{user}}: "[Your response]"\n{{char}}: "okay, i’m listening."';
 }
 const review=validateSafeDraft(field,addition,evidence);
 if(!review.ok)return {text:'',reason:review.reason};
 return {
  text:addition,kind:'expand',method:'offline-guided',confidence:'low',
  reason:'Additional suggested wording based on '+facts.contextType+'. The original text is unchanged until you explicitly append this addition.',
 };
}
