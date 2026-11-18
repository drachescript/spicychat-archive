import {evaluateBlockPreset} from './block-preset-matcher.js';
const get=id=>document.getElementById(id);
const elem=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;};
let catalog,selected='franchise:call-of-duty';
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
    get('discovery').textContent=Number(d.coverage?.creatorIndexedBotsExamined||0).toLocaleString()+
      ' indexed Archive bots examined · '+(d.reviewCandidates?.length||0)+' candidate patterns awaiting review';
  }catch{get('discovery').textContent='Weekly pattern-discovery index is not yet available.';}
}
init();