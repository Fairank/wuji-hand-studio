/* Production single-hand controls and shared purpose workflow; HTTP/devices are synthetic. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const test=require('node:test'),assert=require('node:assert/strict');
const workflow=fs.readFileSync(path.join(__dirname,'web/device_workflows.js'),'utf8');
const source=fs.readFileSync(path.join(__dirname,'web/demonstration_navigation.js'),'utf8');
const controls=source.slice(source.indexOf('    let consoleState='),source.indexOf('    const activeRoute ='));
const copy=v=>structuredClone(v),SN='WH_SYNTHETIC_QUICK',OTHER='WH_SYNTHETIC_OTHER';
const flush=async()=>{for(let n=0;n<35;n++)await Promise.resolve();};
class Element{
 constructor(){this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.options=[];}
 replaceChildren(...items){this.options=items;}
 dispatchEvent(event){this['on'+event.type]?.(event);}
}
function fixture({generation='hand1',side=null,preview='hand2_left',actual='hand1_right',hold=false,rowChanges={},badIdentity=false}={}){
 const listeners={},calls=[],posts=[],pending=[],modelWrites=[],selectionWrites=[],nodes={handSelect:new Element(),connect:new Element(),status:new Element(),handLabel:new Element(),retryCatalog:new Element()};
 let row={sn:SN,serial:SN,kind:'hand',generation,side,selectable:true,stale:false,conflict:false,...rowChanges};
 let model=preview,revision=0,inventory=[row],state={csrf:'quick-service-A',connection:'disconnected',stale:true,
  device_profile:{id:'hand2_left',generation:'hand2',side:'left'},glove:{busy:false},hardware:{active:false},
  connection_switch:{busy:false,can_switch:true,expected_owner:'a'.repeat(64),current_owner:null}};
 const location={hash:'#library'},emit=(type,detail)=>{for(const fn of [...listeners[type]||[]])fn({type,detail});};
 const selectModel=value=>{if(value===model)return true;model=value;revision++;modelWrites.push(value);emit('demonstration-profile',{profile:value,revision});return true;};
 let useHandler,stateRead=null;
 const window={WujiLocale:{lang:'en'},WujiWorkspace:{showPage(){}},
  WujiDemonstration:{profile:()=>model,revision:()=>revision,selectProfile:selectModel},
  WujiDeviceInventory:{snapshot:()=>({devices:copy(inventory)}),refresh:async()=>{},selection:()=>copy(row),select:sn=>selectionWrites.push(sn),setUseHandler:fn=>useHandler=fn},
  WujiConnection:{refresh:async()=>{emit('console-state',copy(state));return copy(state);}},
  addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn),removeEventListener:(type,fn)=>{listeners[type]=(listeners[type]||[]).filter(item=>item!==fn);},dispatchEvent:event=>emit(event.type,event.detail)};
 const ctx={window,location,document:{body:{dataset:{page:'library'}},hidden:false,documentElement:{dataset:{}},
  querySelector:()=>null,querySelectorAll:()=>[],getElementById:id=>id==='demonstration-hand'?nodes.handSelect:null},
  ...nodes,text:(zh,en)=>en,crypto:crypto.webcrypto,Uint8Array,AbortSignal,AbortController,URL,Date,console,
  CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}},Event:class{constructor(type){this.type=type;}},
  Option:class{constructor(text,value){this.text=text;this.value=value;this.disabled=false;}},
  setInterval:()=>0,clearInterval(){},setTimeout,
  fetch:async(url,options={})=>{
   const command=options.body&&JSON.parse(options.body);calls.push({url,command});
   if(url==='/api/state'){const value=stateRead?await stateRead():copy(state);return {ok:true,json:async()=>copy(value)};}
   if(url.startsWith('/api/task-view'))return {ok:true,json:async()=>({current:true,owner_context:state.connection_switch.expected_owner,last_transition_receipt:state.connection_switch.receipt})};
   if(command?.name==='connection_switch'){
    posts.push(command);const service=state.csrf;
    const complete=()=>{
     const receipt={version:1,request_id:command.request_id,attempt_id:'d'.repeat(32),expected_owner:command.expected_owner,
      after_context:'b'.repeat(64),target_task_id:'e'.repeat(24),target:copy(command.target)};
     if(state.csrf===service){state={...state,connection:'connected',stale:false,device_id:command.target.serial,session_generation:7,
      device_profile:{id:actual,generation:actual.split('_')[0],side:actual.split('_')[1]},
      identified_device:{source:badIdentity?'advertisement':'sdk_identity',serial:command.target.serial,profile:actual},
      connection_switch:{...state.connection_switch,expected_owner:receipt.after_context,attempt_id:receipt.attempt_id,receipt,
       current_owner:{mode:'hand',kind:'hand',serial:command.target.serial,session:7}}};emit('console-state',copy(state));}
     return {ok:true,json:async()=>({ok:true,connection_switch_receipt:receipt})};
    };
    if(hold)return new Promise(resolve=>pending.push(()=>resolve(complete())));
    return complete();
   }
   throw Error('Unexpected isolated request '+url);
  }};
 vm.createContext(ctx);vm.runInContext(workflow,ctx);vm.runInContext(controls+';renderSources();',ctx);emit('console-state',copy(state));
 return {window,nodes,calls,posts,pending,modelWrites,selectionWrites,
  select(sn=SN){nodes.handSelect.value=sn;nodes.handSelect.onchange();},click:()=>nodes.connect.onclick(),
  model:()=>model,chooseModel:selectModel,state:()=>state,
  publish(value){state=copy(value);emit('console-state',copy(state));},
  offline(){emit('console-offline');},setInventory(rows){inventory=copy(rows);emit('device-inventory-update');},
  onStateRead(fn){stateRead=fn;},directoryUse:()=>useHandler('hand',copy(row))};
}

for(const generation of ['hand1','hand2'])test(generation+' unknown-side discovery connects once and adopts only verified actual SDK model',async()=>{
 const f=fixture({generation,actual:generation+'_right'}),option=f.nodes.handSelect.options.find(o=>o.value===SN);
 assert.equal(option.disabled,false);assert.match(option.text,/identified|connection/i);assert.doesNotMatch(option.text,/mismatch/i);
 f.select();assert.equal(f.posts.length,0);assert.equal(f.nodes.connect.disabled,false);await f.click();
 assert.equal(f.posts.length,1);assert.equal(f.posts[0].target.profile,generation+'_left');
 assert.equal(f.model(),generation+'_right');assert.equal(f.nodes.handSelect.value,SN);assert.equal(f.nodes.connect.textContent,'Connected');
 assert.equal(f.calls.filter(c=>c.command&&c.command.name!=='connection_switch').length,0);
});

test('an available opposite-side hand connects without an extra preview-model selection',async()=>{
 const f=fixture({generation:'hand2',side:'right',actual:'hand2_right'});f.select();assert.equal(f.nodes.connect.disabled,false);
 await f.click();assert.equal(f.posts[0].target.profile,'hand2_right');assert.equal(f.model(),'hand2_right');
});

for(const change of [{stale:true},{selectable:false},{conflict:true},{generation:'hand3'},{kind:'glove'}])test('invalid discovery remains nonconnectable '+JSON.stringify(change),async()=>{
 const f=fixture({rowChanges:change});f.select();assert.equal(f.nodes.connect.disabled,true);await f.click();assert.equal(f.posts.length,0);
});

test('a deliberate model change while connection is pending survives the late success',async()=>{
 const f=fixture({hold:true});f.select();const pending=f.click();await flush();assert.equal(f.pending.length,1);
 f.chooseModel('hand2_right');f.pending[0]();await pending;assert.equal(f.model(),'hand2_right');assert.deepEqual(f.modelWrites,['hand2_right']);
});

test('old service completion cannot clear a replacement-service connection slot or select its model',async()=>{
 const f=fixture({hold:true});f.select();const old=f.click();await flush();assert.equal(f.pending.length,1);
 f.offline();f.publish({...f.state(),csrf:'quick-service-B'});f.select();const current=f.click();await flush();
 assert.equal(f.pending.length,2);f.pending[0]();await old;assert.equal(f.nodes.connect.textContent,'Connecting…');assert.deepEqual(f.modelWrites,[]);
 f.pending[1]();await current;assert.equal(f.nodes.connect.textContent,'Connected');assert.equal(f.model(),'hand1_right');
});

test('a connected row without SDK identity cannot select a model or report verified connection',async()=>{
 const f=fixture({badIdentity:true});f.select();await f.click();assert.deepEqual(f.modelWrites,[]);assert.match(f.nodes.status.textContent,/verified|identity/i);
});

test('a replacement owner returned by final refresh cannot overwrite the deliberate preview',async()=>{
 const f=fixture();f.window.WujiConnection.refresh=async()=>{
  const s=f.state();return {...copy(s),device_id:OTHER,session_generation:8,identified_device:{source:'sdk_identity',serial:OTHER,profile:'hand2_right'},
   device_profile:{id:'hand2_right',generation:'hand2',side:'right'},connection_switch:{...s.connection_switch,expected_owner:'f'.repeat(64),current_owner:{mode:'hand',kind:'hand',serial:OTHER,session:8}}};
 };
 f.select();await f.click();assert.deepEqual(f.modelWrites,[]);assert.match(f.nodes.status.textContent,/changed|identity|verified/i);
});

test('directory connection with unknown side supplies discovered generation as mapping intent',async()=>{
 const f=fixture({preview:'hand2_right'});await f.directoryUse();assert.equal(f.posts[0].target.profile,'hand1_right');
 assert.deepEqual(f.modelWrites,[],'A connection without navigating keeps deliberate model previews independent');
});

test('a concurrent shared feature remains the owner and reports its pending state',async()=>{
 const f=fixture();f.window.WujiDeviceUse.open=async()=>undefined;f.select();await f.click();
 assert.equal(f.posts.length,0);assert.deepEqual(f.modelWrites,[]);assert.match(f.nodes.status.textContent,/switch is in progress/);
});

test('replacing the chosen physical target while discovery waits cannot dispatch an old connection',async()=>{
 const f=fixture();let release;f.window.WujiDeviceInventory.refresh=()=>new Promise(resolve=>release=resolve);
 f.select();const old=f.click();f.setInventory([{sn:OTHER,kind:'hand',generation:'hand2',side:'right',selectable:true,stale:false}]);
 f.select(OTHER);release();await old;assert.equal(f.posts.length,0);assert.equal(f.nodes.handSelect.value,OTHER);assert.deepEqual(f.modelWrites,[]);
});

test('a same-service replacement owner during discovery requires an explicit new Connect click',async()=>{
 const f=fixture();let release;f.window.WujiDeviceInventory.refresh=()=>new Promise(resolve=>release=resolve);
 f.select();const old=f.click();const s=f.state();f.publish({...s,connection:'connected',device_id:OTHER,session_generation:99,
  identified_device:{source:'sdk_identity',serial:OTHER,profile:'hand2_right'},device_profile:{id:'hand2_right',generation:'hand2',side:'right'},
  connection_switch:{...s.connection_switch,expected_owner:'f'.repeat(64),current_owner:{mode:'hand',kind:'hand',serial:OTHER,session:99}}});
 release();await old;assert.equal(f.posts.length,0);assert.deepEqual(f.modelWrites,[]);assert.match(f.nodes.status.textContent,/changed/);
 f.window.WujiDeviceInventory.refresh=async()=>{};await f.click();assert.equal(f.posts.length,1);assert.equal(f.posts[0].expected_owner,'f'.repeat(64));
});

for(const stopAt of [1,2])test('owner change at shared state read '+stopAt+' cannot adopt the old Connect click',async()=>{
 const f=fixture();let reads=0,release;f.onStateRead(async()=>{if(++reads===stopAt)await new Promise(resolve=>release=resolve);return copy(f.state());});
 f.select();const old=f.click();await flush();assert.equal(typeof release,'function');
 const s=f.state();f.publish({...s,connection_switch:{...s.connection_switch,expected_owner:'f'.repeat(64)}});release();await old;
 assert.equal(f.posts.length,0);assert.deepEqual(f.modelWrites,[]);assert.match(f.nodes.status.textContent,/changed/);
 f.onStateRead(null);await f.click();assert.equal(f.posts.length,1);assert.equal(f.posts[0].expected_owner,'f'.repeat(64));
});

test('normal directory use also pins its first shared owner across the second state read',async()=>{
 const f=fixture();let reads=0;
 f.onStateRead(async()=>{const before=copy(f.state());if(++reads===1){const s=f.state();f.publish({...s,connection_switch:{...s.connection_switch,expected_owner:'f'.repeat(64)}});}return before;});
 await assert.rejects(f.directoryUse(),/changed/);assert.equal(f.posts.length,0);assert.deepEqual(f.modelWrites,[]);
});

test('normal directory click retains its visible owner while its inventory refresh waits',async()=>{
 const f=fixture();let release;f.window.WujiDeviceInventory.refresh=()=>new Promise(resolve=>release=resolve);
 const old=f.directoryUse();const s=f.state();f.publish({...s,connection_switch:{...s.connection_switch,expected_owner:'f'.repeat(64)}});
 release();await assert.rejects(old,/changed/);assert.equal(f.posts.length,0);assert.deepEqual(f.modelWrites,[]);
});

test('direct shared feature entry retains the visible owner before its first state response',async()=>{
 const f=fixture();let release;f.onStateRead(async()=>{await new Promise(resolve=>release=resolve);return copy(f.state());});
 const old=f.window.WujiDeviceUse.open('hand',{sn:SN,kind:'hand',generation:'hand1',side:null});await flush();
 const s=f.state();f.publish({...s,connection_switch:{...s.connection_switch,expected_owner:'f'.repeat(64)}});release();
 await assert.rejects(old,/changed/);assert.equal(f.posts.length,0);assert.deepEqual(f.modelWrites,[]);
});
