'use strict';
// Actual hub async functions with isolated transport; complete page QA is separate.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'web/connection_hub.js'),'utf8');
function section(begin,end){const a=source.indexOf(begin),b=source.indexOf(end,a);assert(a>=0&&b>a);return source.slice(a,b);}
const declarations=source.split('\n').find(line=>line.startsWith(' let active='));assert(declarations);
const ownership=section(' // Service lifetime and each pending slot',' function selectSharedDevice');
const requests=section(' async function refresh(force=false)',' async function scanGloves()');
const listeners=section(" window.addEventListener('console-state',event=>",' function labels()');
function fixture(){
 const calls=[],events={},fields=new Map(),queue=[];let nonce=0;
 const field=id=>{if(!fields.has(id))fields.set(id,{value:'',checked:false});return fields.get(id);};
 const context={window:{crypto:{getRandomValues(array){array.fill(++nonce);return array;}},addEventListener:(name,fn)=>events[name]=fn},
  Uint8Array,AbortSignal:{timeout:()=>undefined},Date,queueMicrotask:fn=>queue.push(fn),
  $:field,object:v=>v!==null&&typeof v==='object'&&!Array.isArray(v),t:(zh,en)=>en,
  calibrationVisible:()=>true,renderStatus:()=>{},renderCalibration:()=>{},previewIntent:()=>null,samePreviewIntent:()=>false,
  fetch:(url,options)=>new Promise(resolve=>calls.push({url,options,resolve})),};
 vm.createContext(context);vm.runInContext(declarations+'\n'+ownership+'\n'+requests+'\n'+listeners+`\nthis.api={action,refresh,snapshot:()=>({busy,calibrationStartPending,cancelStartPending,refreshPending,calibrationFresh,calibration,serviceEpoch,serviceToken,actionError}),setMetadata:value=>{calibration=value;calibrationFresh=true;}};`,context);
 return {calls,fields,queue,api:context.api,event:(name,detail)=>events[name]({detail}),reply(index,calibration){calls[index].resolve({ok:true,json:async()=>({ok:true,calibration})});},replyMetadata(index,calibration){calls[index].resolve({ok:true,json:async()=>calibration});}};
}
const metadata=(id,user='Alice',running=false)=>({available:true,users:[],devices:[],current:{name:user},run:{id,running,status:running?'collecting':'cancelled'}});
const start={serial:'WG_SYNTHETIC_OWNER',profile:'Alice',side:'right',replace:false,create_profile:false,interface:'guided'};
const flush=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
test('old begin result and finally cannot replace a new service metadata read or keep Cancel start alive',async()=>{
 const e=fixture();e.event('console-state',{csrf:'service-A'});e.api.setMetadata(metadata('a'.repeat(32)));
 const begin=e.api.action('calibration_begin',start);assert.equal(e.calls.length,1);
 assert.match(JSON.parse(e.calls[0].options.body).request_id,/^[a-f0-9]{32}$/);
 e.event('console-offline');e.event('console-state',{csrf:'service-B'});
 assert.equal(e.api.snapshot().calibrationStartPending,false);assert.equal(e.api.snapshot().busy,false);
 const read=e.api.refresh(true);assert.equal(e.calls.length,2);assert(e.api.snapshot().refreshPending);
 e.reply(0,metadata('a'.repeat(32),'Alice',true));await begin;
 assert(e.api.snapshot().refreshPending);assert.equal(e.api.snapshot().calibrationFresh,false);
 e.replyMetadata(1,metadata('b'.repeat(32),'Bob',true));await read;
 assert.equal(e.api.snapshot().calibration.run.id,'b'.repeat(32));assert.equal(e.api.snapshot().calibration.current.name,'Bob');
 assert.equal(e.calls.filter(call=>call.options?.method==='POST').length,1);
});
test('pending cancel uses original begin nonce once and late begin success cannot overwrite cancellation',async()=>{
 const e=fixture();e.event('console-state',{csrf:'service-A'});e.api.setMetadata(metadata('a'.repeat(32)));
 const begin=e.api.action('calibration_begin',start),sent=JSON.parse(e.calls[0].options.body);
 const cancel=e.api.action('calibration_cancel');await e.api.action('calibration_cancel');
 assert.equal(e.calls.length,2);assert.deepEqual(JSON.parse(e.calls[1].options.body),{name:'calibration_cancel',request_id:sent.request_id});
 assert.equal(e.calls[1].options.headers['X-Console-Token'],'service-A');
 e.reply(1,metadata('c'.repeat(32)));await cancel;
 e.reply(0,metadata('a'.repeat(32),'Alice',true));await begin;
 assert.equal(e.api.snapshot().calibration.run.id,'c'.repeat(32));assert.equal(e.api.snapshot().calibration.run.running,false);
});
test('active cancel captures exact displayed run rather than a later current run',async()=>{
 const e=fixture();e.event('console-state',{csrf:'same-service'});e.api.setMetadata(metadata('a'.repeat(32),'Alice',true));
 const pending=e.api.action('calibration_cancel');assert.deepEqual(JSON.parse(e.calls[0].options.body),{name:'calibration_cancel',expected_run:'a'.repeat(32)});
 e.event('console-offline');e.event('console-state',{csrf:'next-service'});e.api.setMetadata(metadata('b'.repeat(32),'Bob',true));
 e.reply(0,metadata('a'.repeat(32)));await pending;
 assert.equal(e.api.snapshot().calibration.run.id,'b'.repeat(32));assert.equal(e.api.snapshot().calibration.run.running,true);
});
test('exact cancelled request acknowledgement retires a hung begin without refreshing cached metadata',async()=>{
 const e=fixture();e.event('console-state',{csrf:'A'});e.api.setMetadata(metadata('a'.repeat(32)));
 const begin=e.api.action('calibration_begin',start),nonce=JSON.parse(e.calls[0].options.body).request_id;
 const cancel=e.api.action('calibration_cancel');
 e.calls[1].resolve({ok:true,json:async()=>({ok:true,cancelled_request_id:nonce,calibration:{run:{running:false,status:'cancelled'}}})});await cancel;
 assert.equal(e.api.snapshot().busy,false);assert.equal(e.api.snapshot().calibrationStartPending,false);
 assert.equal(e.api.snapshot().calibrationFresh,false);assert.equal(e.api.snapshot().calibration.available,true);
 assert.equal(e.api.snapshot().calibration.current.name,'Alice');
 assert.equal(e.calls[2].url,'/api/calibration?refresh=1');
 e.replyMetadata(2,metadata('b'.repeat(32),'Bob',true));await flush();
 e.reply(0,metadata('a'.repeat(32),'Alice',true));await begin;
 assert.equal(e.api.snapshot().calibration.run.id,'b'.repeat(32));
});
test('same-service queued old cancel rejection cannot mark the newer published run as failed',async()=>{
 const e=fixture();e.event('console-state',{csrf:'A',calibration:{id:'a'.repeat(32)}});e.api.setMetadata(metadata('a'.repeat(32),'Alice',true));
 const cancel=e.api.action('calibration_cancel');
 e.event('console-state',{csrf:'A',calibration:{id:'b'.repeat(32)}});e.api.setMetadata(metadata('b'.repeat(32),'Bob',true));
 e.calls[0].resolve({ok:false,json:async()=>({ok:false,error:'old cancellation was not sent'})});await cancel;
 assert.equal(e.api.snapshot().actionError,'');assert.equal(e.api.snapshot().calibration.run.id,'b'.repeat(32));
 assert.equal(e.api.snapshot().busy,false);
});
test('task owner revalidation preserves this same-service explicit begin receipt',async()=>{
 const e=fixture();e.event('console-state',{csrf:'same-service'});e.api.setMetadata(metadata('a'.repeat(32)));
 const pending=e.api.action('calibration_begin',start),epoch=e.api.snapshot().serviceEpoch;
 e.event('console-offline',{task_scope:true});e.event('console-state',{csrf:'same-service'});
 assert.equal(e.api.snapshot().serviceEpoch,epoch);assert(e.api.snapshot().calibrationStartPending);
 e.reply(0,metadata('d'.repeat(32),'Alice',true));await pending;
 assert.equal(e.api.snapshot().calibration.run.id,'d'.repeat(32));assert(e.api.snapshot().calibrationFresh);
});
test('late old GET cannot release a new service refresh slot',async()=>{
 const e=fixture();e.event('console-state',{csrf:'A'});const old=e.api.refresh(true);
 e.event('console-offline');e.event('console-state',{csrf:'B'});const current=e.api.refresh(true);
 e.replyMetadata(0,metadata('a'.repeat(32)));await old;assert(e.api.snapshot().refreshPending);
 e.replyMetadata(1,metadata('b'.repeat(32)));await current;assert.equal(e.api.snapshot().calibration.run.id,'b'.repeat(32));
});
test('retirement discards old begin rejection without clearing a new explicit action',async()=>{
 const e=fixture();e.event('console-state',{csrf:'A'});e.api.setMetadata(metadata('a'.repeat(32)));
 const old=e.api.action('calibration_begin',start);e.event('console-state',{csrf:'B'});e.api.setMetadata(metadata('b'.repeat(32)));
 const current=e.api.action('calibration_begin',{...start,profile:'Bob'});
 e.calls[0].resolve({ok:false,json:async()=>({ok:false,error:'old service failure'})});await old;assert(e.api.snapshot().busy);
 e.reply(1,metadata('c'.repeat(32),'Bob',true));await current;assert.equal(e.api.snapshot().calibration.current.name,'Bob');
 await flush();
});
