// Optional local Recovery helpers. Browser AI may be unavailable by policy,
// feature flag or hardware; offline Quick Draft/Expand never needs a model.
import {buildOfflineDraft,buildOfflineExpansion,validateSafeDraft,sensitiveRecoveryContext} from './recovery-local.js';

let browserModelBlockedReason='';
let browserAvailability=null;
const runtimeDisabled=/feature flag|gating model execution|disabled by (?:policy|administrator|browser)|not enabled|permission.?denied|notallowederror/i;

function browserHelp(message){
 return message||'Browser AI cannot execute on this device or browser configuration. Quick Draft and Quick Expand work without an AI model; External AI is optional.';
}
function removeBrokenBrowserButtons(){
 for(const button of document.querySelectorAll('[data-suggest-browser],[data-expand-browser]'))button.remove();
}
export async function reportBrowserModelStatus(){
 const el=document.querySelector('#recovery-browser-ai-status');if(!el)return;
 if(browserModelBlockedReason){
  el.textContent=browserHelp(browserModelBlockedReason);
  removeBrokenBrowserButtons();return;
 }
 const model=window.LanguageModel;
 if(!model||typeof model.create!=='function'){
  el.textContent='On-device Browser AI is not provided by this browser. Quick Draft and Quick Expand still work offline.';
  removeBrokenBrowserButtons();return;
 }
 try{
  const availability=typeof model.availability==='function'?await model.availability():'unknown';
  if(!el.isConnected)return;
  browserAvailability=availability;
  if(availability==='unavailable'){
   el.textContent='Browser AI reports that the model is unavailable. Quick Draft/Expand still work; External AI is optional.';
   removeBrokenBrowserButtons();
   return;
  }
  el.textContent=availability==='available'
   ?'On-device Browser AI reports available. Your selected recovery evidence remains in this browser.'
   :availability==='downloadable'||availability==='downloading'
    ?'Browser AI may require a model download after you click it. Quick Draft/Expand need no download.'
    :'Browser AI readiness could not be verified. If execution fails, use Quick Draft/Expand.';
 }catch(error){
  if(!el.isConnected)return;
  const explanation=String(error?.message||error);
  if(runtimeDisabled.test(explanation)){
   browserModelBlockedReason='Browser AI execution is disabled by this browser or a feature flag. The Archive cannot enable it. Quick Draft and Quick Expand still work offline.';
   removeBrokenBrowserButtons();
   el.textContent=browserModelBlockedReason;
  }else{
   el.textContent='Cannot check Browser AI availability. Quick Draft and Quick Expand still work offline.';
  }
 }
}

