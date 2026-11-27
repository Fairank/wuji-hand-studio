'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'web/glove.js'), 'utf8');
const start = source.indexOf(' function maybeOpenFollowingTerminal(');
const end = source.indexOf(' function render()', start);
assert.ok(start >= 0 && end > start);

function fixture() {
  const calls = [];
  const lease = 'a'.repeat(32);
  const glove = {session_id:7,action_revision:8,busy:true,connection:'receiving',hardware:{active:null},
    follow_pending:true,follow_unconfirmed:false,stream:{fresh:true},
    feedback:{device_id:'hand',connection:'connected',stale:false}};
  const scope = {
    terminalFollow:{operation:1,choice:2,csrf:'local',source:'current',hand:'hand',lease},
    operationVersion:1,selectionVersion:2,lease,leaseSession:7,leaseRevision:8,
    state:{csrf:'local'},online:true,realTerminalView:()=>true,
    sourceIdentity:()=> 'current',openSystemTerminal:automatic=>calls.push(automatic),
  };
  vm.runInNewContext(source.slice(start,end)+'\nthis.maybeOpen=maybeOpenFollowingTerminal;',scope);
  return {calls,glove,scope};
}

test('a pending follow with the exact stop record can later open the terminal once', () => {
  const {scope,glove,calls}=fixture();
  scope.maybeOpen(glove,true,true);
  assert.ok(scope.terminalFollow);
  assert.equal(calls.length,0);
  glove.hardware.active=true;
  glove.follow_pending=false;
  glove.feedback.stale=true;
  scope.maybeOpen(glove,false,true);
  assert.equal(calls.length,0);
  glove.feedback.stale=false;
  scope.maybeOpen(glove,false,true);
  scope.maybeOpen(glove,false,true);
  assert.deepEqual(calls,[true]);
});

test('an ended or superseded follow cannot reopen from an old confirmation', () => {
  for(const retire of [f=>{f.glove.follow_unconfirmed=true;}, f=>{f.scope.operationVersion++;},
    f=>{f.glove.action_revision++;}, f=>{f.scope.realTerminalView=()=>false;}]) {
    const f=fixture();
    retire(f);
    f.scope.maybeOpen(f.glove,false,true);
    assert.equal(f.scope.terminalFollow,null);
    assert.deepEqual(f.calls,[]);
  }
});
