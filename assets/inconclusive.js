import { validBotId, normalizeReviewCase, reviewSourceDiagnostic } from './inconclusive-records.js';
import { archivedBotRecordUrl, archivedBotPreview } from './inconclusive-metadata.js';
// This page reads two static GitHub Pages JSON files only. It does not call
// SpicyChat, Cloudflare R2, or any background moderation service.
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const validId=validBotId;
const STORAGE='spicychat-archive-inconclusive-correction-drafts-v1';
const CHUNK=48;
const PREVIEW_CONCURRENCY=3;
const labels={'api-error':'API error','empty-response':'Empty 200 response','awaiting-confirmation':'Awaiting second 404'};
const designations=new Map([
  ['unreviewed','Not reviewed yet'],['inconclusive','Still inconclusive'],['public','Appears public'],
  ['unavailable','Appears unavailable'],['restricted','Appears restricted']
]);
let cases=[],published=[],drafts={},visible=CHUNK,ready=false,generatedAt=null,loading=false;
try{
  const raw=JSON.parse(localStorage.getItem(STORAGE)||'{}');
  if(raw&&typeof raw==='object'&&!Array.isArray(raw))drafts=raw;
}catch{}

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
const normalizeCase=normalizeReviewCase;
// Metadata is loaded only when a card enters the viewport. This avoids
// fetching ~2,000 individual records on every visit to the review page.
const previewCache=new Map();
const previewRequested=new Set();
const previewQueue=[];
let previewActive=0;
const previewObserver=typeof IntersectionObserver==='function'
  ? new IntersectionObserver(entries=>{
      for(const entry of entries){
        if(!entry.isIntersecting)continue;
        previewObserver.unobserve(entry.target);
        queuePreview(entry.target.dataset.id);
      }
    },{rootMargin:'180px 0px',threshold:0.01})
  : null;

