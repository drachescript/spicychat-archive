const app=document.querySelector('#app');
const params=new URLSearchParams(location.search);
const DEFAULT_DATA_BASE='https://data.spicychatarchive.drache.uk';
const FIELD_DEFS=[
  {key:'name',label:'Name',aliases:['name'],kind:'input'},
  {key:'title',label:'Title',aliases:['title'],kind:'input'},
  {key:'description',label:'Description',aliases:['description'],kind:'textarea'},
  {key:'greeting',label:'Greeting',aliases:['greeting','greetings','first_mes','first_message'],kind:'tall'},
  {key:'personality',label:'Personality',aliases:['persona','personality','definition','character_definition','characterDefinition'],kind:'tall'},
  {key:'scenario',label:'Scenario',aliases:['scenario'],kind:'tall'},
  {key:'example_dialogue',label:'Example Dialogue',aliases:['dialogue','example_dialogue','example_dialogues','mes_example'],kind:'tall'},
  {key:'tags',label:'Tags',aliases:['tags'],kind:'input'},
  {key:'image',label:'Image URL',aliases:['avatar_url','avatar','image'],kind:'input'}
];
const FIELD_BY_KEY=Object.fromEntries(FIELD_DEFS.map(x=>[x.key,x]));
const state={runtime:null,record:null,botId:'',version:'latest',archiveEvidence:{},editor:{},provenance:{},generated:{},generatedProvenance:{},confidence:{},conversation:null,prompt:'',mode:'original'};

function esc(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
function meaningful(value){if(value==null)return false;if(typeof value==='string')return value.trim()!=='';if(Array.isArray(value))return value.some(meaningful);if(typeof value==='object')return Object.values(value).some(meaningful);return true;}
function textValue(value){if(value==null)return '';if(Array.isArray(value))return value.map(textValue).filter(Boolean).join(', ');if(typeof value==='object')return JSON.stringify(value);return String(value);}
function fmt(value){const n=Number(value||0);return Number.isFinite(n)?n.toLocaleString():'0';}
function shortDate(value){const d=new Date(value);return Number.isFinite(d.getTime())?d.toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Unknown';}
function normalizeId(value){const m=String(value||'').toLowerCase().match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);return m?m[0]:'';}
function leafPath(path){const bits=String(path||'').split('.');return bits[bits.length-1]||'';}
function base(runtime){return String(runtime?.publicDataBaseUrl||DEFAULT_DATA_BASE).replace(/\/$/,'');}
function archiveBotUrl(runtime,id){const compact=String(id||'').replaceAll('-','').toLowerCase(),prefix=compact.slice(0,2)||'__';return base(runtime)+'/bots/'+prefix+'/'+encodeURIComponent(String(id||'').toLowerCase())+'.json';}
async function fetchJson(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok){const e=new Error('HTTP '+r.status);e.status=r.status;throw e;}return r.json();}
async function fetchJsonRetry404(url){try{return await fetchJson(url);}catch(e){if(e?.status!==404)throw e;const u=new URL(url,location.href);u.searchParams.set('_fresh',Date.now().toString(36));return fetchJson(u.toString());}}
async function loadRuntime(){if(state.runtime)return state.runtime;try{state.runtime=await fetchJson('../data/runtime.json');}catch{state.runtime={publicDataBaseUrl:DEFAULT_DATA_BASE};}return state.runtime;}

function bestField(record,aliases){
  const last=record?.lastKnown&&typeof record.lastKnown==='object'?record.lastKnown:{};
  for(const key of aliases)if(meaningful(last[key]))return last[key];
  const current=record?.current&&typeof record.current==='object'?record.current:{};
  for(const source of Object.values(current)){if(!source||typeof source!=='object'||Array.isArray(source))continue;for(const key of aliases)if(meaningful(source[key]))return source[key];}
  return '';
}
function fieldForHistoryPath(path){const leaf=leafPath(path);return FIELD_DEFS.find(def=>def.aliases.includes(leaf))||null;}
function evidenceAtVersion(record,version){
  const values={};for(const def of FIELD_DEFS)values[def.key]=bestField(record,def.aliases);
  if(version==='latest')return values;
  const target=new Date(version).getTime();if(!Number.isFinite(target))return values;
  const rows=(record?.fieldHistory||[]).slice().filter(x=>x?.at).sort((a,b)=>new Date(b.at)-new Date(a.at));
  for(const row of rows){const at=new Date(row.at).getTime();if(!Number.isFinite(at)||at<=target)continue;const def=fieldForHistoryPath(row.path);if(!def)continue;values[def.key]=row.from??'';}
  return values;
}
function versionChoices(record){
  const times=[...new Set((record?.fieldHistory||[]).map(x=>x?.at).filter(Boolean))].sort((a,b)=>new Date(b)-new Date(a));
  const rows=[{value:'latest',label:'Latest archived evidence'}];
  for(const at of times.slice(0,120))rows.push({value:at,label:shortDate(at)});
  if(record?.firstSeenAt&&!times.includes(record.firstSeenAt))rows.push({value:record.firstSeenAt,label:'First captured · '+shortDate(record.firstSeenAt)});
  return rows;
}
function imageUrl(record,evidence){const archived=record?.avatarArchive?.publicUrl;if(archived)return archived;return textValue(evidence.image||'');}
function resetEditorFromEvidence(){
  state.generated={};state.generatedProvenance={};state.confidence={};state.editor={};state.provenance={};
  for(const def of FIELD_DEFS){const raw=state.archiveEvidence[def.key];state.editor[def.key]=def.key==='tags'&&Array.isArray(raw)?raw.join(', '):textValue(raw);state.provenance[def.key]=meaningful(raw)?'archived':'missing';}
}

