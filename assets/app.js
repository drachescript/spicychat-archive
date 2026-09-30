const asset = (path) => new URL(`../${path}`, import.meta.url);
const app = document.querySelector('#app');
const page = document.body.dataset.page || 'browse';
const DEFAULT_EXCLUDED = ['NTR','Cheating'];

const esc = (v='') => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v == null || v === '' ? '—' : Number.isFinite(Number(v)) ? Number(v).toLocaleString() : String(v);
const fmtBytes = v => {
  const n=Number(v||0); if(!n)return '0 B';
  const u=['B','KB','MB','GB','TB']; let i=0,x=n;
  while(x>=1000&&i<u.length-1){x/=1000;i++;}
  return `${x>=100?x.toFixed(0):x>=10?x.toFixed(1):x.toFixed(2)} ${u[i]}`;
};
const date = v => {
  if(!v)return 'Unknown';
  const d=new Date(v);
  return Number.isNaN(d.getTime())?String(v):d.toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
};
const shortDate = v => {
  if(!v)return 'Unknown';
  const d=new Date(v);
  return Number.isNaN(d.getTime())?String(v):d.toLocaleDateString(undefined,{month:'short',day:'numeric'});
};
const qs = k => new URLSearchParams(location.search).get(k);
const hasQs = k => new URLSearchParams(location.search).has(k);
const parseTags = value => String(value||'').split(',').map(x=>x.trim()).filter(Boolean);
const detailHref = id => new URL(`../bot/?id=${encodeURIComponent(id)}`, import.meta.url).href;
const spicyHref = id => `https://spicychat.ai/chatbot/${encodeURIComponent(id)}`;

