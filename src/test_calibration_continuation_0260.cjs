'use strict';
// Run with node --test. Execute the real frontend source; no browser/network/SDK.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'web/calibration_continuation.js'),'utf8');
const RUN='a'.repeat(32),ATTEMPT='b'.repeat(32),REV='c'.repeat(64),clone=x=>JSON.parse(JSON.stringify(x));
const saved=(run=RUN,csrf='controller-a')=>({csrf,calibrated_teleoperation:{run_id:run,expected_revision:REV,can_continue:true,busy:false,connected:false,attempt_id:null}});
const busy=(run=RUN,attempt=ATTEMPT,csrf='controller-a')=>({csrf,calibrated_teleoperation:{...saved(run,csrf).calibrated_teleoperation,can_continue:false,busy:true,phase:'exporting',attempt_id:attempt}});
const ended=(run=RUN,attempt=ATTEMPT)=>({...saved(run).calibrated_teleoperation,attempt_id:attempt,phase:'cancelled'});
function environment(){
 const result={children:[],append(...nodes){for(const node of nodes){node.isConnected=true;this.children.push(node);}}};
 const listeners={},requests=[],modes=[],location={hash:'#connection'};
 const element=tag=>({tag,isConnected:false,children:[],classList:{add(){}},setAttribute(){},append(...nodes){this.children.push(...nodes);}});
 const document={querySelector:s=>s==='.oc-result'?result:null,createElement:element};
 const window={WujiLocale:{lang:'en'},WujiTeleoperation:{openMode(mode){modes.push(mode);location.hash=mode==='real'?'#glove':'#virtual-glove';}},addEventListener(name,fn){listeners[name]=fn;}};
 const fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,command:JSON.parse(options.body),headers:options.headers,resolve(value){resolve({ok:true,json:async()=>clone(value)});},reject}));
 vm.runInNewContext(source,{window,document,location,fetch,AbortSignal});
 const fire=state=>listeners['console-state']({detail:clone(state)}),offline=()=>listeners['console-offline']();
 fire(saved());const [virtual,real,cancel,note]=result.children[0].children;
 return {fire,offline,virtual,real,cancel,note,requests,modes,location,navigate(hash){location.hash=hash;listeners.hashchange?.();}};
}
test('fresh exact pending attempt can cancel while the initial begin response is held',async()=>{
 const e=environment(),first=e.virtual.onclick();assert.equal(e.requests.length,1);e.fire(busy());assert.equal(e.cancel.hidden,false);assert.equal(e.cancel.disabled,false);
 const cancellation=e.cancel.onclick();assert.deepEqual(e.requests[1].command,{name:'calibrated_teleoperation_cancel',run_id:RUN,attempt_id:ATTEMPT});assert.equal(e.cancel.disabled,true);
 e.requests[1].resolve({ok:true,calibrated_teleoperation:ended()});await cancellation;assert.equal(e.virtual.disabled,false);
 e.requests[0].resolve({ok:true,calibrated_teleoperation:{...ended(),phase:'preview',connected:true}});await first;
 assert.deepEqual(e.modes,[]);assert.equal(e.cancel.hidden,true);assert.equal(e.virtual.disabled,false);assert.equal(e.note.textContent,'');
});
test('cancel failure remains visible, explicit retry works, and late begin error cannot replace it',async()=>{
 const e=environment(),first=e.virtual.onclick();e.fire(busy());const cancel1=e.cancel.onclick();e.requests[1].reject(Error('cancel transport failed'));await cancel1;
 assert.equal(e.cancel.disabled,false);assert.match(e.note.textContent,/cancel transport failed/);
 e.requests[0].reject(Error('old begin failed'));await first;assert.match(e.note.textContent,/cancel transport failed/);
 const cancel2=e.cancel.onclick();e.requests[2].resolve({ok:true,calibrated_teleoperation:ended()});await cancel2;assert.equal(e.cancel.hidden,true);assert.deepEqual(e.modes,[]);
});
test('unknown or malformed attempt never sends cancellation',async()=>{
 for(const attempt of [null,'','wrong','f'.repeat(31),true]){const e=environment();e.fire(busy(RUN,attempt));assert.equal(e.cancel.disabled,true);await e.cancel.onclick();assert.equal(e.requests.length,0);}
});
test('new run can begin without waiting for the previous run reply; that reply cannot release the new pending state',async()=>{
 const e=environment(),old=e.virtual.onclick(),newRun='d'.repeat(32);e.fire(saved(newRun));assert.equal(e.virtual.disabled,false);
 const current=e.real.onclick();assert.equal(e.requests.length,2);e.requests[0].resolve({ok:true,calibrated_teleoperation:{...ended(),connected:true}});await old;
 assert.equal(e.virtual.disabled,true);assert.deepEqual(e.modes,[]);
 e.requests[1].resolve({ok:true,calibrated_teleoperation:{...ended(newRun),connected:true}});await current;assert.deepEqual(e.modes,['real']);
});
test('offline and controller replacement retire old requests, allow fresh retry, and prevent old navigation',async()=>{
 for(const replacement of ['offline','controller']){const e=environment(),old=e.virtual.onclick();if(replacement==='offline'){e.offline();e.fire(saved());}else e.fire(saved(RUN,'controller-b'));
  assert.equal(e.virtual.disabled,false);e.requests[0].resolve({ok:true,calibrated_teleoperation:{...ended(),connected:true}});await old;
  assert.deepEqual(e.modes,[]);assert.equal(e.virtual.disabled,false);assert.equal(e.note.textContent,'');
 }
});
test('a changed pending attempt retires old cancellation without letting its response replace the new attempt',async()=>{
 const e=environment();e.fire(busy());const old=e.cancel.onclick(),next='e'.repeat(32);e.fire(busy(RUN,next));assert.equal(e.cancel.disabled,false);
 e.requests[0].resolve({ok:true,calibrated_teleoperation:ended()});await old;assert.equal(e.cancel.hidden,false);assert.equal(e.cancel.disabled,false);
 const current=e.cancel.onclick();assert.equal(e.requests[1].command.attempt_id,next);e.requests[1].resolve({ok:true,calibrated_teleoperation:ended(RUN,next)});await current;assert.equal(e.cancel.hidden,true);
});
test('loaded preview does not expose pending cancellation and normal explicit continuation still navigates once',async()=>{
 const e=environment(),p=e.virtual.onclick();e.requests[0].resolve({ok:true,calibrated_teleoperation:{...ended(),connected:true}});await p;assert.deepEqual(e.modes,['virtual']);assert.equal(e.cancel.hidden,true);
 await e.cancel.onclick();assert.equal(e.requests.length,1);
});
test('leaving and returning to the same entry hash retires old automatic navigation but keeps manual use',async()=>{
 const e=environment(),p=e.virtual.onclick();e.navigate('#glove');e.navigate('#connection');e.requests[0].resolve({ok:true,calibrated_teleoperation:{...ended(),connected:true}});await p;
 assert.deepEqual(e.modes,[]);assert.equal(e.location.hash,'#connection');assert.equal(e.virtual.disabled,false);await e.real.onclick();assert.deepEqual(e.modes,['real']);assert.equal(e.requests.length,1);
});
test('completion on another page does not navigate away or cancel the accepted continuation',async()=>{
 const e=environment(),p=e.virtual.onclick();e.navigate('#glove');e.requests[0].resolve({ok:true,calibrated_teleoperation:{...ended(),connected:true}});await p;
 assert.deepEqual(e.modes,[]);assert.equal(e.location.hash,'#glove');assert.equal(e.requests.filter(x=>x.command.name==='calibrated_teleoperation_cancel').length,0);
});
