// Dependency-free Recovery Lab helpers; no network or browser state.
import {sensitiveRecoveryContext,safeRecoveryEvidencePrompt} from './recovery-local.js';
export const NARRATIVE_FIELDS = ['greeting','personality','scenario','example_dialogue'];

export function recoveryReadiness(evidence, fields) {
  const has = key => {
    const v = evidence?.[key];
    return v != null && (typeof v === 'string' ? v.trim().length > 0 :
      Array.isArray(v) ? v.some(x => String(x ?? '').trim()) : Boolean(v));
  };
  const available = fields.filter(x => has(x.key)).map(x => x.key);
  const missing = fields.filter(x => !has(x.key)).map(x => x.key);
  const missingNarrative = NARRATIVE_FIELDS.filter(key => !has(key));
  return {
    available, missing, missingNarrative, complete: available.length, total: fields.length,
    hasConversationCriticalGaps: !has('personality') || !has('scenario') || !has('example_dialogue'),
  };
}

export function evidenceWarnings(evidence, conversation) {
  const sources = ['name','title','greeting','personality','scenario']
    .map(key => String(evidence?.[key] ?? '')).join(' ');
  const warnings = [];
  if (/\b(?:high school|secondary school|middle school|underage|minor|child)\b/i.test(sources) ||
      /\b\d{1,2}\s*(?:years? old|y\/o)\b/i.test(sources)) {
    warnings.push('Age or school-setting references appear in the archived text. Review the original context carefully; do not guess or silently alter ages.');
  }
  if (/\b(?:adopted sister|step-sister|step sister|little sister|younger sister|step-family)\b/i.test(sources)) {
    warnings.push('Family/relationship references appear in the archived text. Preserve their factual wording and review the context before generating anything.');
  }
  if (!conversation || !conversation.messages?.length) {
    warnings.push('No conversation evidence loaded. Missing fields can only be suggested from limited archived material, not recovered word-for-word.');
  }
  return warnings;
}

export function conversationPreview(messages, botName, limit = 6) {
  const sample = (messages || []).filter(x => x && x.text && !x.ooc).slice(0,limit);
  const names = [...new Set((messages || []).filter(x => x.role === 'character' && x.speaker)
    .map(x => String(x.speaker).trim()).filter(Boolean))];
  const norm = s => String(s || '').trim().toLowerCase();
  const mismatch = names.length > 0 && botName &&
    !names.some(x => norm(x) === norm(botName));
  return { sample, names, mismatch };
}

export function suggestionForField(key, evidence, conversation) {
  const instructions = {
    personality: 'Suggest a distinctive personality, voice, mannerisms, motivations and boundaries supported by the character’s actual archived actions/dialogue. Distinguish observable traits from speculation.',
    scenario: 'Suggest a scenario grounded in the opening setting and relationships already established. Avoid introducing unsupported lore, backstory or ages.',
    example_dialogue: 'Suggest a few short example exchanges matching the archived greeting’s voice. These are invented examples, never historical dialogue.'
  };
  const title = String(evidence?.title || '').trim();
  const greeting = String(evidence?.greeting || '').trim();
  const persona = String(evidence?.personality || '').trim();
  const sample = (conversation?.messages || []).filter(m=>m.role === 'character' && !m.ooc)
    .slice(0,3).map(m => m.text.slice(0,550)).join('\n');
  const sensitive=sensitiveRecoveryContext(evidence);
  return {
    label: ({personality:'Personality',scenario:'Scenario',example_dialogue:'Example Dialogue'})[key] || key,
    instruction: instructions[key] || 'Suggest this missing field based only on supporting evidence.',
    clues: sensitive.restricted
      ? safeRecoveryEvidencePrompt(evidence,conversation).split('\n')
      : [
      title ? 'Archived title: '+title.slice(0,260) : '',
      greeting ? 'Archived greeting excerpt: '+greeting.slice(0,580) : '',
      persona && key !== 'personality' ? 'Archived personality excerpt: '+persona.slice(0,300) : '',
      sample ? 'Selected character conversation excerpts: '+sample.slice(0,1200) : ''
    ].filter(Boolean),
  };
}

