// This page reads two static GitHub Pages JSON files only. It does not call
// SpicyChat, Cloudflare R2, or any background moderation service.
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STORAGE='spicychat-archive-inconclusive-correction-drafts-v1';
const CHUNK=48;
const labels={'api-error':'API error','empty-response':'Empty 200 response','awaiting-confirmation':'Awaiting second 404'};
const designations=new Map([
  ['inconclusive','Still inconclusive'],['public','Appears public'],
  ['unavailable','Appears unavailable'],['restricted','Appears restricted']
]);
let cases=[],published=[],drafts={},visible=CHUNK,ready=false,generatedAt=null;
try{
  const raw=JSON.parse(localStorage.getItem(STORAGE)||'{}');
  if(raw&&typeof raw==='object'&&!Array.isArray(raw))drafts=raw;
}catch{}

function validId(v){v=String(v||'').toLowerCase();return UUID.test(v)?v:'';}
function safeUrl(value){
  try{
    const u=new URL(String(value||'').trim());
    return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';
  }catch{return '';}
}
function normalizeReview(row){
  const id=validId(row?.id);
  const designation=String(row?.designation||'').toLowerCase();
  if(!id||!designations.has(designation))return null;
  const result={id,designation};
  const note=String(row.note||'').trim().slice(0,500);
  if(note)result.note=note;
  const evidenceUrl=safeUrl(row.evidenceUrl);
  if(evidenceUrl)result.evidenceUrl=evidenceUrl;
  const correctBotId=validId(row.correctBotId);
  if(correctBotId&&correctBotId!==id)result.correctBotId=correctBotId;
  return result;
}
function merged(){
  const map=new Map(published.map(row=>[row.id,row]));
  for(const [id,row] of Object.entries(drafts)){
    if(!validId(id))continue;
    const normalized=normalizeReview(row);
    if(normalized)map.set(id,normalized);
  }
  // Only export currently unresolved IDs. The server makes the same check
  // and cleans its committed annotations on the next successful run.
  const unresolved=new Set(cases.map(row=>row.id));
  return [...map.values()].filter(row=>unresolved.has(row.id)).sort((a,b)=>a.id.localeCompare(b.id));
}
function decisionFor(id){
  if(Object.hasOwn(drafts,id)){
    const x=normalizeReview(drafts[id]);if(x)return x;
  }
  return published.find(x=>x.id===id)||null;
}
function saveDrafts(){
  try{localStorage.setItem(STORAGE,JSON.stringify(drafts));return true;}catch{return false;}
}
function reviewJson(){return JSON.stringify({schemaVersion:1,corrections:merged()},null,2)+'\n';}
function status(message){$('#ic-draft-status').textContent=message;}
function render(){
  const category=$('#ic-filter').value,search=$('#ic-search').value.trim().toLowerCase(),
    reviewed=$('#ic-reviewed').value;
  const found=cases.filter(row=>{
    if(category!=='all'&&row.category!==category)return false;
    if(search&&!row.id.includes(search))return false;
    const hasReview=!!decisionFor(row.id);
    return reviewed==='all'||(reviewed==='reviewed'&&hasReview)||(reviewed==='pending'&&!hasReview);
  });
  $('#ic-result-count').textContent=found.length.toLocaleString()+' matching cases';
  const list=$('#ic-list');list.innerHTML='';
  if(!found.length){
    list.innerHTML='<p class="ic-empty">'+(ready?'No candidates match these filters. This is not evidence that every ID is available.':'No review index has been generated yet. The next archive run will populate it.')+'</p>';
  }
  for(const row of found.slice(0,visible)){
    const review=decisionFor(row.id)||{designation:'inconclusive'};
    const el=document.createElement('article');el.className='ic-card';el.dataset.id=row.id;
    const opts=[...designations].map(([value,label])=>'<option value="'+value+'"'+(review.designation===value?' selected':'')+'>'+esc(label)+'</option>').join('');
    const when=row.lastCheckedAt?new Date(row.lastCheckedAt).toLocaleString():'Unknown';
    const extra=row.category==='awaiting-confirmation'
      ?'This 404 needs independent confirmation in a later run.'
      :row.category==='empty-response'?'HTTP 200 contained no usable bot data.': 'The server or network response was not conclusive.';
    el.innerHTML='<div class="ic-card-head"><strong>'+esc(labels[row.category]||'Inconclusive')+'</strong>'+
      '<span class="ic-chip">HTTP '+esc(row.httpStatus??'?')+'</span></div>'+
      '<div class="ic-id">'+esc(row.id)+'</div>'+
      '<div class="ic-meta">Last checked: '+esc(when)+' · 404 confirmations: '+Number(row.confirmations||0)+
      (row.errorKind?' · '+esc(row.errorKind):'')+'</div>'+
      '<div class="ic-meta">'+esc(extra)+'</div>'+
      '<div class="ic-links"><a target="_blank" rel="noopener noreferrer" href="'+esc(row.archiveUrl)+'">Archive bot</a>'+
      '<a target="_blank" rel="noopener noreferrer" href="'+esc(row.spicychatUrl)+'">SpicyChat bot</a></div>'+
      '<details><summary>'+(review.note||review.evidenceUrl||review.designation!=='inconclusive'?'Edit existing review':'Add manual review')+'</summary>'+
      '<div class="ic-edit"><label>Proposed designation<select data-field="designation">'+opts+'</select></label>'+
      '<label>Evidence URL (HTTPS)<input data-field="evidenceUrl" type="url" placeholder="https://…" value="'+esc(review.evidenceUrl||'')+'"></label>'+
      '<label>Note / explanation<input data-field="note" type="text" maxlength="500" placeholder="What did you verify?" value="'+esc(review.note||'')+'"></label>'+
      '<label>Correct bot ID, if this is an ID mismatch<input data-field="correctBotId" type="text" placeholder="UUID (optional)" value="'+esc(review.correctBotId||'')+'"></label>'+
      '<div class="ic-buttons"><button data-action="save" type="button">Save review draft</button>'+
      '<button data-action="reset" type="button">Reset local draft</button></div></div></details>';
    list.append(el);
  }
  const more=$('#ic-more');
  more.hidden=found.length<=visible;
  if(!more.hidden)more.textContent='Show more ('+(found.length-visible).toLocaleString()+' remaining)';
  status(merged().length.toLocaleString()+' reviewed designations in current corrections JSON. Draft edits stay in this browser until you copy or download the file.');
}
function saveReview(card){
  const id=validId(card.dataset.id);
  if(!id)return;
  const get=(name)=>card.querySelector('[data-field="'+name+'"]')?.value?.trim()||'';
  const designation=get('designation');
  const raw={id,designation,note:get('note'),evidenceUrl:get('evidenceUrl'),correctBotId:get('correctBotId')};
  if(raw.evidenceUrl&&!safeUrl(raw.evidenceUrl)){
    status('Evidence URL must be a complete HTTPS link.');return;
  }
  if(raw.correctBotId&&!validId(raw.correctBotId)){
    status('Correct bot ID must be a valid UUID or left blank.');return;
  }
  const review=normalizeReview(raw);
  if(!review){status('Invalid review designation.');return;}
  drafts[id]=review;
  if(!saveDrafts()){status('Browser storage unavailable. Copy the JSON before leaving.');return;}
  render();
  status('Review draft saved locally. Copy or download the JSON to publish your corrections.');
}
$('#ic-list').addEventListener('click',event=>{
  const button=event.target.closest('button[data-action]');
  if(!button)return;
  const card=button.closest('.ic-card');
  if(!card)return;
  if(button.dataset.action==='save')saveReview(card);
  if(button.dataset.action==='reset'){
    delete drafts[card.dataset.id];saveDrafts();render();
    status('Local draft reset. Previously committed reviews can be edited on GitHub.');
  }
});
for(const input of ['#ic-filter','#ic-reviewed','#ic-search']){
  $(input).addEventListener(input==='#ic-search'?'input':'change',()=>{visible=CHUNK;render();});
}
$('#ic-more').addEventListener('click',()=>{visible+=CHUNK;render();});
$('#ic-copy').addEventListener('click',async()=>{
  const content=reviewJson();
  try{
    await navigator.clipboard.writeText(content);
    status('Corrections JSON copied. Paste it into data/inconclusive-corrections.json on GitHub.');
  }catch{
    const field=document.createElement('textarea');
    field.value=content;field.style.cssText='position:fixed;left:-9999px;top:0';
    document.body.append(field);field.select();
    const ok=document.execCommand('copy');field.remove();
    status(ok?'Corrections JSON copied.':'Copy unavailable; use Download corrections JSON.');
  }
});
$('#ic-download').addEventListener('click',()=>{
  const blob=new Blob([reviewJson()],{type:'application/json'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download='inconclusive-corrections.json';document.body.append(link);
  link.click();link.remove();URL.revokeObjectURL(url);
  status('Download prepared. Commit the resulting file to data/inconclusive-corrections.json for the next archive run.');
});
async function load(){
  try{
    const [indexResponse,correctionsResponse]=await Promise.all([
      fetch('../data/inconclusive.json',{cache:'no-cache'}),
      fetch('../data/inconclusive-corrections.json',{cache:'no-cache'})
    ]);
    if(!indexResponse.ok)throw Error('Review index HTTP '+indexResponse.status);
    const index=await indexResponse.json();
    if(index.schemaVersion!==1||!Array.isArray(index.cases))throw Error('Unexpected review index format');
    cases=index.cases.filter(x=>validId(x.id)&&labels[x.category]);
    generatedAt=index.generatedAt;
    if(correctionsResponse.ok){
      const corrections=await correctionsResponse.json();
      published=(Array.isArray(corrections.corrections)?corrections.corrections:[])
        .map(normalizeReview).filter(Boolean);
    }
    // Resolved IDs disappear from the JSON and locally saved drafts alike.
    if(generatedAt){
      const unresolved=new Set(cases.map(x=>x.id));
      for(const id of Object.keys(drafts))if(!unresolved.has(id))delete drafts[id];
      saveDrafts();
    }
    ready=Boolean(generatedAt);
    const counts=index.counts||{};
    $('#ic-summary').textContent=
      Number(index.pendingTotal||0).toLocaleString()+' total pending IDs · '+
      Number(index.notYetChecked||0).toLocaleString()+' not yet checked · '+
      Number(counts['api-error']||0).toLocaleString()+' API errors · '+
      Number(counts['empty-response']||0).toLocaleString()+' empty responses · '+
      Number(counts['awaiting-confirmation']||0).toLocaleString()+' awaiting another 404';
    $('#ic-updated').textContent=generatedAt?'Last generated: '+new Date(generatedAt).toLocaleString()+
      ' · Data updates after successful archive runs.':
      'The index will be populated by the next successful archive run.';
    render();
  }catch(error){
    $('#ic-summary').textContent='Review data unavailable: '+error.message;
    $('#ic-result-count').textContent='Unavailable';
    status('The archive queue was not changed.');
  }
}
void load();
