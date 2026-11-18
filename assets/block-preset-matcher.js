// Shared, dependency-free, opt-in title/name matching reference for Archive and QoL.
// Does not infer the gender of a bot or apply native blocks.
export function normalizePresetText(input) {
  return String(input ?? '')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
}
function phraseIn(text, phrase) {
  const needle=normalizePresetText(phrase);
  return !!needle && (' '+text+' ').includes(' '+needle+' ');
}
function matchAlias(text, aliases, context) {
  return (aliases || []).filter(alias => {
    const value=typeof alias==='string'?alias:alias?.text;
    const strength=typeof alias==='string'?'strong':alias?.strength;
    return strength==='strong' && phraseIn(text,value) ||
      strength==='contextual' && context && phraseIn(text,value);
  });
}
function isGenderVariant(text) {
  // Identity of an RP character may be different from the canonical character.
  // Don't turn a canonical gender into a bot-gender verdict in that case.
  return /\b(?:gender ?bent|gender ?bend|gender ?swap|gender ?flipped|fem(?:ale)? version|male version|fem ?ghost|female ghost|male katara|female price|fem ?soap)\b/.test(text);
}
export function evaluateBlockPreset(catalog, presetId, title, options = {}) {
  const preset=catalog?.presets?.find(p=>p.id===presetId);
  const text=normalizePresetText(title);
  const unknown={matched:false,hide:false,reason:'no confident name/title match',characterIds:[],gender:'unknown'};
  if(!preset || !text) return unknown;
  if(preset.kind==='theme') {
    const aliases=matchAlias(text,preset.aliases);
    return aliases.length?{matched:true,hide:true,reason:'explicit theme title wording',characterIds:[],gender:'unknown',terms:aliases.map(a=>typeof a==='string'?a:a.text)}:unknown;
  }
  const chars=(catalog.characters||[]).filter(c=>c.franchiseId===preset.franchiseId);
  const groups=(catalog.groups||[]).filter(g=>g.franchiseId===preset.franchiseId);
  const directFranchise=matchAlias(text,preset.aliases);
  const matchedGroups=groups.filter(g=>matchAlias(text,g.aliases).length);
  // A group mention alone never determines which specific member is the main bot.
  const context=directFranchise.some(a=>a.strength==='strong');
  const matchedChars=chars.filter(c=>matchAlias(text,c.aliases,context).length);
  // Contextual franchise words need corroboration from a recognisable character.
  const strongFranchise=directFranchise.some(a=>a.strength==='strong');
  const eligible=strongFranchise || matchedGroups.length>0 || matchedChars.length>0;
  if(!eligible) return unknown;
  const mode=options.mode||'all';
  const keepCharacters=new Set(options.keepCharacterIds||[]);
  const keepGenders=new Set([...(mode==='keep-female'?['female']:mode==='keep-male'?['male']:[]),
    ...(mode==='custom'?options.keepGenders||[]:[])]);
  const genderVariant=isGenderVariant(text);
  const matchedIds=matchedChars.map(c=>c.id);
  const selected=matchedChars.filter(c=>keepCharacters.has(c.id) || keepGenders.has(c.gender));
  const allKnown=matchedChars.length>0 && !genderVariant && matchedChars.every(c=>c.gender!=='unknown');
  const gender=genderVariant || matchedChars.length!==1?'unknown':matchedChars[0].gender;
  let hide=false,reason='';
  if(selected.length) { hide=false; reason='explicit keep exception for a recognized character'; }
  else if(mode==='all') { hide=true;reason='franchise, group, or character name matched'; }
  else if(genderVariant) {hide=false;reason='gender-variant roleplay: canonical gender not applied';}
  else if(!allKnown) { hide=false;reason='no confident character identity; keep unknown';}
  else if(mode==='keep-female'||mode==='keep-male'||mode==='custom') {
    // If no characters of a selected keep-gender are found, the identified
    // other-gender characters are excluded. Mixed titles containing kept
    // characters were handled above.
    hide=true;reason='identified character(s) outside keep exceptions';
  } else return { ...unknown,reason:'invalid preset mode' };
  return {matched:true,hide,reason,presetId:preset.id,characterIds:matchedIds,
    gender,groupIds:matchedGroups.map(g=>g.id),terms:directFranchise.map(a=>a.text)};
}
