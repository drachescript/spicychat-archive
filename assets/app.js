const asset = (path) => new URL(`../${path}`, import.meta.url);
const app = document.querySelector('#app');
const page = document.body.dataset.page || 'browse';

const esc = (v='') => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v == null || v === '' ? '—' : Number.isFinite(Number(v)) ? Number(v).toLocaleString() : String(v);
const date = v => { if(!v) return 'Unknown'; const d=new Date(v); return Number.isNaN(d.getTime())?String(v):d.toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric'}); };
const qs = (k) => new URLSearchParams(location.search).get(k);
const sleep = ms => new Promise(r=>setTimeout(r,ms));

function detailHref(id){ return new URL(`../bot/?id=${encodeURIComponent(id)}`, import.meta.url).href; }
function resolveAvatar(v){
  if(!v) return '';
  const text=String(v).trim();
  if(/^https?:\/\//i.test(text)) return text;
  if(text.startsWith('//')) return `https:${text}`;
  const clean=text.replace(/^\/+/, '');
  if(clean.startsWith('avatars/')) return `https://cdn.nd-api.com/${clean}${clean.includes('?')?'':'?class=avatar256x256'}`;
  return asset(clean).href;
}

function badge(status){
  const s=(status||'unknown').toLowerCase();
  const label=s==='public'?'Active':s==='deleted'?'Archived':s;
  return `<span class="badge ${esc(s)}">${esc(label)}</span>`;
}

function card(bot, blurNsfw){
  const avatar=resolveAvatar(bot.avatar);
  const img=avatar ? `<img src="${esc(avatar)}" alt="" loading="lazy" class="${blurNsfw&&bot.isNsfw?'nsfw-blur':''}" onerror="this.remove()">` : '';
  const tags=(bot.tags||[]).slice(0,5).map(t=>`<span class="tag">${esc(t)}</span>`).join('');
  const first=bot.firstSeenAt ? `<span>First seen ${date(bot.firstSeenAt)}</span>` : (bot.createdAt ? `<span>Created ${date(bot.createdAt)}</span>` : '');
  const imageLabel=bot.avatarArchived ? '<span>image archived</span>' : '<span>CDN image</span>';
  return `<article class="card"><a class="cardlink" href="${esc(detailHref(bot.id))}"><div class="art">${img}${badge(bot.status)}</div><div class="body"><h3>${esc(bot.name||'Unknown bot')}</h3><div class="creator">${bot.creator?`@${esc(bot.creator)}`:'Unknown creator'}</div><div class="title">${esc(bot.title||'')}</div><div class="taglist">${tags}</div><div class="meta"><span>${fmt(bot.messages)} messages</span><span>${bot.rating==null?'—':`★ ${esc(bot.rating)}`}</span>${first}${imageLabel}</div></div></a></article>`;
}

function chipInput(id, selected, placeholder){
  return `<div class="chips" id="${id}">${selected.map(t=>`<span class="chip" data-value="${esc(t)}">${esc(t)} <button type="button" aria-label="Remove ${esc(t)}">×</button></span>`).join('')}<input placeholder="${esc(placeholder)}" autocomplete="off"></div>`;
}

function parseTags(value){return String(value||'').split(',').map(x=>x.trim()).filter(Boolean)}
function setQuery(state){
  const u=new URL(location.href);
  const set=(k,v)=>{if(v&&(!(Array.isArray(v))||v.length))u.searchParams.set(k,Array.isArray(v)?v.join(','):v);else u.searchParams.delete(k)};
  set('q',state.q); set('include',state.include); set('exclude',state.exclude); set('creator',state.creator); set('sort',state.sort); set('match',state.match);
  history.replaceState(null,'',u);
}

async function fetchJson(url, options={}){
  const r=await fetch(url,{cache:'no-store',...options});
  if(!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function loadManifest(){
  return fetchJson(asset('data/manifest.json'));
}

async function loadRuntime(){
  try{return await fetchJson(asset('data/runtime.json'));}
  catch{return {storageMode:'local'};}
}

async function loadCatalog(){
  const manifest=await loadManifest();
  const parts=await Promise.all((manifest.shards||[]).map(s=>fetchJson(asset(`data/catalog/${s}.json`))));
  return {manifest,bots:parts.flatMap(x=>x.bots||[])};
}

function attachChipControls(state, onChange, tagOptions=[]){
  const list=document.querySelector('#tag-list');
  if(list) list.innerHTML=tagOptions.map(t=>`<option value="${esc(t)}"></option>`).join('');
  for(const id of ['include-tags','exclude-tags']){
    const box=document.querySelector(`#${id}`); if(!box) continue;
    const input=box.querySelector('input'); input.setAttribute('list','tag-list');
    const arr=id==='include-tags'?state.include:state.exclude;
    box.addEventListener('click',e=>{const btn=e.target.closest('button'); if(!btn)return; const chip=btn.closest('.chip'); const i=arr.indexOf(chip.dataset.value); if(i>=0)arr.splice(i,1); chip.remove(); onChange();});
    input.addEventListener('keydown',e=>{if(e.key!=='Enter'&&e.key!==',')return;e.preventDefault();const v=input.value.trim().replace(/,$/,'');if(v&&!arr.some(x=>x.toLowerCase()===v.toLowerCase())){arr.push(v);const span=document.createElement('span');span.className='chip';span.dataset.value=v;span.innerHTML=`${esc(v)} <button type="button" aria-label="Remove ${esc(v)}">×</button>`;box.insertBefore(span,input);input.value='';onChange();}});
  }
}

function controlsHtml(state,{deletedOnly=false,tagOptions=[],live=false}={}){
  const sortOptions=deletedOnly
    ? `<option value="deleted-newest">Recently deleted</option><option value="name">Name</option><option value="popular">Most messages</option><option value="top-rated">Top rated</option>`
    : live
      ? `<option value="trending">Trending</option><option value="popular">Popular</option><option value="top-rated">Top rated</option><option value="latest">Latest</option>`
      : `<option value="trending">Trending</option><option value="popular">Popular</option><option value="top-rated">Top rated</option><option value="latest">Latest</option><option value="discovered">Recently discovered</option><option value="updated">Recently updated</option><option value="deleted-newest">Recently deleted</option><option value="name">Name</option>`;
  const status=deletedOnly
    ? `<select id="status" disabled><option value="deleted">Deleted / archived</option></select>`
    : live
      ? `<select id="status"><option value="public">Active</option><option value="deleted">Deleted / archived</option></select>`
      : `<select id="status"><option value="all">All</option><option value="public">Active</option><option value="deleted">Deleted</option><option value="missing">Missing</option><option value="restricted">Restricted</option></select>`;
  return `<section class="controls"><div class="control-grid"><div class="field"><label>Search</label><input id="search" value="${esc(state.q)}" placeholder="Name, title, creator or tag"></div><div class="field"><label>Creator</label><input id="creator" value="${esc(state.creator)}" placeholder="Any creator"></div><div class="field"><label>Status</label>${status}</div><div class="field"><label>Sort</label><select id="sort">${sortOptions}</select></div></div>
  <div class="tag-row"><div class="field"><label>Include tags</label>${chipInput('include-tags',state.include,'Add tag and press Enter')}</div><div class="field"><label>Exclude tags</label>${chipInput('exclude-tags',state.exclude,'Add tag and press Enter')}</div></div>
  <datalist id="tag-list">${tagOptions.map(t=>`<option value="${esc(t)}"></option>`).join('')}</datalist>
  <div class="options-row"><label><input type="radio" name="match" value="all" ${state.match==='all'?'checked':''}> Match all included tags</label><label><input type="radio" name="match" value="any" ${state.match==='any'?'checked':''}> Match any included tag</label><label><input id="blur-nsfw" type="checkbox" ${state.blurNsfw?'checked':''}> Blur NSFW avatars</label></div></section>`;
}

function statsHtml(manifest){
  const fourth=manifest.storageMode==='r2'
    ? `<div class="stat"><b>R2</b><span>Permanent archive storage</span></div>`
    : `<div class="stat"><b>${fmt(manifest.tagCount)}</b><span>Unique tags</span></div>`;
  return `<section class="stats"><div class="stat"><b>${fmt(manifest.totalBots)}</b><span>Bots discovered</span></div><div class="stat"><b>${fmt(manifest.activeBots)}</b><span>Currently public</span></div><div class="stat"><b>${fmt(manifest.deletedBots)}</b><span>Deleted / archived</span></div>${fourth}</section>`;
}

function localFilterAndSort(bots,state,currentRanks={}){
  const q=state.q.toLowerCase(), creator=state.creator.toLowerCase(), inc=state.include.map(x=>x.toLowerCase()), exc=state.exclude.map(x=>x.toLowerCase());
  const listingRank=(bot,name)=>currentRanks[name]?.get(bot.id) ?? 1e12;
  let rows=bots.filter(b=>{
    if(state.status!=='all'&&b.status!==state.status)return false;
    if(creator&&String(b.creator||'').toLowerCase()!==creator)return false;
    const tags=(b.tags||[]).map(x=>String(x).toLowerCase());
    if(inc.length){const ok=state.match==='any'?inc.some(t=>tags.includes(t)):inc.every(t=>tags.includes(t));if(!ok)return false;}
    if(exc.some(t=>tags.includes(t)))return false;
    if(q){const hay=[b.name,b.title,b.creator,...(b.tags||[])].join(' ').toLowerCase();if(!hay.includes(q))return false;}
    return true;
  });
  rows.sort((a,b)=>{
    switch(state.sort){
      case 'trending': return listingRank(a,'trending')-listingRank(b,'trending') || Number(b.messages24h||0)-Number(a.messages24h||0);
      case 'popular': return listingRank(a,'popular')-listingRank(b,'popular') || Number(b.messages||0)-Number(a.messages||0);
      case 'top-rated': return listingRank(a,'top-rated')-listingRank(b,'top-rated') || Number(b.rating||0)-Number(a.rating||0);
      case 'latest': return listingRank(a,'latest')-listingRank(b,'latest') || String(b.createdAt||'').localeCompare(String(a.createdAt||''));
      case 'discovered': return String(b.firstSeenAt||'').localeCompare(String(a.firstSeenAt||''));
      case 'updated': return String(b.updatedAt||b.lastSeenAt||'').localeCompare(String(a.updatedAt||a.lastSeenAt||''));
      case 'deleted-newest': return String(b.statusSince||'').localeCompare(String(a.statusSince||''));
      case 'name': return String(a.name||'').localeCompare(String(b.name||''));
      default:return 0;
    }
  });
  return rows;
}

async function browseLocal(){
  const {manifest,bots}=await loadCatalog();
  const deletedOnly=page==='deleted';
  const state={q:qs('q')||'',include:parseTags(qs('include')),exclude:parseTags(qs('exclude')),creator:qs('creator')||'',sort:qs('sort')||(deletedOnly?'deleted-newest':'trending'),match:qs('match')||'all',status:deletedOnly?'deleted':(qs('status')||'all'),blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0'};
  const tagOptions=(manifest.topTags||[]).map(x=>x.tag);
  app.innerHTML=`${statsHtml(manifest)}${controlsHtml(state,{deletedOnly,tagOptions})}<div class="options-row scanline">Last scan: ${date(manifest.lastScan)}</div><div class="results-head"><h2>Bots</h2><span id="result-count"></span></div><section class="grid" id="grid"></section><footer class="footer">Public archival project. Deleted status requires repeated explicit public character API 404s; disappearing from a listing alone is not treated as deletion.</footer>`;
  const $=s=>document.querySelector(s), grid=$('#grid'), count=$('#result-count');
  $('#status').value=state.status; $('#sort').value=state.sort;
  const currentRanks={}; for(const [name,ids] of Object.entries(manifest.listings||{})){const m=new Map();(ids||[]).forEach((id,i)=>m.set(id,i+1));currentRanks[name]=m;}
  const render=()=>{state.q=$('#search').value.trim();state.creator=$('#creator').value.trim();state.status=deletedOnly?'deleted':$('#status').value;state.sort=$('#sort').value;state.match=document.querySelector('input[name=match]:checked')?.value||'all';state.blurNsfw=$('#blur-nsfw').checked;localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0');setQuery(state);const rows=localFilterAndSort(bots,state,currentRanks);count.textContent=`${rows.length.toLocaleString()} shown`;grid.innerHTML=rows.length?rows.slice(0,2000).map(b=>card(b,state.blurNsfw)).join(''):`<div class="empty">No bots match these filters.</div>`;if(rows.length>2000)count.textContent+=` · first 2,000 rendered`;};
  attachChipControls(state,render,tagOptions);
  for(const id of ['search','creator','status','sort','blur-nsfw']) document.querySelector(`#${id}`)?.addEventListener(id==='search'||id==='creator'?'input':'change',render);
  document.querySelectorAll('input[name=match]').forEach(x=>x.addEventListener('change',render));
  render();
}

function tsLiteral(value){return `\`${String(value).replace(/\\/g,'\\\\').replace(/`/g,'\\`')}\``;}
function buildTsFilter(runtime,state){
  const parts=[runtime.typesense.baseFilter];
  if(state.creator) parts.push(`creator_username:=${tsLiteral(state.creator)}`);
  if(state.include.length){
    const clauses=state.include.map(t=>`tags:=${tsLiteral(t)}`);
    parts.push(state.match==='any'?`tags:=[${state.include.map(tsLiteral).join(',')}]`:clauses.join(' && '));
  }
  for(const tag of state.exclude) parts.push(`tags:!=${tsLiteral(tag)}`);
  return parts.filter(Boolean).join(' && ');
}

async function typesenseSearch(runtime,search){
  const urls=[runtime.typesense.url,...(runtime.typesense.fallbackUrls||[])];
  let last;
  for(const url of urls){
    try{
      const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-TYPESENSE-API-KEY':runtime.typesense.apiKey},body:JSON.stringify({searches:[search]})});
      if(!r.ok){last=new Error(`Typesense ${r.status}`);continue;}
      const data=await r.json();
      const result=(data.results||[])[0]||{};
      if(result.error){last=new Error(result.error);continue;}
      return result;
    }catch(e){last=e;}
  }
  throw last||new Error('Typesense request failed');
}

function docToCard(doc){
  return {
    id:String(doc.character_id||doc.id||'').toLowerCase(),name:doc.name||doc.title||'Unknown bot',title:doc.title||'',creator:doc.creator_username||'',tags:Array.isArray(doc.tags)?doc.tags:[],status:'public',isNsfw:!!(doc.is_nsfw||doc.avatar_is_nsfw),avatar:resolveAvatar(doc.avatar_url||doc.avatar||doc.image),messages:doc.num_messages,messages24h:doc.num_messages_24h,rating:doc.rating_score,createdAt:doc.createdAt,updatedAt:doc.updatedAt,avatarArchived:false
  };
}

async function browseLive(runtime,manifest){
  const state={q:qs('q')||'',include:parseTags(qs('include')),exclude:parseTags(qs('exclude')),creator:qs('creator')||'',sort:qs('sort')||'trending',match:qs('match')||'all',status:'public',blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0',page:1};
  app.innerHTML=`${statsHtml(manifest)}${controlsHtml(state,{live:true})}<div class="options-row scanline">Archive scan: ${date(manifest.lastScan)} · active browsing queries SpicyChat's public Typesense index directly, while full historical records live in the archive.</div><div class="results-head"><h2>Bots</h2><span id="result-count"></span></div><section class="grid" id="grid"></section><div class="load-more-wrap"><button type="button" class="load-more" id="load-more" hidden>Load more</button></div><footer class="footer">Public archival project. Deleted status requires repeated explicit public character API 404s; disappearing from a listing alone is not treated as deletion.</footer>`;
  const $=s=>document.querySelector(s),grid=$('#grid'),count=$('#result-count'),more=$('#load-more');
  $('#status').value='public'; $('#sort').value=state.sort;
  let rows=[],found=0,requestNo=0,timer;

  function sync(){state.q=$('#search').value.trim();state.creator=$('#creator').value.trim();state.sort=$('#sort').value;state.match=document.querySelector('input[name=match]:checked')?.value||'all';state.blurNsfw=$('#blur-nsfw').checked;localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0');setQuery(state);}
  async function run(reset=true){
    sync(); if(reset){state.page=1;rows=[];grid.innerHTML='<p class="loading">Loading public bots…</p>';}
    const token=++requestNo;
    const sortBy=(runtime.sorts||{})[state.sort]||runtime.sorts?.trending||'num_messages_24h:desc';
    const search={collection:runtime.typesense.collection,q:state.q||'*',query_by:runtime.typesense.queryBy,page:state.page,per_page:60,filter_by:buildTsFilter(runtime,state),sort_by:sortBy,include_fields:'character_id,name,title,tags,creator_username,avatar_url,avatar_is_nsfw,is_nsfw,num_messages,num_messages_24h,rating_score,createdAt,updatedAt',facet_by:'tags',max_facet_values:200};
    try{
      const result=await typesenseSearch(runtime,search); if(token!==requestNo)return;
      found=Number(result.found||0);
      const next=(result.hits||[]).map(h=>docToCard(h.document||{})).filter(b=>b.id);
      rows=reset?next:[...rows,...next];
      grid.innerHTML=rows.length?rows.map(b=>card(b,state.blurNsfw)).join(''):`<div class="empty">No public bots match these filters.</div>`;
      count.textContent=`${found.toLocaleString()} matches · ${rows.length.toLocaleString()} loaded`;
      more.hidden=rows.length>=found||next.length===0;
      const facets=(result.facet_counts||[]).find(f=>f.field_name==='tags');
      if(facets){const list=$('#tag-list');list.innerHTML=(facets.counts||[]).map(x=>`<option value="${esc(x.value)}"></option>`).join('');}
    }catch(e){if(token!==requestNo)return;grid.innerHTML=`<div class="error">Could not query the public bot index: ${esc(e.message||e)}</div>`;count.textContent='';more.hidden=true;}
  }
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(()=>run(true),250);};
  attachChipControls(state,()=>run(true));
  $('#search').addEventListener('input',schedule); $('#creator').addEventListener('input',schedule); $('#sort').addEventListener('change',()=>run(true)); $('#blur-nsfw').addEventListener('change',()=>run(true));
  document.querySelectorAll('input[name=match]').forEach(x=>x.addEventListener('change',()=>run(true)));
  $('#status').addEventListener('change',()=>{if($('#status').value==='deleted'){const u=new URL('../deleted/',import.meta.url);const q=new URLSearchParams(location.search);u.search=q.toString();location.href=u.href;}});
  more.addEventListener('click',()=>{state.page+=1;run(false);});
  await run(true);
}

async function browseDeletedR2(runtime,manifest){
  const state={q:qs('q')||'',include:parseTags(qs('include')),exclude:parseTags(qs('exclude')),creator:qs('creator')||'',sort:qs('sort')||'deleted-newest',match:qs('match')||'all',status:'deleted',blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0'};
  let bots=[];
  if(runtime.deletedIndexUrl){
    const data=await fetchJson(runtime.deletedIndexUrl); bots=data.bots||[];
  }
  const tagOptions=[...new Set(bots.flatMap(b=>b.tags||[]))].sort((a,b)=>String(a).localeCompare(String(b)));
  app.innerHTML=`${statsHtml(manifest)}${controlsHtml(state,{deletedOnly:true,tagOptions})}<div class="options-row scanline">Archive scan: ${date(manifest.lastScan)}</div><div class="results-head"><h2>Deleted bots</h2><span id="result-count"></span></div><section class="grid" id="grid"></section><footer class="footer">These bots were only moved here after repeated explicit public character API 404s. Their last-known archived data remains in R2.</footer>`;
  const $=s=>document.querySelector(s),grid=$('#grid'),count=$('#result-count'); $('#sort').value=state.sort;
  const render=()=>{state.q=$('#search').value.trim();state.creator=$('#creator').value.trim();state.sort=$('#sort').value;state.match=document.querySelector('input[name=match]:checked')?.value||'all';state.blurNsfw=$('#blur-nsfw').checked;localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0');setQuery(state);const rows=localFilterAndSort(bots,state,{});count.textContent=`${rows.length.toLocaleString()} shown`;grid.innerHTML=rows.length?rows.slice(0,2000).map(b=>card(b,state.blurNsfw)).join(''):`<div class="empty">No deleted bots match these filters.</div>`;if(rows.length>2000)count.textContent+=' · first 2,000 rendered';};
  attachChipControls(state,render,tagOptions); $('#search').addEventListener('input',render);$('#creator').addEventListener('input',render);$('#sort').addEventListener('change',render);$('#blur-nsfw').addEventListener('change',render);document.querySelectorAll('input[name=match]').forEach(x=>x.addEventListener('change',render));render();
}

async function browse(){
  const runtime=await loadRuntime();
  if(runtime.storageMode!=='r2') return browseLocal();
  const manifest=await loadManifest();
  if(page==='deleted') return browseDeletedR2(runtime,manifest);
  return browseLive(runtime,manifest);
}

function bestField(record,key){
  const lk=record.lastKnown||{};
  if(lk[key]!==undefined&&lk[key]!==null&&String(lk[key]).trim?.()!=='')return lk[key];
  for(const source of ['character-api','typesense','typesense:trending','typesense:latest','typesense:explore']){const v=record.current?.[source]?.[key];if(v!==undefined&&v!==null)return v;}
  return null;
}

async function loadBotRecord(id,runtime){
  if(runtime.storageMode==='r2'&&runtime.publicDataBaseUrl){
    const compact=id.replaceAll('-','').toLowerCase(); const prefix=compact.slice(0,2)||'__';
    const url=`${runtime.publicDataBaseUrl.replace(/\/$/,'')}/bots/${prefix}/${encodeURIComponent(id.toLowerCase())}.json`;
    try{return await fetchJson(url);}catch(e){console.warn('R2 detail fetch failed, trying local fallback',e);}
  }
  return fetchJson(asset(`data/bots/${encodeURIComponent(id)}.json`));
}

async function botPage(){
  const id=qs('id'); if(!id){app.innerHTML='<div class="error">No bot ID supplied.</div>';return;}
  const runtime=await loadRuntime();
  let record; try{record=await loadBotRecord(id,runtime);}catch{app.innerHTML='<div class="error">This bot has not been captured by the archive yet. The crawler is still expanding through the public catalog.</div>';return;}
  const lk=record.lastKnown||{},status=record.status?.current||'unknown';
  const avatar=record.avatarArchive?.publicUrl||record.avatarArchive?.r2Url||(record.avatarArchive?.path?resolveAvatar(record.avatarArchive.path.replace(/^archive\//,'')):resolveAvatar(lk.avatar_url||lk.avatar||lk.image));
  const fields=['description','greeting','greetings','personality','definition','persona','character_definition','characterDefinition','scenario','example_dialogue','example_dialogues','system_prompt','post_history_instructions','lorebooks'];
  const fieldHtml=fields.map(k=>{const v=bestField(record,k);if(v==null||v==='')return'';const label=k.replaceAll('_',' ');return `<div class="field-block"><h3>${esc(label)} · last known</h3><div class="pre">${esc(Array.isArray(v)?v.join('\n\n'):typeof v==='object'?JSON.stringify(v,null,2):v)}</div></div>`}).join('');
  const tags=(lk.tags||[]).map(t=>`<span class="pill">${esc(t)}</span>`).join('');
  const history=(record.fieldHistory||[]).slice().reverse().slice(0,100).map(h=>`<div class="history-item"><b>${esc(h.path)}</b> · ${esc(h.kind||'value')}<br><span class="detail-sub">${date(h.at)} · ${esc(h.source||'')}</span></div>`).join('');
  app.innerHTML=`<article class="detail"><div class="detail-head"><div class="detail-art">${avatar?`<img src="${esc(avatar)}" alt="">`:''}</div><div><div class="detail-sub">${esc(record.id)}</div><h1>${esc(bestField(record,'name')||bestField(record,'title')||'Unknown bot')}</h1><div class="detail-sub">${bestField(record,'creator_username')?`@${esc(bestField(record,'creator_username'))}`:'Unknown creator'}</div><div class="pill-row"><span class="pill">${esc(status)}</span>${record.avatarArchive?.publicUrl||record.avatarArchive?.path?'<span class="pill">image archived</span>':''}<span class="pill">first seen ${date(record.firstSeenAt)}</span><span class="pill">last seen ${date(record.lastSeenAt)}</span></div><div class="pill-row">${tags}</div><p>${esc(bestField(record,'title')||'')}</p></div></div>
  <section class="section"><h2>Last-known archived fields</h2><p class="detail-sub">A field remains here after SpicyChat stops exposing it. This does not mean the field is still currently public.</p>${fieldHtml||'<p class="detail-sub">No rich definition fields have been recovered yet.</p>'}</section>
  <section class="section"><h2>Latest metrics</h2><div class="pre">${esc(JSON.stringify(record.metrics?.latest||{},null,2))}</div></section>
  <section class="section"><h2>Availability history</h2><div class="pre">${esc(JSON.stringify(record.availabilityHistory||[],null,2))}</div></section>
  <section class="section"><h2>Field history</h2>${history||'<p class="detail-sub">No field changes recorded yet.</p>'}</section>
  <section class="section"><h2>Raw latest observations</h2><div class="pre">${esc(JSON.stringify(record.current||{},null,2))}</div></section></article>`;
}

(async()=>{try{if(page==='bot')await botPage();else await browse();}catch(e){console.error(e);app.innerHTML=`<div class="error">Could not load the archive: ${esc(e.message||e)}</div>`;}})();