function setStatus(text,type){const el=document.querySelector('#recovery-status');if(!el)return;el.textContent=text||'';el.className='recovery-status'+(type?' is-'+type:'');}
function renderLanding(){
  app.innerHTML='<section class="hero recovery-hero"><div><h1>Archive Recovery Lab</h1><p>Recover a lost SpicyChat character from exact archived evidence, then reconstruct missing pieces from one of your old conversations or turn the recovered character into your own version.</p></div><aside class="recovery-hero-note"><b>Original archive data always wins.</b><span>Fields the archive actually captured stay labeled as archived originals. Anything inferred from conversation behavior is labeled reconstructed, and intentional edits are labeled user modified.</span><span>Your conversation files are parsed locally in this browser. They are not uploaded to the Archive.</span></aside></section>'+
    '<section class="recovery-shell"><article class="recovery-card"><h2>Choose a bot</h2><p>Paste a SpicyChat character URL or UUID. Opening Recovery Lab from an archived bot page fills this automatically.</p><div class="recovery-load-row"><input id="recovery-id" placeholder="SpicyChat bot URL or UUID" autocomplete="off"><button class="primary-button" id="recovery-load" type="button">Load archive evidence</button></div><div class="recovery-status" id="recovery-status"></div></article><div id="recovery-workspace"></div></section>';
  const input=document.querySelector('#recovery-id');input.value=params.get('id')||'';
  document.querySelector('#recovery-load').addEventListener('click',()=>void loadBot(input.value));
  input.addEventListener('keydown',e=>{if(e.key==='Enter')void loadBot(input.value);});
  if(normalizeId(input.value))void loadBot(input.value);
}

async function loadBot(raw){
  const id=normalizeId(raw);if(!id){setStatus('Enter a valid SpicyChat bot URL or UUID.','error');return;}
  setStatus('Loading archived bot evidence…');
  try{
    const runtime=await loadRuntime();const record=await fetchJsonRetry404(archiveBotUrl(runtime,id));
    state.record=record;state.botId=id;state.version=params.get('at')||'latest';
    const validVersions=new Set(versionChoices(record).map(x=>x.value));if(!validVersions.has(state.version))state.version='latest';
    state.archiveEvidence=evidenceAtVersion(record,state.version);resetEditorFromEvidence();state.conversation=null;state.prompt='';
    const u=new URL(location.href);u.searchParams.set('id',id);if(state.version==='latest')u.searchParams.delete('at');history.replaceState(null,'',u);
    setStatus('Archive evidence loaded.','good');renderWorkspace();
  }catch(e){setStatus(e?.status===404?'That UUID does not have a detailed archived record yet.':'Could not load archive record: '+(e.message||e),'error');}
}

function evidenceStateLabel(def,value){if(meaningful(value))return 'Archived original';return 'Not archived';}
function renderEvidence(){
  const record=state.record,evidence=state.archiveEvidence,creator=textValue(bestField(record,['creator_username','creator']));const img=imageUrl(record,evidence);
  const status=record?.status?.current||'unknown';const versions=versionChoices(record);
  return '<article class="recovery-card"><div class="recovery-bot-head"><div class="recovery-bot-art">'+(img?'<img src="'+esc(img)+'" alt="">':'BOT')+'</div><div class="recovery-bot-meta"><div class="sub">'+esc(state.botId)+'</div><h2>'+esc(textValue(evidence.name)||'Archived bot')+'</h2><div class="sub">'+(creator?'@'+esc(creator)+' · ':'')+esc(status)+' · first captured '+esc(shortDate(record?.firstSeenAt))+'</div><p>'+esc(textValue(evidence.title)||'')+'</p><div class="recovery-action-row"><a class="secondary-button" href="../bot/?id='+encodeURIComponent(state.botId)+'">Open archive record</a><a class="secondary-button" href="https://spicychat.ai/chatbot/'+encodeURIComponent(state.botId)+'" target="_blank" rel="noopener">Try SpicyChat ↗</a></div></div></div>'+
    '<div class="recovery-version-row"><label for="recovery-version">Base recovery on</label><select class="recovery-control" id="recovery-version">'+versions.map(x=>'<option value="'+esc(x.value)+'"'+(x.value===state.version?' selected':'')+'>'+esc(x.label)+'</option>').join('')+'</select></div>'+
    '<div class="recovery-evidence-grid">'+FIELD_DEFS.map(def=>{const value=evidence[def.key];return '<div class="recovery-evidence-item '+(meaningful(value)?'has-value':'missing')+'"><b>'+esc(def.label)+'</b><span>'+esc(evidenceStateLabel(def,value))+'</span></div>';}).join('')+'</div></article>';
}

