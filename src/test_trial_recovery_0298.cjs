'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFileSync(process.env.WUJI_TRIAL_TEST_PLAYBACK || path.join(__dirname, 'web/playback.js'), 'utf8');

function fixture() {
  const start = source.indexOf('  function trialRecoveryMode()');
  const end = source.indexOf('  function renderHardware()', start);
  assert.ok(start >= 0 && end > start, 'missing trial result recovery controls');

  const visits = [];
  let focused = false;
  let starts = 0;
  const elements = {
    'trial-result-actions': {hidden:true},
    'trial-recovery': {dataset:{}},
    'trial-records': {},
    'trial-dismiss': {},
    'trial-clear': {checked:false, focus(){ focused = true; }},
    'trial-start': {disabled:false, click(){ starts++; }}
  };
  const state = {
    csrf:'original-service', connection:'connected', stale:false, recording:{active:false}, glove:{busy:false},
    hardware:{active:false, stop_confirmed:true, trial_ready:true, trial_result:{accepted:false,started_unix:100}}
  };
  const context = {
    state, hardwarePending:false, ownedLease:null, matching:true, pendingStop:false,
    location:{hash:'#library'},
    window:{WujiDeviceInventory:{show(kind){ visits.push(kind); }}},
    matchesDisplayHand:()=>context.matching, currentStopPending:()=>context.pendingStop,
    get:id=>elements[id], tr:(_zh,en)=>en,
    renderHardware:()=>context.render()
  };

  vm.runInNewContext(source.slice(start,end) + '\nthis.render = renderTrialRecovery; this.recover = recoverTrial; this.dismiss = () => typeof dismissTrialFailure === "function" && dismissTrialFailure(); this.ended = result => typeof manuallyEndedTrial === "function" && manuallyEndedTrial(result);', context);
  const mode = () => {
    context.render();
    return elements['trial-result-actions'].hidden ? '' : elements['trial-recovery'].dataset.action;
  };
  return {context, state, elements, mode, visits, focused:()=>focused, starts:()=>starts};
}

test('an unsuccessful trial offers feedback while the exact hand is outside its start gates', () => {
  const f = fixture();
  for (const blocked of [{trial_ready:false}, {active:null,stop_confirmed:false}, {stop_confirmed:false}]) {
    Object.assign(f.state.hardware,{active:false,stop_confirmed:true,trial_ready:true},blocked);
    assert.equal(f.mode(),'feedback');
    f.context.recover();
    assert.equal(f.context.location.hash,'#feedback');
    assert.equal(f.starts(),0);
  }
});

test('the failed trial offers Restart and Dismiss, including when the device is offline', () => {
  const f = fixture();
  f.state.connection='error';
  f.state.stale=true;
  assert.equal(f.mode(),'devices');
  assert.equal(f.elements['trial-recovery'].textContent,'Restart');
  assert.equal(f.elements['trial-dismiss'].textContent,'Dismiss notice');
  f.context.recover();
  assert.deepEqual(f.visits,['hand']);
  assert.equal(f.starts(),0);
});

test('dismissing a failure survives cloned polls and disconnect, but a later result or service can show again', () => {
  const f = fixture();
  f.state.hardware.active=null;
  f.context.pendingStop=true;
  assert.equal(f.mode(),'feedback');
  f.context.dismiss();
  assert.equal(f.mode(),'');
  assert.equal(f.state.hardware.active,null);
  assert.equal(f.context.pendingStop,true);
  assert.equal(f.state.hardware.trial_result.accepted,false);
  f.state.hardware.trial_result={...f.state.hardware.trial_result};
  f.state.connection='disconnected';
  assert.equal(f.mode(),'');
  f.state.hardware.trial_result={...f.state.hardware.trial_result,started_unix:101};
  assert.equal(f.mode(),'devices');
  f.context.dismiss();
  assert.equal(f.mode(),'');
  f.state.csrf='different-service';
  assert.equal(f.mode(),'devices');
  assert.equal(f.starts(),0);
  assert.deepEqual(f.visits,[]);
});

test('only an explicit end from an active phase with confirmed disable is an intentional ending', () => {
  const {context} = fixture();
  const ended = {reason:'用户停止实机展示', stopped_phase:'playing', stop_confirmed:true};
  assert.equal(context.ended(ended),true);
  assert.equal(context.ended({...ended,stop_confirmed:false}),false);
  assert.equal(context.ended({...ended,reason:'实际姿态超出官方文档行程'}),false);
  assert.equal(context.ended({...ended,stopped_phase:'stop_unconfirmed'}),false);
});

test('explicit retry focuses the existing confirmation and delegates one valid start', () => {
  const f = fixture();
  assert.equal(f.mode(),'retry');
  f.context.recover();
  assert.equal(f.focused(),true);
  assert.equal(f.starts(),0);
  f.elements['trial-clear'].checked=true;
  f.elements['trial-start'].disabled=true;
  f.context.recover();
  assert.equal(f.starts(),0);
  f.elements['trial-start'].disabled=false;
  f.context.recover();
  assert.equal(f.starts(),1);
  f.context.hardwarePending=true;
  f.context.recover();
  assert.equal(f.starts(),1);
});

test('a stale link, changed selection, or running trial cannot replay a failed run', () => {
  const f = fixture();
  f.elements['trial-clear'].checked=true;
  assert.equal(f.mode(),'retry');
  f.context.matching=false;
  f.context.recover();
  assert.equal(f.starts(),0);
  assert.deepEqual(f.visits,[]);
  assert.equal(f.mode(),'devices');
  f.context.recover();
  assert.deepEqual(f.visits,['hand']);
  assert.equal(f.starts(),0);
  f.context.matching=true;
  f.state.stale=true;
  assert.equal(f.mode(),'feedback');
  f.state.stale=false;
  f.state.hardware.active=true;
  f.state.hardware.paused=true;
  assert.equal(f.mode(),'');
  f.state.hardware.active=false;
  f.state.hardware.trial_result.accepted=true;
  assert.equal(f.mode(),'');
});
