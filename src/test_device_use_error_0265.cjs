/* Real shared purpose coordinator; synthetic HTTP only, no device or native SDK. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync(process.env.DEVICE_USE_ERROR_SOURCE||path.join(__dirname,'web/device_workflows.js'),'utf8');
const A='WG_SYNTHETIC_ERROR_A',B='WG_SYNTHETIC_ERROR_B',TOKEN='synthetic-error-service',BASE='a'.repeat(64),NEXT='b'.repeat(64),OTHER='d'.repeat(32),ATTEMPT='e'.repeat(32);
const copy=v=>JSON.parse(JSON.stringify(v));
function ownerState(serial=A,user='person-A',session=44){return {csrf:TOKEN,connection:'disconnected',hardware:{active:false},device_profile:{id:'hand2_right',generation:'hand2',side:'right'},glove:{busy:true,connection:'receiving',session_id:session,glove_serial:serial,user:{user_id:user},profile:'hand2_right'},official_viz:{active:false},connection_switch:{can_switch:true,saved_profile_supported:true,busy:false,phase:'connected',error:null,request_id:OTHER,attempt_id:ATTEMPT,expected_owner:NEXT,current_owner:{mode:'teleoperation',kind:'glove',serial,session,user_id:user,profile:'hand2_right',purpose:'teleoperation'},receipt:{version:1,request_id:OTHER,attempt_id:ATTEMPT,expected_owner:BASE,after_context:NEXT,target:{mode:'teleoperation',serial,user_id:user,profile:'hand2_right'},sdk_session:session}}};}
function fixture(initial=null){
 let state={csrf:TOKEN,connection:'disconnected',hardware:{active:false},device_profile:{id:'hand2_right',generation:'hand2',side:'right'},glove:{busy:false,connection:'disconnected'},official_viz:{backend:'windows_native',active:true,phase:'running',session_id:'c'.repeat(32),reported_device:{sn:A,kind:'glove'}},connection_switch:{can_switch:true,saved_profile_supported:true,busy:false,phase:'idle',expected_owner:BASE,current_owner:{mode:'official',kind:'glove',serial:A,session:'c'.repeat(32),purpose:'cli_view'}}};
 if(initial)state=copy(initial);
 const calls=[],listeners={},pending=[];const window={WujiDeviceInventory:{setUseHandler:()=>{},selection:()=>({kind:'glove',serial:B}),show:()=>{}},WujiWorkspace:{showPage:()=>{}},WujiConnectionHub:{show:()=>{}},WujiOfficialViz:{showDeviceMode:()=>{},selectDevice:async()=>{}},addEventListener:(n,f)=>(listeners[n]??=[]).push(f),removeEventListener:()=>{},dispatchEvent:e=>{for(const f of listeners[e.type]||[])f(e);}};
 const context={window,document:{documentElement:{dataset:{}},querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null},location:{hash:'#home'},crypto:crypto.webcrypto,CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}},Event:class{constructor(type){this.type=type;}},AbortSignal,AbortController,setInterval,clearInterval,setTimeout,URL,Date,console,fetch:async(url,options={})=>{const command=options.body&&JSON.parse(options.body);calls.push({url,command});if(url==='/api/state')return {ok:true,json:async()=>copy(state)};if(url.startsWith('/api/task-view'))return {ok:false,json:async()=>({error:'Synthetic proof read failed'})};if(command?.name==='connection_switch'){const p={command};pending.push(p);return new Promise(resolve=>p.resolve=resolve);}throw Error('Unexpected isolated request '+url);}};
 vm.createContext(context);vm.runInContext(source,context);const publish=next=>{state=copy(next);window.dispatchEvent(new context.CustomEvent('console-state',{detail:copy(state)}));};publish(state);
 return {window,calls,pending,publish,state:()=>copy(state),async start(){const result=window.WujiDeviceUse.open('teleoperation',{kind:'glove',serial:B});result.catch(()=>{});for(let n=0;n<30&&!pending.length;n++)await Promise.resolve();assert.equal(pending.length,1);return {result,request:pending[0]};},failure(p,message='Current task failed'){p.resolve({ok:false,json:async()=>({ok:false,error:message})});},success(p,receipt){p.resolve({ok:true,json:async()=>({ok:true,connection_switch_receipt:receipt})});}};
}

test('late B failure cannot replace the verified current A purpose from a later same-service transaction',async()=>{
 const f=fixture(),{result,request}=await f.start();f.publish(ownerState());f.failure(request,'Old B failed');await assert.rejects(result,/Old B failed/);assert.equal(f.window.WujiDeviceUse.current().serial,A);assert.equal(f.window.WujiDeviceUse.snapshot().error,'');assert.equal(f.calls.filter(c=>c.command?.name==='connection_switch').length,1);
});
test('an already displayed task error retires only when a later exact owner transaction is verified',async()=>{
 const f=fixture(),{result,request}=await f.start();f.failure(request);await result.catch(()=>{});assert.equal(f.window.WujiDeviceUse.snapshot().error,'Current task failed');f.publish(ownerState());assert.equal(f.window.WujiDeviceUse.snapshot().error,'');
});
test('ordinary fresh reads and the current task failure retain its error without automatic retry',async()=>{
 const f=fixture(),{result,request}=await f.start();f.failure(request);await result.catch(()=>{});for(let n=0;n<3;n++)f.publish({...f.state(),frame_sequence:n});assert.equal(f.window.WujiDeviceUse.snapshot().error,'Current task failed');assert.equal(f.calls.filter(c=>c.command?.name==='connection_switch').length,1);
});
test('the same operation committed with a failed proof read stays unverified, even after owner context advanced',async()=>{
 const f=fixture(),{result,request}=await f.start(),next=ownerState(B,'person-B');next.connection_switch.request_id=request.command.request_id;next.connection_switch.receipt.request_id=request.command.request_id;f.publish(next);f.success(request,next.connection_switch.receipt);await assert.rejects(result);assert.notEqual(f.window.WujiDeviceUse.snapshot().error,'');
});
test('a different unconfirmed or failed task cannot silently clear the existing error',async()=>{
 const f=fixture(),{result,request}=await f.start();f.failure(request);await result.catch(()=>{});const next=ownerState();next.connection_switch.phase='failed';next.connection_switch.error='Other task failed';f.publish(next);assert.equal(f.window.WujiDeviceUse.snapshot().error,'Current task failed');
});
test('another wearer and SDK epoch on the same serial is a new verified operation, not the failed old intent',async()=>{
 const f=fixture(),{result,request}=await f.start();f.failure(request);await result.catch(()=>{});f.publish(ownerState(B,'person-C',45));assert.equal(f.window.WujiDeviceUse.snapshot().error,'');
});
test('receipt identity mismatch or incomplete evidence never erases a current error',async()=>{
 const f=fixture(),{result,request}=await f.start();f.failure(request);await result.catch(()=>{});
 for(const change of [s=>s.connection_switch.receipt.sdk_session++,s=>s.connection_switch.receipt.target.user_id='wrong-person',s=>s.connection_switch.receipt.attempt_id='f'.repeat(32),s=>s.connection_switch.receipt.after_context=null,s=>s.connection_switch.current_owner=null]){const next=ownerState();change(next);f.publish(next);assert.equal(f.window.WujiDeviceUse.snapshot().error,'Current task failed');}
});

test('an error superseded by a verified new transaction cannot resurface after its later disconnect',async()=>{
 const f=fixture(),{result,request}=await f.start();f.failure(request);await result.catch(()=>{});f.publish(ownerState());assert.equal(f.window.WujiDeviceUse.snapshot().error,'');const disconnected=ownerState();disconnected.connection_switch.current_owner=null;disconnected.glove={busy:false,connection:'disconnected'};f.publish(disconnected);assert.equal(f.window.WujiDeviceUse.snapshot().error,'');
});

test('an old completed receipt present before R cannot retire R failure after only an SDK control revision changes',async()=>{
 const f=fixture(ownerState()),{result,request}=await f.start();f.failure(request,'Current B failed to open');await result.catch(()=>{});assert.equal(f.window.WujiDeviceUse.snapshot().error,'Current B failed to open');
 const control=f.state();control.glove.action_revision=2;control.connection_switch.expected_owner='f'.repeat(64);f.publish(control);assert.equal(f.window.WujiDeviceUse.snapshot().error,'Current B failed to open');
});
test('pre-dispatch feature failure also keeps the prior completed transition bound across ordinary owner-context changes',async()=>{
 const f=fixture(ownerState());f.window.WujiWorkspace.showPage=()=>{throw Error('Current page unavailable')};await assert.rejects(f.window.WujiDeviceUse.open('teleoperation',{kind:'glove',serial:B}),/Current page unavailable/);assert.equal(f.calls.filter(c=>c.command).length,0);
 const control=f.state();control.glove.action_revision=2;control.connection_switch.expected_owner='f'.repeat(64);f.publish(control);assert.equal(f.window.WujiDeviceUse.snapshot().error,'Current page unavailable');
});
