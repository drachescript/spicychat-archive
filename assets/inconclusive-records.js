// Shared by the live review page and the generated-data regression tests.
// Accept the archive's canonical schema, and tolerate older/partial snapshots.
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const URL_UUID=/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\b|$)/i;
export const REVIEW_CATEGORIES=['api-error','empty-response','awaiting-confirmation'];

export function validBotId(value){
  const raw=String(value??'').trim();
  return UUID.test(raw)?raw.toLowerCase():'';
}
function extractIdFromLink(value){
  const text=String(value??'');
  const match=text.match(URL_UUID);
  return match?validBotId(match[1]):'';
}
export function normalizeReviewCase(row){
  if(!row||typeof row!=='object'||Array.isArray(row))return null;
  const id=validBotId(row.id)||validBotId(row.botId)||
    validBotId(row.characterId)||extractIdFromLink(row.spicychatUrl)||
    extractIdFromLink(row.archiveUrl);
  if(!id)return null;
  const httpStatus=Number(row.httpStatus??row.lastStatus??0);
  let category=String(row.category??'').trim().toLowerCase();
  if(!REVIEW_CATEGORIES.includes(category)){
    // Older exports may lack category; use their last API outcome.
    category=httpStatus===404?'awaiting-confirmation':
      httpStatus===200?'empty-response':'api-error';
  }
  return {
    ...row,id,category,httpStatus,
    confirmations:Math.max(0,Number(row.confirmations??row.count??
      (category==='awaiting-confirmation'?1:0))||0),
    // Never accept arbitrary link targets from JSON.
    spicychatUrl:'https://spicychat.ai/chatbot/'+id,
    archiveUrl:'https://spicychatarchive.drache.uk/bot/?id='+id
  };
}
export function reviewSourceDiagnostic(name,rawRows,normalizedCount){
  if(!Array.isArray(rawRows))return name+': cases is not an array';
  if(!rawRows.length)return name+': 0 raw records';
  if(normalizedCount)return name+': '+normalizedCount+' of '+rawRows.length+' accepted';
  const first=rawRows[0];
  if(!first||typeof first!=='object')return name+': no usable entries; first record type='+typeof first;
  return name+': 0 of '+rawRows.length+' accepted; first record fields='+
    Object.keys(first).slice(0,12).join(',')+
    '; first id='+String(first.id??'(missing)').slice(0,80)+
    '; first category='+String(first.category??'(missing)').slice(0,60);
}