export function wireLocalRecovery(host,{state,getMessages,promptForField,renderSuggestions,applyDraft}){
 if(host.dataset.localRecoveryWired)return;
 host.dataset.localRecoveryWired='1';
 const message=(token,value,bad=false)=>{
  const label=host.querySelector('[data-local-status="'+token+'"]');
  if(label){label.textContent=value;label.className='recovery-status'+(bad?' is-error':'');}
 };
 const refresh=()=>{
  const card=host.querySelector('#recovery-suggestions');
  if(card)card.outerHTML=renderSuggestions();
  void reportBrowserModelStatus();
 };
 const parseToken=token=>token.startsWith('expand:')?{field:token.slice(7),kind:'expand'}:{field:token,kind:'missing'};
 const check=(field,kind)=>{
  if(!['personality','scenario','example_dialogue','greeting'].includes(field))return 'Unsupported recovery field.';
  if(kind==='expand'){
   if(!String(state.editor[field]||'').trim())return 'There is no field text to expand. Use Quick Draft for a missing field.';
  }else{
   if(field==='greeting')return 'Greetings are expanded rather than reconstructed using this control.';
   if(String(state.archiveEvidence?.[field]||'').trim())return 'An archived original exists. Use Expand instead of replacing it.';
  }
  return '';
 };
 const storeDraft=(token,data,kind)=>{
  state.drafts[token]={...data,kind};
  refresh();
  message(token,kind==='expand'
   ?'An additional passage is ready. Review it and choose Append to edited field to keep the archived original.'
   :'Draft ready. Review and choose Use this draft.');
 };
 const browserDraft=(field,kind)=>{
  const token=kind==='expand'?'expand:'+field:field;
  const issue=check(field,kind);
  if(issue){message(token,issue,true);return;}
  if(browserModelBlockedReason||browserAvailability==='unavailable'){
   message(token,browserHelp(browserModelBlockedReason||'Browser AI is unavailable.'),true);return;
  }
  const model=window.LanguageModel;
  if(!model||typeof model.create!=='function'){
   message(token,'Browser AI is unsupported. Quick Draft/Expand works without a model.',true);return;
  }
  const record=state.record,version=state.version;
  let promise;
  try{promise=model.create();}catch(error){
   const msg=String(error?.message||error);
   if(runtimeDisabled.test(msg)){
    browserModelBlockedReason='Browser AI model execution is disabled by a browser feature flag or policy. The Archive cannot override it. Quick Draft and Quick Expand work without this model.';
    refresh();
   }
   message(token,browserHelp(browserModelBlockedReason||'Browser AI could not start: '+msg),true);return;
  }
  message(token,'Starting on-device Browser AI…');
  void (async()=>{
   let session;
   try{
    session=await promise;
    const prompt=promptForField(field,getMessages(),kind).slice(0,6500);
    const result=await session.prompt(prompt+'\nUse only non-explicit established character details. Do not invent a character age or relationship.');
    if(state.record!==record||state.version!==version)return;
    const draft=String(result||'').trim().replace(/^\x60{3}(?:text)?\s*/,'').replace(/\s*\x60{3}$/,'').slice(0,12000);
    const review=validateSafeDraft(field,draft,state.archiveEvidence);
    if(!review.ok)throw Error(review.reason);
    storeDraft(token,{text:draft,method:'browser-ai',reason:kind==='expand'
     ?'Optional on-device AI addition, not an archived original. Appending changes only your editable recovery.'
     :'On-device AI draft based on selected evidence, not archived original wording.'},kind);
   }catch(error){
    if(state.record!==record)return;
    const msg=String(error?.message||error);
    if(runtimeDisabled.test(msg)){
     browserModelBlockedReason='Browser AI execution is disabled by your browser/feature flag. The website cannot enable model execution. Use Quick Draft or Quick Expand without the model.';
     refresh();
     message(token,browserModelBlockedReason,true);
    }else message(token,'Browser AI could not create a suggestion: '+msg+'. Use Quick Draft/Expand instead.',true);
   }finally{try{session?.destroy?.();}catch{}}
  })();
 };
 host.addEventListener('click',event=>{
  const target=event.target;
  const quick=target.closest('[data-suggest-quick],[data-expand-quick]');
  if(quick){
   const kind=quick.hasAttribute('data-expand-quick')?'expand':'missing';
   const field=kind==='expand'?quick.dataset.expandQuick:quick.dataset.suggestQuick;
   const token=kind==='expand'?'expand:'+field:field;
   const issue=check(field,kind);
   if(issue){message(token,issue,true);return;}
   const evidence=kind==='expand'?{...state.archiveEvidence,[field]:state.editor[field]}:state.archiveEvidence;
   const result=kind==='expand'?buildOfflineExpansion(field,evidence,{messages:getMessages()})
     :buildOfflineDraft(field,evidence,{messages:getMessages()});
   if(!result.text){message(token,result.reason||'Not enough evidence for this expansion.',true);return;}
   storeDraft(token,result,kind);
   return;
  }
  const browser=target.closest('[data-suggest-browser],[data-expand-browser]');
  if(browser){
   const kind=browser.hasAttribute('data-expand-browser')?'expand':'missing';
   browserDraft(kind==='expand'?browser.dataset.expandBrowser:browser.dataset.suggestBrowser,kind);
   return;
  }
  const accept=target.closest('[data-draft-apply]');
  if(accept){
   const token=accept.dataset.draftApply,{field,kind}=parseToken(token);
   const source=state.drafts[token],issue=check(field,kind);
   if(issue){message(token,issue,true);return;}
   if(!source)return;
   const box=host.querySelector('[data-draft-edit="'+token+'"]');
   const text=String(box?.value??source.text).trim();
   const review=validateSafeDraft(field,text,state.archiveEvidence);
   if(!review.ok){message(token,review.reason,true);return;}
   if(kind==='expand'){
    if(!window.confirm('Append this proposed addition to '+field+'? The original archived text remains preserved, but the edited field will be marked User Modified.'))return;
   }else if(String(state.editor[field]||'').trim()&&!window.confirm('Replace your current field text with this draft?'))return;
   const edited=text!==source.text;
   applyDraft(field,text,source,edited,kind);
   delete state.drafts[token];
   refresh();
   return;
  }
  const discard=target.closest('[data-draft-dismiss]');
  if(discard){delete state.drafts[discard.dataset.draftDismiss];refresh();}
 });
 void reportBrowserModelStatus();
}
