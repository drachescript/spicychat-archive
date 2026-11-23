import {evaluateBlockPreset} from './block-preset-matcher.js';
const get=id=>document.getElementById(id);
const elem=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;};
let catalog,selected='franchise:call-of-duty',discovered=[];
function preview(){
  if(!catalog)return;
  const name=get('preview-name').value,mode=get('preview-mode').value;
  const r=evaluateBlockPreset(catalog,selected,name,{mode});
  const box=get('preview-result');box.replaceChildren();
  box.append(elem('strong',!name.trim()?'Enter a bot name or title to test.':
    !r.matched?'No confident match — keep visible.':
    r.hide?'MATCH: would hide when enabled.':'MATCH: kept by exception or uncertain identity.'));
  if(r.matched){
    box.append(elem('p',r.reason));
    if(r.characterIds?.length)box.append(elem('small','Recognized characters: '+r.characterIds.join(', ')));
  }
}
function render(){
  if(!catalog)return;
  const term=get('preset-search').value.trim().toLowerCase();
  const list=get('preset-list');list.replaceChildren();
  for(const p of catalog.presets){
    if(term&&!String(p.name+' '+p.category).toLowerCase().includes(term))continue;
    const b=elem('button',p.name,'preset-select'+(selected===p.id?' active':''));
    b.type='button';b.addEventListener('click',()=>{selected=p.id;render();});
    list.append(b);
  }
  const p=catalog.presets.find(x=>x.id===selected);if(!p)return;
  get('preset-title').textContent=p.name;
  get('preset-description').textContent=p.kind==='franchise'?
    'Franchise · '+p.category+' · '+p.characterIds.length+' curated character identities':
    'Optional theme title-keyword filter';
  get('preview-mode').disabled=p.kind!=='franchise';
  const chars=get('characters');chars.replaceChildren();
  if(p.kind==='franchise'){
    for(const c of catalog.characters.filter(c=>c.franchiseId===p.franchiseId))
      chars.append(elem('span',c.name+' · '+c.gender,'preset-character'));
  }
  preview();
}
function renderCandidates() {
  const query=get('candidate-search').value.trim().toLowerCase();
  const labels=new Map((catalog?.presets||[]).map(p=>[p.id,p.name]));
  const shown=discovered.filter(row=>!query||
    String((labels.get(row.presetId)||row.presetId)+' '+row.candidateText).toLowerCase().includes(query));
  const target=get('candidate-queue');
  target.replaceChildren();
  get('candidate-count').textContent=shown.length+' of '+discovered.length+
    ' review-only suggestions. None are active aliases.';
  for(const candidate of shown.slice(0,60)) {
    const card=elem('article',null,'preset-candidate');
    const franchise=labels.get(candidate.presetId)||candidate.presetId;
    card.append(elem('strong',franchise+' · '+candidate.candidateText),
      elem('small',Number(candidate.observedTitleCount||0).toLocaleString()+
        ' public title observations · NOT a verified character or active block'));
    const example=Array.isArray(candidate.examples)?candidate.examples[0]:null;
    if(typeof example?.botId==='string' && /^[0-9a-f-]{36}$/i.test(example.botId)) {
      const a=elem('a','View a public Archive example');
      a.href='/bot/?id='+encodeURIComponent(example.botId);
      a.target='_blank';a.rel='noopener noreferrer';card.append(a);
    }
    const review=elem('a','Suggest review or correction');
    review.href='https://github.com/drachescript/spicychat-archive/issues/new?template=block-preset-alias.yml&title='+
      encodeURIComponent(('[Preset review] '+franchise+': '+candidate.candidateText).slice(0,110));
    review.rel='noopener noreferrer';review.target='_blank';card.append(review);
    target.append(card);
  }
  if(shown.length>60)target.append(elem('p','Only the first 60 are shown. Refine the search to see others.'));
  if(!shown.length)target.append(elem('p','No matching suggestions are available.'));
}
async function init(){
  try{
    const r=await fetch('/data/block-presets/catalog.json');
    if(!r.ok)throw Error('HTTP '+r.status);
    const data=await r.json();
    if(data.schemaVersion!==1||!Array.isArray(data.presets))throw Error('unsupported catalog');
    catalog=data;
    get('status').textContent=data.presets.length+' opt-in presets · '+data.characters.length+' curated character identities';
    get('preset-search').addEventListener('input',render);
    get('preview-name').addEventListener('input',preview);
    get('preview-mode').addEventListener('change',preview);
    render();
  }catch(e){get('status').textContent='Cannot load catalog: '+e.message;}
  try{
    const r=await fetch('https://data.spicychatarchive.drache.uk/indexes/block-presets/discovery.json');
    if(!r.ok)throw Error('unavailable');
    const d=await r.json();
    discovered=Array.isArray(d.reviewCandidates)?d.reviewCandidates:[];
    get('discovery').textContent=Number(d.coverage?.creatorIndexedBotsExamined||0).toLocaleString()+
      ' indexed Archive bots examined · '+discovered.length+' candidate patterns for review'+
      (d.generatedAt?' · last updated '+new Date(d.generatedAt).toLocaleDateString():'');
    get('candidate-search').addEventListener('input',renderCandidates);
    renderCandidates();
  }catch{
    get('discovery').textContent='Weekly pattern-discovery index is not yet available.';
    get('candidate-count').textContent='Review candidates unavailable; existing presets still work.';
  }
}
init();