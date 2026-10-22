import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';

const app = fs.readFileSync(new URL('../assets/recovery.js',import.meta.url),'utf8');
const assistant = fs.readFileSync(new URL('../assets/recovery-assistant.js',import.meta.url),'utf8');
const storageFns = app.slice(app.indexOf('function recoveryStorageGet('),app.indexOf('function markPromptDirty('));

test('blocked browser storage does not interrupt Recovery Lab initialization',()=>{
  const window={get localStorage(){throw new DOMException('Access denied','SecurityError');}};
  const {recoveryStorageGet,recoveryStorageSet}=vm.runInNewContext(storageFns+
    '\n({recoveryStorageGet,recoveryStorageSet})',{window,DOMException});
  assert.equal(recoveryStorageGet('sca-recovery-ai-endpoint'),'');
  assert.equal(recoveryStorageSet('sca-recovery-ai-model','test-model'),false);
});

test('optional browser storage is readable/writable where allowed',()=>{
  const saved=new Map([['sca-recovery-ai-endpoint','https://local.example/v1/chat/completions']]);
  const window={localStorage:{getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value)}};
  const {recoveryStorageGet,recoveryStorageSet}=vm.runInNewContext(storageFns+
    '\n({recoveryStorageGet,recoveryStorageSet})',{window});
  assert.equal(recoveryStorageGet('sca-recovery-ai-endpoint'),'https://local.example/v1/chat/completions');
  assert.equal(recoveryStorageSet('sca-recovery-ai-model','test'),true);
  assert.equal(saved.get('sca-recovery-ai-model'),'test');
});

test('essential field/export actions initialize before optional helpers',()=>{
  const render=app.slice(app.indexOf('function renderWorkspace(){'),app.indexOf('function normalizeRole('));
  assert.ok(render.indexOf('bindEditorOnly();')>=0);
  assert.ok(render.indexOf('bindEditorOnly();')<render.indexOf('bindWorkspace();'));
  assert.match(render,/try\s*\{\s*wireLocalRecovery\(/);
  assert.match(render,/catch\s*\(error\)\s*\{\s*console\.error/);
  assert.doesNotMatch(render,/localStorage\.getItem\(/);
  assert.match(render,/recoveryStorageGet\('sca-recovery-ai-endpoint'\)/);
  assert.match(render,/recoveryStorageGet\('sca-recovery-ai-model'\)/);
  for(const name of [
    'recovery-export-package','recovery-export-card','recovery-export-fields',
    'recovery-export-png','recovery-preview-export','recovery-copy-fields'
  ])assert.match(app,new RegExp(name));
});

test('sensitive bot UI clearly explains automatic writing is unavailable without dead buttons',()=>{
  const render=app.slice(app.indexOf('function renderSuggestions(){'),app.indexOf('function updatePrivacyPreview(){'));
  assert.doesNotMatch(render,/disabled title="Automatic expansion/);
  assert.match(render,/sensitive\.blocked\s*\?/);
  assert.match(render,/Automatic writing is unavailable for this field/);
  assert.match(render,/Copy prompt/);
  assert.match(render,/data-auto-draft-disabled/);
  assert.match(assistant,/if\(el\.dataset\.autoDraftDisabled==='1'\)return/);
});
