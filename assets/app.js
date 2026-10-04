const asset = (path) => new URL(`../${path}`, import.meta.url);
const app = document.querySelector('#app');
const page = document.body.dataset.page || 'browse';
const DEFAULT_EXCLUDED = ['NTR','Cheating'];
const PAGE_SIZE = 100;
const TRANSLATION_ENDPOINT = 'https://spicychat-archive-import.dragongraf.workers.dev/api/translate';
const TRANSPARENT_GIF = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';
const SMALL_CAPS = Object.freeze({
  'ᴀ':'a','ʙ':'b','ᴄ':'c','ᴅ':'d','ᴇ':'e','ꜰ':'f','ғ':'f','ɢ':'g','ʜ':'h','ɪ':'i','ᴊ':'j','ᴋ':'k','ʟ':'l','ᴍ':'m','ɴ':'n','ᴏ':'o','ᴘ':'p','ǫ':'q','ʀ':'r','ꜱ':'s','s':'s','ᴛ':'t','ᴜ':'u','ᴠ':'v','ᴡ':'w','x':'x','ʏ':'y','ᴢ':'z',
  'ᴬ':'A','ᴮ':'B','ᴰ':'D','ᴱ':'E','ᴳ':'G','ᴴ':'H','ᴵ':'I','ᴶ':'J','ᴷ':'K','ᴸ':'L','ᴹ':'M','ᴺ':'N','ᴼ':'O','ᴾ':'P','ᴿ':'R','ᵀ':'T','ᵁ':'U','ⱽ':'V','ᵂ':'W'
});
const translationMemory = new Map();

