/* Actual Hub read/action ownership, isolated transport and renderer only. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'web/connection_hub.js'),'utf8');
const A='a'.repeat(32),B='b'.repeat(32),C='c'.repeat(32),VIEW='b'.repeat(32),SN='WG_SYNTHETIC_RESULT_SCOPE';
function slice(begin,end){const a=source.indexOf(begin),b=source.indexOf(end,a);assert(a>=0&&b>a);return source.slice(a,b);}
const declarations=source.split('\n').find(line=>line.startsWith(' let active='));assert(declarations);
function fixture(){
 const calls=[],events={},fields=new Map(),queue=[];let nonce=0;
 const field=id=>{if(!fields.has(id))fields.set(id,{value:'',checked:false});return fields.get(id);};
 field('hub-profile').value='Morgan';field('hub-calib-interface').value='terminal';
 const context={window:{crypto:{getRandomValues:array=>array.fill(++nonce)},addEventListener:(name,fn)=>events[name]=fn},Uint8Array,AbortSignal:{timeout:()=>undefined},Date,queueMicrotask:fn=>queue.push(fn),
  $:field,object:v=>v!==null&&typeof v==='object'&&!Array.isArray(v),t:(zh,en)=>en,calibrationVisible:()=>true,renderStatus:()=>{},renderCalibration:()=>{},previewIntent:()=>null,samePreviewIntent:()=>false,
  officialViz:{snapshot:()=>({fresh:true,state:{active:true,phase:'running',session_id:VIEW,reported_device:{sn:SN}}})},
  fetch:(url,options)=>new Promise(resolve=>calls.push({url,options,resolve}))};
 vm.createContext(context);vm.runInContext(declarations+'\n'+slice(' // Service lifetime and each pending slot',' function selectSharedDevice')+'\n'+slice(' async function refresh(force=false)',' async function scanGloves()')+'\n'+slice(" window.addEventListener('console-state',event=>",' function labels()')+`
 this.api={action,refresh,set:value=>{calibration=value;calibrationFresh=true;},snapshot:()=>({calibration,calibrationFresh,busy,refreshPending,calibrationEpoch,serviceEpoch,lastStartedRunId,context:typeof lastStartedContext==='undefined'?null:lastStartedContext})};`,context);
 return {api:context.api,calls,fields,queue,event:(name,detail)=>events[name]({detail}),metadata:(i,value,ok=true)=>calls[i].resolve({ok,json:async()=>value}),action:(i,value,ok=true)=>calls[i].resolve({ok,json:async()=>({ok,calibration:value})})};
}
const metadata=(id,running=false)=>({available:true,users:[{name:'Morgan'}],devices:[],current:{name:'Morgan'},run:{id,serial:SN,user:'Morgan',side:'right',running,status:running?'collecting':'completed'}});
const state=(id,nested=false)=>({csrf:'service-A',calibration:nested?{run:{id,running:true}}:{id,running:true}});
const command={serial:SN,profile:'Morgan',side:'right',replace:true,create_profile:false,interface:'guided'};
function renderer(){const text=fs.readFileSync(path.join(__dirname,'test_calibration_result_scope_0276.cjs'),'utf8'),c={require,__dirname};vm.createContext(c);vm.runInContext(text.slice(0,text.indexOf("test('same service"))+'\nthis.make=fixture;',c);return c.make();}
test('a completed begin receipt is bound to its pre-start Viewer rather than retired by that old snapshot',async()=>{
 const e=fixture();e.event('console-state',state(A));e.api.set(metadata(A));const pending=e.api.action('calibration_begin',command);e.action(0,metadata(B));await pending;
 const f=renderer();f.data.run.id=B;f.ctx.lastStartedRunId=B;f.ctx.lastStartedContext=e.api.snapshot().context;f.newViewer();f.render();assert.equal(f.calib.dataset.currentResult,'true');
 f.viewer({fresh:true,state:{active:false,phase:'idle',session_id:null}});f.render();assert.equal(f.calib.dataset.currentResult,'true');
 f.viewer({fresh:true,state:{active:true,phase:'running',session_id:C,reported_device:{sn:SN,side:'right'}}});f.render();assert.equal(f.calib.dataset.currentResult,'false');
});
test('a new public flat run retires an old metadata GET and its finally cannot clear the new GET slot',async()=>{
 const e=fixture();e.event('console-state',state(A));e.api.set(metadata(A));const old=e.api.refresh(true);e.event('console-state',state(B));assert.equal(e.api.snapshot().calibrationFresh,false);
 const current=e.api.refresh(true);assert.equal(e.calls.length,2);e.metadata(0,metadata(A));await old;assert.equal(e.api.snapshot().refreshPending,true);assert.equal(e.api.snapshot().calibrationFresh,false);
 e.metadata(1,metadata(B,true));await current;assert.equal(e.api.snapshot().calibration.run.id,B);assert.equal(e.api.snapshot().calibration.run.running,true);assert.equal(e.api.snapshot().calibrationFresh,true);
});
test('a nested public run also retires the old read while missing or malformed identities do not churn a current read',async()=>{
 const e=fixture();e.event('console-state',state(A,true));e.api.set(metadata(A));const old=e.api.refresh(true);e.event('console-state',state(B,true));const current=e.api.refresh(true);assert.equal(e.calls.length,2);
 for(const id of [B,null,'invalid',7])e.event('console-state',state(id));assert.equal(e.api.snapshot().refreshPending,true);e.metadata(0,metadata(A));await old;assert.equal(e.api.snapshot().refreshPending,true);e.metadata(1,metadata(B,true));await current;assert.equal(e.api.snapshot().calibration.run.id,B);
});
test('a late baseline public run cannot discard this page accepted begin receipt',async()=>{
 const e=fixture();e.event('console-state',state(A));e.api.set(metadata(A));const pending=e.api.action('calibration_begin',command);e.action(0,metadata(B));await pending;
 e.event('console-state',state(B));e.event('console-state',state(A));assert.equal(e.api.snapshot().calibration.run.id,B);assert.equal(e.api.snapshot().calibrationFresh,true);
});
test('a different public run during a pending begin keeps the action but rejects its obsolete metadata receipt',async()=>{
 const e=fixture();e.event('console-state',state(A));e.api.set(metadata(A));const pending=e.api.action('calibration_begin',command);e.event('console-state',state(C));assert.equal(e.api.snapshot().busy,true);
 e.action(0,metadata(B));await pending;assert.notEqual(e.api.snapshot().calibration.run.id,B);assert.equal(e.api.snapshot().calibrationFresh,false);assert.equal(e.api.snapshot().refreshPending,true);assert.equal(e.calls.length,2);
 e.metadata(1,metadata(C,true));for(let i=0;i<5;i++)await Promise.resolve();assert.equal(e.api.snapshot().calibration.run.id,C);assert.equal(e.api.snapshot().calibrationFresh,true);assert.equal(e.calls.filter(c=>c.options?.method==='POST').length,1);
});
test('service retirement removes result bindings without resetting the wearer or terminal interface',async()=>{
 const e=fixture();e.event('console-state',state(A));e.api.set(metadata(A));const pending=e.api.action('calibration_begin',command);e.action(0,metadata(B));await pending;
 e.event('console-offline');e.event('console-state',{csrf:'service-B',calibration:{id:C}});assert.equal(e.api.snapshot().context,null);assert.equal(e.api.snapshot().lastStartedRunId,'');assert.equal(e.fields.get('hub-profile').value,'Morgan');assert.equal(e.fields.get('hub-calib-interface').value,'terminal');
});
