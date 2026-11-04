import {parseTagList, matchesTagFilters, groupTags} from './rulebook-filters.mjs';
// Public Rulebook metadata only. Private rule bodies and hidden values are
// intentionally not in the Archive's Rulebook data source.
const app = document.getElementById('app');
const isDetail = document.body.dataset.page === 'rulebook';
const removedView = document.body.dataset.page === 'deleted-rulebooks';
const params = new URLSearchParams(location.search);
let coverRefs=Object.create(null);
let tagCatalog=[];
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
})[ch]);
const fmt = v => v == null || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString();
const date = value => {
  if (!value) return 'Not available';
  const n = typeof value === 'string' && /^\d{10,13}$/.test(value) ? Number(value) : value;
  const d = new Date(typeof n === 'number' && n < 100000000000 ? n * 1000 : n);
  return Number.isFinite(d.getTime()) ? d.toLocaleString() : 'Not available';
};
async function fetchJson(url) {
  const res = await fetch(url, {cache: 'no-store'});
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}
async function sourceBase() {
  const runtime = await fetchJson('../data/runtime.json');
  return String(runtime.publicDataBaseUrl || '').replace(/\/$/, '');
}
function detailHref(id) {return '../rulebook/?id=' + encodeURIComponent(id);}
function tagsHtml(values) {
  return (Array.isArray(values)?values:[]).slice(0,14).map(v=>'<span>'+esc(v)+'</span>').join('');
}
function imageUrl(raw) {
  const value = String(raw || '').trim();
  if (!value || value.length > 2048) return '';
  if (/^https:\/\//i.test(value)) {
    try {
      const host = new URL(value).hostname.toLowerCase();
      if (host === 'cdn.nd-api.com' || host === 'cms.cdn.nd-api.com' || host === 'spicychat.ai' || host.endsWith('.spicychat.ai')) return value;
    } catch {}
    return '';
  }
  if (/^(?:\/\/|[a-z]+:)/i.test(value) || value.includes('..') || !/^[\w/.%?&=+\-]+$/.test(value)) return '';
  return 'https://cdn.nd-api.com/' + value.replace(/^\/+/, '');
}
function savedCover(item) {
  for(const key of ['cover','cover_image_url','coverImageUrl','cover_url','coverUrl','cover_image','coverImage','cover_path','coverPath','avatar_url','image_url','thumbnail_url','avatar','image']) {
    const src = imageUrl(item?.[key]);
    if(src) return src;
  }
  return '';
}
function coverMarkup(url, name, large=false) {
  const src=imageUrl(url);
  return '<div class="rulebook-cover '+(large?'rulebook-detail-cover':'rulebook-card-cover')+'">'+
    '<div class="rulebook-cover-empty">RB</div>'+
    (src?'<img src="'+esc(src)+'" alt="Cover for '+esc(name)+'" loading="lazy" decoding="async" data-rulebook-cover>':'')+
    '</div>';
}
function bindBrokenCovers(root=document) {
  root.querySelectorAll('img[data-rulebook-cover]').forEach(img=>img.addEventListener('error',()=>img.remove(),{once:true}));
}
function card(x) {
  const gone=x.status==='not-public',name=x.name||x.id;
  const cover=savedCover(x)||imageUrl(coverRefs[x.id]);
  return '<article class="lorebook-card rulebook-card" data-rulebook-id="'+esc(x.id)+'">'+
    '<a class="lorebook-cover" href="'+esc(detailHref(x.id))+'">'+coverMarkup(cover,name)+
      (x.isNsfw?'<span class="lorebook-nsfw">NSFW</span>':'')+'</a>'+
    '<div class="lorebook-card-body"><div class="lorebook-card-top">'+
    '<a class="lorebook-name" href="'+esc(detailHref(x.id))+'">'+esc(name)+'</a>'+
    (gone?'<span class="lorebook-status is-archived">No longer public</span>':'')+'</div>'+
    (x.creator?'<a class="creator-link" href="../creator/?name='+encodeURIComponent(x.creator)+'">@'+esc(x.creator)+'</a>':'')+
    '<p class="lorebook-description">'+esc(x.description||'No description saved.')+'</p>'+
    '<div class="lorebook-tags">'+tagsHtml(x.tags)+'</div>'+
    '<div class="lorebook-card-foot"><span>'+fmt(x.numRules)+' '+(Number(x.numRules)===1?'rule':'rules')+'</span>'+
    '<span>Last seen '+esc(date(x.lastSeenAt).split(',')[0])+'</span></div></div></article>';
}
function setParams(values) {
  const url=new URL(location.href);
  for(const [key,val] of Object.entries(values)){
    if(val==null||val===''||val===1||val==='newest')url.searchParams.delete(key);
    else url.searchParams.set(key,String(val));
  }
  history.replaceState(null,'',url);
}
function renderBrowse(payload) {
  const all=Array.isArray(payload.rulebooks)?payload.rulebooks:[];
  const tagGroups=groupTags(tagCatalog, all.flatMap(x=>Array.isArray(x.tags)?x.tags:[]));
  const only=removedView?'not-public':'public';
  const title=removedView?'Deleted / Unlisted Rulebooks':'Public Rulebooks';
  const summary=removedView
    ?'Rulebooks that were saved while public and later disappeared from SpicyChat’s public listings. They may be deleted, private or unlisted; disappearance alone cannot tell us which.'
    :'Rulebooks still appearing in SpicyChat’s public listings, with saved details, covers where available and change history.';
  const statuses=Object.create(null);
  for(const x of all)for(const t of Array.isArray(x.tags)?x.tags:[]){
    const key=String(t).toLowerCase();statuses[key]=(statuses[key]||0)+1;
  }
  const tagList=tagGroups.map(group=>
    '<section class="rulebook-tag-group" data-rb-tag-group><h3>'+esc(group.name)+'</h3>'+
    group.tags.map(t=>
      '<div class="rulebook-tag-row" data-rb-tag-row data-rb-tag-name="'+esc(t.toLowerCase())+'">'+
        '<span class="rulebook-tag-name" title="'+esc(t)+'">'+esc(t)+'</span>'+
        '<small>'+fmt(statuses[t.toLowerCase()]||0)+'</small>'+
        '<button type="button" class="tag-toggle include" data-rb-tag-action="include" data-rb-tag="'+esc(t)+'" aria-label="Include '+esc(t)+'" aria-pressed="false" title="Include '+esc(t)+'">+</button>'+
        '<button type="button" class="tag-toggle exclude" data-rb-tag-action="exclude" data-rb-tag="'+esc(t)+'" aria-label="Exclude '+esc(t)+'" aria-pressed="false" title="Exclude '+esc(t)+'">−</button>'+
      '</div>'
    ).join('')+'</section>').join('');
  app.innerHTML=
    '<section class="hero lorebook-hero"><div><h1>'+title+'</h1>'+
    '<p>'+summary+'</p>'+
    '<p class="filter-note">Only public listing information is archived. Private rules and hidden values are not included.</p>'+
    '<div class="detail-actions"><a class="secondary-button" href="../deleted-rulebooks/">No longer public</a>'+
    '<a class="secondary-button" href="../rulebooks/">Still public</a></div></div>'+
    '<div class="lorebook-hero-stats"><div><b>'+fmt(payload.notPublic)+'</b><span>No longer public</span></div>'+
    '<div><b>'+fmt(payload.publicNow)+'</b><span>Still public</span></div>'+
    '<div><b>'+fmt(payload.totalArchived)+'</b><span>Saved in total</span></div></div></section>'+
    '<div class="lorebook-toolbar rulebook-toolbar">'+
    '<input id="rb-q" type="search" placeholder="Search Rulebooks or creators" aria-label="Search Rulebooks">'+
    '<select id="rb-status" aria-label="Public availability"><option value="'+only+'">'+(removedView?'No longer public':'Still public')+'</option>'+
    '<option value="all">All archived</option><option value="'+(removedView?'public':'not-public')+'">'+
      (removedView?'Still public':'No longer public')+'</option></select>'+
    '<select id="rb-sort" aria-label="Sort Rulebooks"><option value="newest">Newest created</option>'+
    '<option value="seen">Last seen</option><option value="name">Name</option><option value="rules">Most rules</option></select></div>'+
    '<section class="rulebook-tag-panel" aria-label="Rulebook tag filters">'+
    '<div class="rulebook-tag-heading"><strong>Narrow by tag</strong><span>Include + / Exclude −</span>'+
    '<button type="button" id="rb-clear-tags">Clear tags</button></div>'+
    '<div class="rulebook-tag-controls"><input id="rb-tag-search" type="search" placeholder="Search tags" aria-label="Find Rulebook tags">'+
    '<select id="rb-match" aria-label="Included tag matching"><option value="any">Match any included tag</option><option value="all">Match all included tags</option></select></div>'+
    '<div id="rb-selected-tags" class="selected-filters" aria-live="polite"></div>'+
    '<div id="rb-tag-groups" class="rulebook-tag-groups">'+tagList+'</div></section>'+
    '<div class="results-head"><h2>'+(removedView?'Saved unavailable Rulebooks':'Still-public Rulebooks')+'</h2>'+
    '<span id="rb-count"></span></div><div id="rb-pager-top" class="pager-wrap pager-top"></div>'+
    '<section id="rb-grid" class="lorebook-grid"></section><div id="rb-pager" class="pager-wrap"></div>'+
    '<p class="detail-sub">Last catalog check: '+esc(date(payload.generatedAt))+
    (payload.lastScanComplete===false?' (incomplete scan; older statuses were kept)':'')+'</p>';
  const q=document.getElementById('rb-q'), status=document.getElementById('rb-status'),
    sort=document.getElementById('rb-sort'), match=document.getElementById('rb-match'),
    tagSearch=document.getElementById('rb-tag-search'), tagPanel=document.getElementById('rb-tag-groups');
  const state={
    include:parseTagList(params.get('include')||params.get('tag')),
    exclude:parseTagList(params.get('exclude')),
  };
  q.value=params.get('q')||'';
  status.value=params.get('status')||only;
  sort.value=params.get('sort')||'newest';
  match.value=params.get('match')==='all'?'all':'any';
  let page=Math.max(1,Number(params.get('p'))||1);
  function selectedTagsHtml(kind,values) {
    return values.map(t=>'<button type="button" class="chip '+kind+'" data-rb-remove="'+kind+'" data-rb-tag="'+esc(t)+'" title="Remove '+kind+' tag '+esc(t)+'">'+
      (kind==='include'?'+ ':'− ')+esc(t)+' ×</button>').join('');
  }
  function refreshTagControls() {
    const included=new Set(state.include.map(t=>t.toLowerCase()));
    const excluded=new Set(state.exclude.map(t=>t.toLowerCase()));
    for(const button of tagPanel.querySelectorAll('[data-rb-tag-action]')){
      const active=(button.dataset.rbTagAction==='include'?included:excluded).has(button.dataset.rbTag.toLowerCase());
      button.classList.toggle('active',active);
      button.setAttribute('aria-pressed',String(active));
    }
    document.getElementById('rb-selected-tags').innerHTML=
      selectedTagsHtml('include',state.include)+selectedTagsHtml('exclude',state.exclude);
  }
  function render() {
    const term=q.value.toLowerCase().trim();
    let rows=all.filter(x=>{
      if(status.value!=='all'&&x.status!==status.value)return false;
      const rowTags=Array.isArray(x.tags)?x.tags:[];
      if(!matchesTagFilters(rowTags,state.include,state.exclude,match.value))return false;
      return !term||[x.id,x.name,x.description,x.creator,...rowTags].join(' ').toLowerCase().includes(term);
    });
    rows.sort((a,b)=>sort.value==='name'?String(a.name||'').localeCompare(String(b.name||'')):
      sort.value==='rules'?Number(b.numRules||0)-Number(a.numRules||0):
      sort.value==='seen'?String(b.lastSeenAt||'').localeCompare(String(a.lastSeenAt||'')):
      String(b.createdAt||b.firstSeenAt||'').localeCompare(String(a.createdAt||a.firstSeenAt||'')));
    const per=48,pages=Math.max(1,Math.ceil(rows.length/per));page=Math.min(page,pages);
    setParams({q:q.value,tag:null,include:state.include.join(','),exclude:state.exclude.join(','),
      match:match.value==='all'?'all':null,status:status.value===only?'':status.value,
      sort:sort.value,p:page});
    document.getElementById('rb-count').textContent=fmt(rows.length)+' matches';
    const grid=document.getElementById('rb-grid');
    grid.innerHTML=rows.slice((page-1)*per,page*per).map(card).join('')||
      '<div class="empty">No Rulebooks match those filters.</div>';
    bindBrokenCovers(grid);
    const pager=pages<=1?'':'<nav class="pager"><button data-rb-page="'+(page-1)+'" '+(page===1?'disabled':'')+'>Previous</button>'+
      '<span>Page '+page+' of '+pages+'</span><button data-rb-page="'+(page+1)+'" '+(page===pages?'disabled':'')+'>Next</button></nav>';
    for(const id of ['rb-pager-top','rb-pager'])document.getElementById(id).innerHTML=pager;
    document.querySelectorAll('button[data-rb-page]').forEach(button=>button.onclick=()=>{
      page=Number(button.dataset.rbPage);render();window.scrollTo({top:0,behavior:'smooth'});
    });
    refreshTagControls();
  }
  function updateTag(kind,value,remove=false) {
    const tag=String(value||'').trim();if(!tag)return;
    const own=state[kind],other=state[kind==='include'?'exclude':'include'];
    const index=own.findIndex(t=>t.toLowerCase()===tag.toLowerCase());
    if(index>=0)own.splice(index,1);
    else if(!remove){
      const opposite=other.findIndex(t=>t.toLowerCase()===tag.toLowerCase());
      if(opposite>=0)other.splice(opposite,1);
      own.push(tag);
    }
    page=1;render();
  }
  tagPanel.addEventListener('click',event=>{
    const button=event.target.closest('button[data-rb-tag-action]');
    if(button)updateTag(button.dataset.rbTagAction,button.dataset.rbTag);
  });
  document.getElementById('rb-selected-tags').addEventListener('click',event=>{
    const button=event.target.closest('button[data-rb-remove]');
    if(button)updateTag(button.dataset.rbRemove,button.dataset.rbTag,true);
  });
  tagSearch.addEventListener('input',()=>{
    const term=tagSearch.value.trim().toLowerCase();
    for(const group of tagPanel.querySelectorAll('[data-rb-tag-group]')){
      let count=0;
      for(const row of group.querySelectorAll('[data-rb-tag-row]')){
        const visible=row.dataset.rbTagName.includes(term);row.hidden=!visible;if(visible)count++;
      }
      group.hidden=count===0;
    }
  });
  document.getElementById('rb-clear-tags').addEventListener('click',()=>{
    state.include=[];state.exclude=[];page=1;render();
  });
  for(const el of [q,status,sort,match])el.addEventListener(el===q?'input':'change',()=>{page=1;render();});
  render();
}
function renderDetail(record, id) {
  const doc=record.listing||{}, tags=doc.tags||[], versions=record.versions||[],
    statuses=record.statusHistory||[], status=record.status?.current||'unknown';
  const name=doc.name||doc.title||id;
  document.title=name+' · SpicyChat Archive';
  const image=savedCover(doc)||imageUrl(coverRefs[id]);
  app.innerHTML='<section class="lorebook-detail-hero rulebook-detail-hero">'+coverMarkup(image,name,true)+'<div>'+
    '<div class="lorebook-detail-title"><h1>'+esc(name)+'</h1>'+
    (status!=='public'?'<span class="lorebook-status">No longer public</span>':'')+'</div>'+
    (doc.creator_username?'<span class="creator-link">@'+esc(doc.creator_username)+'</span>':'')+
    '<p>'+esc(doc.description||'No description saved.')+'</p>'+
    '<div class="lorebook-tags">'+tagsHtml(tags)+'</div>'+
    '<div class="detail-actions"><a class="secondary-button" href="../deleted-rulebooks/">No longer public</a>'+
    '<a class="secondary-button" href="../rulebooks/">Still public</a>'+
    '<a class="secondary-button" href="https://spicychat.ai/rulebook/'+esc(id)+'" target="_blank" rel="noopener">Original SpicyChat page</a></div>'+
    (status==='not-public'?'<p class="filter-note">Not in the last complete public listing. This does not prove deletion.</p>':'')+
    '</div></section>'+
    '<section class="stats-grid lorebook-detail-stats">'+
    '<div class="stat"><b>'+fmt(doc.num_rules??doc.numRules??doc.rule_count)+'</b><span>Rules shown in listing</span></div>'+
    '<div class="stat"><b>'+fmt(versions.length)+'</b><span>Saved listing versions</span></div>'+
    '<div class="stat"><b>'+fmt(doc.numAttachedBots??doc.numAttachedCharacters)+'</b><span>Attached bots</span></div>'+
    '<div class="stat"><b>'+fmt(doc.numAttachedPersonas)+'</b><span>Attached personas</span></div></section>'+
    '<section class="table-card"><h2>Archive dates</h2><div class="lorebook-meta-grid">'+
    '<div><span>Created (SpicyChat)</span><b>'+esc(date(doc.createdAt))+'</b></div>'+
    '<div><span>First saved by Archive</span><b>'+esc(date(record.firstSeenAt))+'</b></div>'+
    '<div><span>Last observed</span><b>'+esc(date(record.lastObservedAt))+'</b></div>'+
    '<div><span>Last updated</span><b>'+esc(date(doc.updatedAt))+'</b></div>'+
    '<div><span>Rulebook ID</span><b class="mono">'+esc(id)+'</b></div></div></section>'+
    '<section class="table-card"><h2>What was saved?</h2><p>Public listing details and any cover reference SpicyChat shared. '+
    'Rule content, private configuration and hidden variables are not captured here.</p></section>'+
    '<section class="table-card"><h2>Availability history</h2><div class="lorebook-history-list">'+
    (statuses.length?statuses.slice().reverse().map(x=>
      '<div class="recent-run"><span>'+esc(date(x.at))+'</span><b>'+
      esc(x.status==='not-public'?'No longer public':x.status)+'</b></div>').join(''):
      '<div class="empty">No changes in public listing status yet.</div>')+'</div></section>'+
    '<section class="table-card"><h2>Saved listing versions</h2><div class="lorebook-history-list">'+
    (versions.length?versions.slice().reverse().map((x,i)=>
      '<details class="history-detail"><summary>Listing '+esc(date(x.at))+'</summary>'+
      '<div class="pre">'+esc(JSON.stringify(x.metadata||{},null,2))+'</div></details>').join(''):
      '<div class="empty">No saved listing versions yet.</div>')+'</div></section>';
  bindBrokenCovers(app);
}
async function main() {
  const [covers,tags]=await Promise.all([
    fetchJson('../data/rulebook-cover-refs.json').catch(()=>({})),
    fetchJson('../rulebook-tags.json').catch(()=>({})),
  ]);
  coverRefs=covers&&typeof covers.covers==='object'&&covers.covers?covers.covers:Object.create(null);
  tagCatalog=Array.isArray(tags.groups)?tags.groups:[];
  const base=await sourceBase();
  if (!base) throw new Error('Archive data source is not configured');
  if(isDetail) {
    const id=String(params.get('id')||'').trim().toLowerCase();
    if(!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)){
      app.innerHTML='<div class="error">A valid Rulebook ID is needed.</div>';return;
    }
    const prefix=id.replaceAll('-','').slice(0,2);
    const record=await fetchJson(base+'/rulebooks/'+prefix+'/'+encodeURIComponent(id)+'.json');
    renderDetail(record,id);
  } else {
    const index=await fetchJson(base+'/indexes/rulebooks.json');
    renderBrowse(index);
  }
}
main().catch(error=>{
  console.warn('Rulebook archive not ready',error);
  app.innerHTML='<section class="hero lorebook-hero"><div><h1>Rulebooks</h1>'+
    '<p>The Rulebook archive does not have a published catalog yet, or it could not be loaded.</p>'+
    '<a class="secondary-button" href="https://spicychat.ai/rulebooks/explore" target="_blank" rel="noopener">Explore public Rulebooks on SpicyChat</a></div></section>';
});