function normalizeDisplayText(value){
  let text=String(value??'');
  try{text=text.normalize('NFKC');}catch{}
  text=[...text].map(ch=>SMALL_CAPS[ch]??ch).join('');
  return text
    .replace(/[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF\u00AD]/g,'')
    .replace(/[‘’‚‛`´]/g,"'")
    .replace(/[“”„‟]/g,'"')
    .replace(/[‐‑‒–—―]/g,'-')
    .replace(/[⁄∕]/g,'/')
    .replace(/…/g,'...')
    .replace(/\s+/g,' ')
    .trim();
}
function parsePage(value){
  const n=Math.floor(Number(value)||1);return Math.max(1,n);
}
function likelyNeedsTranslation(value,bot={}){
  const text=String(value||'').trim();if(text.length<2)return false;
  const tags=(bot.tags||[]).map(tag=>String(tag).toLowerCase());
  if(tags.includes('non-english'))return true;
  return /[\u0370-\u03FF\u0400-\u052F\u0530-\u058F\u0590-\u05FF\u0600-\u06FF\u0750-\u077F\u0900-\u097F\u0E00-\u0E7F\u10A0-\u10FF\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF]/u.test(text)||/[À-ÖØ-öø-ÿĀ-ž]/u.test(text);
}
function languageDisplayName(code){
  const clean=String(code||'').trim().toLowerCase();if(!clean)return 'another language';
  try{return new Intl.DisplayNames(['en'],{type:'language'}).of(clean)||clean.toUpperCase();}catch{return clean.toUpperCase();}
}

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
const creatorHref = creator => new URL(`../creator/?name=${encodeURIComponent(creator)}`, import.meta.url).href;
const spicyHref = id => `https://spicychat.ai/chatbot/${encodeURIComponent(id)}`;

async function fetchJson(url, options={}){
  const r=await fetch(url,{cache:'no-store',...options});
  if(!r.ok)throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
async function fetchCachedJson(url){
  const r=await fetch(url,{cache:'default'});
  if(!r.ok)throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
async function loadManifest(){return fetchJson(asset('data/manifest.json'));}
async function loadStats(){try{return await fetchJson(asset('data/stats.json'));}catch{return null;}}
async function loadTags(){try{return (await fetchJson(asset('data/tags.json'))).tags||[];}catch{return [];}}
async function loadArchiveHistory(){try{return await fetchJson(asset('data/archive-history.json'));}catch{return { milestones: [] };}}
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
function setQuery(state,push=false){
  const u=new URL(location.href);
  const set=(k,v)=>{
    if(Array.isArray(v)){
      if(v.length)u.searchParams.set(k,v.join(','));
      else u.searchParams.delete(k);
    }else if(v)u.searchParams.set(k,v);
    else u.searchParams.delete(k);
  };
  set('q',state.q); set('include',state.include); set('creator',state.creator); set('sort',state.sort); set('match',state.match);
  set('definition',state.definition==='any'?'':state.definition);
  set('definitionSize',state.definitionSize==='any'?'':state.definitionSize);
  set('lorebook',state.lorebook==='any'?'':state.lorebook);
  set('language',state.language==='any'?'':state.language);
  set('rating',state.contentRating==='any'?'':state.contentRating);
  set('created',state.created==='any'?'':state.created);
  set('firstSeen',state.firstSeen==='any'?'':state.firstSeen);
  set('lastSeen',state.lastSeen==='any'?'':state.lastSeen);
  set('savedField',state.savedField==='any'?'':state.savedField);
  if(Number(state.page)>1)u.searchParams.set('p',String(Math.floor(Number(state.page))));else u.searchParams.delete('p');
  if(state.exclude.length)u.searchParams.set('exclude',state.exclude.join(','));
  else u.searchParams.set('exclude','none');
  history[push?'pushState':'replaceState'](null,'',u);
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
  const animated=/\.gif(?:$|[?#])/i.test(src);
  return `<img src="${esc(src)}"${fallbackAttr} alt="${esc(normalizeDisplayText(alt))}" loading="lazy" class="${blur?'nsfw-blur':''}"${animated?' data-hover-animate="1"':''} onerror="if(this.dataset.fallback&&this.src!==this.dataset.fallback){this.src=this.dataset.fallback;this.dataset.fallback=''}else{this.remove()}">`;
}
function card(bot, blurNsfw){
  const tags=(bot.tags||[]).slice(0,5).map(t=>`<span class="tag">${esc(t)}</span>`).join('');
  const first=bot.firstSeenAt?`<span>Seen ${shortDate(bot.firstSeenAt)}</span>`:(bot.createdAt?`<span>Made ${shortDate(bot.createdAt)}</span>`:'');
  const rawName=String(bot.name||'Unknown bot'),rawTitle=String(bot.title||'');
  const displayName=normalizeDisplayText(rawName)||'Unknown bot',displayTitle=normalizeDisplayText(rawTitle);
  return `<article class="card" data-bot-id="${esc(bot.id||'')}" data-original-name="${esc(rawName)}" data-original-title="${esc(rawTitle)}"><a class="cardlink" href="${esc(detailHref(bot.id))}">
    <div class="art">${imgHtml(bot.avatar,bot.avatarFallback,{blur:blurNsfw&&bot.isNsfw,alt:displayName})}${badge(bot.status)}</div>
    <div class="body"><h3 data-card-name${rawName!==displayName?` title="${esc(rawName)}"`:''}>${esc(displayName)}</h3><div class="creator${bot.creator?' creator-link':''}"${bot.creator?` data-creator-link="${esc(bot.creator)}" title="Open creator archive"`:''}>${bot.creator?`@${esc(bot.creator)}`:'Unknown creator'}</div>
    <div class="title" data-card-title${rawTitle!==displayTitle?` title="${esc(rawTitle)}"`:''}>${esc(displayTitle)}</div><div class="translation-note" hidden></div><div class="taglist">${tags}</div>
    <div class="meta"><span>${fmt(bot.messages)} msgs</span><span>${bot.rating==null?'—':`★ ${esc(bot.rating)}`}</span>${first}<span>${bot.avatarArchived?'image archived':'CDN image'}</span></div></div>
  </a></article>`;
}

document.addEventListener('click',event=>{
  const target=event.target.closest?.('[data-creator-link]');
  if(!target)return;
  const creator=target.getAttribute('data-creator-link');
  if(!creator)return;
  event.preventDefault();
  event.stopPropagation();
  location.href=creatorHref(creator);
});

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
  const selected=(value,wanted)=>value===wanted?' selected':'';
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
      <section class="filter-section archive-data-filters"><h3>More filters</h3>
        <label>Personality / definition<select class="filter-select" data-meta-filter="definition"><option value="any"${selected(state.definition,'any')}>Any visibility</option><option value="exposed"${selected(state.definition,'exposed')}>Exposed</option><option value="hidden"${selected(state.definition,'hidden')}>Hidden</option></select></label>
        <label>Definition size<select class="filter-select" data-meta-filter="definitionSize"><option value="any"${selected(state.definitionSize,'any')}>Any size</option><option value="low"${selected(state.definitionSize,'low')}>Low</option><option value="mid"${selected(state.definitionSize,'mid')}>Medium</option><option value="high"${selected(state.definitionSize,'high')}>High</option></select></label>
        <label>Lorebook<select class="filter-select" data-meta-filter="lorebook"><option value="any"${selected(state.lorebook,'any')}>Any</option><option value="yes"${selected(state.lorebook,'yes')}>Has lorebook</option><option value="no"${selected(state.lorebook,'no')}>No lorebook</option></select></label>
        <label>Language<select class="filter-select" data-meta-filter="language"><option value="any"${selected(state.language,'any')}>Any language</option><option value="en"${selected(state.language,'en')}>English</option><option value="non-en"${selected(state.language,'non-en')}>Non-English</option></select></label>
        <label>Content<select class="filter-select" data-meta-filter="contentRating"><option value="any"${selected(state.contentRating,'any')}>SFW + NSFW</option><option value="sfw"${selected(state.contentRating,'sfw')}>SFW only</option><option value="nsfw"${selected(state.contentRating,'nsfw')}>NSFW only</option></select></label>
        <label>Created<select class="filter-select" data-meta-filter="created"><option value="any"${selected(state.created,'any')}>Any time</option><option value="1d"${selected(state.created,'1d')}>Last 24 hours</option><option value="7d"${selected(state.created,'7d')}>Last 7 days</option><option value="30d"${selected(state.created,'30d')}>Last 30 days</option></select></label>
        <label>First captured<select class="filter-select" data-meta-filter="firstSeen"><option value="any"${selected(state.firstSeen,'any')}>Any time</option><option value="1d"${selected(state.firstSeen,'1d')}>Last 24 hours</option><option value="7d"${selected(state.firstSeen,'7d')}>Last 7 days</option><option value="30d"${selected(state.firstSeen,'30d')}>Last 30 days</option><option value="90d"${selected(state.firstSeen,'90d')}>Last 90 days</option><option value="1y"${selected(state.firstSeen,'1y')}>Last year</option></select></label>
        <label>Last seen<select class="filter-select" data-meta-filter="lastSeen"><option value="any"${selected(state.lastSeen,'any')}>Any time</option><option value="1d"${selected(state.lastSeen,'1d')}>Last 24 hours</option><option value="7d"${selected(state.lastSeen,'7d')}>Last 7 days</option><option value="30d"${selected(state.lastSeen,'30d')}>Last 30 days</option><option value="90d"${selected(state.lastSeen,'90d')}>Last 90 days</option><option value="1y"${selected(state.lastSeen,'1y')}>Last year</option></select></label>
        <div class="filter-note">Created is SpicyChat metadata. First captured / Last seen come from this archive's own observation history.</div>
      </section>
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
    state.definition='any';state.definitionSize='any';state.lorebook='any';state.language='any';state.contentRating='any';state.created='any';state.firstSeen='any';state.lastSeen='any';state.savedField='any';
    const s=document.querySelector('#search');if(s)s.value='';
    const c=document.querySelector('#creator');if(c)c.value='';
    const so=document.querySelector('#sort');if(so)so.value=state.sort;
    const all=document.querySelector('input[name=match][value=all]');if(all)all.checked=true;
    document.querySelectorAll('[data-meta-filter]').forEach(select=>{select.value='any';});
    const sf=document.querySelector('#saved-field');if(sf)sf.value='any';
    const safety=document.querySelector('#safety-filter');if(safety)safety.value='all';
    redraw();onChange();
  });
  document.querySelectorAll('input[name=match]').forEach(x=>x.addEventListener('change',()=>{state.match=document.querySelector('input[name=match]:checked')?.value||'all';onChange();}));
  document.querySelectorAll('[data-meta-filter]').forEach(select=>select.addEventListener('change',()=>{const key=select.dataset.metaFilter;if(key)state[key]=select.value||'any';if(key==='contentRating'){const safety=document.querySelector('#safety-filter');if(safety)safety.value=state[key]==='any'?'all':state[key];}onChange();}));
  redraw();
}

function toolbar(state,deletedOnly){
  const sorts=deletedOnly
    ? `<option value="deleted-newest">Recently deleted</option><option value="popular">Most messages</option><option value="top-rated">Top rated</option><option value="name">Name</option>`
    : `<option value="trending">Trending</option><option value="popular">Popular</option><option value="top-rated">Top rated</option>`;
  const selected=(value,wanted)=>value===wanted?' selected':'';
  return `<button type="button" class="secondary-button mobile-filter-toggle" id="mobile-filter-toggle">Filters</button>
    <div class="toolbar archive-search-toolbar"><input id="search" value="${esc(state.q)}" placeholder="Search bots">
      <input id="creator" value="${esc(state.creator)}" placeholder="Creator username">
      <select id="sort">${sorts}</select>
      <select id="saved-field" aria-label="Archived field saved">
        <option value="any"${selected(state.savedField,'any')}>Everything</option>
        <option value="personality"${selected(state.savedField,'personality')}>Personality</option>
        <option value="scenario"${selected(state.savedField,'scenario')}>Scenario</option>
        <option value="dialogue"${selected(state.savedField,'dialogue')}>Example Dialogue</option>
      </select>
      <select id="safety-filter" aria-label="SFW or NSFW">
        <option value="all"${selected(state.contentRating,'any')}>SFW + NSFW</option>
        <option value="sfw"${selected(state.contentRating,'sfw')}>SFW only</option>
        <option value="nsfw"${selected(state.contentRating,'nsfw')}>NSFW only</option>
      </select></div>
      <div class="rich-search-note">The extra-field selector filters by what the Archive actually saved for each bot; it is not limited by SpicyChat's public Typesense field list.</div>`;
}
function tsLiteral(value){return `\`${String(value).replace(/\\/g,'\\\\').replace(/`/g,'\\`')}\``;}
function buildTsFilter(runtime,state){
  const parts=[runtime.typesense.baseFilter];
  if(state.creator)parts.push(`creator_username:=${tsLiteral(state.creator)}`);
  if(state.definition==='exposed')parts.push('definition_visible:=true'); else if(state.definition==='hidden')parts.push('definition_visible:=false');
  if(state.definitionSize&&state.definitionSize!=='any')parts.push(`definition_size_category:=${tsLiteral(state.definitionSize)}`);
  if(state.lorebook==='yes')parts.push('has_lorebooks:=true'); else if(state.lorebook==='no')parts.push('has_lorebooks:=false');
  if(state.language==='en')parts.push(`language:=${tsLiteral('en')}`); else if(state.language==='non-en')parts.push(`language:!=${tsLiteral('en')}`);
  if(state.contentRating==='sfw')parts.push('is_nsfw:=false'); else if(state.contentRating==='nsfw')parts.push('is_nsfw:=true');
  const createdDays=state.created==='1d'?1:state.created==='7d'?7:state.created==='30d'?30:0;
  if(createdDays)parts.push(`createdAt:>=${Date.now()-createdDays*86400000}`);
  if(state.include.length){
    if(state.match==='any')parts.push(`tags:=[${state.include.map(tsLiteral).join(',')}]`);
    else parts.push(...state.include.map(t=>`tags:=${tsLiteral(t)}`));
  }
  for(const tag of state.exclude)parts.push(`tags:!=${tsLiteral(tag)}`);
  return parts.filter(Boolean).join(' && ');
}
let archiveRichFieldIndexPromise=null;
function flagsFromRichFieldMask(mask){
  const value=Number(mask||0);
  return {personality:!!(value&1),scenario:!!(value&2),dialogue:!!(value&4)};
}
async function loadArchiveRichFieldIndex(runtime){
  if(archiveRichFieldIndexPromise)return archiveRichFieldIndexPromise;
  archiveRichFieldIndexPromise=(async()=>{
    const base=String(runtime.publicDataBaseUrl||'').replace(/\/$/,'');
    if(!base)throw new Error('Archive rich-field index is unavailable.');
    const payload=await fetchCachedJson(`${base}/indexes/rich-fields.json`);
    if(!payload||payload.complete!==true||!payload.bots||typeof payload.bots!=='object'){
      throw new Error('Archive rich-field index is still rebuilding.');
    }
    return payload;
  })().catch(error=>{archiveRichFieldIndexPromise=null;throw error;});
  return archiveRichFieldIndexPromise;
}
async function archiveFieldPresence(runtime,ids){
  const wanted=[...new Set((ids||[]).map(id=>String(id||'').toLowerCase()).filter(Boolean))];
  const index=await loadArchiveRichFieldIndex(runtime);
  return new Map(wanted.map(id=>[id,flagsFromRichFieldMask(index.bots?.[id])]));
}
let archiveTimesIndexPromise=null;
async function loadArchiveTimesIndex(runtime){
  if(archiveTimesIndexPromise)return archiveTimesIndexPromise;
  archiveTimesIndexPromise=(async()=>{
    const base=String(runtime.publicDataBaseUrl||'').replace(/\/$/,'');
    if(!base)throw new Error('Archive time index is unavailable.');
    const payload=await fetchCachedJson(`${base}/indexes/archive-times.json`);
    if(!payload||payload.complete!==true||!payload.bots||typeof payload.bots!=='object'){
      throw new Error('Archive time index is still rebuilding.');
    }
    return payload;
  })().catch(error=>{archiveTimesIndexPromise=null;throw error;});
  return archiveTimesIndexPromise;
}
function archiveTimeCutoff(value){
  const days=value==='1d'?1:value==='7d'?7:value==='30d'?30:value==='90d'?90:value==='1y'?365:0;
  return days?Date.now()-days*86400000:0;
}
function matchesArchiveMeta(id,state,richIndex,timesIndex){
  if(state.savedField&&state.savedField!=='any'){
    const flags=flagsFromRichFieldMask(richIndex?.bots?.[id]);
    if(!flags[state.savedField])return false;
  }
  const row=timesIndex?.bots?.[id];
  if(state.firstSeen&&state.firstSeen!=='any'){
    const cutoff=archiveTimeCutoff(state.firstSeen);
    if(!Array.isArray(row)||Number(row[0]||0)<cutoff)return false;
  }
  if(state.lastSeen&&state.lastSeen!=='any'){
    const cutoff=archiveTimeCutoff(state.lastSeen);
    if(!Array.isArray(row)||Number(row[1]||0)<cutoff)return false;
  }
  return true;
}

async function typesenseMultiSearch(runtime,searches){
  const urls=[runtime.typesense.url,...(runtime.typesense.fallbackUrls||[])];
  let last;
  for(const url of urls){
    try{
      const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-TYPESENSE-API-KEY':runtime.typesense.apiKey},body:JSON.stringify({searches})});
      if(!r.ok){last=new Error(`Typesense ${r.status}`);continue;}
      const data=await r.json(),results=Array.isArray(data.results)?data.results:[];
      if(!results.length){last=new Error('Typesense returned no search results');continue;}
      return results;
    }catch(e){last=e;}
  }
  throw last||new Error('Typesense request failed');
}
async function typesenseSearch(runtime,search){
  const result=(await typesenseMultiSearch(runtime,[search]))[0]||{};
  if(result.error)throw new Error(result.error);
  return result;
}
function docToCard(doc){
  return {
    id:String(doc.character_id||doc.id||'').toLowerCase(),
    name:doc.name||doc.title||'Unknown bot',title:doc.title||'',creator:doc.creator_username||'',
    tags:Array.isArray(doc.tags)?doc.tags:[],status:'public',
    isNsfw:!!(doc.is_nsfw||doc.avatar_is_nsfw),
    avatar:doc.avatar_url||doc.avatar||doc.image,
    messages:doc.num_messages,messages24h:doc.num_messages_24h,rating:doc.rating_score,
    createdAt:doc.createdAt,updatedAt:doc.updatedAt,avatarArchived:false,
    definitionVisible:doc.definition_visible===true?true:doc.definition_visible===false?false:null,
    definitionSize:String(doc.definition_size_category||'').toLowerCase(),
    hasLorebooks:doc.has_lorebooks===true?true:doc.has_lorebooks===false?false:null,
    language:String(doc.language||'').toLowerCase()
  };
}
function botHasSavedField(bot,field){
  if(!field||field==='any')return true;
  return !!bot?.savedFields?.[field];
}
function localFilterAndSort(bots,state){
  const q=state.q.toLowerCase(), creator=state.creator.toLowerCase(), inc=state.include.map(x=>x.toLowerCase()), exc=state.exclude.map(x=>x.toLowerCase());
  const createdDays=state.created==='1d'?1:state.created==='7d'?7:state.created==='30d'?30:0,createdCutoff=createdDays?Date.now()-createdDays*86400000:0;
  const firstSeenCutoff=archiveTimeCutoff(state.firstSeen),lastSeenCutoff=archiveTimeCutoff(state.lastSeen);
  let rows=bots.filter(b=>{
    if(creator&&String(b.creator||'').toLowerCase()!==creator)return false;
    const tags=(b.tags||[]).map(x=>String(x).toLowerCase());
    if(inc.length){const ok=state.match==='any'?inc.some(t=>tags.includes(t)):inc.every(t=>tags.includes(t));if(!ok)return false;}
    if(exc.some(t=>tags.includes(t)))return false;
    if(state.definition==='exposed'&&b.definitionVisible!==true)return false;if(state.definition==='hidden'&&b.definitionVisible!==false)return false;
    if(state.definitionSize&&state.definitionSize!=='any'&&String(b.definitionSize||'').toLowerCase()!==state.definitionSize)return false;
    if(state.lorebook==='yes'&&b.hasLorebooks!==true)return false;if(state.lorebook==='no'&&b.hasLorebooks!==false)return false;
    if(state.language==='en'&&String(b.language||'').toLowerCase()!=='en')return false;if(state.language==='non-en'&&(!b.language||String(b.language).toLowerCase()==='en'))return false;
    if(state.contentRating==='sfw'&&b.isNsfw)return false;if(state.contentRating==='nsfw'&&!b.isNsfw)return false;
    if(createdCutoff&&Number(b.createdAt||0)<createdCutoff)return false;
    if(firstSeenCutoff&&new Date(b.firstSeenAt||0).getTime()<firstSeenCutoff)return false;
    if(lastSeenCutoff&&new Date(b.lastSeenAt||0).getTime()<lastSeenCutoff)return false;
    if(!botHasSavedField(b,state.savedField))return false;
    if(q&&!([b.id,b.name,b.title,b.creator,...(b.tags||[])].join(' ').toLowerCase().includes(q)))return false;
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

function pagerPages(current,total){
  if(total<=1)return [];
  const end=current<=1?Math.min(total,10):Math.min(total,current+6);
  const start=current<=1?1:Math.max(1,current-5);
  return Array.from({length:Math.max(0,end-start+1)},(_,i)=>start+i);
}
function pagerMarkup(current,total){
  if(total<=1)return '';
  const parts=pagerPages(current,total).map(item=>`<button type="button" class="pager-page${item===current?' active':''}" data-page="${item}"${item===current?' aria-current="page"':''}>${item}</button>`).join('');
  return `<nav class="pager" aria-label="Bot pages"><button type="button" class="pager-page pager-prev" data-page="${Math.max(1,current-1)}" ${current<=1?'disabled':''}>‹ Prev</button>${parts}<button type="button" class="pager-page pager-next" data-page="${Math.min(total,current+1)}" ${current>=total?'disabled':''}>Next ›</button></nav>`;
}
function renderPagers(current,total){
  const html=pagerMarkup(current,total);document.querySelectorAll('[data-archive-pager]').forEach(el=>{el.innerHTML=html;});
}
function renderSavedFieldPagers(current,hasNext,exactTotalPages=null){
  const total=exactTotalPages!=null?Math.max(1,exactTotalPages):(hasNext?(current<=1?10:current+6):current);
  const html=pagerMarkup(current,total);
  document.querySelectorAll('[data-archive-pager]').forEach(el=>{el.innerHTML=html;});
}
function bindPagers(onPage){
  document.querySelectorAll('[data-archive-pager]').forEach(host=>host.addEventListener('click',event=>{
    const button=event.target.closest('button[data-page]');if(!button||button.disabled)return;
    const target=parsePage(button.dataset.page);onPage(target);
  }));
}
function setupHoverAnimatedImages(root){
  root.querySelectorAll('img[data-hover-animate="1"]').forEach(img=>{
    if(img.dataset.hoverBound==='1')return;img.dataset.hoverBound='1';
    const freeze=()=>{
      if(img.dataset.hoverReady==='1'||!img.naturalWidth||!img.naturalHeight)return;
      const art=img.closest('.art');if(!art)return;
      const canvas=document.createElement('canvas');canvas.width=256;canvas.height=256;canvas.className='hover-still';
      if(img.classList.contains('nsfw-blur'))canvas.classList.add('nsfw-blur');
      try{
        const ctx=canvas.getContext('2d');const w=img.naturalWidth,h=img.naturalHeight,size=Math.min(w,h),sx=(w-size)/2,sy=(h-size)/2;
        ctx.drawImage(img,sx,sy,size,size,0,0,256,256);
      }catch{return;}
      const animatedSrc=img.currentSrc||img.src;if(!animatedSrc)return;
      img.dataset.animatedSrc=animatedSrc;img.dataset.hoverReady='1';img.classList.add('hover-animated-img');
      art.insertBefore(canvas,img);let active=false;
      const stop=()=>{active=false;canvas.style.opacity='1';img.style.opacity='0';if(img.src!==TRANSPARENT_GIF)img.src=TRANSPARENT_GIF;};
      const start=()=>{if(active)return;active=true;img.style.opacity='0';canvas.style.opacity='1';
        const reveal=()=>{if(!active)return;img.style.opacity='1';canvas.style.opacity='0';};
        img.addEventListener('load',reveal,{once:true});img.src=animatedSrc;if(img.complete&&img.naturalWidth)reveal();
      };
      art.addEventListener('mouseenter',start);art.addEventListener('mouseleave',stop);
      const cardEl=art.closest('.card');cardEl?.addEventListener('focusin',start);cardEl?.addEventListener('focusout',event=>{if(!cardEl.contains(event.relatedTarget))stop();});
      stop();
    };
    if(img.complete&&img.naturalWidth)freeze();else img.addEventListener('load',freeze,{once:true});
  });
}
function applyTranslation(article,kind,original,result){
  if(!article||!result||result.error)return;
  const source=String(result.sourceLanguage||'').toLowerCase();const translated=normalizeDisplayText(result.translated||'');
  if(!translated||source.startsWith('en')||translated.toLowerCase()===normalizeDisplayText(original).toLowerCase())return;
  const node=article.querySelector(kind==='name'?'[data-card-name]':'[data-card-title]');if(!node)return;
  node.textContent=translated;node.title=String(original||'');
  const note=article.querySelector('.translation-note');if(note){note.hidden=false;note.textContent=`Translated from ${languageDisplayName(source)}`;const prior=note.title?`${note.title}\n`:'';note.title=`${prior}Original ${kind}: ${original}`;}
}
async function translateVisibleCards(grid,bots){
  const byId=new Map((bots||[]).map(bot=>[String(bot.id||''),bot])),items=[],targets=new Map();
  grid.querySelectorAll('.card[data-bot-id]').forEach(article=>{
    const bot=byId.get(article.dataset.botId);if(!bot)return;
    for(const [kind,value] of [['name',bot.name],['title',bot.title]]){
      const original=String(value||'').trim();if(!likelyNeedsTranslation(original,bot))continue;
      const memoryKey=`${kind}\u0000${original}`;const cached=translationMemory.get(memoryKey);
      if(cached){applyTranslation(article,kind,original,cached);continue;}
      const id=`${bot.id}:${kind}`;items.push({id,text:original});targets.set(id,{article,kind,original,memoryKey});
    }
  });
  if(!items.length)return;
  try{
    const response=await fetch(TRANSLATION_ENDPOINT,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:'en',items})});
    if(!response.ok)return;const payload=await response.json();
    for(const result of payload.items||[]){const target=targets.get(String(result.id||''));if(!target)continue;translationMemory.set(target.memoryKey,result);applyTranslation(target.article,target.kind,target.original,result);}
  }catch{}
}
function enhanceRenderedCards(grid,bots){setupHoverAnimatedImages(grid);void translateVisibleCards(grid,bots);}


async function browseLive(runtime,manifest,tags,stats){
  const state={q:qs('q')||'',include:parseTags(qs('include')),exclude:readExcluded(),creator:qs('creator')||'',sort:qs('sort')||'trending',match:qs('match')||'all',definition:qs('definition')||'any',definitionSize:qs('definitionSize')||'any',lorebook:qs('lorebook')||'any',language:qs('language')||'any',contentRating:qs('rating')||'any',created:qs('created')||'any',firstSeen:qs('firstSeen')||'any',lastSeen:qs('lastSeen')||'any',savedField:qs('savedField')||'any',blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0',page:parsePage(qs('p'))};
  app.innerHTML=`<section class="hero"><div class="hero-row"><div><h1>SpicyChat Archive</h1><p>A public historical catalog of discoverable SpicyChat characters. Discovery currently has priority while the archive expands through the catalog.</p></div></div></section>
    ${growthBanner(stats)}<div class="layout">${tagSidebar(state,tags)}<section class="results">${toolbar(state,false)}
      <div class="scanline">Archive scan: <strong>${date(manifest.lastScan)}</strong> · ${fmt(stats?.totalBots||manifest.totalBots)} bots captured so far.</div>
      <div class="results-head"><h2>Public bots</h2><span id="result-count"></span></div><div data-archive-pager class="pager-wrap pager-top"></div><section class="grid" id="grid"></section><div data-archive-pager class="pager-wrap"></div>
      <footer class="footer">Active browsing queries SpicyChat's public Typesense index. Historical bot records and archived images are stored separately in the archive.</footer>
    </section></div>`;
  const $=s=>document.querySelector(s),grid=$('#grid'),count=$('#result-count');$('#sort').value=state.sort;
  let requestNo=0,timer,found=0;
  const fieldScanCache=new Map();
  const sync=()=>{state.q=$('#search').value.trim();state.creator=$('#creator').value.trim();state.sort=$('#sort').value;state.savedField=$('#saved-field')?.value||'any';state.contentRating=($('#safety-filter')?.value||state.contentRating||'any');if(state.contentRating==='all')state.contentRating='any';state.match=document.querySelector('input[name=match]:checked')?.value||'all';document.querySelectorAll('[data-meta-filter]').forEach(select=>{const key=select.dataset.metaFilter;if(key&&key!=='contentRating')state[key]=select.value||'any';});const sidebarSafety=document.querySelector('[data-meta-filter="contentRating"]');if(sidebarSafety)sidebarSafety.value=state.contentRating;state.blurNsfw=$('#blur-nsfw').checked;localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0');setQuery(state);};
  async function run(){
    sync();grid.innerHTML='<p class="loading">Loading public bots…</p>';const token=++requestNo;
    const sortBy=(runtime.sorts||{})[state.sort]||runtime.sorts?.trending||'num_messages_24h:desc';
    const exactId=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(state.q)?state.q.toLowerCase():'';
    const baseFilter=buildTsFilter(runtime,state);
    const search={collection:runtime.typesense.collection,q:exactId?'*':(state.q||'*'),query_by:runtime.typesense.queryBy,page:state.page,per_page:PAGE_SIZE,filter_by:exactId?`${baseFilter} && character_id:=${tsLiteral(exactId)}`:baseFilter,sort_by:sortBy,include_fields:'character_id,name,title,tags,creator_username,avatar_url,avatar_is_nsfw,is_nsfw,num_messages,num_messages_24h,rating_score,createdAt,updatedAt,definition_visible,definition_size_category,has_lorebooks,language'};
    const archivePredicate=state.savedField!=='any'||state.firstSeen!=='any'||state.lastSeen!=='any';
    try{
      if(!archivePredicate){
        const result=await typesenseSearch(runtime,search);if(token!==requestNo)return;found=Number(result.found||0);const totalPages=Math.max(1,Math.ceil(found/PAGE_SIZE));
        if(found&&state.page>totalPages){state.page=totalPages;setQuery(state);return run();}
        const rows=(result.hits||[]).map(h=>docToCard(h.document||{})).filter(b=>b.id);grid.innerHTML=rows.length?rows.map(b=>card(b,state.blurNsfw)).join(''):'<div class="empty">No public bots match these filters.</div>';
        const start=found?((state.page-1)*PAGE_SIZE)+1:0,end=Math.min(found,state.page*PAGE_SIZE);count.textContent=found?`${start.toLocaleString()}–${end.toLocaleString()} of ${found.toLocaleString()} matches`:'0 matches';renderPagers(state.page,totalPages);enhanceRenderedCards(grid,rows);
      }else{
        const cacheKey=JSON.stringify({q:search.q,filter:search.filter_by,sort:search.sort_by,field:state.savedField,firstSeen:state.firstSeen,lastSeen:state.lastSeen});
        let scan=fieldScanCache.get(cacheKey);
        if(!scan){scan={matched:[],nextTypesensePage:1,exhausted:false,underlyingFound:0};fieldScanCache.set(cacheKey,scan);}
        const need=state.page*PAGE_SIZE+1;
        const [richIndex,timesIndex]=await Promise.all([
          state.savedField!=='any'?loadArchiveRichFieldIndex(runtime):Promise.resolve(null),
          (state.firstSeen!=='any'||state.lastSeen!=='any')?loadArchiveTimesIndex(runtime):Promise.resolve(null)
        ]);if(token!==requestNo)return;
        let batchesThisRun=0;
        while(scan.matched.length<need&&!scan.exhausted&&batchesThisRun<5){
          const probes=Array.from({length:8},(_,offset)=>({...search,page:scan.nextTypesensePage+offset,per_page:250}));
          const results=await typesenseMultiSearch(runtime,probes);if(token!==requestNo)return;
          for(const result of results){
            if(result?.error)throw new Error(result.error);
            const hits=result?.hits||[];
            scan.underlyingFound=Number(result?.found||scan.underlyingFound||0);
            if(!hits.length){scan.exhausted=true;break;}
            for(const hit of hits){
              const doc=hit?.document||{};
              const id=String(doc.character_id||doc.id||'').toLowerCase();
              if(id&&matchesArchiveMeta(id,state,richIndex,timesIndex))scan.matched.push(doc);
            }
            scan.nextTypesensePage+=1;
            if(hits.length<250||scan.nextTypesensePage>Math.ceil(scan.underlyingFound/250)){scan.exhausted=true;break;}
          }
          batchesThisRun+=1;
        }
        const startIndex=(state.page-1)*PAGE_SIZE;
        const pageDocs=scan.matched.slice(startIndex,startIndex+PAGE_SIZE);
        const rows=pageDocs.map(docToCard).filter(b=>b.id);
        const hasNext=scan.matched.length>state.page*PAGE_SIZE||!scan.exhausted;
        const exactTotal=scan.exhausted?scan.matched.length:null;
        const exactPages=exactTotal!=null?Math.max(1,Math.ceil(exactTotal/PAGE_SIZE)):null;
        if(exactPages!=null&&state.page>exactPages){state.page=exactPages;setQuery(state);return run();}
        const labels=[];
        if(state.savedField!=='any')labels.push(state.savedField==='personality'?'Personality':state.savedField==='scenario'?'Scenario':'Example Dialogue');
        if(state.firstSeen!=='any')labels.push('first captured '+state.firstSeen);
        if(state.lastSeen!=='any')labels.push('last seen '+state.lastSeen);
        const label=labels.join(' + ')||'archive filters';
        grid.innerHTML=rows.length?rows.map(b=>card(b,state.blurNsfw)).join(''):'<div class="empty">No public bots match these archive filters.</div>';
        const start=rows.length?startIndex+1:0,end=startIndex+rows.length;
        const checked=Math.min(scan.underlyingFound,(scan.nextTypesensePage-1)*250);
        count.textContent=exactTotal!=null
          ? `${start.toLocaleString()}–${end.toLocaleString()} of ${exactTotal.toLocaleString()} with saved ${label}`
          : (scan.matched.length<need
              ? `${start.toLocaleString()}–${end.toLocaleString()} with saved ${label} · checked ${checked.toLocaleString()} matching public bots; narrow search/tags to scan deeper`
              : `${start.toLocaleString()}–${end.toLocaleString()} with saved ${label}`);
        renderSavedFieldPagers(state.page,hasNext,exactPages);
        enhanceRenderedCards(grid,rows);
      }
    }catch(e){if(token!==requestNo)return;grid.innerHTML=`<div class="error">Could not query the public bot index: ${esc(e.message||e)}</div>`;count.textContent='';renderPagers(1,1);}
  }
  const resetAndRun=()=>{state.page=1;run();};const schedule=()=>{clearTimeout(timer);state.page=1;timer=setTimeout(()=>run(),240);};
  attachSidebar(state,resetAndRun);$('#search').addEventListener('input',schedule);$('#creator').addEventListener('input',schedule);$('#sort').addEventListener('change',resetAndRun);$('#saved-field').addEventListener('change',resetAndRun);$('#safety-filter').addEventListener('change',resetAndRun);$('#blur-nsfw').addEventListener('change',resetAndRun);
  $('#mobile-filter-toggle').addEventListener('click',()=>$('#filters').classList.toggle('open'));
  bindPagers(target=>{state.page=target;setQuery(state,true);void run();document.querySelector('.results')?.scrollIntoView({behavior:'smooth',block:'start'});});
  window.addEventListener('popstate',()=>{state.page=parsePage(qs('p'));void run();});
  await run();
}


async function browseDeleted(runtime,manifest,tags,stats){
  if(runtime.r2ReadAllowed===false){app.innerHTML='<section class="hero"><h1>Deleted bots</h1></section><div class="error">Archived details are temporarily paused by the R2 quota safety guard.</div>';return;}
  const state={q:qs('q')||'',include:parseTags(qs('include')),exclude:readExcluded(),creator:qs('creator')||'',sort:qs('sort')||'deleted-newest',match:qs('match')||'all',definition:qs('definition')||'any',definitionSize:qs('definitionSize')||'any',lorebook:qs('lorebook')||'any',language:qs('language')||'any',contentRating:qs('rating')||'any',created:qs('created')||'any',firstSeen:qs('firstSeen')||'any',lastSeen:qs('lastSeen')||'any',savedField:qs('savedField')||'any',blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0',page:parsePage(qs('p'))};
  let bots=[];if(runtime.deletedIndexUrl){try{const payload=await fetchJson(runtime.deletedIndexUrl);if(Array.isArray(payload?.bots))bots=payload.bots;else if(payload&&typeof payload==='object')bots=Object.values(payload).filter(row=>row&&typeof row==='object');}catch{}}
  app.innerHTML=`<section class="hero"><h1>Deleted bots</h1><p>Characters confirmed unavailable by repeated public character API 404s. Last-known public data remains preserved.</p></section>
    ${growthBanner(stats)}<div class="layout">${tagSidebar(state,tags)}<section class="results">${toolbar(state,true)}
      <div class="scanline">Archive scan: <strong>${date(manifest.lastScan)}</strong></div>
      <div class="results-head"><h2>Deleted / archived</h2><span id="result-count"></span></div><div data-archive-pager class="pager-wrap pager-top"></div><section class="grid" id="grid"></section><div data-archive-pager class="pager-wrap"></div>
      <footer class="footer">A bot is only moved here after repeated explicit public character API 404s. Disappearing from a listing alone is not deletion evidence.</footer>
    </section></div>`;
  const $=s=>document.querySelector(s),grid=$('#grid'),count=$('#result-count');$('#sort').value=state.sort;
  let deletedRenderToken=0;
  async function ensureDeletedSavedFields(){
    if(state.savedField==='any')return;
    const missing=bots.filter(bot=>!bot.savedFields).map(bot=>String(bot.id||'').toLowerCase()).filter(Boolean);
    if(!missing.length)return;
    const presence=await archiveFieldPresence(runtime,missing);
    for(const bot of bots){
      const id=String(bot.id||'').toLowerCase();
      if(!bot.savedFields)bot.savedFields=presence.get(id)||{personality:false,scenario:false,dialogue:false};
    }
  }
  const render=async()=>{
    const token=++deletedRenderToken;
    state.q=$('#search').value.trim();state.creator=$('#creator').value.trim();state.sort=$('#sort').value;state.savedField=$('#saved-field')?.value||'any';state.contentRating=($('#safety-filter')?.value||state.contentRating||'any');if(state.contentRating==='all')state.contentRating='any';state.match=document.querySelector('input[name=match]:checked')?.value||'all';document.querySelectorAll('[data-meta-filter]').forEach(select=>{const key=select.dataset.metaFilter;if(key&&key!=='contentRating')state[key]=select.value||'any';});const sidebarSafety=document.querySelector('[data-meta-filter="contentRating"]');if(sidebarSafety)sidebarSafety.value=state.contentRating;state.blurNsfw=$('#blur-nsfw').checked;localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0');setQuery(state);
    if(state.savedField!=='any'){grid.innerHTML='<p class="loading">Checking archived fields…</p>';await ensureDeletedSavedFields();if(token!==deletedRenderToken)return;}
    const rows=localFilterAndSort(bots,state),totalConfirmed=Math.max(bots.length,Number(stats?.deletedBots)||0),totalPages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));if(state.page>totalPages)state.page=totalPages;
    const startIndex=(state.page-1)*PAGE_SIZE,visible=rows.slice(startIndex,startIndex+PAGE_SIZE),start=rows.length?startIndex+1:0,end=Math.min(rows.length,startIndex+PAGE_SIZE);
    count.textContent=rows.length===totalConfirmed?`${start.toLocaleString()}–${end.toLocaleString()} of ${totalConfirmed.toLocaleString()} confirmed`:`${start.toLocaleString()}–${end.toLocaleString()} of ${rows.length.toLocaleString()} matching · ${totalConfirmed.toLocaleString()} confirmed total`;
    grid.innerHTML=visible.length?visible.map(b=>card(b,state.blurNsfw)).join(''):'<div class="empty">No deleted bots match these filters.</div>';renderPagers(state.page,totalPages);enhanceRenderedCards(grid,visible);
  };
  const resetAndRender=()=>{state.page=1;void render();};attachSidebar(state,resetAndRender);$('#search').addEventListener('input',resetAndRender);$('#creator').addEventListener('input',resetAndRender);$('#sort').addEventListener('change',resetAndRender);$('#saved-field').addEventListener('change',resetAndRender);$('#safety-filter').addEventListener('change',resetAndRender);$('#blur-nsfw').addEventListener('change',resetAndRender);$('#mobile-filter-toggle').addEventListener('click',()=>$('#filters').classList.toggle('open'));
  bindPagers(target=>{state.page=target;setQuery(state,true);void render();document.querySelector('.results')?.scrollIntoView({behavior:'smooth',block:'start'});});window.addEventListener('popstate',()=>{state.page=parsePage(qs('p'));void render();});void render();
}

async function browse(){
  const [runtime,manifest,tags,stats]=await Promise.all([loadRuntime(),loadManifest(),loadTags(),loadStats()]);
  if(runtime.storageMode!=='r2')throw new Error('This website update expects the completed R2 migration.');
  return page==='deleted'?browseDeleted(runtime,manifest,tags,stats):browseLive(runtime,manifest,tags,stats);
}

function meaningfulFieldValue(value){
  if(value==null)return false;
  if(typeof value==='string')return value.trim()!=='';
  if(Array.isArray(value))return value.some(meaningfulFieldValue);
  if(typeof value==='object')return Object.values(value).some(meaningfulFieldValue);
  return true;
}
function bestField(record,key){
  const lk=record.lastKnown||{};
  if(meaningfulFieldValue(lk[key]))return lk[key];
  const current=record.current||{};
  for(const source of ['character-api','typesense','typesense:trending','typesense:popular','typesense:top-rated','typesense:explore']){
    const v=current[source]?.[key];if(meaningfulFieldValue(v))return v;
  }
  for(const value of Object.values(current)){if(value&&typeof value==='object'&&meaningfulFieldValue(value[key]))return value[key];}
  return null;
}
function bestFieldAny(record,keys){
  for(const key of keys){const value=bestField(record,key);if(meaningfulFieldValue(value))return value;}
  return null;
}
function archiveFieldLabel(path){
  const leaf=String(path||'').split('.').pop();
  if(leaf==='description'||leaf==='title')return 'Title';
  if(['persona','personality','definition','character_definition','characterDefinition'].includes(leaf))return 'Personality';
  if(leaf==='scenario')return 'Scenario';
  if(['dialogue','example_dialogue','example_dialogues'].includes(leaf))return 'Example Dialogue';
  if(leaf==='greeting'||leaf==='greetings')return 'Greeting';
  if(leaf==='lorebooks')return 'Lorebooks';
  if(leaf==='system_prompt')return 'System Prompt';
  if(leaf==='post_history_instructions')return 'Post History Instructions';
  return String(path||'').replaceAll('_',' ');
}
const HISTORY_VOLATILE=new Set(['updatedAt','updated_at','lastUpdatedAt','last_updated_at','num_messages','num_messages_24h','rating_score','rating_count','token_count','rank','ranking']);
function meaningfulHistoryRows(record){
  return (record.fieldHistory||[]).filter(row=>{
    const leaf=String(row?.path||'').split('.').pop();
    return row?.path&&!HISTORY_VOLATILE.has(leaf);
  });
}
function flattenArchiveObject(value,prefix='',out={}){
  if(value&&typeof value==='object'&&!Array.isArray(value)){
    for(const [key,item] of Object.entries(value)){
      const path=prefix?prefix+'.'+key:key;
      if(item&&typeof item==='object'&&!Array.isArray(item))flattenArchiveObject(item,path,out);
      else out[path]=item;
    }
  }else if(prefix)out[prefix]=value;
  return out;
}
function versionSnapshot(record,target){
  const flat=flattenArchiveObject(structuredClone(record.lastKnown||{}));
  if(target==='current')return flat;
  const targetTime=new Date(target).getTime(),firstTime=new Date(record.firstSeenAt||0).getTime();
  if(Number.isFinite(firstTime)&&firstTime>0&&Number.isFinite(targetTime)&&targetTime<firstTime)return {};
  const rows=meaningfulHistoryRows(record).slice().sort((a,b)=>new Date(b.at)-new Date(a.at));
  for(const row of rows){
    const at=new Date(row.at).getTime();
    if(!Number.isFinite(at)||at<=targetTime)continue;
    if(Object.prototype.hasOwnProperty.call(row,'from'))flat[row.path]=structuredClone(row.from);
    else delete flat[row.path];
  }
  return flat;
}
function compareValueText(value){
  if(value==null)return '—';
  if(typeof value==='string')return value;
  try{return JSON.stringify(value,null,2);}catch{return String(value);}
}
function versionCompareRows(record,older,newer){
  const a=versionSnapshot(record,older),b=versionSnapshot(record,newer),paths=[...new Set([...Object.keys(a),...Object.keys(b)])];
  return paths.filter(path=>{
    const leaf=path.split('.').pop();if(HISTORY_VOLATILE.has(leaf))return false;
    try{return JSON.stringify(a[path])!==JSON.stringify(b[path]);}catch{return String(a[path])!==String(b[path]);}
  }).sort((x,y)=>archiveFieldLabel(x).localeCompare(archiveFieldLabel(y))).map(path=>({path,from:a[path],to:b[path]}));
}
function sourceDisplayName(source){
  const s=String(source||'');
  if(s==='character-api')return 'Character API';
  if(s==='qol-bot-status-import')return 'QoL Bot Status';
  if(s.startsWith('typesense'))return 'Typesense';
  if(s==='public-submission')return 'Public contribution';
  return s.replaceAll('-',' ').replaceAll('_',' ')||'Unknown source';
}
function renderSourcePills(record){
  const keys=[...new Set([...Object.keys(record.current||{}),...Object.keys(record.sources||{})])];
  const seen=new Set(),rows=[];
  for(const key of keys){const label=sourceDisplayName(key);if(seen.has(label))continue;seen.add(label);rows.push('<span class="pill source-pill" title="'+esc(key)+'">'+esc(label)+'</span>');}
  return rows.join('');
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
  const lk=record.lastKnown||{},status=record.status?.current||'unknown',requestedAt=qs('at')||'';
  const requestedSnapshot=requestedAt?versionSnapshot(record,requestedAt):null;
  const snapshotValue=keys=>{if(!requestedSnapshot)return bestFieldAny(record,keys);for(const key of keys){if(meaningfulFieldValue(requestedSnapshot[key]))return requestedSnapshot[key];}return null;};
  const cdn=resolveAvatar(lk.avatar_url||lk.avatar||lk.image),archived=archivedAvatar(record,runtime);
  const primary=status==='deleted'?(archived||cdn):(cdn||archived),fallback=status==='deleted'?cdn:archived;
  const currentTitle=normalizeDisplayText(requestedAt?(snapshotValue(['title','description'])||''):(bestField(record,'title')||bestField(record,'description')||''));
  const fieldSpecs=[
    {label:'Title',keys:['description'],skip:value=>normalizeDisplayText(value)===currentTitle},
    {label:'Greeting',keys:['greeting','greetings']},
    {label:'Personality',keys:['persona','personality','definition','character_definition','characterDefinition']},
    {label:'Scenario',keys:['scenario']},
    {label:'Example Dialogue',keys:['dialogue','example_dialogue','example_dialogues']},
    {label:'System Prompt',keys:['system_prompt']},
    {label:'Post History Instructions',keys:['post_history_instructions']},
    {label:'Lorebooks',keys:['lorebooks']}
  ];
  const fieldHtml=fieldSpecs.map(spec=>{const v=requestedAt?snapshotValue(spec.keys):bestFieldAny(record,spec.keys);if(!meaningfulFieldValue(v)||spec.skip?.(v))return'';return `<div class="field-block"><h3>${esc(spec.label)} · last known</h3><div class="pre">${esc(Array.isArray(v)?v.map(item=>typeof item==='object'?JSON.stringify(item,null,2):String(item)).join('\n\n'):typeof v==='object'?JSON.stringify(v,null,2):v)}</div></div>`}).join('');
  const botName=normalizeDisplayText(bestField(record,'name')||bestField(record,'title')||'Unknown bot'),creator=bestField(record,'creator_username')||bestField(record,'creator')||'';
  document.title=`${botName} · SpicyChat Archive`;
  const tags=(lk.tags||[]).map(t=>`<span class="pill">${esc(t)}</span>`).join('');
  const allHistoryRows=meaningfulHistoryRows(record).slice().reverse();
  const historyRows=allHistoryRows.slice(0,150);
  const history=historyRows.map(h=>`<details class="history-item history-detail"><summary><b>${esc(archiveFieldLabel(h.path))}</b> · ${esc(h.kind||'value')}<br><span class="detail-sub">${date(h.at)} · ${esc(sourceDisplayName(h.source||''))}</span></summary><div class="history-diff"><div><span>Before</span><div class="pre">${esc(compareValueText(h.from))}</div></div><div><span>After</span><div class="pre">${esc(compareValueText(h.to))}</div></div></div></details>`).join('');
  const points=['current',...new Set([requestedAt,...historyRows.map(x=>x.at)].filter(Boolean))];
  const options=points.map((point,index)=>`<option value="${esc(point)}">${point==='current'?'Current last-known':date(point)}${index===1?' · previous':''}</option>`).join('');
  const sources=renderSourcePills(record);
  const sourceCount=[...new Set([...Object.keys(record.current||{}),...Object.keys(record.sources||{})])].length;
  const availabilityCount=Array.isArray(record.availabilityHistory)?record.availabilityHistory.length:0;
  const richCaptured=fieldSpecs.filter(spec=>meaningfulFieldValue(requestedAt?snapshotValue(spec.keys):bestFieldAny(record,spec.keys))).map(spec=>spec.label);
  const imageHistory=allHistoryRows.filter(row=>['avatar','avatar_url','image'].includes(String(row.path||'').split('.').pop()));
  const beforeFirst=requestedAt&&new Date(requestedAt).getTime()<new Date(record.firstSeenAt||0).getTime();
  const snapshotLabel=requestedAt?'<div class="history-banner">'+(beforeFirst?'This bot had <b>not yet been observed by the archive</b> on '+esc(date(requestedAt))+'.':'Viewing reconstructed archived fields as of <b>'+esc(date(requestedAt))+'</b>. Fields first captured later are rolled back when the archive recorded that transition.')+'</div>':'';
  const timelineEvents=[
    record.firstSeenAt?{at:record.firstSeenAt,label:'First captured',detail:'Archive first observed this bot'}:null,
    ...(record.availabilityHistory||[]).filter(Boolean).map(row=>({at:row.from||row.at,label:String(row.status||'Availability change'),detail:sourceDisplayName(row.source||'')})),
    ...allHistoryRows.map(row=>({at:row.at,label:archiveFieldLabel(row.path)+' changed',detail:sourceDisplayName(row.source||'')})),
    record.lastSeenAt?{at:record.lastSeenAt,label:'Last observed',detail:'Latest archived observation'}:null
  ].filter(x=>x&&x.at).sort((a,b)=>new Date(a.at)-new Date(b.at));
  const timelineHtml=timelineEvents.slice(-160).map(x=>'<div class="history-item"><b>'+esc(x.label)+'</b><br><span class="detail-sub">'+esc(date(x.at))+(x.detail?' · '+esc(x.detail):'')+'</span></div>').join('');
  app.innerHTML=`<article class="detail"><div class="detail-head"><div class="detail-art">${imgHtml(primary,fallback,{alt:bestField(record,'name')||''})}</div>
    <div><div class="detail-sub">${esc(record.id)}</div><h1>${esc(botName)}</h1>
    <div class="detail-sub">${creator?`<a class="creator-profile-link" href="${esc(creatorHref(creator))}">@${esc(creator)}</a>`:'Unknown creator'}</div>
    <div class="pill-row"><span class="pill">${esc(status)}</span>${archived?'<span class="pill">image archived</span>':''}<span class="pill">first seen ${date(record.firstSeenAt)}</span><span class="pill">last seen ${date(record.lastSeenAt)}</span></div>
    ${sources?`<div class="pill-row source-row"><span class="detail-sub">Observed through</span>${sources}</div>`:''}
    <div class="pill-row">${tags}</div><p>${esc(normalizeDisplayText(bestField(record,'title')||''))}</p>
    <div class="detail-actions"><a class="primary-button" href="${esc(spicyHref(record.id))}" target="_blank" rel="noopener">Open in SpicyChat ↗</a><a class="secondary-button" href="../recovery/?id=${encodeURIComponent(record.id)}${requestedAt?'&at='+encodeURIComponent(requestedAt):''}">Recovery Lab</a><a class="secondary-button" href="${creator?esc(creatorHref(creator)):'../'}">${creator?'Creator archive':'Back to archive'}</a><a class="secondary-button" href="../changes/?q=${encodeURIComponent(record.id)}">Changes</a><button class="secondary-button" type="button" id="export-bot-history">Export history JSON</button></div></div></div>
    ${snapshotLabel}
    <section class="section"><h2>Archive completeness</h2><p class="detail-sub">Concrete coverage facts only; the archive does not invent a completeness percentage for time it never observed.</p><div class="stats-grid"><div class="stat"><b>${esc(date(record.firstSeenAt))}</b><span>First captured</span></div><div class="stat"><b>${esc(date(record.lastSeenAt))}</b><span>Last observed</span></div><div class="stat"><b>${fmt(allHistoryRows.length)}</b><span>Meaningful field changes recorded</span></div><div class="stat"><b>${fmt(availabilityCount)}</b><span>Availability observations</span></div><div class="stat"><b>${fmt(sourceCount)}</b><span>Archive sources represented</span></div><div class="stat"><b>${fmt(richCaptured.length)}</b><span>Rich field groups recovered</span></div></div><p class="detail-sub">${richCaptured.length?'Recovered fields: '+esc(richCaptured.join(', '))+'.':'No rich definition fields recovered yet.'}</p></section>
    <section class="section"><h2>Archive timeline</h2><p class="detail-sub">First/last observations, availability changes and meaningful creator-content edits in chronological order. The newest 160 events are shown.</p>${timelineHtml||'<p class="detail-sub">No timeline events recorded yet.</p>'}</section>
    <section class="section"><h2>Last-known archived fields</h2><p class="detail-sub">A field stays here after SpicyChat stops exposing it. That does not mean it is still currently public.</p>${fieldHtml||'<p class="detail-sub">No rich definition fields have been recovered yet.</p>'}</section>
    <section class="section"><h2>Version history & compare</h2><p class="detail-sub">Versions are reconstructed from the archive's recorded before/after field changes. Untouched fields use the last-known archive value.</p>
      ${points.length>1?`<div class="version-controls"><label>Older<select id="version-older">${options}</select></label><label>Newer<select id="version-newer">${options}</select></label><a class="secondary-button" id="snapshot-link" href="#">Permalink newer point</a></div><div id="version-compare"></div>`:'<p class="detail-sub">No meaningful creator-content edits have been recorded yet.</p>'}
    </section>
    <section class="section"><h2>Image history</h2>${imageHistory.length?imageHistory.slice(0,50).map(h=>`<details class="history-item"><summary><b>Image changed</b> · ${date(h.at)}</summary><div class="history-diff"><div><span>Before</span><div class="pre">${esc(compareValueText(h.from))}</div></div><div><span>After</span><div class="pre">${esc(compareValueText(h.to))}</div></div></div></details>`).join(''):'<p class="detail-sub">No archived image URL changes have been recorded yet.</p>'}</section>
    <section class="section"><h2>Latest metrics</h2><div class="pre">${esc(JSON.stringify(record.metrics?.latest||{},null,2))}</div></section>
    <section class="section"><h2>Availability history</h2><div class="pre">${esc(JSON.stringify(record.availabilityHistory||[],null,2))}</div></section>
    <section class="section"><h2>Field history</h2>${history||'<p class="detail-sub">No meaningful field changes recorded yet.</p>'}</section>
    <section class="section"><h2>Raw latest observations</h2><div class="pre">${esc(JSON.stringify(record.current||{},null,2))}</div></section>
  </article>`;
  const older=document.querySelector('#version-older'),newer=document.querySelector('#version-newer'),compare=document.querySelector('#version-compare');
  if(older&&newer&&compare){
    older.value=points.find(x=>x!=='current'&&x!==requestedAt)||points[1]||'current';newer.value=requestedAt||'current';
    const snapshotLink=document.querySelector('#snapshot-link');
    const renderCompare=()=>{const rows=versionCompareRows(record,older.value,newer.value);if(snapshotLink){const u=new URL(location.href);if(newer.value==='current')u.searchParams.delete('at');else u.searchParams.set('at',newer.value);snapshotLink.href=u.href;}compare.innerHTML=rows.length?`<div class="version-table">${rows.map(row=>`<article class="version-row"><h3>${esc(archiveFieldLabel(row.path))}</h3><div class="version-values"><div><span>Older</span><div class="pre">${esc(compareValueText(row.from))}</div></div><div><span>Newer</span><div class="pre">${esc(compareValueText(row.to))}</div></div></div></article>`).join('')}</div>`:'<div class="empty compact-empty">Those two points have no tracked content differences.</div>';};
    older.addEventListener('change',renderCompare);newer.addEventListener('change',renderCompare);renderCompare();
  }
  document.querySelector('#export-bot-history')?.addEventListener('click',()=>{
    const payload={schemaVersion:1,exportedAt:new Date().toISOString(),id:record.id,firstSeenAt:record.firstSeenAt,lastSeenAt:record.lastSeenAt,status:record.status,sources:record.sources||{},availabilityHistory:record.availabilityHistory||[],fieldHistory:record.fieldHistory||[],lastKnown:record.lastKnown||{},avatarArchive:record.avatarArchive||{}};
    const blob=new Blob([JSON.stringify(payload,null,2)+'\n'],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='spicychat-archive-'+record.id+'-history.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  });
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
  const [stats,manifest,tags,runtime,historyData]=await Promise.all([loadStats(),loadManifest(),loadTags(),loadRuntime(),loadArchiveHistory()]);
  if(!stats){app.innerHTML='<section class="hero"><h1>Archive stats</h1><p>The public stats file will be created by the next successful archive run after this update.</p></section>';return;}
  const runs=(stats.runs||[]).filter(r=>r.kind==='archive-run'),recent=runs.filter(r=>Number(r.addedSincePrevious??r.exploration?.new??0)>0||Number(r.exploration?.new||0)>0).slice(-10).reverse(),latest=runs.at(-1)||{};
  const table=recent.map(r=>`<tr><td>${date(r.at)}</td><td>+${fmt(r.addedSincePrevious??0)}</td><td>${fmt(r.exploration?.new)}</td><td>${fmt(r.exploration?.pagesCompleted)}/${fmt(r.exploration?.pageBudget)}</td><td>${r.runDurationSeconds?`${Math.round(r.runDurationSeconds/60)}m`:'—'}</td></tr>`).join('');
  const guard=runtime.r2QuotaGuard||{},usage=guard.usage||{},adaptive=stats.adaptiveDiscovery||{},milestones=Array.isArray(historyData?.milestones)?historyData.milestones:[];
  const historyRows=[...milestones.map(row=>({...row,_historyKind:'milestone'})),...(stats.runs||[]).map(row=>({...row,_historyKind:row.kind||'archive-run'}))].filter(row=>row?.at).sort((a,b)=>new Date(b.at)-new Date(a.at));
  app.innerHTML=`<section class="hero"><h1>Archive stats</h1><p>How the archive is growing, what recent batches found, and the full project history from the early small archive through the current crawler.</p><div class="detail-actions"><a class="secondary-button" href="../health/">Archive health</a><a class="secondary-button" href="../changes/">Recently changed</a><a class="secondary-button" href="../restored/">Restored bots</a></div></section>
    <section class="stats-grid"><div class="stat"><b>${fmt(stats.totalBots)}</b><span>Bots captured by the archive</span></div><div class="stat"><b>~${fmt(Math.round(stats.growth?.averagePerDay||0))}/day</b><span>Observed archive growth pace</span></div><div class="stat"><b>${fmt(stats.publicIndexBots)}</b><span>Public bots reported by SpicyChat's index</span></div><div class="stat"><b>${ageText(stats.startedAt)}</b><span>Archive site age</span></div><div class="stat"><b>+${fmt(stats.growth?.added24h)}</b><span>Captured in the last ~24h window</span></div><div class="stat"><b>+${fmt(stats.growth?.added7d)}</b><span>Captured in the last ~7d window</span></div><div class="stat"><b>${fmt(stats.deletedBots)}</b><span>Confirmed deleted / archived</span></div><div class="stat"><b>${fmt(tags.length)}</b><span>Current supported SpicyChat tags</span></div></section>
    <div class="stats-layout"><section class="chart-card"><h2>Archive growth</h2>${growthChart(stats.runs)}</section><section class="table-card"><h2>Crawler right now</h2><div class="recent-list"><div class="recent-run"><div><b>${fmt(adaptive.nextPageBudget||latest.exploration?.nextPageBudget)} pages</b><br><span>next normal scan</span></div><div><b>${fmt(adaptive.maxPages)}</b><br><span>largest manual test allowed</span></div></div><div class="recent-run"><div><b>${Math.round((adaptive.timeLimitSeconds||3600)/60)} min</b><br><span>hard stop for discovery</span></div><div><b>+${fmt(adaptive.growthPerSuccess)}</b><br><span>automatic page increase</span></div></div><div class="recent-run"><div><b>${fmt(latest.exploration?.new)}</b><br><span>new bots in the last batch</span></div><div><b>${fmt(latest.exploration?.pagesCompleted)}</b><br><span>pages finished</span></div></div></div></section></div>
    <div class="stats-layout"><section class="table-card"><h2>Recent batches</h2><p class="filter-note">The latest batches that actually added bots. Zero-add maintenance runs are kept in History below instead of duplicating this list.</p><div style="overflow:auto"><table class="stats-table"><thead><tr><th>Run</th><th>Saved</th><th>New bots</th><th>Deep-search pages</th><th>Took</th></tr></thead><tbody>${table||'<tr><td colspan="5">No batches with new bots yet.</td></tr>'}</tbody></table></div></section><section class="table-card"><h2>Storage + crawler</h2><div class="recent-list"><div class="recent-run"><div><b>${fmtBytes(latest.storage?.usedBytes||manifest.storage?.usedBytes)}</b><br><span>archive storage used</span></div><div><b>${fmt(latest.storage?.objects||usage.objectCount)}</b><br><span>files / objects in R2</span></div></div><div class="recent-run"><div><b>${fmt(usage.classA)}</b><br><span>R2 writes this month</span></div><div><b>${fmt(usage.classB)}</b><br><span>R2 reads this month</span></div></div><div class="recent-run"><div><b>${fmt(latest.enrichment?.enriched)}</b><br><span>full bot details refreshed last batch</span></div><div><b>${fmt(latest.images?.saved)}</b><br><span>images saved last batch</span></div></div></div></section></div>
    <section class="table-card history-card"><div class="history-head"><div><h2>History</h2><p class="filter-note">Everything from the early project milestones through every recorded crawler/import run. Unlike Recent batches, +0 maintenance runs stay here too.</p></div><span class="manager-count" id="archive-history-count"></span></div><div class="history-list" id="archive-history-list"></div><div class="history-actions"><button class="load-more" id="archive-history-more" type="button">Load more</button></div></section>
    <footer class="footer">“Bots captured by the archive” is not the same thing as SpicyChat's total public index count. The archive grows as discovery continues through the catalog.</footer>`;
  let historyVisible=15;const list=document.querySelector('#archive-history-list'),more=document.querySelector('#archive-history-more'),count=document.querySelector('#archive-history-count');
  const markup=row=>{if(row._historyKind==='milestone')return `<article class="history-run"><div><span class="history-kind">Project milestone · ${esc(date(row.at))}</span><b>${esc(row.title||'Archive milestone')}</b><p>${esc(row.summary||'')}</p></div>${row.totalBots!=null?`<div class="history-numbers"><b>${fmt(row.totalBots)}</b><span>${esc(row.totalLabel||'bots')}</span></div>`:''}</article>`;if(row._historyKind==='migration-baseline')return `<article class="history-run"><div><span class="history-kind">Migration baseline · ${esc(date(row.at))}</span><b>Global archive baseline</b><p>${fmt(row.totalBots)} bot records were already preserved when the current crawler history began.</p></div><div class="history-numbers"><b>${fmt(row.totalBots)}</b><span>saved records</span></div></article>`;const added=Number(row.addedSincePrevious??row.exploration?.new??0)||0;return `<article class="history-run"><div><span class="history-kind">Archive batch · ${esc(date(row.at))}</span><b>${fmt(row.exploration?.pagesCompleted)} deep-search pages · ${fmt(row.exploration?.hits)} bot listings checked</b><p>${fmt(row.enrichment?.enriched)} full bot details refreshed · ${fmt(row.images?.saved)} images saved · ${fmt(row.deletedConfirmed)} bots confirmed gone${row.runDurationSeconds?` · took ${Math.round(row.runDurationSeconds/60)}m`:''}</p></div><div class="history-numbers"><b>+${fmt(added)}</b><span>${fmt(row.exploration?.new)} newly found</span></div></article>`;};
  const render=()=>{const shown=historyRows.slice(0,historyVisible);if(list)list.innerHTML=shown.map(markup).join('')||'<div class="filter-note">No history rows yet.</div>';const remaining=Math.max(0,historyRows.length-shown.length);if(count)count.textContent=`${shown.length.toLocaleString()} / ${historyRows.length.toLocaleString()} entries`;if(more){more.hidden=!remaining;more.textContent=remaining?`Load ${Math.min(15,remaining)} more (${remaining} left)`:'Everything loaded';}};more?.addEventListener('click',()=>{historyVisible=Math.min(historyRows.length,historyVisible+15);render();});render();
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