async function fetchJson(url, options={}){
  const r=await fetch(url,{cache:'no-store',...options});
  if(!r.ok)throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
async function loadManifest(){return fetchJson(asset('data/manifest.json'));}
async function loadStats(){try{return await fetchJson(asset('data/stats.json'));}catch{return null;}}
async function loadTags(){try{return (await fetchJson(asset('data/tags.json'))).tags||[];}catch{return [];}}
const R2_RUNTIME_FALLBACK={
  schemaVersion:1,storageMode:'r2',r2ReadAllowed:true,
  publicDataBaseUrl:'https://data.spicychatarchive.drache.uk',
  deletedIndexUrl:'https://data.spicychatarchive.drache.uk/indexes/deleted.json',
  typesense:{
    url:'https://ts-lb.nd-api.com/multi_search',
    fallbackUrls:['https://etmzpxgvnid370fyp.a1.typesense.net/multi_search'],
    apiKey:'STHKtT6jrC5z1IozTJHIeSN4qN9oL1s3',
    collection:'public_characters_alias',
    queryBy:'name,title,tags,creator_username,character_id,type',
    baseFilter:'application_ids:=spicychat && type:!=META && visibility:=public'
  },
  sorts:{
    trending:'_text_match(buckets: 3):desc,num_messages_24h:desc',
    'top-rated':'rating_score:desc,num_messages:desc',
    popular:'num_messages:desc'
  }
};
async function loadRuntime(){
  let runtime;
  try{runtime=await fetchJson(asset('data/runtime.json'));}
  catch{runtime=JSON.parse(JSON.stringify(R2_RUNTIME_FALLBACK));}
  try{
    const guard=await fetchJson(asset('data/r2-usage.json'));
    if(typeof guard.r2ReadAllowed==='boolean')runtime.r2ReadAllowed=guard.r2ReadAllowed;
    runtime.r2QuotaGuard=guard;
  }catch{}
  return runtime;
}

function readExcluded(){
  if(!hasQs('exclude'))return [...DEFAULT_EXCLUDED];
  const value=qs('exclude');
  if(value==='none'||value==='')return [];
  return parseTags(value);
}
function setQuery(state){
  const u=new URL(location.href);
  const set=(k,v)=>{
    if(Array.isArray(v)){
      if(v.length)u.searchParams.set(k,v.join(','));
      else u.searchParams.delete(k);
    }else if(v)u.searchParams.set(k,v);
    else u.searchParams.delete(k);
  };
  set('q',state.q); set('include',state.include); set('creator',state.creator); set('sort',state.sort); set('match',state.match);
  if(state.exclude.length)u.searchParams.set('exclude',state.exclude.join(','));
  else u.searchParams.set('exclude','none');
  history.replaceState(null,'',u);
}
function resolveAvatar(v){
  if(!v)return '';
  const text=String(v).trim();
  if(/^https?:\/\//i.test(text))return text;
  if(text.startsWith('//'))return `https:${text}`;
  const clean=text.replace(/^\/+/,'');
  if(clean.startsWith('avatars/'))return `https://cdn.nd-api.com/${clean}${clean.includes('?')?'':'?class=avatar256x256'}`;
  return asset(clean).href;
}
function badge(status){
  const s=(status||'unknown').toLowerCase();
  const label=s==='public'?'Active':s==='deleted'?'Archived':s;
  return `<span class="badge ${esc(s)}">${esc(label)}</span>`;
}
function imgHtml(primary,fallback,{blur=false,alt=''}={}){
  const p=resolveAvatar(primary), f=resolveAvatar(fallback);
  if(!p&&!f)return '<div class="art-placeholder">No image</div>';
  const src=p||f;
  const fallbackAttr=f&&f!==src?` data-fallback="${esc(f)}"`:'';
  return `<img src="${esc(src)}"${fallbackAttr} alt="${esc(alt)}" loading="lazy" class="${blur?'nsfw-blur':''}" onerror="if(this.dataset.fallback&&this.src!==this.dataset.fallback){this.src=this.dataset.fallback;this.dataset.fallback=''}else{this.remove()}">`;
}
function card(bot, blurNsfw){
  const tags=(bot.tags||[]).slice(0,5).map(t=>`<span class="tag">${esc(t)}</span>`).join('');
  const first=bot.firstSeenAt?`<span>Seen ${shortDate(bot.firstSeenAt)}</span>`:(bot.createdAt?`<span>Made ${shortDate(bot.createdAt)}</span>`:'');
  return `<article class="card"><a class="cardlink" href="${esc(detailHref(bot.id))}">
    <div class="art">${imgHtml(bot.avatar,bot.avatarFallback,{blur:blurNsfw&&bot.isNsfw,alt:bot.name||''})}${badge(bot.status)}</div>
    <div class="body"><h3>${esc(bot.name||'Unknown bot')}</h3><div class="creator">${bot.creator?`@${esc(bot.creator)}`:'Unknown creator'}</div>
    <div class="title">${esc(bot.title||'')}</div><div class="taglist">${tags}</div>
    <div class="meta"><span>${fmt(bot.messages)} msgs</span><span>${bot.rating==null?'—':`★ ${esc(bot.rating)}`}</span>${first}<span>${bot.avatarArchived?'image archived':'CDN image'}</span></div></div>
  </a></article>`;
}
function growthBanner(stats){
  if(!stats)return '';
  const pace=Number(stats.growth?.averagePerDay||0);
  const recent=(stats.runs||[]).filter(r=>r.kind==='archive-run').slice(-3).reverse();
  const updates=recent.map(r=>`<span>${shortDate(r.at)} <b>+${fmt(r.addedSincePrevious??r.exploration?.new??0)}</b></span>`).join('');
  return `<section class="growth-banner"><div class="growth-main"><b>Adding ~${fmt(Math.round(pace))} bots/day</b><span> observed archive growth</span></div><div class="growth-updates">${updates||'<span>Growth history starts with the next archive scans.</span>'}</div></section>`;
}
function tagSidebar(state,tags){
  const rows=tags.map(tag=>{
    const inc=state.include.some(x=>x.toLowerCase()===tag.toLowerCase());
    const exc=state.exclude.some(x=>x.toLowerCase()===tag.toLowerCase());
    return `<div class="tag-filter-row" data-tag="${esc(tag)}"><span class="tag-filter-name" title="${esc(tag)}">${esc(tag)}</span>
      <button type="button" class="tag-toggle include ${inc?'active':''}" data-action="include" aria-label="Include ${esc(tag)}">+</button>
      <button type="button" class="tag-toggle exclude ${exc?'active':''}" data-action="exclude" aria-label="Exclude ${esc(tag)}">−</button></div>`;
  }).join('');
  return `<aside class="filters" id="filters"><div class="filters-head"><h2>Filters</h2><button class="link-button" id="reset-filters" type="button">Reset</button></div>
    <div class="filter-scroll">
      <section class="filter-section"><h3>Narrow by tag</h3><input class="filter-search" id="tag-search" placeholder="Search ${tags.length} tags">
        <div class="selected-filters" id="selected-filters"></div>
        <div class="tag-filter-list" id="tag-filter-list">${rows}</div>
      </section>
      <section class="filter-section"><h3>Included tags</h3><div class="radio-stack">
        <label><input type="radio" name="match" value="all" ${state.match==='all'?'checked':''}> Match all</label>
        <label><input type="radio" name="match" value="any" ${state.match==='any'?'checked':''}> Match any</label>
      </div></section>
      <section class="filter-section"><h3>Images</h3><label class="checkline"><input id="blur-nsfw" type="checkbox" ${state.blurNsfw?'checked':''}> <span class="filter-note">Blur NSFW avatars</span></label></section>
      <section class="filter-section"><div class="filter-note default-note">NTR and Cheating are excluded by default. Use the − buttons above to remove either exclusion.</div></section>
    </div></aside>`;
}
function selectedFilterChips(state){
  const include=state.include.map(t=>`<span class="chip include">+ ${esc(t)} <button data-chip="include" data-value="${esc(t)}">×</button></span>`);
  const exclude=state.exclude.map(t=>`<span class="chip exclude">− ${esc(t)} <button data-chip="exclude" data-value="${esc(t)}">×</button></span>`);
  return [...include,...exclude].join('');
}
function attachSidebar(state,onChange){
  const filters=document.querySelector('#filters');
  const selected=document.querySelector('#selected-filters');
  const redraw=()=>{
    if(selected)selected.innerHTML=selectedFilterChips(state);
    document.querySelectorAll('.tag-filter-row').forEach(row=>{
      const tag=row.dataset.tag;
      row.querySelector('.include')?.classList.toggle('active',state.include.some(x=>x.toLowerCase()===tag.toLowerCase()));
      row.querySelector('.exclude')?.classList.toggle('active',state.exclude.some(x=>x.toLowerCase()===tag.toLowerCase()));
    });
  };
  filters?.addEventListener('click',e=>{
    const toggle=e.target.closest('.tag-toggle');
    if(toggle){
      const tag=toggle.closest('.tag-filter-row').dataset.tag;
      const action=toggle.dataset.action;
      const mine=action==='include'?state.include:state.exclude;
      const other=action==='include'?state.exclude:state.include;
      const found=mine.findIndex(x=>x.toLowerCase()===tag.toLowerCase());
      if(found>=0)mine.splice(found,1);
      else{
        const oi=other.findIndex(x=>x.toLowerCase()===tag.toLowerCase()); if(oi>=0)other.splice(oi,1);
        mine.push(tag);
      }
      redraw();onChange();return;
    }
    const chip=e.target.closest('[data-chip]');
    if(chip){
      const arr=chip.dataset.chip==='include'?state.include:state.exclude;
      const i=arr.findIndex(x=>x.toLowerCase()===chip.dataset.value.toLowerCase());if(i>=0)arr.splice(i,1);
      redraw();onChange();
    }
  });
  document.querySelector('#tag-search')?.addEventListener('input',e=>{
    const q=e.target.value.trim().toLowerCase();
    document.querySelectorAll('.tag-filter-row').forEach(row=>row.hidden=q&&!row.dataset.tag.toLowerCase().includes(q));
  });
  document.querySelector('#reset-filters')?.addEventListener('click',()=>{
    state.q='';state.creator='';state.include=[];state.exclude=[...DEFAULT_EXCLUDED];state.match='all';state.sort=page==='deleted'?'deleted-newest':'trending';
    const s=document.querySelector('#search');if(s)s.value='';
    const c=document.querySelector('#creator');if(c)c.value='';
    const so=document.querySelector('#sort');if(so)so.value=state.sort;
    const all=document.querySelector('input[name=match][value=all]');if(all)all.checked=true;
    redraw();onChange();
  });
  document.querySelectorAll('input[name=match]').forEach(x=>x.addEventListener('change',()=>{state.match=document.querySelector('input[name=match]:checked')?.value||'all';onChange();}));
  redraw();
}

function toolbar(state,deletedOnly){
  const sorts=deletedOnly
    ? `<option value="deleted-newest">Recently deleted</option><option value="popular">Most messages</option><option value="top-rated">Top rated</option><option value="name">Name</option>`
    : `<option value="trending">Trending</option><option value="popular">Popular</option><option value="top-rated">Top rated</option>`;
  return `<button type="button" class="secondary-button mobile-filter-toggle" id="mobile-filter-toggle">Filters</button>
    <div class="toolbar"><input id="search" value="${esc(state.q)}" placeholder="Search bots">
      <input id="creator" value="${esc(state.creator)}" placeholder="Creator username">
      <select id="sort">${sorts}</select></div>`;
}
function tsLiteral(value){return `\`${String(value).replace(/\\/g,'\\\\').replace(/`/g,'\\`')}\``;}
function buildTsFilter(runtime,state){
  const parts=[runtime.typesense.baseFilter];
  if(state.creator)parts.push(`creator_username:=${tsLiteral(state.creator)}`);
  if(state.include.length){
    if(state.match==='any')parts.push(`tags:=[${state.include.map(tsLiteral).join(',')}]`);
    else parts.push(...state.include.map(t=>`tags:=${tsLiteral(t)}`));
  }
  for(const tag of state.exclude)parts.push(`tags:!=${tsLiteral(tag)}`);
  return parts.filter(Boolean).join(' && ');
}
async function typesenseSearch(runtime,search){
  const urls=[runtime.typesense.url,...(runtime.typesense.fallbackUrls||[])];
  let last;
  for(const url of urls){
    try{
      const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-TYPESENSE-API-KEY':runtime.typesense.apiKey},body:JSON.stringify({searches:[search]})});
      if(!r.ok){last=new Error(`Typesense ${r.status}`);continue;}
      const data=await r.json(), result=(data.results||[])[0]||{};
      if(result.error){last=new Error(result.error);continue;}
      return result;
    }catch(e){last=e;}
  }
  throw last||new Error('Typesense request failed');
}
function docToCard(doc){
  return {
    id:String(doc.character_id||doc.id||'').toLowerCase(),
    name:doc.name||doc.title||'Unknown bot',title:doc.title||'',creator:doc.creator_username||'',
    tags:Array.isArray(doc.tags)?doc.tags:[],status:'public',
    isNsfw:!!(doc.is_nsfw||doc.avatar_is_nsfw),
    avatar:doc.avatar_url||doc.avatar||doc.image,
    messages:doc.num_messages,messages24h:doc.num_messages_24h,rating:doc.rating_score,
    createdAt:doc.createdAt,updatedAt:doc.updatedAt,avatarArchived:false
  };
}
function localFilterAndSort(bots,state){
  const q=state.q.toLowerCase(), creator=state.creator.toLowerCase(), inc=state.include.map(x=>x.toLowerCase()), exc=state.exclude.map(x=>x.toLowerCase());
  let rows=bots.filter(b=>{
    if(creator&&String(b.creator||'').toLowerCase()!==creator)return false;
    const tags=(b.tags||[]).map(x=>String(x).toLowerCase());
    if(inc.length){const ok=state.match==='any'?inc.some(t=>tags.includes(t)):inc.every(t=>tags.includes(t));if(!ok)return false;}
    if(exc.some(t=>tags.includes(t)))return false;
    if(q&&!([b.name,b.title,b.creator,...(b.tags||[])].join(' ').toLowerCase().includes(q)))return false;
    return true;
  });
  rows.sort((a,b)=>{
    switch(state.sort){
      case 'popular':return Number(b.messages||0)-Number(a.messages||0);
      case 'top-rated':return Number(b.rating||0)-Number(a.rating||0);
      case 'name':return String(a.name||'').localeCompare(String(b.name||''));
      default:return String(b.statusSince||'').localeCompare(String(a.statusSince||''));
    }
  });
  return rows;
}