export function fieldSuggestionPrompt(key, evidence, conversation) {
  const idea = suggestionForField(key, evidence, conversation);
  const restricted=sensitiveRecoveryContext(evidence).restricted;
  return [
    'Create ONE proposed SpicyChat '+idea.label+' field for a recovered character.',
    'This is a RECONSTRUCTION SUGGESTION, not original archived wording.',
    restricted
      ?'NON-EXPLICIT ONLY: This archived record contains school-age ambiguity or family relationships alongside sexual themes. Write only everyday, non-romantic character interactions. Do not mention sexual anatomy, erotic content, romance, dating, intimacy, or invent an age. The evidence below is a safe summary, NOT raw original text.'
      :'Preserve the original character voice and established relationships without inventing facts.',
    idea.instruction,
    'Do not invent age, identity, consent, or backstory details unsupported by the evidence.',
    'If evidence is insufficient, say so instead of inventing certainty.',
    'Return only the suggested field text (no surrounding explanation).',
    ...idea.clues.map(x => '\n'+x)
  ].join('\n');
}

export function cardV2Payload(fields, context = {}) {
  return {
    spec: 'chara_card_v2', spec_version: '2.0',
    data: {
      name: fields.name || '',
      description: fields.description || '',
      personality: fields.personality || '',
      scenario: fields.scenario || '',
      first_mes: fields.greeting || '',
      mes_example: fields.example_dialogue || '',
      tags: Array.isArray(fields.tags) ? fields.tags : [],
      creator_notes: 'Recovered using SpicyChat Archive Recovery Lab. Reconstructed text is not an archived original.',
      extensions: {
        spicychat_archive_recovery: {
          bot_id: context.botId || '',
          archive_base: context.archiveBase || 'latest',
          archived_title: fields.title || '',
          provenance: {...context.provenance},
          confidence: {...context.confidence},
          image_url: fields.image || ''
        }
      }
    }
  };
}

// Tavern-format PNG chara tEXt chunk, inserted after IHDR. Preserves source image pixels.
// PNG has signature(8), IHDR length(4)+type(4)+data(13)+CRC(4).
function crc32(bytes) {
  let c = 0xffffffff;
  for (const byte of bytes) {
    c ^= byte;
    for (let i=0;i<8;i++)c = (c >>> 1) ^ ((c & 1) ? 0xedb88320 : 0);
  }
  return (c ^ 0xffffffff) >>> 0;
}
export function packCardPng(pngBytes, base64Card) {
  const data = pngBytes instanceof Uint8Array ? pngBytes : new Uint8Array(pngBytes);
  const signature=[137,80,78,71,13,10,26,10];
  if (data.length < 33 || !signature.every((value,i)=>data[i]===value)) throw Error('Image is not a valid PNG.');
  const view=new DataView(data.buffer,data.byteOffset,data.byteLength);
  const ihdr=view.getUint32(8);
  if (ihdr!==13 || String.fromCharCode(...data.slice(12,16))!=='IHDR')throw Error('Missing PNG IHDR chunk.');
  const where=8+12+ihdr;
  const payload=new TextEncoder().encode('chara\0'+base64Card);
  const chunk=new Uint8Array(payload.length+12);
  const cv=new DataView(chunk.buffer);
  cv.setUint32(0,payload.length);
  chunk.set([116,69,88,116],4); // tEXt
  chunk.set(payload,8);
  cv.setUint32(8+payload.length,crc32(chunk.slice(4,8+payload.length)));
  const out=new Uint8Array(data.length+chunk.length);
  out.set(data.slice(0,where));out.set(chunk,where);out.set(data.slice(where),where+chunk.length);
  return out;
}
