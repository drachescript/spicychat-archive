const asset = (path) => new URL(`../${path}`, import.meta.url);
const app = document.querySelector('#app');
const page = document.body.dataset.page || 'browse';

const esc = (v='') => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v == null || v === '' ? '—' : Number.isFinite(Number(v)) ? Number(v).toLocaleString() : String(v);
const date = v => { if(!v) return 'Unknown'; const d=new Date(v); return Number.isNaN(d.getTime())?String(v):d.toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric'}); };
const qs = (k) => new URLSearchParams(location.search).get(k);

function detailHref(id){ return new URL(`../bot/?id=${encodeURIComponent(id)}`, import.meta.url).href; }
function resolveAvatar(v){ return !v ? '' : (/^https?:\/\//i.test(v) ? v : asset(v).href); }

function badge(status){
  const s=(status||'unknown').toLowerCase();
  const label=s==='public'?'Active':s==='deleted'?'Archived':s;
  return `<span class="badge ${esc(s)}">${esc(label)}</span>`;
}

function card(bot, blurNsfw){
  const avatar=resolveAvatar(bot.avatar);
  const img=avatar ? `<img src="${esc(avatar)}" alt="" loading="lazy" class="${blurNsfw&&bot.isNsfw?'nsfw-blur':''}" onerror="this.remove()">` : '';
  const tags=(bot.tags||[]).slice(0,5).map(t=>`<span class="tag">${esc(t)}</span>`).join('');
  return `<article class="card"><a class="cardlink" href="${esc(detailHref(bot.id))}"><div class="art">${img}${badge(bot.status)}</div><div class="body"><h3>${esc(bot.name||'Unknown bot')}</h3><div class="creator">${bot.creator?`@${esc(bot.creator)}`:'Unknown creator'}</div><div class="title">${esc(bot.title||'')}</div><div class="taglist">${tags}</div><div class="meta"><span>${fmt(bot.messages)} messages</span><span>${bot.rating==null?'—':`★ ${esc(bot.rating)}`}</span><span>First seen ${date(bot.firstSeenAt)}</span><span>${bot.avatarArchived?'image archived':'CDN image'}</span></div></div></a></article>`;
}

function chipInput(id, selected, onChange, placeholder){
  return `<div class="chips" id="${id}">${selected.map(t=>`<span class="chip" data-value="${esc(t)}">${esc(t)} <button type="button" aria-label="Remove ${esc(t)}">×</button></span>`).join('')}<input placeholder="${esc(placeholder)}" autocomplete="off"></div>`;
}

async function loadCatalog(){
  const manifest=await fetch(asset('data/manifest.json'),{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('manifest');return r.json()});
  const parts=await Promise.all((manifest.shards||[]).map(s=>fetch(asset(`data/catalog/${s}.json`),{cache:'no-store'}).then(r=>r.json())));
  return {manifest,bots:parts.flatMap(x=>x.bots||[])};
}

function parseTags(value){return String(value||'').split(',').map(x=>x.trim()).filter(Boolean)}
function setQuery(state){
  const u=new URL(location.href);
  const set=(k,v)=>{if(v&&(!(Array.isArray(v))||v.length))u.searchParams.set(k,Array.isArray(v)?v.join(','):v);else u.searchParams.delete(k)};
  set('q',state.q); set('include',state.include); set('exclude',state.exclude); set('creator',state.creator); set('sort',state.sort); set('match',state.match);
  if(state.status && state.status!=='all') set('status',state.status); else u.searchParams.delete('status');
  history.replaceState(null,'',u);
}

async function browse(){
  const {manifest,bots}=await loadCatalog();
  const deletedOnly=page==='deleted';
  const initial={
    q:qs('q')||'', include:parseTags(qs('include')), exclude:parseTags(qs('exclude')), creator:qs('creator')||'',
    sort:qs('sort')||(deletedOnly?'deleted-newest':'trending'), match:qs('match')||'all', status:deletedOnly?'deleted':(qs('status')||'all'),
    blurNsfw:localStorage.getItem('sca-blur-nsfw')!=='0'
  };
  const state={...initial};
  const tagOptions=(manifest.topTags||[]).map(x=>x.tag);
  const creators=[...new Set(bots.map(b=>b.creator).filter(Boolean))].sort((a,b)=>a.localeCompare(b));

  app.innerHTML=`<section class="stats"><div class="stat"><b>${fmt(manifest.totalBots)}</b><span>Bots discovered</span></div><div class="stat"><b>${fmt(manifest.activeBots)}</b><span>Currently public</span></div><div class="stat"><b>${fmt(manifest.deletedBots)}</b><span>Deleted / archived</span></div><div class="stat"><b>${fmt(manifest.tagCount)}</b><span>Unique tags</span></div></section>
  <section class="controls"><div class="control-grid"><div class="field"><label>Search</label><input id="search" value="${esc(state.q)}" placeholder="Name, title, creator or tag"></div><div class="field"><label>Creator</label><input id="creator" list="creator-list" value="${esc(state.creator)}" placeholder="Any creator"><datalist id="creator-list">${creators.slice(0,5000).map(x=>`<option value="${esc(x)}"></option>`).join('')}</datalist></div><div class="field"><label>Status</label><select id="status" ${deletedOnly?'disabled':''}><option value="all">All</option><option value="public">Active</option><option value="deleted">Deleted</option><option value="missing">Missing</option><option value="restricted">Restricted</option></select></div><div class="field"><label>Sort</label><select id="sort"><option value="trending">Trending</option><option value="popular">Popular</option><option value="top-rated">Top rated</option><option value="latest">Latest</option><option value="discovered">Recently discovered</option><option value="updated">Recently updated</option><option value="deleted-newest">Recently deleted</option><option value="name">Name</option></select></div></div>
  <div class="tag-row"><div class="field"><label>Include tags</label>${chipInput('include-tags',state.include,null,'Add tag and press Enter')}</div><div class="field"><label>Exclude tags</label>${chipInput('exclude-tags',state.exclude,null,'Add tag and press Enter')}</div></div>
  <datalist id="tag-list">${tagOptions.map(t=>`<option value="${esc(t)}"></option>`).join('')}</datalist>
  <div class="options-row"><label><input type="radio" name="match" value="all" ${state.match==='all'?'checked':''}> Match all included tags</label><label><input type="radio" name="match" value="any" ${state.match==='any'?'checked':''}> Match any included tag</label><label><input id="blur-nsfw" type="checkbox" ${state.blurNsfw?'checked':''}> Blur NSFW avatars</label><span>Last scan: ${date(manifest.lastScan)}</span></div></section>
  <div class="results-head"><h2 id="result-title">Bots</h2><span id="result-count"></span></div><section class="grid" id="grid"></section><footer class="footer">Public archival project. Deleted status requires repeated explicit public character API 404s; disappearing from a listing alone is not treated as deletion.</footer>`;

  const $=s=>document.querySelector(s), grid=$('#grid'), count=$('#result-count');
  $('#status').value=state.status; $('#sort').value=state.sort;
  for(const id of ['include-tags','exclude-tags']){
    const box=$(`#${id}`), input=box.querySelector('input'); input.setAttribute('list','tag-list');
    const arr=id==='include-tags'?state.include:state.exclude;
    box.addEventListener('click',e=>{const btn=e.target.closest('button'); if(!btn)return; const chip=btn.closest('.chip'); const i=arr.indexOf(chip.dataset.value); if(i>=0)arr.splice(i,1); chip.remove(); render();});
    input.addEventListener('keydown',e=>{if(e.key!=='Enter'&&e.key!==',')return;e.preventDefault();const v=input.value.trim().replace(/,$/,'');if(v&&!arr.some(x=>x.toLowerCase()===v.toLowerCase())){arr.push(v);const span=document.createElement('span');span.className='chip';span.dataset.value=v;span.innerHTML=`${esc(v)} <button type="button">×</button>`;box.insertBefore(span,input);input.value='';render();}});
  }

  const currentRanks={}; for(const [name,ids] of Object.entries(manifest.listings||{})){const m=new Map();(ids||[]).forEach((id,i)=>m.set(id,i+1));currentRanks[name]=m;}
  function listingRank(bot,name){ return currentRanks[name]?.get(bot.id) ?? 1e12; }
  function render(){
    state.q=$('#search').value.trim(); state.creator=$('#creator').value.trim(); state.status=deletedOnly?'deleted':$('#status').value; state.sort=$('#sort').value; state.match=document.querySelector('input[name=match]:checked')?.value||'all'; state.blurNsfw=$('#blur-nsfw').checked; localStorage.setItem('sca-blur-nsfw',state.blurNsfw?'1':'0'); setQuery(state);
    const q=state.q.toLowerCase(), creator=state.creator.toLowerCase(), inc=state.include.map(x=>x.toLowerCase()), exc=state.exclude.map(x=>x.toLowerCase());
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
    count.textContent=`${rows.length.toLocaleString()} shown`;
    grid.innerHTML=rows.length?rows.slice(0,2000).map(b=>card(b,state.blurNsfw)).join(''):`<div class="empty">No bots match these filters.</div>`;
    if(rows.length>2000) count.textContent+=` · first 2,000 rendered`;
  }
  for(const id of ['search','creator','status','sort','blur-nsfw']) document.querySelector(`#${id}`)?.addEventListener(id==='search'||id==='creator'?'input':'change',render);
  document.querySelectorAll('input[name=match]').forEach(x=>x.addEventListener('change',render));
  render();
}

function bestField(record,key){
  const lk=record.lastKnown||{};
  if(lk[key]!==undefined&&lk[key]!==null&&String(lk[key]).trim?.()!=='')return lk[key];
  for(const source of ['character-api','typesense:trending','typesense:latest','typesense:explore']){const v=record.current?.[source]?.[key];if(v!==undefined&&v!==null)return v;}
  return null;
}

async function botPage(){
  const id=qs('id'); if(!id){app.innerHTML='<div class="error">No bot ID supplied.</div>';return;}
  const r=await fetch(asset(`data/bots/${encodeURIComponent(id)}.json`),{cache:'no-store'}); if(!r.ok){app.innerHTML='<div class="error">This bot is not in the archive.</div>';return;} const record=await r.json();
  const lk=record.lastKnown||{}, status=record.status?.current||'unknown', avatar=record.avatarArchive?.path?resolveAvatar(record.avatarArchive.path.replace(/^archive\//,'')):resolveAvatar(lk.avatar_url||lk.avatar||lk.image);
  const fields=['description','greeting','greetings','personality','definition','persona','character_definition','characterDefinition','scenario','example_dialogue','example_dialogues','system_prompt','post_history_instructions','lorebooks'];
  const fieldHtml=fields.map(k=>{const v=bestField(record,k);if(v==null||v==='')return'';const label=k.replaceAll('_',' ');return `<div class="field-block"><h3>${esc(label)} · last known</h3><div class="pre">${esc(Array.isArray(v)?v.join('\n\n'):typeof v==='object'?JSON.stringify(v,null,2):v)}</div></div>`}).join('');
  const tags=(lk.tags||[]).map(t=>`<span class="pill">${esc(t)}</span>`).join('');
  const history=(record.fieldHistory||[]).slice().reverse().slice(0,100).map(h=>`<div class="history-item"><b>${esc(h.path)}</b> · ${esc(h.kind||'value')}<br><span class="detail-sub">${date(h.at)} · ${esc(h.source||'')}</span></div>`).join('');
  app.innerHTML=`<article class="detail"><div class="detail-head"><div class="detail-art">${avatar?`<img src="${esc(avatar)}" alt="">`:''}</div><div><div class="detail-sub">${esc(record.id)}</div><h1>${esc(bestField(record,'name')||bestField(record,'title')||'Unknown bot')}</h1><div class="detail-sub">${bestField(record,'creator_username')?`@${esc(bestField(record,'creator_username'))}`:'Unknown creator'}</div><div class="pill-row"><span class="pill">${esc(status)}</span>${record.avatarArchive?.path?'<span class="pill">image archived</span>':''}<span class="pill">first seen ${date(record.firstSeenAt)}</span><span class="pill">last seen ${date(record.lastSeenAt)}</span></div><div class="pill-row">${tags}</div><p>${esc(bestField(record,'title')||'')}</p></div></div>
  <section class="section"><h2>Last-known archived fields</h2><p class="detail-sub">A field remains here after SpicyChat stops exposing it. This does not mean the field is still currently public.</p>${fieldHtml||'<p class="detail-sub">No rich definition fields have been recovered yet.</p>'}</section>
  <section class="section"><h2>Latest metrics</h2><div class="pre">${esc(JSON.stringify(record.metrics?.latest||{},null,2))}</div></section>
  <section class="section"><h2>Availability history</h2><div class="pre">${esc(JSON.stringify(record.availabilityHistory||[],null,2))}</div></section>
  <section class="section"><h2>Field history</h2>${history||'<p class="detail-sub">No field changes recorded yet.</p>'}</section>
  <section class="section"><h2>Raw latest observations</h2><div class="pre">${esc(JSON.stringify(record.current||{},null,2))}</div></section></article>`;
}

(async()=>{try{if(page==='bot')await botPage();else await browse();}catch(e){console.error(e);app.innerHTML=`<div class="error">Could not load the archive: ${esc(e.message||e)}</div>`;}})();
