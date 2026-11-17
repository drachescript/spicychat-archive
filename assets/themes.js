// Public Archive tag explorer. Loads small vocabulary on demand; never scans bots.
const root=document.querySelector('#theme-app');
const qs=s=>document.querySelector(s);
const URLS={
  taxonomy:'/data/community-tags/taxonomy.json',
  rules:'/data/community-tags/rules.json',
  api:'https://spicychat-archive-import.dragongraf.workers.dev/api/community-tags/lookup',
  live:'https://spicychat-archive-import.dragongraf.workers.dev/api/community-tags/manifest'
};
const UUID=/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const pretty=n=>Number(n||0).toLocaleString();
const el=(tag,text,className)=>{const x=document.createElement(tag);if(text!=null)x.textContent=text;if(className)x.className=className;return x;};
let tags=[];
function showTags(){
  const word=String(qs('#theme-search').value||'').toLowerCase().trim();
  const scope=qs('#theme-scope').value;
  const matched=tags.filter(t=>(scope==='all'||t.scopes.includes(scope)) &&
    (!word||t.label.toLowerCase().includes(word)||t.id.includes(word)));
  const container=qs('#theme-tag-list');container.replaceChildren();
  const fragment=document.createDocumentFragment();
  for(const t of matched){
    const line=el('div',null,'theme-tag-row');
    line.append(el('b',t.label),el('span',t.kind==='official'?'Official catalog':'Community / extended'));
    line.append(el('small',t.scopes.join(' · ')));
    fragment.append(line);
  }
  container.append(fragment);
  qs('#theme-results-count').textContent=pretty(matched.length)+' matching tags/themes';
}
function showSignals(result){
  const container=qs('#theme-lookup-result');container.replaceChildren();
  if(!result.ok){
    container.append(el('p','Lookup is currently unavailable. Nothing was classified as safe.', 'error'));
    return;
  }
  const id=String(qs('#theme-bot-id').value||'').trim().toLowerCase();
  const data=result.matches?.[id];
  if(!data){
    container.append(el('p','No reviewed, inferred, or historical signals recorded for this ID. This means UNKNOWN — not safe, and not evidence that no theme applies.','filter-note'));
    return;
  }
  container.append(el('p','Last-known Archive status: '+data.status+'. This is not a live availability check.','filter-note'));
  const list=el('div',null,'theme-signal-list');
  for(const s of data.signals||[]){
    const wrap=el('article',null,'theme-signal');
    const concept=tags.find(t=>t.id===s.tagId);
    wrap.append(el('b',(concept?.label||s.tagId)+' · '+s.confidence),
      el('p',s.reason||'Public metadata signal'));
    if(s.kind==='historical-tag')wrap.append(el('small','Historical tag only. Not proof of a current theme.'));
    if(s.kind==='rule-candidate')wrap.append(el('small','Unverified deterministic title match. Treat as a warning only.'));
    if(s.kind==='reviewed'){
      wrap.append(el('small','Reviewed '+(s.reviewedAt||'')+'. Metadata revisions may invalidate this verdict.'));
      if(s.evidenceUrl){const a=el('a','See public evidence');a.href=s.evidenceUrl;a.rel='noopener noreferrer';a.target='_blank';wrap.append(a);}
    }
    list.append(wrap);
  }
  container.append(list);
}
async function load(){
  try{
    const [catalog,rules]=await Promise.all([
      fetch(URLS.taxonomy).then(r=>{if(!r.ok)throw Error('taxonomy');return r.json();}),
      fetch(URLS.rules).then(r=>{if(!r.ok)throw Error('rules');return r.json();})
    ]);
    tags=Array.isArray(catalog.tags)?catalog.tags:[];
    qs('#theme-count').textContent=pretty(tags.length)+' distinct tag/theme concepts · '+pretty(rules.rules?.length)+' conservative inference rules';
    qs('#theme-search').addEventListener('input',showTags);
    qs('#theme-scope').addEventListener('change',showTags);
    showTags();
  }catch(err){qs('#theme-count').textContent='Tag catalog unavailable — '+err.message;}
  try{
    const res=await fetch(URLS.live);
    const live=await res.json();
    qs('#theme-index-status').textContent=live.ok?
      pretty(live.coverage?.indexedBots)+' bot records examined · '+pretty(live.coverage?.signalBots)+' with sparse evidence signals · indexed '+new Date(live.generatedAt).toLocaleString():
      'Live bot-ID evidence index is being prepared or temporarily unavailable.';
  }catch{qs('#theme-index-status').textContent='Live bot-ID evidence index temporarily unavailable.';}
}
qs('#theme-lookup').addEventListener('submit',async e=>{
  e.preventDefault();
  const id=qs('#theme-bot-id').value.trim().toLowerCase();
  if(!UUID.test(id)){qs('#theme-lookup-result').textContent='Enter a valid bot UUID.';return;}
  const button=qs('#theme-lookup button');button.disabled=true;
  qs('#theme-lookup-result').textContent='Checking public Archive signals…';
  try{
    const response=await fetch(URLS.api,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[id]})
    });
    const body=await response.json();
    showSignals(body);
  }catch{qs('#theme-lookup-result').textContent='Archive signal lookup unavailable; classification remains UNKNOWN.';}
  finally{button.disabled=false;}
});
load();
