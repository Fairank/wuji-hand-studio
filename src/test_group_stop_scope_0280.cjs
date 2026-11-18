'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/workspaces.js'),'utf8');
const expose=`globalThis.scope={stopAll,onCommand,beat,act,token:()=>csrf,setToken:v=>csrf=v,
 setLease:(lease,service)=>{leases.set('main',lease);leaseAt.set('main',performance.now());if(typeof leaseServices!=='undefined')leaseServices.set('main',service);},
 lease:()=>leases.get('main'),setSnapshot:v=>snap=v};`;
const code=source.slice(0,source.lastIndexOf('})();'))+expose+source.slice(source.lastIndexOf('})();'));
const A='a'.repeat(32),B='b'.repeat(32);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
 const posts=[],reads=[],held=[];let holdStart=false,holdBeat=false;
 const reply=(value,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify(value)});
 const snapshot={workspaces:[{id:'main',members:['L','R'],run:{active:true}}],endpoints:[],actions:[],discovery:{devices:[]}};
 const context={URLSearchParams,AbortController,setTimeout,clearTimeout,performance,location:{search:''},localStorage:{getItem:()=>null},window:{},document:{readyState:'loading',hidden:false,addEventListener(){}},fetch:async(url,options={})=>{
  if(!options.method){reads.push(url);return reply(url==='/api/state'?{csrf:'service-B'}:snapshot);}
  const body=JSON.parse(options.body);const row={...body,headers:options.headers,keepalive:options.keepalive};posts.push(row);
  if((holdStart&&body.name==='ensemble_start')||(holdBeat&&body.name==='ensemble_beat'))return new Promise(resolve=>held.push({row,resolve:(body,status)=>resolve(reply(body,status))}));
  return reply(body.name==='ensemble_start'?{ok:true,group_lease:A}:{ok:true});
 }};
 vm.createContext(context);vm.runInContext(code,context);context.scope.setSnapshot(snapshot);context.scope.setToken('service-A');
 return {...context.scope,posts,reads,held,holdStart:()=>holdStart=true,holdBeat:()=>holdBeat=true};
}
const spec={members:['L','R'],mode:'preview',kind:'pair',action:'pair_wave',speed:4,cycles:1,amplitude:.5};

test('old pagehide cleanup retains its original lease and service after replacement',async()=>{
 const f=fixture();f.setLease(A,'service-A');f.setToken('service-B');f.stopAll();await flush();
 assert.equal(f.posts.length,1);assert.equal(f.posts[0].lease,A);assert.equal(f.posts[0].headers['X-Console-Token'],'service-A');assert.equal(f.posts[0].keepalive,true);
 assert.equal(f.lease(),undefined);assert.equal(f.reads.length,0);
});
test('pending start reply after explicit stop cleans only that returned reservation',async()=>{
 const f=fixture();f.holdStart();const start=f.onCommand('start',spec);await flush();
 await f.onCommand('stop');f.held[0].resolve({ok:true,group_lease:A});await start;
 const stops=f.posts.filter(p=>p.name==='ensemble_stop');assert.equal(stops.length,2);assert.equal(stops[0].lease,undefined);assert.equal(stops[1].lease,A);assert.equal(stops[1].headers['X-Console-Token'],'service-A');
});
test('pagehide while start acknowledgement is pending cleans its exact late reply',async()=>{
 const f=fixture();f.holdStart();const start=f.onCommand('start',spec);await flush();f.stopAll();f.held[0].resolve({ok:true,group_lease:A});await start;
 assert.equal(f.lease(),undefined);const stops=f.posts.filter(p=>p.name==='ensemble_stop');assert.equal(stops.length,1);assert.equal(stops[0].lease,A);assert.equal(stops[0].headers['X-Console-Token'],'service-A');
});
test('a late start reply cannot adopt a replacement service or its token for cleanup',async()=>{
 const f=fixture();f.holdStart();const start=f.onCommand('start',spec);await flush();f.setToken('service-B');f.held[0].resolve({ok:true,group_lease:A});await start;
 assert.equal(f.lease(),undefined);assert.equal(f.posts.filter(p=>p.name==='ensemble_beat').length,0);
 for(const stop of f.posts.filter(p=>p.name==='ensemble_stop')){assert.equal(stop.lease,A);assert.equal(stop.headers['X-Console-Token'],'service-A');}
 assert.equal(f.token(),'service-B');
});
test('explicit current Stop remains unscoped and uses the current service',async()=>{
 const f=fixture();f.setLease(A,'service-A');f.setToken('service-B');await f.onCommand('stop');
 assert.equal(f.posts[0].lease,undefined);assert.equal(f.posts[0].headers['X-Console-Token'],'service-B');
});
test('an unresolved old beat neither blocks nor clears a replacement lease heartbeat',async()=>{
 const f=fixture();f.holdBeat();f.setLease(A,'service-A');f.beat();await flush();
 f.setLease(B,'service-B');f.setToken('service-B');f.beat();await flush();assert.equal(f.held.length,2);
 f.held[0].resolve({error:'old service'},403);await flush();assert.equal(f.token(),'service-B');f.beat();await flush();assert.equal(f.held.length,2);
 assert.equal(f.held[1].row.lease,B);assert.equal(f.held[1].row.headers['X-Console-Token'],'service-B');f.held[1].resolve({ok:true});await flush();
});
