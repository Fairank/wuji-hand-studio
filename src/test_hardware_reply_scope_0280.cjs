/* Actual playback action/keepalive code; only synthetic HTTP and state events. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'web/playback.js'),'utf8');
const actions=source.slice(source.indexOf('  let ownedLease='),source.indexOf("  get('hardware-start').addEventListener"));
const beat=source.slice(source.indexOf('  async function beat(){'),source.indexOf('  beat();'));
const observations=source.slice(source.indexOf("  window.addEventListener('console-state',"),source.indexOf("  window.addEventListener('hashchange',"));
const A='a'.repeat(32),B='b'.repeat(32),hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const flush=async()=>{for(let n=0;n<15;n++)await new Promise(resolve=>setImmediate(resolve));};
function fixture(){
 const listeners={},requests=[],nodes=new Map(),timers=[],get=id=>{if(!nodes.has(id))nodes.set(id,{value:id.includes('speed')?'1':id.includes('amplitude')?'.25':id.includes('cycles')?'1':id==='probe-joint'?'6':id==='probe-direction'?'1':'fist',checked:true,textContent:'',hidden:true});return nodes.get(id);};
 const state={csrf:'hardware-service-A',connection:'connected',stale:false,device_id:'WH_SYNTHETIC_A',session_generation:1,
  identified_device:{source:'sdk_identity',serial:'WH_SYNTHETIC_A',profile:'hand2_right'},device_profile:{id:'hand2_right'},
  hardware_control_id:null,hardware:{active:false,stop_confirmed:true},glove:{busy:false}};
 const window={WujiDemonstration:{selectedHand:()=>ctx.state.device_id},addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn)};
 const ctx={state:structuredClone(state),window,document:{getElementById:get},get,globalStop:{},displayProfile:()=>ctx.state?.device_profile?.id,
  matchesDisplayHand:()=>true,tr:(zh,en)=>en,performance,crypto:crypto.webcrypto,TextEncoder,render(){},previewUIEpoch:0,
  setTimeout:fn=>timers.push(fn),AbortSignal,
  fetch:async(url,options)=>{assert.equal(url,'/api/action');const command=JSON.parse(options.body);let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});requests.push({command,headers:options.headers,resolve:value=>resolve({ok:value.ok!==false,json:async()=>value}),reject});if(command.name==='hardware_stop'&&!Object.hasOwn(command,'lease'))resolve({ok:true,json:async()=>({ok:true})});return promise;}};
 vm.createContext(ctx);vm.runInContext(actions+'\nrenderHardware=()=>{};'+beat+observations,ctx);
 return {requests,nodes,get,timers,ctx,action:name=>ctx.hardwareAction(name),beat:()=>ctx.beat(),
  status:()=>vm.runInContext('({lease:ownedLease,pending:hardwarePending,generation:hardwareGeneration})',ctx),
  publish(changes={}){const next={...structuredClone(ctx.state),...changes};for(const fn of listeners['console-state']||[])fn({detail:next});},
  offline(){for(const fn of listeners['console-offline']||[])fn({});},
  active(lease){this.publish({hardware_control_id:hash(lease),hardware:{active:true,stop_confirmed:false}});},
  stopped(){this.publish({hardware_control_id:null,hardware:{active:false,stop_confirmed:true}});},
  accept(index,lease){requests[index].resolve({ok:true,...lease?{lease}:{}});}};
}

async function stoppedStart(f){
 const first=f.action('hardware_trial');await flush();assert.equal(f.requests.length,1);
 f.publish({hardware_control_id:hash(A),hardware:{active:null,start_pending:true,stop_confirmed:false}});
 const stop=f.action('hardware_stop');await flush();assert.equal(f.requests.length,2);
 f.stopped();f.accept(1);await stop;return {first};
}

test('old start success after explicit Stop and new start cannot stop or clear the new lease',async()=>{
 const f=fixture(),{first:old}=await stoppedStart(f),current=f.action('hardware_trial');await flush();
 f.active(B);f.accept(2,B);await current;assert.equal(f.status().lease,B);
 f.accept(0,A);await old;assert.deepEqual(f.requests.map(r=>r.command.name),['hardware_trial','hardware_stop','hardware_trial']);assert.equal(f.status().lease,B);
});

test('old start finally and error cannot release a newer pending request or replace its message',async()=>{
 const f=fixture(),{first:old}=await stoppedStart(f),current=f.action('hardware_trial');await flush();
 f.get('service-error').textContent='Current operation';f.requests[0].reject(Error('Old startup error'));await old;
 assert.equal(f.status().pending,true);assert.equal(f.get('service-error').textContent,'Current operation');
 f.active(B);f.accept(2,B);await current;assert.equal(f.status().lease,B);
});

test('old heartbeat rejection cannot clear a replacement lease on the same hand',async()=>{
 const f=fixture(),first=f.action('hardware_trial');await flush();f.active(A);f.accept(0,A);await first;
 const beat=f.beat();await flush();const stop=f.action('hardware_stop');await flush();f.stopped();f.accept(2);await stop;
 const current=f.action('hardware_trial');await flush();f.active(B);f.accept(3,B);await current;
 f.requests[1].reject(Error('Old keepalive failure'));await beat;assert.equal(f.status().lease,B);
});

test('a replacement service retires old pending start and its error without clearing its new slot',async()=>{
 const f=fixture(),old=f.action('hardware_trial');await flush();f.offline();
 f.ctx.state={csrf:'hardware-service-B',connection:'connected',stale:false,device_id:'WH_SYNTHETIC_B',session_generation:2,device_profile:{id:'hand2_right'},identified_device:{source:'sdk_identity',serial:'WH_SYNTHETIC_B',profile:'hand2_right'},hardware:{active:false,stop_confirmed:true},hardware_control_id:null,glove:{busy:false}};
 f.publish();const current=f.action('hardware_trial');await flush();assert.equal(f.requests.length,2);
 f.get('service-error').textContent='Current service';f.requests[0].reject(Error('Old service failed'));await old;
 assert.equal(f.status().pending,true);assert.equal(f.get('service-error').textContent,'Current service');
 f.active(B);f.accept(1,B);await current;assert.equal(f.status().lease,B);assert.equal(f.requests[1].headers['X-Console-Token'],'hardware-service-B');
});

test('the same device with a replacement SDK session cannot adopt an old start reply',async()=>{
 const f=fixture(),old=f.action('hardware_trial');await flush();f.publish({session_generation:2});
 const current=f.action('hardware_trial');await flush();assert.equal(f.requests.length,2);f.active(B);f.accept(1,B);await current;
 f.accept(0,A);await old;assert.equal(f.requests.length,2);assert.equal(f.status().lease,B);
});

test('a same-session newer public control identity rejects the old late lease without issuing Stop',async()=>{
 const f=fixture(),old=f.action('hardware_trial');await flush();f.active(B);f.accept(0,A);await old;
 assert.equal(f.requests.length,1);assert.equal(f.status().lease,null);
});

test('same-session replacement control retires the old pause error and its pending slot',async()=>{
 const f=fixture(),first=f.action('hardware_trial');await flush();f.active(A);f.accept(0,A);await first;
 const pause=f.action('hardware_pause');await flush();assert.equal(f.status().pending,true);
 f.get('service-error').textContent='Current B';f.active(B);assert.equal(f.status().pending,false);
 f.requests[1].reject(Error('Old pause failure'));await pause;
 assert.equal(f.get('service-error').textContent,'Current B');assert.equal(f.requests.length,2);assert.equal(f.status().lease,null);
});
