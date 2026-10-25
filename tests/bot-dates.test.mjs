import test from 'node:test';
import assert from 'node:assert/strict';
import {botTimestamp,reportedBotTimestamp} from '../assets/bot-dates.js';

const now=Date.parse('2026-10-25T23:00:00Z');
const iso='2026-10-11T20:42:00.000Z';
const seconds=Date.parse(iso)/1000;

test('accepts SpicyChat timestamps in ISO, Unix seconds, milliseconds and digit strings',()=>{
  for(const value of [iso,seconds,seconds*1000,String(seconds),String(seconds*1000)]){
    assert.equal(botTimestamp(value,now),iso);
  }
});

test('rejects absent, implausible, invalid and future dates',()=>{
  for(const value of [null,'',0,'0',false,'unknown','not-a-date',Date.UTC(1970,0,1),Date.parse('2027-12-01T00:00:00Z')]){
    assert.equal(botTimestamp(value,now),null);
  }
});

test('uses only actual created/published fields, not firstSeenAt or updatedAt',()=>{
  const record={
    firstSeenAt:'2026-10-20T01:00:00Z',
    lastKnown:{updatedAt:Date.parse('2026-10-24T12:00:00Z'),createdAt:seconds},
    current:{'character-api':{publishedAt:'2026-10-12T05:00:00Z'}}
  };
  assert.equal(reportedBotTimestamp(record,['createdAt','created_at'],now),iso);
  assert.equal(reportedBotTimestamp(record,['publishedAt','published_at'],now),'2026-10-12T05:00:00.000Z');
  assert.equal(reportedBotTimestamp({firstSeenAt:iso,lastKnown:{updatedAt:seconds}},['publishedAt','published_at'],now),null);
});

test('reads creation dates from historical source observations when lastKnown has no value',()=>{
  const rec={lastKnown:{},current:{typesense:{createdAt:String(seconds)}}};
  assert.equal(reportedBotTimestamp(rec,['createdAt','created_at'],now),iso);
});