function renderConversationSummary(){
  const c=state.conversation;if(!c)return '<div class="recovery-status">No conversation loaded yet.</div>';
  return '<div class="recovery-summary"><div><b>'+fmt(c.messages.length)+'</b><span>messages</span></div><div><b>'+fmt(c.counts.character)+'</b><span>character</span></div><div><b>'+fmt(c.counts.user)+'</b><span>user</span></div><div><b>'+fmt(c.counts.ooc)+'</b><span>OOC / system</span></div><div><b>'+fmt(c.counts.regen)+'</b><span>possible regenerations</span></div></div><div class="recovery-status is-good">Loaded '+esc(c.sourceName||'conversation')+(c.detectedGreeting?' · opening character message detected':'')+'.</div>';
}
function renderConversationCard(){
  return '<article class="recovery-card"><h2>Add conversation evidence</h2><p>Use a QoL export, S.AI Toolkit export, JSON/HTML transcript, Markdown, plain text, or paste the conversation directly. Parsing happens locally.</p><div class="recovery-input-grid"><div><label class="recovery-drop" id="recovery-drop"><input type="file" id="recovery-file" accept=".json,.html,.htm,.txt,.md,application/json,text/html,text/plain"><b>Drop a conversation export here</b><span>or click to choose a file</span></label></div><div><textarea class="recovery-textarea" id="recovery-paste" placeholder="Or paste conversation text / JSON here"></textarea><div class="recovery-action-row"><button class="secondary-button" id="recovery-use-paste" type="button">Use pasted conversation</button><button class="secondary-button" id="recovery-clear-chat" type="button">Clear conversation</button></div></div></div><div id="recovery-conversation-summary">'+renderConversationSummary()+'</div><div class="recovery-options"><label class="recovery-check"><input type="checkbox" id="recovery-include-user" checked> Use user messages as relationship/context evidence</label><label class="recovery-check"><input type="checkbox" id="recovery-include-ooc"> Include OOC/system/directive messages</label><label class="recovery-check"><input type="checkbox" id="recovery-include-regen"> Include possible regenerated/swiped replies</label></div><p class="recovery-privacy">Nothing in this section is sent to the Archive. If you later use a direct AI endpoint, only the selected archive evidence and filtered conversation sample are sent to that endpoint.</p></article>';
}

function renderRecoveryControls(){
  return '<article class="recovery-card"><h2>Recovery approach</h2><div class="recovery-mode-grid"><label class="recovery-mode '+(state.mode==='original'?'is-active':'')+'"><div><input type="radio" name="recovery-mode" value="original" '+(state.mode==='original'?'checked':'')+'><b>Reconstruct Original</b></div><p>Archived original fields are locked as evidence. Missing fields are inferred as closely as possible from the conversation.</p></label><label class="recovery-mode '+(state.mode==='personal'?'is-active':'')+'"><div><input type="radio" name="recovery-mode" value="personal" '+(state.mode==='personal'?'checked':'')+'><b>Make It Mine</b></div><p>Use the recovered character as a base, then intentionally adapt it to your instructions.</p></label></div><label style="display:grid;gap:6px;margin-top:12px"><span style="font-size:12px;color:var(--muted)">Optional instructions</span><textarea class="recovery-textarea" id="recovery-instructions" placeholder="Examples: keep her personality but make the scenario AnyPOV; make him less aggressive; preserve everything except the relationship setup."></textarea></label><div class="recovery-action-row"><button class="primary-button" id="recovery-build-prompt" type="button">Build reconstruction prompt</button><button class="secondary-button" id="recovery-copy-prompt" type="button">Copy prompt</button><button class="secondary-button" id="recovery-download-prompt" type="button">Download prompt</button></div><details class="recovery-ai"><summary>Direct AI reconstruction (optional)</summary><p>Connect any browser-accessible chat-completions-compatible endpoint. Endpoint/model can be remembered by the browser; the API key is kept only in this page session and is never stored by the Archive.</p><div class="recovery-ai-grid"><label>Endpoint<input class="recovery-control" id="recovery-ai-endpoint" placeholder="https://…/v1/chat/completions"></label><label>Model<input class="recovery-control" id="recovery-ai-model" placeholder="model name"></label><label>API key<input class="recovery-control" id="recovery-ai-key" type="password" placeholder="optional for local endpoints"></label></div><div class="recovery-action-row"><button class="primary-button" id="recovery-run-ai" type="button">Reconstruct with AI</button></div><div class="recovery-status" id="recovery-ai-status"></div></details><div id="recovery-prompt-wrap" class="recovery-hidden"><h3 style="margin-top:16px">Prompt preview</h3><div class="recovery-prompt-preview" id="recovery-prompt-preview"></div></div></article>';
}

