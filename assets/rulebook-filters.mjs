// Shared Rulebook tag matching for public and no-longer-public listings.
export function parseTagList(value) {
  const items=Array.isArray(value)?value:String(value||'').split(',');
  const seen=new Set();
  return items.map(x=>String(x).trim()).filter(tag=>{
    const key=tag.toLowerCase();
    if(!key||seen.has(key))return false;
    seen.add(key);return true;
  });
}
export function matchesTagFilters(tags, included, excluded, match='any') {
  const own=new Set(parseTagList(Array.isArray(tags)?tags:[]).map(t=>t.toLowerCase()));
  const yes=parseTagList(included).map(t=>t.toLowerCase());
  const no=parseTagList(excluded).map(t=>t.toLowerCase());
  if(no.some(t=>own.has(t)))return false;
  if(!yes.length)return true;
  return match==='all'?yes.every(t=>own.has(t)):yes.some(t=>own.has(t));
}
export function groupTags(groups, observed) {
  const seen=new Set(),result=[];
  for(const group of Array.isArray(groups)?groups:[]) {
    if(!group||!Array.isArray(group.tags))continue;
    const tags=parseTagList(group.tags).filter(tag=>{
      const key=tag.toLowerCase();if(seen.has(key))return false;seen.add(key);return true;
    });
    if(tags.length)result.push({name:String(group.name||'Tags'),tags});
  }
  const extra=parseTagList(observed).filter(tag=>!seen.has(tag.toLowerCase()))
    .sort((a,b)=>a.localeCompare(b));
  if(extra.length)result.push({name:'Other',tags:extra});
  return result;
}