async function browseLive(runtime,manifest,tags,stats){
  const state={q:qs('q')||'',include:parseTags(qs('include')),exclude:readExcluded(),creator:qs('creator')||'',sort:qs('sort')||'trending',match:qs('match')||'all',blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0',page:1};
  app.innerHTML=`<section class="hero"><div class="hero-row"><div><h1>SpicyChat Archive</h1><p>A public historical catalog of discoverable SpicyChat characters. Discovery currently has priority while the archive expands through the catalog.</p></div></div></section>
    ${growthBanner(stats)}<div class="layout">${tagSidebar(state,tags)}<section class="results">${toolbar(state,false)}
      <div class="scanline">Archive scan: <strong>${date(manifest.lastScan)}</strong> · ${fmt(stats?.totalBots||manifest.totalBots)} bots captured so far.</div>
      <div class="results-head"><h2>Public bots</h2><span id="result-count"></span></div><section class="grid" id="grid"></section>
      <div class="load-more-wrap"><button type="button" class="load-more" id="load-more" hidden>Load more</button></div>
      <footer class="footer">Active browsing queries SpicyChat's public Typesense index. Historical bot records and archived images are stored separately in the archive.</footer>
    </section></div>`;
  const $=s=>document.querySelector(s),grid=$('#grid'),count=$('#result-count'),more=$('#load-more');
  $('#sort').value=state.sort;
  let rows=[],found=0,requestNo=0,timer;
  const sync=()=>{state.q=$('#search').value.trim();state.creator=$('#creator').value.trim();state.sort=$('#sort').value;state.match=document.querySelector('input[name=match]:checked')?.value||'all';state.blurNsfw=$('#blur-nsfw').checked;localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0');setQuery(state);};
  async function run(reset=true){
    sync();if(reset){state.page=1;rows=[];grid.innerHTML='<p class="loading">Loading public bots…</p>';}
    const token=++requestNo;
    const sortBy=(runtime.sorts||{})[state.sort]||runtime.sorts?.trending||'num_messages_24h:desc';
    const search={collection:runtime.typesense.collection,q:state.q||'*',query_by:runtime.typesense.queryBy,page:state.page,per_page:60,filter_by:buildTsFilter(runtime,state),sort_by:sortBy,include_fields:'character_id,name,title,tags,creator_username,avatar_url,avatar_is_nsfw,is_nsfw,num_messages,num_messages_24h,rating_score,createdAt,updatedAt'};
    try{
      const result=await typesenseSearch(runtime,search);if(token!==requestNo)return;
      found=Number(result.found||0);const next=(result.hits||[]).map(h=>docToCard(h.document||{})).filter(b=>b.id);
      rows=reset?next:[...rows,...next];
      grid.innerHTML=rows.length?rows.map(b=>card(b,state.blurNsfw)).join(''):'<div class="empty">No public bots match these filters.</div>';
      count.textContent=`${found.toLocaleString()} matches · ${rows.length.toLocaleString()} loaded`;
      more.hidden=rows.length>=found||!next.length;
    }catch(e){if(token!==requestNo)return;grid.innerHTML=`<div class="error">Could not query the public bot index: ${esc(e.message||e)}</div>`;count.textContent='';more.hidden=true;}
  }
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(()=>run(true),240);};
  attachSidebar(state,()=>run(true));
  $('#search').addEventListener('input',schedule);$('#creator').addEventListener('input',schedule);$('#sort').addEventListener('change',()=>run(true));$('#blur-nsfw').addEventListener('change',()=>run(true));
  $('#mobile-filter-toggle').addEventListener('click',()=>$('#filters').classList.toggle('open'));
  more.addEventListener('click',()=>{state.page++;run(false);});
  await run(true);
}

async function browseDeleted(runtime,manifest,tags,stats){
  if(runtime.r2ReadAllowed===false){
    app.innerHTML='<section class="hero"><h1>Deleted bots</h1></section><div class="error">Archived details are temporarily paused by the R2 quota safety guard.</div>';return;
  }
  const state={q:qs('q')||'',include:parseTags(qs('include')),exclude:readExcluded(),creator:qs('creator')||'',sort:qs('sort')||'deleted-newest',match:qs('match')||'all',blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0'};
  let bots=[];
  if(runtime.deletedIndexUrl){
    try{
      const payload=await fetchJson(runtime.deletedIndexUrl);
      if(Array.isArray(payload?.bots))bots=payload.bots;
      else if(payload&&typeof payload==='object')bots=Object.values(payload).filter(row=>row&&typeof row==='object');
    }catch{}
  }
  app.innerHTML=`<section class="hero"><h1>Deleted bots</h1><p>Characters confirmed unavailable by repeated public character API 404s. Last-known public data remains preserved.</p></section>
    ${growthBanner(stats)}<div class="layout">${tagSidebar(state,tags)}<section class="results">${toolbar(state,true)}
      <div class="scanline">Archive scan: <strong>${date(manifest.lastScan)}</strong></div>
      <div class="results-head"><h2>Deleted / archived</h2><span id="result-count"></span></div><section class="grid" id="grid"></section>
      <footer class="footer">A bot is only moved here after repeated explicit public character API 404s. Disappearing from a listing alone is not deletion evidence.</footer>
    </section></div>`;
  const $=s=>document.querySelector(s),grid=$('#grid'),count=$('#result-count');$('#sort').value=state.sort;
  const render=()=>{state.q=$('#search').value.trim();state.creator=$('#creator').value.trim();state.sort=$('#sort').value;state.match=document.querySelector('input[name=match]:checked')?.value||'all';state.blurNsfw=$('#blur-nsfw').checked;localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0');setQuery(state);const rows=localFilterAndSort(bots,state);const totalConfirmed=Math.max(bots.length,Number(stats?.deletedBots)||0);count.textContent=rows.length===totalConfirmed?`${rows.length.toLocaleString()} shown`:`${rows.length.toLocaleString()} shown · ${totalConfirmed.toLocaleString()} confirmed total`;grid.innerHTML=rows.length?rows.slice(0,2000).map(b=>card(b,state.blurNsfw)).join(''):'<div class="empty">No deleted bots match these filters.</div>';};
  attachSidebar(state,render);$('#search').addEventListener('input',render);$('#creator').addEventListener('input',render);$('#sort').addEventListener('change',render);$('#blur-nsfw').addEventListener('change',render);$('#mobile-filter-toggle').addEventListener('click',()=>$('#filters').classList.toggle('open'));render();
}
async function browse(){
  const [runtime,manifest,tags,stats]=await Promise.all([loadRuntime(),loadManifest(),loadTags(),loadStats()]);
  if(runtime.storageMode!=='r2')throw new Error('This website update expects the completed R2 migration.');
  return page==='deleted'?browseDeleted(runtime,manifest,tags,stats):browseLive(runtime,manifest,tags,stats);
}

function bestField(record,key){
  const lk=record.lastKnown||{};
  if(lk[key]!==undefined&&lk[key]!==null&&String(lk[key]).trim?.()!=='')return lk[key];
  const current=record.current||{};
  for(const source of ['character-api','typesense','typesense:trending','typesense:popular','typesense:top-rated','typesense:explore']){
    const v=current[source]?.[key];if(v!==undefined&&v!==null)return v;
  }
  for(const value of Object.values(current)){if(value&&typeof value==='object'&&value[key]!==undefined&&value[key]!==null)return value[key];}
  return null;
}
async function loadBotRecord(id,runtime){
  if(runtime.storageMode==='r2'&&runtime.r2ReadAllowed===false){const e=new Error('R2_READ_BUDGET_PAUSED');e.code='R2_READ_BUDGET_PAUSED';throw e;}
  if(runtime.storageMode==='r2'&&runtime.publicDataBaseUrl){
    const compact=id.replaceAll('-','').toLowerCase(),prefix=compact.slice(0,2)||'__';
    return fetchJson(`${runtime.publicDataBaseUrl.replace(/\/$/,'')}/bots/${prefix}/${encodeURIComponent(id.toLowerCase())}.json`);
  }
  return fetchJson(asset(`data/bots/${encodeURIComponent(id)}.json`));
}
function archivedAvatar(record,runtime){
  const a=record.avatarArchive||{};
  if(a.publicUrl)return a.publicUrl;
  if(a.r2Url)return a.r2Url;
  if(a.r2Key&&runtime.publicDataBaseUrl)return `${runtime.publicDataBaseUrl.replace(/\/$/,'')}/${String(a.r2Key).replace(/^\/+/,'')}`;
  if(a.path&&runtime.publicDataBaseUrl){
    const clean=String(a.path).replace(/^\/+/,'').replace(/^archive\//,'');
    const mediaIndex=clean.indexOf('media/');
    if(mediaIndex>=0)return `${runtime.publicDataBaseUrl.replace(/\/$/,'')}/${clean.slice(mediaIndex)}`;
  }
  return '';
}
async function botPage(){
  const id=qs('id');if(!id){app.innerHTML='<div class="error">No bot ID supplied.</div>';return;}
  const runtime=await loadRuntime();let record;
  try{record=await loadBotRecord(id,runtime);}catch(e){app.innerHTML=`<div class="error">${e?.code==='R2_READ_BUDGET_PAUSED'?'Archived bot details are temporarily paused by the R2 quota safety guard.':'This bot has not been captured by the archive yet.'}</div>`;return;}
  const lk=record.lastKnown||{},status=record.status?.current||'unknown';
  const cdn=resolveAvatar(lk.avatar_url||lk.avatar||lk.image), archived=archivedAvatar(record,runtime);
  const primary=status==='deleted'?(archived||cdn):(cdn||archived), fallback=status==='deleted'?cdn:archived;
  const fields=['description','greeting','greetings','personality','definition','persona','character_definition','characterDefinition','scenario','example_dialogue','example_dialogues','system_prompt','post_history_instructions','lorebooks'];
  const fieldHtml=fields.map(k=>{const v=bestField(record,k);if(v==null||v==='')return'';return `<div class="field-block"><h3>${esc(k.replaceAll('_',' '))} · last known</h3><div class="pre">${esc(Array.isArray(v)?v.join('\n\n'):typeof v==='object'?JSON.stringify(v,null,2):v)}</div></div>`}).join('');
  const tags=(lk.tags||[]).map(t=>`<span class="pill">${esc(t)}</span>`).join('');
  const history=(record.fieldHistory||[]).filter(h=>!['updatedAt','updated_at','lastUpdatedAt','last_updated_at'].includes(String(h.path||'').split('.').pop())).slice().reverse().slice(0,100).map(h=>`<div class="history-item"><b>${esc(h.path)}</b> · ${esc(h.kind||'value')}<br><span class="detail-sub">${date(h.at)} · ${esc(h.source||'')}</span></div>`).join('');
  app.innerHTML=`<article class="detail"><div class="detail-head"><div class="detail-art">${imgHtml(primary,fallback,{alt:bestField(record,'name')||''})}</div>
    <div><div class="detail-sub">${esc(record.id)}</div><h1>${esc(bestField(record,'name')||bestField(record,'title')||'Unknown bot')}</h1>
    <div class="detail-sub">${bestField(record,'creator_username')?`@${esc(bestField(record,'creator_username'))}`:'Unknown creator'}</div>
    <div class="pill-row"><span class="pill">${esc(status)}</span>${archived?'<span class="pill">image archived</span>':''}<span class="pill">first seen ${date(record.firstSeenAt)}</span><span class="pill">last seen ${date(record.lastSeenAt)}</span></div>
    <div class="pill-row">${tags}</div><p>${esc(bestField(record,'title')||'')}</p>
    <div class="detail-actions"><a class="primary-button" href="${esc(spicyHref(record.id))}" target="_blank" rel="noopener">Open in SpicyChat ↗</a><a class="secondary-button" href="../">Back to archive</a></div></div></div>
    <section class="section"><h2>Last-known archived fields</h2><p class="detail-sub">A field stays here after SpicyChat stops exposing it. That does not mean it is still currently public.</p>${fieldHtml||'<p class="detail-sub">No rich definition fields have been recovered yet.</p>'}</section>
    <section class="section"><h2>Latest metrics</h2><div class="pre">${esc(JSON.stringify(record.metrics?.latest||{},null,2))}</div></section>
    <section class="section"><h2>Availability history</h2><div class="pre">${esc(JSON.stringify(record.availabilityHistory||[],null,2))}</div></section>
    <section class="section"><h2>Field history</h2>${history||'<p class="detail-sub">No meaningful field changes recorded yet.</p>'}</section>
    <section class="section"><h2>Raw latest observations</h2><div class="pre">${esc(JSON.stringify(record.current||{},null,2))}</div></section>
  </article>`;
}

function ageText(start){
  const t=new Date(start).getTime();if(!Number.isFinite(t))return 'Unknown';
  const days=Math.max(0,(Date.now()-t)/86400000);
  if(days<1)return `${Math.max(1,Math.floor(days*24))} hours`;
  if(days<60)return `${Math.floor(days)} days`;
  return `${(days/30.44).toFixed(1)} months`;
}
function growthChart(runs){
  const rows=(runs||[]).filter(r=>Number.isFinite(Number(r.totalBots))).slice(-120);
  if(rows.length<2)return '<div class="empty">More successful archive runs are needed before the growth chart has enough points.</div>';
  const W=900,H=260,pad={l:56,r:16,t:15,b:30};
  const vals=rows.map(r=>Number(r.totalBots)),min=Math.min(...vals),max=Math.max(...vals),span=Math.max(1,max-min);
  const pts=rows.map((r,i)=>{const x=pad.l+(i/(rows.length-1))*(W-pad.l-pad.r),y=pad.t+(1-(Number(r.totalBots)-min)/span)*(H-pad.t-pad.b);return [x,y];});
  const path=pts.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const dots=pts.filter((_,i)=>i===0||i===pts.length-1||i%Math.max(1,Math.floor(pts.length/10))===0).map(p=>`<circle class="chart-dot" cx="${p[0]}" cy="${p[1]}" r="3"/>`).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Archived bot growth chart">
    <line class="chart-axis" x1="${pad.l}" y1="${H-pad.b}" x2="${W-pad.r}" y2="${H-pad.b}"/>
    <line class="chart-axis" x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${H-pad.b}"/>
    <text class="chart-label" x="4" y="${pad.t+8}">${fmt(max)}</text><text class="chart-label" x="4" y="${H-pad.b}">${fmt(min)}</text>
    <text class="chart-label" x="${pad.l}" y="${H-8}">${shortDate(rows[0].at)}</text><text class="chart-label" text-anchor="end" x="${W-pad.r}" y="${H-8}">${shortDate(rows.at(-1).at)}</text>
    <path class="chart-line" d="${path}"/>${dots}</svg>`;
}
async function statsPage(){
  const [stats,manifest,tags,runtime]=await Promise.all([loadStats(),loadManifest(),loadTags(),loadRuntime()]);
  if(!stats){app.innerHTML='<section class="hero"><h1>Archive stats</h1><p>The public stats file will be created by the next successful archive run after this update.</p></section>';return;}
  const runs=(stats.runs||[]).filter(r=>r.kind==='archive-run');
  const recent=runs.slice(-10).reverse();
  const latest=runs.at(-1)||{};
  const recentHtml=recent.map(r=>`<div class="recent-run"><div><b>${date(r.at)}</b><br><span>${fmt(r.exploration?.pagesCompleted)} discovery pages · ${fmt(r.exploration?.hits)} hits</span></div><div><b>+${fmt(r.addedSincePrevious??r.exploration?.new??0)}</b><br><span>${fmt(r.exploration?.new)} new this scan</span></div></div>`).join('');
  const table=recent.map(r=>`<tr><td>${date(r.at)}</td><td>+${fmt(r.addedSincePrevious??0)}</td><td>${fmt(r.exploration?.new)}</td><td>${fmt(r.exploration?.pagesCompleted)}/${fmt(r.exploration?.pageBudget)}</td><td>${r.runDurationSeconds?`${Math.round(r.runDurationSeconds/60)}m`:'—'}</td></tr>`).join('');
  const guard=runtime.r2QuotaGuard||{},usage=guard.usage||{},adaptive=stats.adaptiveDiscovery||{};
  app.innerHTML=`<section class="hero"><h1>Archive stats</h1><p>Growth, discovery depth and storage statistics for the public archive. Full scan history lives in R2; this page reads a compact public history file.</p></section>
    <section class="stats-grid">
      <div class="stat"><b>${fmt(stats.totalBots)}</b><span>Bots captured by the archive</span></div>
      <div class="stat"><b>~${fmt(Math.round(stats.growth?.averagePerDay||0))}/day</b><span>Observed archive growth pace</span></div>
      <div class="stat"><b>${fmt(stats.publicIndexBots)}</b><span>Public bots reported by SpicyChat's index</span></div>
      <div class="stat"><b>${ageText(stats.startedAt)}</b><span>Archive site age</span></div>
      <div class="stat"><b>+${fmt(stats.growth?.added24h)}</b><span>Captured in the last ~24h window</span></div>
      <div class="stat"><b>+${fmt(stats.growth?.added7d)}</b><span>Captured in the last ~7d window</span></div>
      <div class="stat"><b>${fmt(stats.deletedBots)}</b><span>Confirmed deleted / archived</span></div>
      <div class="stat"><b>${fmt(tags.length)}</b><span>Current supported SpicyChat tags</span></div>
    </section>
    <div class="stats-layout"><section class="chart-card"><h2>Archive growth</h2>${growthChart(stats.runs)}</section>
      <section class="table-card"><h2>Discovery right now</h2><div class="recent-list">
        <div class="recent-run"><div><b>${fmt(adaptive.nextPageBudget||latest.exploration?.nextPageBudget)} pages</b><br><span>next adaptive discovery budget</span></div><div><b>${fmt(adaptive.maxPages)}</b><br><span>configured cap</span></div></div>
        <div class="recent-run"><div><b>${Math.round((adaptive.timeLimitSeconds||3600)/60)} min</b><br><span>discovery time ceiling</span></div><div><b>+${fmt(adaptive.growthPerSuccess)}</b><br><span>page after healthy run</span></div></div>
        <div class="recent-run"><div><b>${fmt(latest.exploration?.new)}</b><br><span>new bots last scan</span></div><div><b>${fmt(latest.exploration?.pagesCompleted)}</b><br><span>pages completed</span></div></div>
      </div></section></div>
    <div class="stats-layout"><section class="table-card"><h2>Recent archive runs</h2><div style="overflow:auto"><table class="stats-table"><thead><tr><th>Run</th><th>Archive Δ</th><th>New discovered</th><th>Pages</th><th>Total runtime</th></tr></thead><tbody>${table||'<tr><td colspan="5">No run history yet.</td></tr>'}</tbody></table></div></section>
      <section class="table-card"><h2>R2 / crawler</h2><div class="recent-list">
        <div class="recent-run"><div><b>${fmtBytes(latest.storage?.usedBytes||manifest.storage?.usedBytes)}</b><br><span>archive storage</span></div><div><b>${fmt(latest.storage?.objects||usage.objectCount)}</b><br><span>R2 objects</span></div></div>
        <div class="recent-run"><div><b>${fmt(usage.classA)}</b><br><span>Class A this month</span></div><div><b>${fmt(usage.classB)}</b><br><span>Class B this month</span></div></div>
        <div class="recent-run"><div><b>${fmt(latest.enrichment?.enriched)}</b><br><span>enriched last scan</span></div><div><b>${fmt(latest.images?.saved)}</b><br><span>images archived last scan</span></div></div>
      </div></section></div>
    <section class="table-card"><h2>Latest number updates</h2><div class="recent-list">${recentHtml||'<div class="filter-note">No completed scan rows yet.</div>'}</div></section>
    <footer class="footer">“Bots captured by the archive” is not the same thing as SpicyChat's total public index count. The archive grows as discovery continues through the catalog.</footer>`;
}

(async()=>{
  try{
    if(page==='bot')await botPage();
    else if(page==='stats')await statsPage();
    else await browse();
  }catch(e){
    console.error(e);
    app.innerHTML=`<div class="error">Could not load the archive: ${esc(e.message||e)}</div>`;
  }
})();