function provenanceLabel(value){return value==='archived'?'Archived Original':value==='reconstructed'?'Reconstructed':value==='modified'?'User Modified':'Missing';}
function provenanceClass(value){return value==='archived'?'archived':value==='reconstructed'?'reconstructed':value==='modified'?'modified':'missing';}
function renderEditor(){
  const fields=FIELD_DEFS.map(def=>{const value=state.editor[def.key]??'';const prov=state.provenance[def.key]||'missing';const conf=state.confidence[def.key];const control=def.kind==='input'?'<input data-field="'+esc(def.key)+'" value="'+esc(value)+'">':'<textarea data-field="'+esc(def.key)+'" class="'+(def.kind==='tall'?'is-tall':'')+'">'+esc(value)+'</textarea>';return '<div class="recovery-field"><div class="recovery-field-head"><div class="recovery-field-title"><b>'+esc(def.label)+'</b><span class="recovery-badge '+provenanceClass(prov)+'">'+esc(provenanceLabel(prov))+'</span></div><button class="recovery-reset" type="button" data-reset-field="'+esc(def.key)+'">Reset</button></div>'+control+(conf?'<div class="recovery-confidence">Confidence: '+esc(String(conf.level||conf))+(conf.reason?' · '+esc(conf.reason):'')+'</div>':'')+'</div>';}).join('');
  return '<article class="recovery-card"><h2>Edit & export</h2><p>Everything is editable. Changing an archived or reconstructed value marks that field as User Modified. Exports preserve the provenance labels so inferred text is never presented as an original creator field.</p><div class="recovery-editor" id="recovery-editor">'+fields+'</div><div class="recovery-output-actions"><button class="primary-button" id="recovery-export-package" type="button">Download recovery package</button><button class="secondary-button" id="recovery-export-card" type="button">Download character-card JSON</button><button class="secondary-button" id="recovery-export-fields" type="button">Download field JSON</button><button class="secondary-button" id="recovery-copy-fields" type="button">Copy fields</button></div></article>';
}

function renderWorkspace(){
  const host=document.querySelector('#recovery-workspace');if(!host||!state.record)return;
  host.innerHTML=renderEvidence()+renderConversationCard()+renderRecoveryControls()+renderEditor();
  bindWorkspace();
}
function bindWorkspace(){
  document.querySelector('#recovery-version')?.addEventListener('change',e=>{state.version=e.target.value;state.archiveEvidence=evidenceAtVersion(state.record,state.version);resetEditorFromEvidence();state.prompt='';const u=new URL(location.href);if(state.version==='latest')u.searchParams.delete('at');else u.searchParams.set('at',state.version);history.replaceState(null,'',u);renderWorkspace();});
  const file=document.querySelector('#recovery-file'),drop=document.querySelector('#recovery-drop');
  file?.addEventListener('change',()=>{if(file.files?.[0])void consumeConversationFile(file.files[0]);});
  drop?.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('is-drag');});drop?.addEventListener('dragleave',()=>drop.classList.remove('is-drag'));drop?.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('is-drag');const f=e.dataTransfer?.files?.[0];if(f)void consumeConversationFile(f);});
  document.querySelector('#recovery-use-paste')?.addEventListener('click',()=>{const text=document.querySelector('#recovery-paste')?.value||'';if(text.trim())consumeConversationText(text,'pasted conversation');});
  document.querySelector('#recovery-clear-chat')?.addEventListener('click',()=>{state.conversation=null;const box=document.querySelector('#recovery-conversation-summary');if(box)box.innerHTML=renderConversationSummary();});
  for(const el of document.querySelectorAll('input[name="recovery-mode"]'))el.addEventListener('change',e=>{state.mode=e.target.value;for(const card of document.querySelectorAll('.recovery-mode'))card.classList.toggle('is-active',card.querySelector('input')?.checked);});
  document.querySelector('#recovery-build-prompt')?.addEventListener('click',()=>buildAndShowPrompt());
  document.querySelector('#recovery-copy-prompt')?.addEventListener('click',async()=>{if(!state.prompt)buildAndShowPrompt();if(state.prompt)await copyText(state.prompt,'Prompt copied.');});
  document.querySelector('#recovery-download-prompt')?.addEventListener('click',()=>{if(!state.prompt)buildAndShowPrompt();if(state.prompt)downloadBlob('spicychat-recovery-'+state.botId+'-prompt.txt',state.prompt,'text/plain;charset=utf-8');});
  const ep=document.querySelector('#recovery-ai-endpoint'),model=document.querySelector('#recovery-ai-model');if(ep)ep.value=localStorage.getItem('sca-recovery-ai-endpoint')||'';if(model)model.value=localStorage.getItem('sca-recovery-ai-model')||'';
  document.querySelector('#recovery-run-ai')?.addEventListener('click',()=>void runAiReconstruction());
  for(const input of document.querySelectorAll('[data-field]'))input.addEventListener('input',e=>updateEditedField(e.target.dataset.field,e.target.value));
  for(const btn of document.querySelectorAll('[data-reset-field]'))btn.addEventListener('click',()=>resetOneField(btn.dataset.resetField));
  document.querySelector('#recovery-export-package')?.addEventListener('click',exportRecoveryPackage);document.querySelector('#recovery-export-card')?.addEventListener('click',exportCharacterCard);document.querySelector('#recovery-export-fields')?.addEventListener('click',exportFields);document.querySelector('#recovery-copy-fields')?.addEventListener('click',()=>void copyFields());
}

