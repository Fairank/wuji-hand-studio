'use strict';
// node --test: execute production workflow code with isolated DOM/HTTP contracts.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict'),crypto=require('node:crypto'),{test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'web/device_workflows.js'),'utf8'),clone=x=>JSON.parse(JSON.stringify(x));
const OWNER='a'.repeat(64),SN='WG-SYNTHETIC',HAND='WH-SYNTHETIC';
const before=()=>({csrf:'controller-a',connection:'connected',device_id:HAND,device_profile:{generation:'hand2'},connection_switch:{can_switch:true,busy:false,expected_owner:OWNER}});
const reply=value=>({ok:true,status:200,json:async()=>clone(value)});
const catalog=()=>({...before(),connection_switch:{...before().connection_switch,user_catalog:{busy:false,users:[{user_id:'owner-a',name:'Alice'}]}}});
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};}
async function until(condition){for(let i=0;i<40&&!condition();i++)await new Promise(r=>setTimeout(r,1));assert(condition(),'Fixture did not reach its intended await boundary');}
function environment(fetcher){
 const handlers=new Map(),events=[],sheets=[],posts=[],timers=[],elements=[];
 const add=(map,n,fn)=>{if(!map.has(n))map.set(n,new Set());map.get(n).add(fn);};
 const element=tag=>{const local=new Map();return{tag,children:[],style:{},classList:{add(){}},append(...x){this.children.push(...x);},addEventListener(n,fn){add(local,n,fn);},emit(n){for(const fn of [...local.get(n)||[]])fn();},focus(){}};};
 const window={WujiDeviceInventory:{setUseHandler(){}},WujiLocale:{lang:'en'},addEventListener(n,fn){add(handlers,n,fn);},removeEventListener(n,fn){handlers.get(n)?.delete(fn);},dispatchEvent(e){events.push(e);for(const fn of [...handlers.get(e.type)||[]])fn(e);},HandWorkbenchSheet:{create(options){const sheet={options,body:element('body'),element:element('sheet'),opened:false,closed:0,destroyed:0,open(){this.opened=true;sheets.push(this);},close(){this.closed++;this.element.emit('close');},destroy(){this.destroyed++;}};return sheet;}}};
 const fetch=async(url,opts={})=>{if(opts.body)posts.push(JSON.parse(opts.body));return fetcher(url,opts);};
 vm.runInNewContext(source,{window,document:{getElementById(){},createElement(tag){const e=element(tag);elements.push(e);return e;}},fetch,crypto:crypto.webcrypto,Uint8Array,AbortSignal,AbortController,CustomEvent:class{constructor(type,v){this.type=type;Object.assign(this,v);}},Option:class{constructor(text,value){this.text=text;this.value=value;}},setInterval(fn){const timer={fn,active:true};timers.push(timer);return timer;},clearInterval(timer){timer.active=false;},setTimeout,Date});
 const emit=(name,detail)=>window.dispatchEvent({type:name,detail});
 return {window,events,sheets,posts,timers,elements,fire(token){emit('console-state',{csrf:token});},offline(){emit('console-offline');},run:target=>window.WujiConnectionSwitch.run(target),busy:()=>window.WujiConnectionSwitch.busy()};
}
test('normal unchanged same-controller switch completes through the existing request and state checks',async()=>{
 let states=0;const e=environment(async(url,opts)=>{if(url==='/api/state'){states++;return reply(before());}const command=JSON.parse(opts.body);assert.equal(command.name,'connection_switch');return reply({ok:true,connection_switch:{unchanged:true,request_id:command.request_id}});});e.fire('controller-a');
 const result=await e.run({mode:'hand',serial:HAND});assert.equal(result.unchanged,true);assert.equal(result.state.csrf,'controller-a');assert.equal(states,2);assert.equal(e.posts.length,1);assert.equal(e.busy(),false);
});
test('initial state GET after offline or token replacement cannot submit a switch',async()=>{
 for(const offline of [true,false]){const d=deferred(),e=environment(async()=>d.promise);e.fire('controller-a');const p=e.run({mode:'hand',serial:HAND}).catch(e=>e);if(offline)e.offline();e.fire('controller-b');d.resolve(reply(before()));assert.match((await p).message,/connection changed/);assert.equal(e.posts.length,0);assert.equal(e.busy(),false);}
});
test('final state GET from an old controller cannot be adopted after a new controller appears',async()=>{
 let states=0;const d=deferred(),e=environment(async(url,opts)=>{if(url==='/api/state')return ++states===1?reply(before()):d.promise;const c=JSON.parse(opts.body);return reply({ok:true,connection_switch:{unchanged:true,request_id:c.request_id}});});e.fire('controller-a');const p=e.run({mode:'hand',serial:HAND}).catch(e=>e);await until(()=>states===2);e.fire('controller-b');d.resolve(reply(before()));assert.match((await p).message,/connection changed/);assert.equal(e.posts.length,1);assert.equal(e.busy(),false);
});
test('late profile state GET cannot mount a stale selection sheet',async()=>{
 let states=0;const d=deferred(),e=environment(async(url)=>url==='/api/state'?(++states===1?reply(before()):d.promise):reply({ok:true}));e.fire('controller-a');const p=e.run({mode:'teleoperation',serial:SN,profile:'hand2_left'}).catch(e=>e);await until(()=>states===2);e.offline();e.fire('controller-b');d.resolve(reply(catalog()));assert.match((await p).message,/connection changed/);assert.equal(e.sheets.length,0);assert.equal(e.busy(),false);assert.equal(e.posts.filter(x=>x.name==='connection_switch').length,0);
});
test('late profile JSON after service replacement cannot request further state or open a sheet',async()=>{
 const d=deferred();let states=0,reading=false;const e=environment(async(url)=>{if(url==='/api/state'){states++;return reply(before());}return{ok:true,json:async()=>{reading=true;return d.promise;}};});e.fire('controller-a');const p=e.run({mode:'teleoperation',serial:SN,profile:'hand2_left'}).catch(e=>e);await until(()=>reading);e.fire('controller-b');d.resolve({ok:true});assert.match((await p).message,/connection changed/);assert.equal(states,1);assert.equal(e.sheets.length,0);assert.equal(e.busy(),false);
});
test('an already-open profile sheet closes on service change and releases only the local pending UI',async()=>{
 for(const offline of [true,false]){let reads=0;const e=environment(async(url)=>url==='/api/state'?reply(++reads===1?before():catalog()):reply({ok:true}));e.fire('controller-a');const p=e.run({mode:'teleoperation',serial:SN,profile:'hand2_left'}).catch(e=>e);await until(()=>e.sheets.length===1);if(offline)e.offline();else e.fire('controller-b');assert.match((await p).message,/connection changed/);assert.equal(e.sheets[0].closed,1);assert.equal(e.sheets[0].destroyed,1);assert.equal(e.busy(),false);assert.deepEqual(e.posts.map(x=>x.name),['connection_switch_users']);}
});
test('progress already in flight cannot emit a late event after the switch completed',async()=>{
 const post=deferred(),progress=deferred();let states=0,progressReads=0,command;
 const e=environment(async(url,opts)=>{if(url==='/api/state'){states++;return reply(before());}if(url.startsWith('/api/connection-switch?')){progressReads++;return progress.promise;}command=JSON.parse(opts.body);return post.promise;});e.fire('controller-a');const p=e.run({mode:'hand',serial:HAND});await until(()=>e.posts.length===1);const tick=e.timers[0].fn();await until(()=>progressReads===1);post.resolve(reply({ok:true,connection_switch:{unchanged:true,request_id:command.request_id}}));await p;const eventCount=e.events.length;
 progress.resolve(reply({connection_switch:{request_id:command.request_id,busy:true,phase:'connecting'}}));await tick;assert.equal(e.events.length,eventCount);assert.equal(e.timers[0].active,false);assert.equal(states,2);assert.equal(e.busy(),false);
});
