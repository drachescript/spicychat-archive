const app=document.querySelector('#app');
const page=document.body.dataset.page||'lorebooks';
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const qs=k=>new URLSearchParams(location.search).get(k)||'';
const fmt=n=>Number(n||0).toLocaleString();
const date=v=>{if(!v)return'Unknown';const d=new Date(v);return Number.isNaN(d.getTime())?'Unknown':d.toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});};
const shortDate=v=>{if(!v)return'Unknown';const d=new Date(v);return Number.isNaN(d.getTime())?'Unknown':d.toLocaleDateString([], {year:'numeric',month:'short',day:'numeric'});};
const fetchJson=async url=>{const r=await fetch(url,{cache:'no-store'});if(!r.ok){const e=new Error('HTTP '+r.status);e.status=r.status;throw e;}return r.json();};
const fetchJsonRetryStale404=async url=>{try{return await fetchJson(url);}catch(e){if(e?.status!==404)throw e;const u=new URL(url,location.href);u.searchParams.set('_fresh',Date.now().toString(36));return fetchJson(u.toString());}};
const runtimePath=page==='lorebooks'?'../data/runtime.json':'../data/runtime.json';
async function runtime(){return fetchJson(runtimePath);}
const base=r=>String(r.publicDataBaseUrl||'').replace(/\/$/,'');
function detailHref(id){return '../lorebook/?id='+encodeURIComponent(id);}
function creatorHref(name){return '../creator/?name='+encodeURIComponent(name);}
function imageUrl(raw){
  const value=String(raw||'').trim();
  if(!value)return'';
  if(/^https?:\/\//i.test(value))return value;
  return 'https://cdn.nd-api.com/'+value.replace(/^\/+/, '');
}
function normalizeTags(tags){return Array.isArray(tags)?tags.map(String).filter(Boolean):[];}
function setParams(values){
  const u=new URL(location.href);
  for(const [k,v] of Object.entries(values)){if(v==null||v===''||v==='any'||v===1)u.searchParams.delete(k);else u.searchParams.set(k,String(v));}
  history.replaceState(null,'',u);
}
function pager(pageNum,pages){
  if(pages<=1)return'';
  const parts=[];
  const add=(label,p,active=false,disabled=false,cls='')=>parts.push('<button class="pager-page '+cls+(active?' active':'')+'" data-page="'+p+'" '+(disabled?'disabled':'')+'>'+label+'</button>');
  add('‹ Prev',Math.max(1,pageNum-1),false,pageNum===1,'pager-prev');
  let start=Math.max(1,pageNum-4),end=Math.min(pages,start+8);start=Math.max(1,end-8);
  if(start>1){add('1',1,pageNum===1);if(start>2)parts.push('<span class="pager-gap">…</span>');}
  for(let p=start;p<=end;p++)add(String(p),p,p===pageNum);
  if(end<pages){if(end<pages-1)parts.push('<span class="pager-gap">…</span>');add(String(pages),pages,pageNum===pages);}
  add('Next ›',Math.min(pages,pageNum+1),false,pageNum===pages,'pager-next');
  return '<nav class="pager">'+parts.join('')+'</nav>';
}
function bindPager(render){
  document.addEventListener('click',e=>{const b=e.target.closest('.pager-page[data-page]');if(!b||b.disabled)return;render(Number(b.dataset.page)||1);window.scrollTo({top:0,behavior:'smooth'});});
}
function card(x){
  const img=imageUrl(x.avatar);
  const tags=normalizeTags(x.tags);
  const statusBadge=x.status==='deleted'
    ?'<span class="lorebook-status is-deleted">Deleted</span>'
    :x.status==='not-public'
      ?'<span class="lorebook-status is-archived">No longer public</span>'
      :'';
  return '<article class="lorebook-card '+(x.status==='public'?'':'lorebook-card-muted')+'">'+
    '<a class="lorebook-cover" href="'+esc(detailHref(x.id))+'">'+(img?'<img src="'+esc(img)+'" alt="" loading="lazy">':'<div class="lorebook-cover-empty">LB</div>')+(x.isNsfw?'<span class="lorebook-nsfw">NSFW</span>':'')+'</a>'+
    '<div class="lorebook-card-body"><div class="lorebook-card-top"><a class="lorebook-name" href="'+esc(detailHref(x.id))+'">'+esc(x.name||x.id)+'</a>'+statusBadge+'</div>'+
    (x.creator?'<a class="creator-link" href="'+esc(creatorHref(x.creator))+'">@'+esc(x.creator)+'</a>':'')+
    '<p class="lorebook-description">'+esc(x.description||'No description archived.')+'</p>'+
    '<div class="lorebook-tags">'+tags.slice(0,8).map(t=>'<span>'+esc(t)+'</span>').join('')+'</div>'+
    '<div class="lorebook-card-foot"><span>'+fmt(x.numEntries)+' entr'+(Number(x.numEntries)===1?'y':'ies')+'</span><span>Updated '+shortDate(x.updatedAt||x.lastChangeAt||x.lastSeenAt)+'</span></div></div></article>';
}
async function browse(){
  const rt=await runtime();
  const payload=await fetchJsonRetryStale404(base(rt)+'/indexes/lorebooks.json');
  const all=Array.isArray(payload.lorebooks)?payload.lorebooks:[];
  const deletedView=page==='deleted-lorebooks';
  const state={q:qs('q'),creator:qs('creator'),tag:qs('tag')||'any',rating:qs('rating')||'any',sort:qs('sort')||'updated',page:Math.max(1,Number(qs('p'))||1)};
  const tags=[...new Set(all.flatMap(x=>normalizeTags(x.tags)))].sort((a,b)=>a.localeCompare(b));
  const unavailableCount=all.filter(x=>x.status!=='public').length;
  app.innerHTML=deletedView
    ?'<section class="hero lorebook-hero"><div><h1>Deleted Lorebooks</h1><p>Lorebooks that disappeared from the live public index stay archived here. When the public data only proves disappearance, the archive keeps the more precise “No longer public” label instead of pretending a deletion was confirmed.</p></div><div class="lorebook-hero-stats"><div><b>'+fmt(unavailableCount)+'</b><span>No longer public</span></div><div><b>'+fmt(payload.totalArchived)+'</b><span>Total archived</span></div></div></section>'
    :'<section class="hero lorebook-hero"><div><h1>Lorebooks</h1><p>Public SpicyChat Lorebooks discovered from the live public index, with archived detail, entries, versions and public-status history.</p></div><div class="lorebook-hero-stats"><div><b>'+fmt(payload.publicNow)+'</b><span>Public</span></div><div><b>'+fmt(payload.totalArchived)+'</b><span>Archived</span></div></div></section>';
  app.innerHTML+='<div class="lorebook-toolbar"><input id="lb-q" placeholder="Lorebook, description, creator or ID"><input id="lb-creator" placeholder="Creator username"><select id="lb-tag"><option value="any">All tags</option>'+tags.map(t=>'<option value="'+esc(t)+'">'+esc(t)+'</option>').join('')+'</select><select id="lb-rating"><option value="any">SFW + NSFW</option><option value="sfw">SFW only</option><option value="nsfw">NSFW only</option></select><select id="lb-sort"><option value="updated">Recently updated</option><option value="created">Newest created</option><option value="entries">Most entries</option><option value="name">Name</option></select></div>'+
    '<div class="results-head"><h2>'+(deletedView?'Archived unavailable Lorebooks':'Discovered Lorebooks')+'</h2><span id="lb-count"></span></div><div id="lb-pager-top" class="pager-wrap pager-top"></div><section id="lb-grid" class="lorebook-grid"></section><div id="lb-pager-bottom" class="pager-wrap"></div>';
  for(const [id,val] of [['lb-q',state.q],['lb-creator',state.creator],['lb-tag',state.tag],['lb-rating',state.rating],['lb-sort',state.sort]])document.querySelector('#'+id).value=val;
  function render(next){
    if(next)state.page=next;
    state.q=document.querySelector('#lb-q').value.trim();state.creator=document.querySelector('#lb-creator').value.trim();state.tag=document.querySelector('#lb-tag').value;state.rating=document.querySelector('#lb-rating').value;state.sort=document.querySelector('#lb-sort').value;
    const q=state.q.toLowerCase(),creator=state.creator.toLowerCase();
    let rows=all.filter(x=>{
      if(deletedView?x.status==='public':x.status!=='public')return false;
      if(state.rating==='sfw'&&x.isNsfw)return false;if(state.rating==='nsfw'&&!x.isNsfw)return false;
      if(creator&&String(x.creator||'').toLowerCase()!==creator)return false;
      if(state.tag!=='any'&&!normalizeTags(x.tags).some(t=>t.toLowerCase()===state.tag.toLowerCase()))return false;
      if(q&&![x.id,x.name,x.description,x.creator,...normalizeTags(x.tags)].join(' ').toLowerCase().includes(q))return false;
      return true;
    });
    rows.sort((a,b)=>state.sort==='name'?String(a.name||'').localeCompare(String(b.name||'')):state.sort==='entries'?Number(b.numEntries||0)-Number(a.numEntries||0):state.sort==='created'?new Date(b.createdAt||0)-new Date(a.createdAt||0):new Date(b.updatedAt||b.lastChangeAt||b.lastSeenAt||0)-new Date(a.updatedAt||a.lastChangeAt||a.lastSeenAt||0));
    const per=48,pages=Math.max(1,Math.ceil(rows.length/per));if(state.page>pages)state.page=pages;const start=(state.page-1)*per,visible=rows.slice(start,start+per);
    document.querySelector('#lb-count').textContent=rows.length?(start+1)+'–'+Math.min(rows.length,start+per)+' of '+fmt(rows.length):'0 matches';
    document.querySelector('#lb-grid').innerHTML=visible.length?visible.map(card).join(''):'<div class="empty">'+(deletedView?'No deleted/no-longer-public Lorebooks have been archived yet.':'No Lorebooks match these filters.')+'</div>';
    const p=pager(state.page,pages);document.querySelector('#lb-pager-top').innerHTML=p;document.querySelector('#lb-pager-bottom').innerHTML=p;
    setParams({q:state.q,creator:state.creator,tag:state.tag,rating:state.rating,sort:state.sort,p:state.page});
  }
  for(const id of['lb-q','lb-creator'])document.querySelector('#'+id).addEventListener('input',()=>{state.page=1;render();});
  for(const id of['lb-tag','lb-rating','lb-sort'])document.querySelector('#'+id).addEventListener('change',()=>{state.page=1;render();});
  bindPager(render);render();
}
function renderValue(v){
  if(v==null)return'—';
  if(Array.isArray(v))return v.map(x=>typeof x==='string'?x:JSON.stringify(x)).join(', ');
  if(typeof v==='object')return JSON.stringify(v,null,2);
  return String(v);
}
function entryCard(entry){
  const keys=normalizeTags(entry.keywords);
  return '<article class="lorebook-entry"><div class="lorebook-entry-head"><h3>'+esc(entry.name||'Unnamed entry')+'</h3><span>Priority '+esc(entry.sortPriority??'—')+'</span></div><div class="lorebook-entry-keywords">'+keys.map(k=>'<span>'+esc(k)+'</span>').join('')+'</div><div class="pre">'+esc(entry.content||'')+'</div><div class="lorebook-entry-meta"><span>Version '+esc(entry.version??'—')+'</span><span>'+esc(entry.status||'')+'</span><span>'+shortDate(entry.updatedAt||entry.createdAt)+'</span></div></article>';
}
async function detail(){
  const id=qs('id').trim().toLowerCase();if(!id){app.innerHTML='<div class="error">No Lorebook ID supplied.</div>';return;}
  const rt=await runtime(),compact=id.replaceAll('-',''),prefix=compact.slice(0,2)||'__';
  let record;
  try{record=await fetchJsonRetryStale404(base(rt)+'/lorebooks/'+prefix+'/'+encodeURIComponent(id)+'.json');}catch{app.innerHTML='<div class="error">This Lorebook does not have an archived record yet.</div>';return;}
  const detail=record.detail||record.listing||{},entries=Array.isArray(detail.entries)?detail.entries:[],tags=normalizeTags(detail.tags||record.listing?.tags);
  document.title=(detail.name||'Lorebook')+' · SpicyChat Archive';
  const img=imageUrl(detail.avatar_url||record.listing?.avatar_url);
  const currentStatus=record.status?.current||'unknown';
  const statusBadge=currentStatus==='deleted'
    ?'<span class="lorebook-status is-deleted">Deleted</span>'
    :currentStatus==='not-public'
      ?'<span class="lorebook-status is-archived">No longer public</span>'
      :'';
  const versions=Array.isArray(record.versions)?record.versions:[],history=Array.isArray(record.history)?record.history:[],statusHistory=Array.isArray(record.statusHistory)?record.statusHistory:[];
  app.innerHTML='<section class="lorebook-detail-hero">'+
    (img?'<img class="lorebook-detail-cover" src="'+esc(img)+'" alt="">':'<div class="lorebook-detail-cover lorebook-cover-empty">LB</div>')+
    '<div><div class="lorebook-detail-title"><h1>'+esc(detail.name||id)+'</h1>'+statusBadge+'</div>'+
    (detail.creator_username?'<a class="creator-link" href="'+esc(creatorHref(detail.creator_username))+'">@'+esc(detail.creator_username)+'</a>':'')+
    '<p>'+esc(detail.description||'No description archived.')+'</p><div class="lorebook-tags">'+tags.map(t=>'<span>'+esc(t)+'</span>').join('')+'</div>'+
    '<div class="detail-actions"><a class="secondary-button" href="https://spicychat.ai/lorebook/'+encodeURIComponent(id)+'" target="_blank" rel="noopener">Open on SpicyChat</a><a class="secondary-button" href="../lorebooks/?creator='+encodeURIComponent(detail.creator_username||'')+'">More from creator</a></div></div></section>'+
    '<section class="stats-grid lorebook-detail-stats"><div class="stat"><b>'+fmt(detail.num_entries||entries.length)+'</b><span>Entries</span></div><div class="stat"><b>'+fmt(detail.numAttachedCharacters)+'</b><span>Attached characters</span></div><div class="stat"><b>'+fmt(detail.version)+'</b><span>Lorebook version</span></div><div class="stat"><b>'+fmt(versions.length)+'</b><span>Archived versions</span></div></section>'+
    '<section class="table-card"><h2>Archive record</h2><div class="lorebook-meta-grid"><div><span>First captured</span><b>'+date(record.firstSeenAt)+'</b></div><div><span>Last observed</span><b>'+date(record.lastObservedAt)+'</b></div><div><span>Created</span><b>'+date(detail.createdAt)+'</b></div><div><span>Updated</span><b>'+date(detail.updatedAt)+'</b></div><div><span>ID</span><b class="mono">'+esc(id)+'</b></div><div><span>Rating</span><b>'+(detail.is_nsfw?'NSFW':'SFW')+'</b></div></div></section>'+
    '<section class="table-card"><div class="results-head"><h2>Entries</h2><span>'+fmt(entries.length)+'</span></div><div class="lorebook-entry-list">'+(entries.length?entries.map(entryCard).join(''):'<div class="empty">The archived detail response did not include entries.</div>')+'</div></section>'+
    '<section class="table-card"><h2>Public-status history</h2><div class="lorebook-history-list">'+(statusHistory.length?statusHistory.slice().reverse().map(x=>'<div class="recent-run"><span>'+date(x.from||x.at)+'</span><b>'+esc(x.status||'unknown')+'</b></div>').join(''):'<div class="empty">No status history yet.</div>')+'</div></section>'+
    '<section class="table-card"><h2>Content history</h2><p class="filter-note">Each meaningful Lorebook version is preserved. Entry-content changes are represented by version snapshots rather than source noise.</p><div class="lorebook-history-list">'+(history.length?history.slice().reverse().slice(0,200).map(x=>'<details class="history-detail"><summary><b>'+esc(x.field||'Change')+'</b><span>'+date(x.at)+'</span></summary><div class="history-diff"><div><span>Before</span><div class="pre">'+esc(renderValue(x.from??(x.fromCount!=null?x.fromCount:'—')))+'</div></div><div><span>After</span><div class="pre">'+esc(renderValue(x.to??(x.toCount!=null?x.toCount:'—')))+'</div></div></div></details>').join(''):'<div class="empty">No content changes after the first archived version yet.</div>')+'</div></section>';
}
(async()=>{try{if(page==='lorebook')await detail();else await browse();}catch(e){console.error(e);app.innerHTML='<div class="error">Could not load Lorebook archive: '+esc(e.message||e)+'</div>';}})();