function paintPreview(card,preview){
  if(!card||!card.isConnected)return;
  const heading=card.querySelector('.ic-bot-name');
  const description=card.querySelector('.ic-bot-description');
  const photo=card.querySelector('.ic-bot-avatar');
  if(!heading||!description||!photo)return;
  heading.textContent=preview?.name||'Name not saved';
  const details=[preview?.subtitle,preview?.creator?'@'+preview.creator:''].filter(Boolean);
  description.textContent=details.join(' · ')||'';
  description.hidden=!details.length;
  photo.replaceChildren();
  if(preview?.image){
    const img=document.createElement('img');
    img.src=preview.image;
    img.alt=preview.name?preview.name+' avatar':'Archived bot avatar';
    img.loading='lazy';
    img.decoding='async';
    img.referrerPolicy='no-referrer';
    img.addEventListener('error',()=>{
      if(preview.fallbackImage&&img.src!==preview.fallbackImage){
        img.src=preview.fallbackImage;
      }else{
        photo.textContent='No saved image';
      }
    });
    photo.append(img);
  }else{
    photo.textContent='No saved image';
  }
}
function updateVisiblePreviews(id,preview){
  for(const card of document.querySelectorAll('#ic-list .ic-card')){
    if(card.dataset.id===id)paintPreview(card,preview);
  }
}
async function getArchivedPreview(id){
  const url=archivedBotRecordUrl(id);
  if(!url)return null;
  try{
    // Same existing public archive record used on the bot detail page.
    // The page's shard-fetch helper also supports older sharded records.
    const response=await fetch(url,{cache:'force-cache'});
    if(!response.ok)return null;
    const record=await response.json();
    if(record?.id&&validId(record.id)!==id)return null;
    return archivedBotPreview(record);
  }catch{return null;}
}
function pumpPreviews(){
  while(previewActive<PREVIEW_CONCURRENCY&&previewQueue.length){
    const id=previewQueue.shift();
    previewActive++;
    void getArchivedPreview(id).then(preview=>{
      previewCache.set(id,preview);
      updateVisiblePreviews(id,preview);
    }).finally(()=>{previewActive--;pumpPreviews();});
  }
}
function queuePreview(id){
  if(!validId(id))return;
  if(previewCache.has(id)){
    updateVisiblePreviews(id,previewCache.get(id));
    return;
  }
  if(previewRequested.has(id))return;
  previewRequested.add(id);
  previewQueue.push(id);
  pumpPreviews();
}
function merged(){
  const map=new Map(published.map(row=>[row.id,row]));
  for(const [id,row] of Object.entries(drafts)){
    if(!validId(id))continue;
    const normalized=normalizeReview(row);
    if(normalized)map.set(id,normalized);
  }
  // Download is a prefilled, editable review queue, not an empty list of
  // decisions. Save all unresolved cases with links and status evidence;
  // manual designations overwrite only the relevant fields.
  return cases.map(c=>{
    const review=map.get(c.id)||{designation:'unreviewed'};
    return {
      id:c.id,designation:review.designation,category:c.category,
      httpStatus:c.httpStatus,lastCheckedAt:c.lastCheckedAt,
      spicychatUrl:c.spicychatUrl,archiveUrl:c.archiveUrl,
      note:review.note||'',
      ...(review.evidenceUrl?{evidenceUrl:review.evidenceUrl}:{}),
      ...(review.correctBotId?{correctBotId:review.correctBotId}:{})
    };
  }).sort((a,b)=>a.id.localeCompare(b.id));
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
    const hasReview=Boolean(decisionFor(row.id)&&decisionFor(row.id).designation!=='unreviewed');
    return reviewed==='all'||(reviewed==='reviewed'&&hasReview)||(reviewed==='pending'&&!hasReview);
  });
  $('#ic-result-count').textContent=found.length.toLocaleString()+' matching cases';
  const list=$('#ic-list');list.innerHTML='';
  if(!found.length){
    const message=cases.length
      ? 'No review candidates match the current filters. There are '+cases.length.toLocaleString()+' cases loaded; clear the filters to see them.'
      : ready?'The review index contains no cases. Try Reload review data, or check the warning above.':'No review index has been generated yet. The next archive run will populate it.';
    list.innerHTML='<p class="ic-empty">'+esc(message)+'</p>';
  }
  for(const row of found.slice(0,visible)){
    const review=decisionFor(row.id)||{designation:'unreviewed'};
    const el=document.createElement('article');el.className='ic-card';el.dataset.id=row.id;
    const opts=[...designations].map(([value,label])=>'<option value="'+value+'"'+(review.designation===value?' selected':'')+'>'+esc(label)+'</option>').join('');
    const when=row.lastCheckedAt?new Date(row.lastCheckedAt).toLocaleString():'Unknown';
    const extra=row.category==='awaiting-confirmation'
      ?'This 404 needs independent confirmation in a later run.'
      :row.category==='empty-response'?'HTTP 200 contained no usable bot data.': 'The server or network response was not conclusive.';
    el.innerHTML='<div class="ic-card-head"><strong>'+esc(labels[row.category]||'Inconclusive')+'</strong>'+
      '<span class="ic-chip">HTTP '+esc(row.httpStatus??'?')+'</span></div>'+
      '<div class="ic-bot-preview"><div class="ic-bot-avatar">No image</div>'+
      '<div class="ic-bot-text"><h3 class="ic-bot-name">Checking saved details…</h3>'+
      '<p class="ic-bot-description" hidden></p></div></div>'+
      '<div class="ic-id">'+esc(row.id)+'</div>'+
      '<div class="ic-meta">Last checked: '+esc(when)+' · 404 confirmations: '+Number(row.confirmations||0)+
      (row.errorKind?' · '+esc(row.errorKind):'')+'</div>'+
      '<div class="ic-meta">'+esc(extra)+'</div>'+
      '<div class="ic-links"><a target="_blank" rel="noopener noreferrer" href="'+esc(row.archiveUrl)+'">Archive bot</a>'+
      '<a target="_blank" rel="noopener noreferrer" href="'+esc(row.spicychatUrl)+'">SpicyChat bot</a></div>'+
      '<details><summary>'+(review.note||review.evidenceUrl||review.designation!=='unreviewed'?'Edit existing review':'Add manual review')+'</summary>'+
      '<div class="ic-edit"><label>Proposed designation<select data-field="designation">'+opts+'</select></label>'+
      '<label>Evidence URL (HTTPS)<input data-field="evidenceUrl" type="url" placeholder="https://…" value="'+esc(review.evidenceUrl||'')+'"></label>'+
      '<label>Note / explanation<input data-field="note" type="text" maxlength="500" placeholder="What did you verify?" value="'+esc(review.note||'')+'"></label>'+
      '<label>Correct bot ID, if this is an ID mismatch<input data-field="correctBotId" type="text" placeholder="UUID (optional)" value="'+esc(review.correctBotId||'')+'"></label>'+
      '<div class="ic-buttons"><button data-action="save" type="button">Save review draft</button>'+
      '<button data-action="reset" type="button">Reset local draft</button></div></div></details>';
    list.append(el);
    if(previewCache.has(row.id))paintPreview(el,previewCache.get(row.id));
    else if(previewObserver)previewObserver.observe(el);
    else queuePreview(row.id);
  }
  const more=$('#ic-more');
  more.hidden=found.length<=visible;
  if(!more.hidden)more.textContent='Show more ('+(found.length-visible).toLocaleString()+' remaining)';
  const rows=merged();
  status(rows.length.toLocaleString()+' IDs prefilled in corrections JSON · '+
    rows.filter(row=>row.designation!=='unreviewed').length.toLocaleString()+
    ' manually designated. Changes stay in this browser until you copy or download the JSON.');
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
$('#ic-clear-filters').addEventListener('click',()=>{
  $('#ic-filter').value='all';
  $('#ic-reviewed').value='all';
  $('#ic-search').value='';
  visible=CHUNK;render();
});
$('#ic-reload').addEventListener('click',()=>void load());
// Reset restored filters on first view, so an old selection cannot hide cases.
$('#ic-filter').value='all';
$('#ic-reviewed').value='all';
$('#ic-search').value='';
$('#ic-copy').addEventListener('click',async()=>{
  if(!cases.length){status('No review cards loaded. Reload review data before exporting.');return;}
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
  if(!cases.length){status('No review cards loaded. Reload review data before exporting.');return;}
  const blob=new Blob([reviewJson()],{type:'application/json'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download='inconclusive-corrections.json';document.body.append(link);
  link.click();link.remove();URL.revokeObjectURL(url);
  status('Download prepared. Commit the resulting file to data/inconclusive-corrections.json for the next archive run.');
});
async function load(){
  if(loading)return;
  loading=true;
  $('#ic-reload').disabled=true;
  $('#ic-updated').textContent='Loading the latest review data…';
  try{
    // Bypass stale browser/CDN copies. Each source can recover the other:
    // the corrections JSON includes a full snapshot of unresolved review IDs.
    const stamp=Date.now();
    const results=await Promise.allSettled([
      fetch('../data/inconclusive.json?v='+stamp,{cache:'no-store'}),
      fetch('../data/inconclusive-corrections.json?v='+stamp,{cache:'no-store'})
    ]);
    const readJson=async (result,name)=>{
      if(result.status!=='fulfilled')throw Error(name+': '+String(result.reason));
      if(!result.value.ok)throw Error(name+' HTTP '+result.value.status);
      return result.value.json();
    };
    const [indexResult,correctionsResult]=await Promise.allSettled([
      readJson(results[0],'Review index'),
      readJson(results[1],'Corrections')
    ]);
    const index=indexResult.status==='fulfilled'?indexResult.value:null;
    const corrections=correctionsResult.status==='fulfilled'?correctionsResult.value:null;
    if(index&&(index.schemaVersion!==1||!Array.isArray(index.cases)))
      throw Error('Unexpected review index format');
    if(corrections&&(corrections.schemaVersion!==1||!Array.isArray(corrections.corrections)))
      throw Error('Unexpected corrections format');
    if(!index&&!corrections)throw Error('Both review JSON files were unavailable');
    const indexCases=(index?.cases||[]).map(normalizeCase).filter(Boolean);
    const correctionCases=(corrections?.corrections||[]).map(normalizeCase).filter(Boolean);
    const expected=Math.max(Number(index?.caseCount||0),
      Object.values(index?.counts||{}).reduce((n,v)=>n+(Number(v)||0),0));
    const recovered=correctionCases.length>indexCases.length&&
      (indexCases.length<expected||!index);
    cases=recovered?correctionCases:indexCases;
    if(!cases.length&&correctionCases.length)cases=correctionCases;
    if(expected>0&&!cases.length)throw Error(
      'The index reports '+expected+' cases, but the data did not contain usable review records. '+
      reviewSourceDiagnostic('Index',index?.cases||[],indexCases.length)+'. '+
      reviewSourceDiagnostic('Corrections',corrections?.corrections||[],correctionCases.length));
    generatedAt=index?.generatedAt||null;
    published=(corrections?.corrections||[]).map(normalizeReview).filter(Boolean);
    // Keep local annotations if files have differing snapshots. A complete,
    // consistent index is needed before any draft can be pruned.
    if(generatedAt&&indexCases.length>=expected&&
      (!correctionCases.length||correctionCases.length===indexCases.length)){
      const unresolved=new Set(cases.map(x=>x.id));
      for(const id of Object.keys(drafts))if(!unresolved.has(id))delete drafts[id];
      saveDrafts();
    }
    ready=Boolean(index?.generatedAt||cases.length);
    const counts=index?.counts||{};
    const total=Number(index?.pendingTotal||0),unqueried=Number(index?.notYetChecked||0);
    $('#ic-summary').textContent=
      (index?total.toLocaleString()+' total pending IDs · '+
      unqueried.toLocaleString()+' not yet checked · '+
      Number(counts['api-error']||0).toLocaleString()+' API errors · '+
      Number(counts['empty-response']||0).toLocaleString()+' empty responses · '+
      Number(counts['awaiting-confirmation']||0).toLocaleString()+' awaiting another 404 · ':'')+
      cases.length.toLocaleString()+' review cards loaded';
    const warnings=[];
    if(recovered)warnings.push('Recovered cards from corrections JSON because the review index was missing or incomplete');
    if(indexCases.length===0&&Array.isArray(index?.cases)&&index.cases.length)
      warnings.push(reviewSourceDiagnostic('Index',index.cases,indexCases.length));
    if(correctionCases.length===0&&Array.isArray(corrections?.corrections)&&corrections.corrections.length)
      warnings.push(reviewSourceDiagnostic('Corrections',corrections.corrections,correctionCases.length));
    if(index&&expected!==indexCases.length)
      warnings.push('Review index says '+expected+' cases, but contains '+indexCases.length+' usable records');
    if(index&&corrections&&correctionCases.length!==indexCases.length)
      warnings.push('Review JSON files have different counts');
    if(index&&!corrections)warnings.push('Corrections data unavailable; draft exports still work');
    if(!index)warnings.push('Review index unavailable; loaded corrections data only');
    $('#ic-updated').textContent=(generatedAt?'Last generated: '+
      new Date(generatedAt).toLocaleString()+' · Data updates after successful archive runs.':
      'Data loaded from the review corrections file.')+
      (warnings.length?' Warning: '+warnings.join('. ')+'.':'');
    visible=CHUNK;
    render();
  }catch(error){
    cases=[];published=[];ready=false;
    $('#ic-summary').textContent='Review data unavailable: '+error.message;
    $('#ic-updated').textContent='The archive verification queue was not changed. Try Reload review data.';
    $('#ic-result-count').textContent='Unable to load';
    $('#ic-list').innerHTML='<p class="ic-empty">Could not load review candidates: '+
      esc(error.message)+'. Try Reload review data.</p>';
    status('No review records loaded. The queue was not changed.');
  }finally{
    loading=false;
    $('#ic-reload').disabled=false;
  }
}
void load();