function normalizeRole(raw,speaker,botName,item){
  if(item?.is_user===true||item?.isUser===true)return 'user';if(item?.is_user===false||item?.isUser===false)return 'character';
  const role=String(raw||'').toLowerCase();if(['assistant','ai','bot','character','char'].includes(role))return 'character';if(['user','human','you','me'].includes(role))return 'user';if(['system','ooc','directive'].includes(role))return 'system';
  const s=String(speaker||'').trim().toLowerCase(),b=String(botName||'').trim().toLowerCase();if(b&&s===b)return 'character';if(['user','you','me','human'].includes(s))return 'user';return 'unknown';
}
function messageContent(item){
  if(!item||typeof item!=='object')return '';let value=item.content??item.message??item.text??item.mes??item.body??item.value??item.response??'';
  if(Array.isArray(value))value=value.map(x=>typeof x==='string'?x:(x?.text??x?.content??'')).join('\n');if(value&&typeof value==='object')value=value.text??value.content??'';return String(value||'').trim();
}
function isMessageLike(item){return !!(item&&typeof item==='object'&&!Array.isArray(item)&&messageContent(item));}
function findMessageArray(root){
  let best=null,bestScore=0;const seen=new Set();
  function walk(value,depth){if(depth>7||value==null||typeof value!=='object'||seen.has(value))return;seen.add(value);if(Array.isArray(value)){const count=value.filter(isMessageLike).length;const score=count*3+(count===value.length?count:0);if(count>=2&&score>bestScore){best=value;bestScore=score;}for(const item of value.slice(0,3000))walk(item,depth+1);return;}for(const [key,v] of Object.entries(value)){if(['messages','history','conversation','chat','items','turns'].includes(key)&&Array.isArray(v)){const count=v.filter(isMessageLike).length,score=count*4;if(count>=2&&score>bestScore){best=v;bestScore=score;}}walk(v,depth+1);}}
  walk(root,0);return best||[];
}
function oocText(text,role){return role==='system'||/^\s*(?:\[|\()?\s*(?:ooc|system|creator note|directive)\b/i.test(String(text||''));}
function normalizeMessages(items,source){
  const botName=textValue(state.archiveEvidence.name||bestField(state.record,['name']));const out=[];let previous='';
  for(const item of items){if(!item||typeof item!=='object')continue;const text=messageContent(item);if(!text)continue;const speaker=String(item.name??item.author??item.speaker??item.sender_name??item.senderName??'').trim();const role=normalizeRole(item.role??item.type??item.sender??item.from,speaker,botName,item);const explicitRegen=!!(item.is_regeneration||item.isRegeneration||item.regenerated||item.is_alternative||item.isAlternative||Number(item.swipe_id||item.swipeId||item.variant_index||item.variantIndex||0)>0);const sig=role+'\u0000'+text;const duplicate=sig===previous;previous=sig;out.push({role,speaker,text,ooc:oocText(text,role),regen:explicitRegen||duplicate,timestamp:item.createdAt||item.created_at||item.timestamp||item.time||'',source});}
  return out;
}
function parseJsonConversation(text,source){const data=JSON.parse(text);const arr=findMessageArray(data);if(!arr.length)throw new Error('No message array was found in that JSON export.');return normalizeMessages(arr,source);}
function parseHtmlConversation(text,source){
  const doc=new DOMParser().parseFromString(text,'text/html');
  for(const script of doc.querySelectorAll('script[type="application/json"],script#__NEXT_DATA__')){const raw=script.textContent||'';if(raw.trim().length<4)continue;try{const arr=findMessageArray(JSON.parse(raw));if(arr.length>=2)return normalizeMessages(arr,source);}catch{}}
  const selectors=['[data-message-id]','[data-message-role]','[data-role].message','.chat-message','.message','.mes'];let nodes=[];for(const sel of selectors){const found=[...doc.querySelectorAll(sel)];if(found.length>=2){nodes=found;break;}}
  if(!nodes.length)throw new Error('No recognizable chat messages were found in that HTML export.');
  const botName=textValue(state.archiveEvidence.name||bestField(state.record,['name']));const out=[],seen=new Set();
  for(const node of nodes){const contentNode=node.querySelector('[data-message-content],.message-content,.mes_text,.markdown,.prose,.content')||node;const text=String(contentNode.textContent||'').trim();if(!text||text.length<1)continue;const speakerNode=node.querySelector('[data-speaker],.message-name,.mes_name,.speaker,.name');const speaker=String(speakerNode?.textContent||node.dataset?.speaker||node.dataset?.name||'').trim();const rawRole=node.dataset?.messageRole||node.dataset?.role||node.getAttribute('data-author-role')||'';const role=normalizeRole(rawRole,speaker,botName,{});const sig=role+'\u0000'+text;if(seen.has(sig))continue;seen.add(sig);out.push({role,speaker,text,ooc:oocText(text,role),regen:/regen|swipe|alternative/i.test(node.className||''),timestamp:node.getAttribute('datetime')||'',source});}
  if(out.length<2)throw new Error('The HTML looked like a chat export, but fewer than two usable messages were found.');return out;
}
function parseTextConversation(text,source){
  const lines=String(text||'').replace(/\r/g,'').split('\n');const rows=[];let current=null;
  const prefix=/^\s*(?:\*\*)?([^:\n]{1,60})(?:\*\*)?\s*:\s*(.*)$/;
  for(const line of lines){const m=line.match(prefix);if(m){if(current&&current.text.trim())rows.push(current);current={speaker:m[1].trim(),text:m[2],role:'unknown'};}else if(current){current.text+='\n'+line;}}if(current&&current.text.trim())rows.push(current);
  if(rows.length>=2){const botName=textValue(state.archiveEvidence.name||bestField(state.record,['name']));return rows.map(x=>({role:normalizeRole('',x.speaker,botName,{}),speaker:x.speaker,text:x.text.trim(),ooc:oocText(x.text,'unknown'),regen:false,timestamp:'',source}));}
  const blocks=String(text||'').split(/\n\s*\n+/).map(x=>x.trim()).filter(Boolean);if(blocks.length<2)throw new Error('Could not detect message boundaries. Use JSON/HTML export or speaker-prefixed text such as User: … and Character: ….');
  return blocks.map(x=>({role:'unknown',speaker:'',text:x,ooc:oocText(x,'unknown'),regen:false,timestamp:'',source}));
}
function summarizeConversation(messages,sourceName){
  const counts={character:0,user:0,ooc:0,regen:0,unknown:0};for(const m of messages){if(m.role==='character')counts.character++;else if(m.role==='user')counts.user++;else counts.unknown++;if(m.ooc)counts.ooc++;if(m.regen)counts.regen++;}
  const firstCharacter=messages.find(m=>m.role==='character'&&!m.ooc);return {messages,counts,sourceName,detectedGreeting:!!firstCharacter};
}
async function consumeConversationFile(file){try{const text=await file.text();consumeConversationText(text,file.name);}catch(e){const box=document.querySelector('#recovery-conversation-summary');if(box)box.innerHTML='<div class="recovery-status is-error">Could not read export: '+esc(e.message||e)+'</div>';}}
function consumeConversationText(text,sourceName){
  try{let messages;const trimmed=String(text||'').trim();if(!trimmed)throw new Error('The conversation is empty.');if(trimmed[0]==='{'||trimmed[0]==='[')messages=parseJsonConversation(trimmed,sourceName);else if(/^<!doctype html|^<html|<body[\s>]/i.test(trimmed))messages=parseHtmlConversation(trimmed,sourceName);else messages=parseTextConversation(trimmed,sourceName);state.conversation=summarizeConversation(messages,sourceName);const box=document.querySelector('#recovery-conversation-summary');if(box)box.innerHTML=renderConversationSummary();state.prompt='';}catch(e){const box=document.querySelector('#recovery-conversation-summary');if(box)box.innerHTML='<div class="recovery-status is-error">Could not parse conversation: '+esc(e.message||e)+'</div>';}
}

function selectedConversationMessages(){
  if(!state.conversation)return [];const includeUser=document.querySelector('#recovery-include-user')?.checked!==false,includeOoc=!!document.querySelector('#recovery-include-ooc')?.checked,includeRegen=!!document.querySelector('#recovery-include-regen')?.checked;
  return state.conversation.messages.filter(m=>{if(m.role==='user'&&!includeUser)return false;if(m.ooc&&!includeOoc)return false;if(m.regen&&!includeRegen)return false;return m.role==='character'||m.role==='user'||(includeOoc&&(m.role==='system'||m.ooc));});
}
function truncate(text,max){const s=String(text||'');return s.length<=max?s:s.slice(0,max)+'\n[…truncated '+fmt(s.length-max)+' characters…]';}
function sampleConversation(messages,maxMessages=140,maxChars=90000){
  if(messages.length<=maxMessages){let used=0,out=[];for(const m of messages){const line='['+m.role.toUpperCase()+(m.speaker?' '+m.speaker:'')+'] '+m.text;if(used+line.length>maxChars)break;out.push(line);used+=line.length;}return out.join('\n\n');}
  const keep=new Set();for(let i=0;i<25&&i<messages.length;i++)keep.add(i);for(let i=Math.max(0,messages.length-25);i<messages.length;i++)keep.add(i);const middleSlots=maxMessages-keep.size;for(let n=0;n<middleSlots;n++){const idx=Math.floor(25+(n+1)*(Math.max(1,messages.length-50)/(middleSlots+1)));if(idx<messages.length-25)keep.add(idx);}const chosen=[...keep].sort((a,b)=>a-b).map(i=>messages[i]);return sampleConversation(chosen,chosen.length,maxChars);
}
function buildPrompt(){
  state.mode=document.querySelector('input[name="recovery-mode"]:checked')?.value||state.mode||'original';const instructions=String(document.querySelector('#recovery-instructions')?.value||'').trim();const messages=selectedConversationMessages();
  const archived=FIELD_DEFS.map(def=>{const value=state.archiveEvidence[def.key];if(!meaningful(value))return def.label+': [NOT ARCHIVED]';return def.label+' [ARCHIVED ORIGINAL]:\n'+truncate(def.key==='tags'&&Array.isArray(value)?value.join(', '):textValue(value),def.kind==='tall'?24000:8000);}).join('\n\n');
  const modeRules=state.mode==='original'?['Goal: reconstruct the original bot as closely as evidence allows.','Every ARCHIVED ORIGINAL field is exact evidence. Copy it exactly and do not rewrite it.','Infer only fields marked NOT ARCHIVED. If conversation behavior conflicts with archived evidence, archived evidence wins.','Do not claim inferred wording came from the original creator.']:['Goal: make a personalized derivative using the archived character as the base.','Treat archived fields as factual source material, but you may modify them when the user instructions call for it.','Keep the character recognizable unless the instructions explicitly request a larger change.','Any field changed from archived evidence must be treated as user-modified, not original.'];
  const conversation=messages.length?sampleConversation(messages):'[NO CONVERSATION EVIDENCE PROVIDED]';
  const summary=state.conversation?'Loaded conversation: '+state.conversation.messages.length+' total messages; '+state.conversation.counts.character+' character, '+state.conversation.counts.user+' user, '+state.conversation.counts.ooc+' OOC/system, '+state.conversation.counts.regen+' possible regenerations.':'No conversation export was loaded.';
  state.prompt=['You are reconstructing a SpicyChat character from archive evidence and private conversation evidence.','Never blur the difference between archived original text and inference.','',...modeRules,'','Return ONLY valid JSON with this shape:','{"fields":{"name":"","title":"","description":"","greeting":"","personality":"","scenario":"","example_dialogue":"","tags":[],"image":""},"confidence":{"personality":{"level":"high|medium|low","reason":""}},"notes":[""]}','Confidence is about how strongly the evidence supports an inferred field. Do not assign confidence to exact archived fields.','',instructions?'USER CUSTOMIZATION INSTRUCTIONS:\n'+instructions:'USER CUSTOMIZATION INSTRUCTIONS: [NONE]','','ARCHIVE BASE: '+(state.version==='latest'?'latest archived evidence':shortDate(state.version)),'BOT UUID: '+state.botId,'',archived,'','CONVERSATION SUMMARY: '+summary,'','CONVERSATION EVIDENCE:','---',conversation,'---'].join('\n');
  return state.prompt;
}
function buildAndShowPrompt(){const prompt=buildPrompt();const wrap=document.querySelector('#recovery-prompt-wrap'),box=document.querySelector('#recovery-prompt-preview');if(wrap)wrap.classList.remove('recovery-hidden');if(box)box.textContent=prompt;return prompt;}

async function copyText(text,success){try{await navigator.clipboard.writeText(String(text||''));setStatus(success||'Copied.','good');}catch{setStatus('Clipboard access was blocked by the browser.','error');}}
function downloadBlob(name,text,type){const blob=new Blob([text],{type:type||'text/plain;charset=utf-8'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
function parseAiPayload(data){
  if(data&&typeof data==='object'&&data.fields)return data;let raw=data?.choices?.[0]?.message?.content??data?.output_text??data?.response??data?.content??data;if(Array.isArray(raw))raw=raw.map(x=>x?.text??x?.content??'').join('');if(typeof raw==='object'&&raw)return raw;let text=String(raw||'').trim().replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,'');const first=text.indexOf('{'),last=text.lastIndexOf('}');if(first>=0&&last>first)text=text.slice(first,last+1);return JSON.parse(text);
}
async function runAiReconstruction(){
  const status=document.querySelector('#recovery-ai-status'),endpoint=String(document.querySelector('#recovery-ai-endpoint')?.value||'').trim(),model=String(document.querySelector('#recovery-ai-model')?.value||'').trim(),key=String(document.querySelector('#recovery-ai-key')?.value||'').trim();
  if(!endpoint||!model){if(status){status.textContent='Enter an endpoint and model first.';status.className='recovery-status is-error';}return;}
  let url;try{url=new URL(endpoint);if(!['http:','https:'].includes(url.protocol))throw new Error();}catch{if(status){status.textContent='Endpoint must be a valid http(s) URL.';status.className='recovery-status is-error';}return;}
  localStorage.setItem('sca-recovery-ai-endpoint',endpoint);localStorage.setItem('sca-recovery-ai-model',model);const prompt=buildAndShowPrompt();if(status){status.textContent='Sending selected evidence to your AI endpoint…';status.className='recovery-status';}
  try{const headers={'Content-Type':'application/json'};if(key)headers.Authorization='Bearer '+key;const body={model,messages:[{role:'system',content:'Return only the requested JSON. Preserve all fields explicitly labeled ARCHIVED ORIGINAL unless the requested mode permits intentional customization.'},{role:'user',content:prompt}],temperature:state.mode==='original'?0.2:0.55};const r=await fetch(endpoint,{method:'POST',headers,body:JSON.stringify(body)});if(!r.ok)throw new Error('HTTP '+r.status+' '+(await r.text()).slice(0,300));const payload=parseAiPayload(await r.json());applyAiResult(payload);if(status){status.textContent='Reconstruction applied. Review every inferred field before exporting.';status.className='recovery-status is-good';}}catch(e){if(status){status.textContent='AI request failed: '+(e.message||e)+'. If the endpoint blocks browser CORS, use Copy prompt instead.';status.className='recovery-status is-error';}}
}
function normalizeGeneratedValue(def,value){if(def.key==='tags'){if(Array.isArray(value))return value.join(', ');return String(value||'');}return textValue(value);}
function applyAiResult(payload){
  const fields=payload?.fields&&typeof payload.fields==='object'?payload.fields:payload?.card&&typeof payload.card==='object'?payload.card:payload;state.generated={};state.generatedProvenance={};state.confidence=payload?.confidence&&typeof payload.confidence==='object'?payload.confidence:{};
  for(const def of FIELD_DEFS){let value=fields?.[def.key];if(value==null){for(const alias of def.aliases){if(fields&&fields[alias]!=null){value=fields[alias];break;}}}const archived=state.archiveEvidence[def.key],hasArchive=meaningful(archived);if(state.mode==='original'&&hasArchive)value=def.key==='tags'&&Array.isArray(archived)?archived.join(', '):textValue(archived);if(value==null)value=hasArchive?(def.key==='tags'&&Array.isArray(archived)?archived.join(', '):textValue(archived)):'';const normalized=normalizeGeneratedValue(def,value);state.editor[def.key]=normalized;state.generated[def.key]=normalized;if(hasArchive&&normalized===(def.key==='tags'&&Array.isArray(archived)?archived.join(', '):textValue(archived))){state.provenance[def.key]='archived';state.generatedProvenance[def.key]='archived';}else if(normalized){state.provenance[def.key]=state.mode==='personal'?'modified':'reconstructed';state.generatedProvenance[def.key]=state.provenance[def.key];}else{state.provenance[def.key]='missing';state.generatedProvenance[def.key]='missing';}}
  const editorCard=document.querySelector('#recovery-editor')?.closest('.recovery-card');if(editorCard){editorCard.outerHTML=renderEditor();bindEditorOnly();}
}
function archiveTextForKey(key){const def=FIELD_BY_KEY[key],raw=state.archiveEvidence[key];return def?.key==='tags'&&Array.isArray(raw)?raw.join(', '):textValue(raw);}
function updateEditedField(key,value){state.editor[key]=value;const archived=archiveTextForKey(key);if(meaningful(state.archiveEvidence[key])&&value===archived)state.provenance[key]='archived';else if(state.generated[key]!=null&&value===state.generated[key])state.provenance[key]=state.generatedProvenance[key]||'reconstructed';else state.provenance[key]=value?'modified':'missing';const field=document.querySelector('[data-field="'+CSS.escape(key)+'"]')?.closest('.recovery-field');if(field){const badge=field.querySelector('.recovery-badge');if(badge){badge.className='recovery-badge '+provenanceClass(state.provenance[key]);badge.textContent=provenanceLabel(state.provenance[key]);}}}
function resetOneField(key){const value=state.generated[key]!=null?state.generated[key]:archiveTextForKey(key);state.editor[key]=value||'';state.provenance[key]=state.generated[key]!=null?(state.generatedProvenance[key]||'reconstructed'):(meaningful(state.archiveEvidence[key])?'archived':'missing');const input=document.querySelector('[data-field="'+CSS.escape(key)+'"]');if(input)input.value=state.editor[key];updateEditedField(key,state.editor[key]);}
function bindEditorOnly(){for(const input of document.querySelectorAll('[data-field]'))input.addEventListener('input',e=>updateEditedField(e.target.dataset.field,e.target.value));for(const btn of document.querySelectorAll('[data-reset-field]'))btn.addEventListener('click',()=>resetOneField(btn.dataset.resetField));document.querySelector('#recovery-export-package')?.addEventListener('click',exportRecoveryPackage);document.querySelector('#recovery-export-card')?.addEventListener('click',exportCharacterCard);document.querySelector('#recovery-export-fields')?.addEventListener('click',exportFields);document.querySelector('#recovery-copy-fields')?.addEventListener('click',()=>void copyFields());}

function editorFieldObject(){const out={};for(const def of FIELD_DEFS){let value=state.editor[def.key]??'';if(def.key==='tags')value=String(value).split(',').map(x=>x.trim()).filter(Boolean);out[def.key]=value;}return out;}
function conversationSummaryForExport(){const c=state.conversation;if(!c)return null;return {sourceName:c.sourceName,messageCount:c.messages.length,counts:c.counts,detectedGreeting:c.detectedGreeting};}
function exportRecoveryPackage(){const payload={schemaVersion:1,kind:'spicychat-archive-recovery-package',createdAt:new Date().toISOString(),botId:state.botId,archiveBase:state.version,mode:state.mode,fields:editorFieldObject(),provenance:{...state.provenance},confidence:state.confidence,conversationSummary:conversationSummaryForExport(),privacy:'Conversation message text is intentionally not included in this package.'};downloadBlob('spicychat-recovery-'+state.botId+'.json',JSON.stringify(payload,null,2)+'\n','application/json;charset=utf-8');}
function exportFields(){const payload={botId:state.botId,fields:editorFieldObject(),provenance:{...state.provenance},confidence:state.confidence};downloadBlob('spicychat-recovery-'+state.botId+'-fields.json',JSON.stringify(payload,null,2)+'\n','application/json;charset=utf-8');}
function exportCharacterCard(){const f=editorFieldObject();const payload={spec:'chara_card_v2',spec_version:'2.0',data:{name:f.name||'',description:f.description||'',personality:f.personality||'',scenario:f.scenario||'',first_mes:f.greeting||'',mes_example:f.example_dialogue||'',tags:Array.isArray(f.tags)?f.tags:[],creator_notes:'Recovered with SpicyChat Archive Recovery Lab. Check provenance metadata before treating reconstructed text as original.',extensions:{spicychat_archive_recovery:{bot_id:state.botId,archive_base:state.version,provenance:{...state.provenance},image_url:f.image||''}}}};downloadBlob('character-'+(f.name||state.botId).replace(/[^a-z0-9._-]+/gi,'_')+'.json',JSON.stringify(payload,null,2)+'\n','application/json;charset=utf-8');}
async function copyFields(){const f=editorFieldObject();const lines=[];for(const def of FIELD_DEFS){lines.push(def.label+' ['+provenanceLabel(state.provenance[def.key])+']\n'+(Array.isArray(f[def.key])?f[def.key].join(', '):f[def.key]||''));}await copyText(lines.join('\n\n'),'Recovered fields copied.');}

renderLanding();