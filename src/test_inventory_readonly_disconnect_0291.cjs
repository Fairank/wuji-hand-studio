/* Exact server-owned readonly reader eligibility, never a motor-state shortcut. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const prelude=fs.readFileSync(path.join(__dirname,'test_inventory_hand_disconnect_0279.cjs'),'utf8').split('const reply=')[0];
const context={require,__dirname,AbortSignal};vm.runInNewContext(prelude+'\nthis.fixture=fixture;',context);
function fixture(){const f=context.fixture();f.state.hardware.stop_confirmed=false;f.state.hand_feedback_session={source:'owned_feedback_reader',serial:f.state.device_id,session:f.state.session_generation,profile:f.state.device_profile.id,can_disconnect:true,exit_confirmed:false};return f;}
const reply=()=>({ok:true,json:async()=>({ok:true})});

test('an exact current readonly reader can use the original explicit disconnect without an unnecessary motor stop',async()=>{
 const f=fixture(),pending=f.c.disconnect();assert.equal(f.calls.length,1);
 assert.deepEqual(JSON.parse(f.calls[0].options.body),{name:'hand_feedback_disconnect',expected_owner:'a'.repeat(64),serial:f.state.device_id,expected_session:12});
 await f.c.disconnect();assert.equal(f.calls.length,1);f.pending[0](reply());await pending;assert.equal(f.c.using,false);
});
test('a different reader, model, epoch or refused eligibility never bypasses unconfirmed stop',async()=>{
 for(const mutate of [f=>delete f.state.hand_feedback_session,f=>f.state.hand_feedback_session.serial='WH_OTHER',f=>f.state.hand_feedback_session.session=11,f=>f.state.hand_feedback_session.source='history',f=>f.state.hand_feedback_session.profile='hand2_left',f=>f.state.hand_feedback_session.can_disconnect=false]){
  const f=fixture();mutate(f);await f.c.disconnect();assert.equal(f.calls.length,0);
 }
});
test('server reader eligibility cannot authorize active or unknown motors or another live activity',async()=>{
 for(const mutate of [f=>f.state.hardware.active=true,f=>f.state.hardware.active=null,f=>f.state.recording.active=true,f=>f.state.program.active=true,f=>f.state.group.active=true,f=>f.state.glove={busy:true},f=>f.state.hardware_control_id='current-control',f=>f.state.unconfirmed_devices=[{device_id:'WH_OTHER'}]]){
  const f=fixture();mutate(f);await f.c.disconnect();assert.equal(f.calls.length,0);
 }
});
