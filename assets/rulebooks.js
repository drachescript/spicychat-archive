// Public Rulebook metadata only. Private rule bodies and hidden values are
// intentionally not in the Archive's Rulebook data source.
const app = document.getElementById('app');
const isDetail = document.body.dataset.page === 'rulebook';
const params = new URLSearchParams(location.search);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
})[ch]);
const fmt = v => Number.isFinite(Number(v)) ? Number(v).toLocaleString() : '—';
const date = value => {
  if (!value) return 'Not available';
  const d = new Date(value);
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
function card(x) {
  const unavailable = x.status !== 'public';
  return '<article class="lorebook-card '+(unavailable?'lorebook-card-muted':'')+'">'+
    '<div class="lorebook-card-body"><div class="lorebook-card-top">'+
    '<a class="lorebook-name" href="'+esc(detailHref(x.id))+'">'+esc(x.name||x.id)+'</a>'+
    (unavailable?'<span class="lorebook-status">No longer public</span>':'')+'</div>'+
    (x.creator?'<span class="creator-link">@'+esc(x.creator)+'</span>':'')+
    '<p class="lorebook-description">'+esc(x.description||'No description saved.')+'</p>'+
    '<div class="lorebook-tags">'+tagsHtml(x.tags)+'</div>'+
    '<div class="lorebook-card-foot"><span>'+fmt(x.numRules)+' rules listed</span>'+
    '<span>Last seen '+esc(date(x.lastSeenAt))+'</span></div></div></article>';
}
function renderBrowse(payload) {
  const all = Array.isArray(payload.rulebooks)?payload.rulebooks:[];
  const tags = [...new Set(all.flatMap(x => Array.isArray(x.tags)?x.tags:[]))].sort((a,b)=>String(a).localeCompare(String(b)));
  app.innerHTML =
    '<section class="hero lorebook-hero"><div><h1>Rulebooks</h1>'+
    '<p>Public Rulebooks saved from SpicyChat’s public listings. Browse older details, see what changed and find Rulebooks that are no longer listed.</p>'+
    '<p class="filter-note">The Archive saves public listing details only. Private or hidden rules are not included.</p></div>'+
    '<div class="lorebook-hero-stats"><div><b>'+fmt(payload.publicNow)+'</b><span>Public in last full check</span></div>'+
    '<div><b>'+fmt(payload.totalArchived)+'</b><span>Archived</span></div></div></section>'+
    '<div class="lorebook-toolbar"><input id="rb-q" type="search" placeholder="Search Rulebooks, creators or tags" aria-label="Search Rulebooks">'+
    '<select id="rb-tag" aria-label="Rulebook tag"><option value="">All tags</option>'+
    tags.map(t=>'<option value="'+esc(t)+'">'+esc(t)+'</option>').join('')+'</select>'+
    '<select id="rb-status" aria-label="Public availability"><option value="public">Currently listed</option>'+
    '<option value="all">All saved Rulebooks</option><option value="not-public">No longer public</option></select>'+
    '<select id="rb-sort" aria-label="Sort Rulebooks"><option value="newest">Newest created</option>'+
    '<option value="name">Name</option><option value="rules">Most rules</option></select></div>'+
    '<div class="results-head"><h2>Saved Rulebooks</h2><span id="rb-count"></span></div>'+
    '<section id="rb-grid" class="lorebook-grid"></section><div id="rb-pager" class="pager-wrap"></div>'+
    '<p class="detail-sub">Last catalog check: '+esc(date(payload.generatedAt))+
    (payload.lastScanComplete===false?' (incomplete scan; older statuses were kept)':'')+'</p>';
  const q=document.getElementById('rb-q'), tag=document.getElementById('rb-tag'),
    status=document.getElementById('rb-status'), sort=document.getElementById('rb-sort');
  q.value=params.get('q')||''; tag.value=params.get('tag')||'';
  status.value=params.get('status')||'public';
  let page=1;
  function render() {
    const text=q.value.toLowerCase().trim(), current=tag.value.toLowerCase();
    let rows=all.filter(x=>
      (status.value==='all'||x.status===status.value) &&
      (!current||(x.tags||[]).some(t=>String(t).toLowerCase()===current)) &&
      (!text||[x.id,x.name,x.description,x.creator,...(x.tags||[])]
        .join(' ').toLowerCase().includes(text))
    );
    rows.sort((a,b)=>sort.value==='name'?
      String(a.name).localeCompare(String(b.name)):sort.value==='rules'?
      Number(b.numRules||0)-Number(a.numRules||0):
      String(b.createdAt||b.firstSeenAt||'').localeCompare(String(a.createdAt||a.firstSeenAt||'')));
    const per=48, pages=Math.max(1,Math.ceil(rows.length/per));
    page=Math.min(page,pages);
    const start=(page-1)*per;
    document.getElementById('rb-count').textContent=fmt(rows.length)+' matches';
    document.getElementById('rb-grid').innerHTML=rows.slice(start,start+per).map(card).join('')||
      '<div class="empty">No Rulebooks match those filters.</div>';
    document.getElementById('rb-pager').innerHTML=pages<=1?'':('<nav class="pager">'+
      '<button id="rb-prev" '+(page===1?'disabled':'')+'>Previous</button>'+
      '<span>Page '+page+' of '+pages+'</span>'+
      '<button id="rb-next" '+(page===pages?'disabled':'')+'>Next</button></nav>');
    const prev=document.getElementById('rb-prev'),next=document.getElementById('rb-next');
    if(prev)prev.onclick=()=>{page--;render();};
    if(next)next.onclick=()=>{page++;render();};
  }
  for(const element of [q,tag,status,sort])element.addEventListener(
    element===q?'input':'change',()=>{page=1;render();}
  );
  render();
}
function renderDetail(record, id) {
  const doc=record.listing||{}, tags=doc.tags||[], versions=record.versions||[],
    statuses=record.statusHistory||[], status=record.status?.current||'unknown';
  const name=doc.name||doc.title||id;
  document.title=name+' · SpicyChat Archive';
  app.innerHTML='<section class="lorebook-detail-hero"><div>'+
    '<div class="lorebook-detail-title"><h1>'+esc(name)+'</h1>'+
    (status!=='public'?'<span class="lorebook-status">No longer public</span>':'')+'</div>'+
    (doc.creator_username?'<span class="creator-link">@'+esc(doc.creator_username)+'</span>':'')+
    '<p>'+esc(doc.description||'No description saved.')+'</p>'+
    '<div class="lorebook-tags">'+tagsHtml(tags)+'</div>'+
    '<div class="detail-actions"><a class="secondary-button" href="../rulebooks/">All Rulebooks</a>'+
    '<a class="secondary-button" href="https://spicychat.ai/rulebooks/explore" target="_blank" rel="noopener">Explore on SpicyChat</a></div></div></section>'+
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
    '<section class="table-card"><h2>What was saved?</h2><p>Only public listing metadata. '+
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
}
async function main() {
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
