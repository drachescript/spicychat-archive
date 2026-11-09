import {REVIEW_RULES,parseBotId,normalizeReviewBot,reviewSignals,discoveryTerms} from './flagged-rules.js';

const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const storageKey='sca-guideline-reviewed-v1';
const category=$('#fg-category'),input=$('#fg-bot-id'),scanButton=$('#fg-scan'),
  checkButton=$('#fg-check-one'),list=$('#fg-list'),message=$('#fg-status'),
  count=$('#fg-count'),images=$('#fg-show-images'),hideMarked=$('#fg-hide-marked'),
  includeRestored=$('#fg-restored');
for(const rule of REVIEW_RULES){
  const o=document.createElement('option');
  o.value=rule.id;o.textContent=rule.label;category.append(o);
}
let runtimePromise,restoredPromise,items=[],busy=false;
let reviewed=new Set();
try{
  const data=JSON.parse(localStorage.getItem(storageKey)||'[]');
  if(Array.isArray(data))reviewed=new Set(data.filter(x=>typeof x==='string'&&UUID.test(x)).slice(-5000));
}catch{}

function note(value,error=false){
  message.textContent=value;
  message.style.color=error?'var(--danger)':'var(--muted)';
}
function setBusy(value){
  busy=value;scanButton.disabled=value;checkButton.disabled=value;
}
async function runtime(){
  if(!runtimePromise)runtimePromise=fetch('../data/runtime.json',{cache:'default'})
    .then(r=>{if(!r.ok)throw new Error('Runtime configuration unavailable: '+r.status);return r.json();})
    .catch(e=>{runtimePromise=null;throw e;});
  return runtimePromise;
}
function searchQuery(config,term,id=null,perPage=14){
  const base=String(config.typesense?.baseFilter||'application_ids:=spicychat && type:!=META && visibility:=public');
  const quotedId=String.fromCharCode(96)+id+String.fromCharCode(96);
  return {
    collection:config.typesense?.collection||'public_characters_alias',
    q:term,query_by:'name,title,tags',num_typos:0,page:1,
    per_page:perPage,
    filter_by:id?base+' && character_id:='+quotedId:base,
    sort_by:'num_messages:desc',
    include_fields:'character_id,name,title,tags,creator_username,avatar_url,is_nsfw,num_messages'
  };
}
async function typesense(config,searches){
  if(!searches.length)return [];
  const urls=[config.typesense?.url,...(config.typesense?.fallbackUrls||[])].filter(Boolean);
  let lastError;
  for(const url of urls){
    try{
      const response=await fetch(url,{method:'POST',headers:{
        'Content-Type':'application/json','X-TYPESENSE-API-KEY':String(config.typesense?.apiKey||'')
      },body:JSON.stringify({searches})});
      if(!response.ok)throw new Error('Public listing search HTTP '+response.status);
      const payload=await response.json();
      if(!Array.isArray(payload.results))throw new Error('Public listing search returned no results');
      const err=payload.results.find(row=>row?.error);
      if(err)throw new Error(String(err.error).slice(0,150));
      return payload.results;
    }catch(e){lastError=e;}
  }
  throw lastError||new Error('Public listing search unavailable');
}
async function restoredRecords(config){
  // At most one existing R2 index fetch per page session. Never enumerate bot
  // objects or scan materialized R2 records.
  if(config.r2ReadAllowed===false)throw new Error('R2 read protection is active');
  if(!restoredPromise){
    const base=String(config.publicDataBaseUrl||'').replace(/\/$/,'');
    if(!base)throw new Error('Archive index endpoint is missing');
    restoredPromise=fetch(base+'/indexes/restored.json',{cache:'default'})
      .then(r=>{if(!r.ok)throw new Error('Restoration index HTTP '+r.status);return r.json();})
      .then(x=>{
        if(!Array.isArray(x?.bots))throw new Error('Restoration index is incomplete');
        return x.bots.map(x=>normalizeReviewBot(x,'restored')).filter(Boolean);
      }).catch(e=>{restoredPromise=null;throw e;});
  }
  return restoredPromise;
}
function makeItems(rows,{visualReview=false}={}){
  const map=new Map();
  for(const bot of rows){
    if(!bot)continue;
    const signals=reviewSignals(bot,category.value);
    const showForVisual=visualReview&&bot.source==='public'&&(category.value==='all'||category.value==='photo');
    if(!signals.length&&!showForVisual)continue;
    const old=map.get(bot.id);
    if(old){
      if(bot.source==='public'&&old.bot.source!=='public')old.bot=bot;
      for(const s of signals)if(!old.signals.some(x=>x.id===s.id))old.signals.push(s);
      old.visualReview=old.visualReview||showForVisual;
      continue;
    }
    map.set(bot.id,{bot,signals,visualReview:showForVisual});
  }
  return [...map.values()].sort((a,b)=>b.signals.length-a.signals.length||b.bot.messages-a.bot.messages).slice(0,160);
}
function imageSrc(raw){
  const s=String(raw||'').trim();
  if(/^https:\/\/[^<>"'\s]+$/i.test(s))return s;
  if(/^avatars\/[a-z0-9_./-]+(?:\?[^<>"'\s]+)?$/i.test(s))return 'https://cdn.nd-api.com/'+s;
  return '';
}
function render(){
  const data=hideMarked.checked?items.filter(x=>!reviewed.has(x.bot.id)):items;
  count.textContent=data.length.toLocaleString()+' candidate'+(data.length===1?'':'s')+
    (data.length!==items.length?' ('+(items.length-data.length)+' reviewed locally hidden)':'');
  list.innerHTML='';
  if(!data.length){
    const el=document.createElement('p');el.className='fg-empty';
    el.textContent='No matches in this bounded sample. This does not establish that all bots comply with the guidelines.';
    list.append(el);return;
  }
  for(const {bot,signals} of data){
    const article=document.createElement('article');article.className='fg-card';
    const photo=document.createElement('div');photo.className='fg-avatar';
    const src=imageSrc(bot.image);
    if(bot.nsfw&&!images.checked){
      photo.textContent='NSFW thumbnail hidden';
    }else if(src){
      const img=document.createElement('img');img.src=src;img.loading='lazy';
      img.alt='Character avatar; human review needed';
      img.referrerPolicy='no-referrer';
      img.onerror=()=>{img.remove();photo.textContent='Image unavailable';};
      photo.append(img);
    }else{photo.textContent='No public thumbnail';}
    article.append(photo);
    const info=document.createElement('div');
    const label=bot.source==='restored'?'Previously verified restored'+(bot.status==='deleted'?' · currently unavailable':''):'Public listing';
    const signs=signals.length?
      '<ul class="fg-signals">'+signals.map(s=>'<li><b>'+esc(s.label)+'</b> · '+esc(s.field)+' matched <b>'+esc(s.matched)+'</b><small>'+esc(s.note)+'</small></li>').join('')+'</ul>':
      '<p class="fg-summary">Visual sample only — not automatically flagged. Inspect the avatar if you wish.</p>';
    info.innerHTML='<h3><a href="../bot/?id='+encodeURIComponent(bot.id)+'">'+esc(bot.name)+'</a></h3>'+
      '<div class="fg-meta">'+esc(label)+(bot.creator?' · @'+esc(bot.creator):'')+'</div>'+
      '<p class="fg-summary">'+esc(bot.title||'No listing title')+'</p>'+
      '<div class="fg-tags">'+bot.tags.slice(0,4).map(t=>'<span class="fg-tag">'+esc(t)+'</span>').join('')+'</div>'+
      signs+
      '<div class="fg-actions"><a href="../bot/?id='+encodeURIComponent(bot.id)+'">Open archive</a>'+
      '<a href="https://spicychat.ai/chatbot/'+encodeURIComponent(bot.id)+'" target="_blank" rel="noopener noreferrer">SpicyChat</a>'+
      '<button class="fg-mark" type="button" data-review-id="'+esc(bot.id)+'">'+(reviewed.has(bot.id)?'Reviewed on this device':'Mark reviewed locally')+'</button></div>';
    article.append(info);list.append(article);
  }
}
async function scan(){
  if(busy)return;
  setBusy(true);list.innerHTML='';count.textContent='Checking…';
  note('Checking a bounded set of current public listings…');
  try{
    const config=await runtime(),terms=discoveryTerms(category.value);
    // Image sample is for human visual review only. Text-based metadata cannot
    // decide if an actual avatar is photorealistic or identify any person.
    const imageSample=category.value==='all'||category.value==='photo';
    const searches=terms.map(t=>searchQuery(config,t));
    if(imageSample)searches.push(searchQuery(config,'*',null,48));
    const results=await typesense(config,searches);
    const bots=[],samples=new Set();
    for(let i=0;i<results.length;i++){
      const sample=imageSample&&i===results.length-1;
      for(const hit of results[i]?.hits||[]){
        const bot=normalizeReviewBot(hit.document,'public');
        if(bot){bots.push(bot);if(sample)samples.add(bot.id);}
      }
    }
    let restoredMessage='';
    if(includeRestored.checked){
      try{
        const old=await restoredRecords(config);
        bots.push(...old);
        restoredMessage=' Also included '+old.length.toLocaleString()+' verified-restoration records from one existing index.';
      }catch(e){restoredMessage=' Restoration index not included: '+e.message+'.';}
    }
    items=makeItems(bots,{visualReview:true}).filter(x=>x.signals.length>0||samples.has(x.bot.id));
    render();
    note('Checked '+results.reduce((n,r)=>n+(r.hits?.length||0),0).toLocaleString()+
      ' public listing hits across '+terms.length+' focused terms'+
      (imageSample?' plus a 48-avatar visual sample.':'.')+restoredMessage+
      ' These are leads for review, not claims of violations or illegality.');
  }catch(e){items=[];render();note('Review unavailable: '+e.message,true);}
  finally{setBusy(false);}
}
async function checkOne(){
  if(busy)return;
  const id=parseBotId(input.value);
  if(!id){note('Please enter a valid SpicyChat/Archive bot URL or UUID.',true);return;}
  setBusy(true);list.innerHTML='';count.textContent='Checking ID…';
  note('Checking public listing for '+id+'…');
  const url=new URL(location.href);url.searchParams.set('id',id);history.replaceState(null,'',url);
  try{
    const config=await runtime();
    const results=await typesense(config,[searchQuery(config,'*',id,1)]);
    const hit=results[0]?.hits?.[0];
    let bot=hit?normalizeReviewBot(hit.document,'public'):null;
    if(!bot&&includeRestored.checked){
      const historical=await restoredRecords(config);
      bot=historical.find(x=>x.id===id)||null;
    }
    if(!bot){items=[];render();note('No current public listing'+
      (includeRestored.checked?' or verified restoration':'')+
      ' found for this ID. Private and unverified archived copies are deliberately excluded.');return;}
    items=[{bot,signals:reviewSignals(bot,category.value),visualReview:true}];
    render();
    note('Showing this exact bot for manual avatar and metadata review. No additional full-bot R2 read was performed.');
  }catch(e){items=[];render();note('Could not check this bot: '+e.message,true);}
  finally{setBusy(false);}
}
list.addEventListener('click',event=>{
  const button=event.target.closest?.('[data-review-id]');
  if(!button)return;
  const id=button.getAttribute('data-review-id');
  if(!UUID.test(id))return;
  if(reviewed.has(id))reviewed.delete(id);else reviewed.add(id);
  try{localStorage.setItem(storageKey,JSON.stringify([...reviewed].slice(-5000)));}catch{}
  render();
});
scanButton.addEventListener('click',()=>void scan());
checkButton.addEventListener('click',()=>void checkOne());
input.addEventListener('keydown',e=>{if(e.key==='Enter')void checkOne();});
images.addEventListener('change',render);
hideMarked.addEventListener('change',render);
const initialId=new URL(location.href).searchParams.get('id');
if(initialId){input.value=initialId;void checkOne();}
