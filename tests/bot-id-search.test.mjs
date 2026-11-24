import test from 'node:test';
import assert from 'node:assert/strict';
import {exactBotId} from '../assets/bot-id-search.js';
const id='5d1ce47c-9bbe-4a31-9a30-605d8feb4b27';
const chat='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
test('exact bot ID and supported links normalize to the bot UUID',()=>{
  for(const value of [id, ' '+id.toUpperCase()+' ', 'https://spicychat.ai/chatbot/'+id,
    'https://spicychat.ai/ru/chatbot/'+id, 'https://spicychat.ai/chat/'+id+'/'+chat,
    'spicychat.ai/chat/'+id+'/'+chat, 'https://spicychatarchive.drache.uk/bot/?id='+id,
    'https://spicychatarchive.drache.uk/recovery/?id='+id+'&exclude=NTR']){
    assert.equal(exactBotId(value),id,value);
  }
});
test('normal text, malformed IDs, unrelated URLs and conversation-only paths stay ordinary searches',()=>{
  for(const value of ['', 'Dragon roommate', id.slice(0,-1), 'example.com/chatbot/'+id,
    'https://spicychat.ai/other/'+id, 'https://spicychat.ai/chat/nope/'+chat,
    'https://spicychat.ai/lorebook/'+id, 'https://spicychat.ai@evil.test/chatbot/'+id]){
    assert.equal(exactBotId(value),'',value);
  }
});
