// Lightweight previews for the inconclusive-review page.
// Detailed archive records are fetched only for cards the visitor actually views.
import {validBotId} from './inconclusive-records.js';

export const DEFAULT_ARCHIVE_DATA_BASE='https://data.spicychatarchive.drache.uk';

function safeHttps(value){
  if(typeof value!=='string'||!value.trim())return '';
  try{
    const u=new URL(value.trim());
    return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';
  }catch{return '';}
}
function baseUrl(value){
  return safeHttps(value)||DEFAULT_ARCHIVE_DATA_BASE;
}
export function archivedBotRecordUrl(id,base=DEFAULT_ARCHIVE_DATA_BASE){
  const botId=validBotId(id);
  if(!botId)return '';
  const prefix=botId.replaceAll('-','').slice(0,2);
  return baseUrl(base).replace(/\/$/,'')+'/bots/'+prefix+'/'+botId+'.json';
}
export function resolveArchivedAvatar(value,base=DEFAULT_ARCHIVE_DATA_BASE){
  if(typeof value!=='string')return '';
  const v=value.trim();
  if(!v)return '';
  if(v.startsWith('//'))return safeHttps('https:'+v);
  if(/^https:\/\//i.test(v))return safeHttps(v);
  const clean=v.replace(/^\/+/, '');
  if(/^avatars\/[a-z0-9_./?=&%-]+$/i.test(clean)){
    return 'https://cdn.nd-api.com/'+clean+(clean.includes('?')?'':'?class=avatar256x256');
  }
  if(/^media\/[a-z0-9_./?=&%-]+$/i.test(clean)){
    return baseUrl(base).replace(/\/$/,'')+'/'+clean;
  }
  return '';
}
function text(value){
  return typeof value==='string'?value.trim().slice(0,240):'';
}
export function archivedBotPreview(record,base=DEFAULT_ARCHIVE_DATA_BASE){
  if(!record||typeof record!=='object'||Array.isArray(record))return null;
  const known=record.lastKnown&&typeof record.lastKnown==='object'?record.lastKnown:{};
  const archived=record.avatarArchive&&typeof record.avatarArchive==='object'?record.avatarArchive:{};
  const imageFallback=resolveArchivedAvatar(known.avatar_url||known.avatar||known.image,base);
  const storedImage=resolveArchivedAvatar(archived.publicUrl||archived.r2Key||archived.path,base);
  const name=text(known.name||known.title);
  const subtitle=text(known.title);
  const creator=text(known.creator_username||known.creator);
  return {
    name:name&&name.toLowerCase()!=='unknown bot'?name:'',
    subtitle:subtitle!==name?subtitle:'',
    creator,
    image:storedImage||imageFallback,
    fallbackImage:storedImage&&imageFallback!==storedImage?imageFallback:''
  };
}
