/* Real inventory source with synthetic API completion times; never devices. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'web/device_inventory.js'),'utf8'),SN='WG_SYNTHETIC_CYCLE_0288';
const clone=value=>JSON.parse(JSON.stringify(value));
function fn(name){const pattern=new RegExp(' (?:async )?function '+name+'\\('),match=pattern.exec(source);assert(match,'Actual inventory function '+name);const begin=match.index;let depth=0;for(let index=source.indexOf('{',begin);index<source.length;index++){if(source[index]==='{')depth++;else if(source[index]==='}'&&!--depth)return source.slice(begin,index+1);}throw Error('Unclosed source function');}
function projection(){let now=1000000;class Clock extends Date{static now(){return now;}}
 const c={window:{WujiDeviceUse:{current:()=>null}},consoleState:{connection:'disconnected',glove:{busy:false,connection:'disconnected'},calibration:{}},
  inventory:{devices:[],ttl_s:30,scanning:false},online:true,scanning:false,previousDisplayRows:[],localError:'',Number,Date:Clock};
 const begin=source.indexOf(' const getTime='),end=source.indexOf(' const visible=()=>',begin);
 vm.runInNewContext(fn('retainedHand')+'\n'+source.slice(begin,end)+'\nthis.rows=rows;this.usable=usable;this.remember=rememberDiscovery;',c);
 return{c,rows:()=>clone(c.rows()),time:value=>now=value,read(value){c.inventory=clone(value);c.remember(c.inventory);}};
}
const advertisement=()=>({sn:SN,kind:'glove',last_seen:990,stale:false,in_use:false,selectable:true,official_available:true});
function cadence(inventory){let now=1021000;class Clock extends Date{static now(){return now;}}const calls=[];
 const c={inventory:clone(inventory),destroyed:false,online:true,discoveryWanted:true,lastRead:now,Date:Clock,
  visible:()=>true,refresh:async()=>calls.push('refresh'),scan:async manual=>calls.push(['scan',manual]),render(){},schedule(){}};
 const a=source.indexOf(' const getTime='),b=source.indexOf(' const sessionOwner=',a);
 vm.runInNewContext(source.slice(a,b)+'\n'+fn('tick')+'\nthis.tick=tick;',c);
 return{c,calls,time(value){now=value;c.lastRead=value;}};
}
test('a slow SDK cycle leaves five full seconds after completion before the next scan',async()=>{
 const f=cadence({devices:[],scanning:false,scanned_at:1000,scan_completed_at:1020});
 await f.c.tick();assert.deepEqual(f.calls,[]);
 f.time(1024999);await f.c.tick();assert.deepEqual(f.calls,[]);
 f.time(1025000);await f.c.tick();assert.deepEqual(f.calls,[['scan',false]]);
});
test('legacy timestamp fallback and future completion never permanently stall discovery',async()=>{
 const old=cadence({devices:[],scanning:false,scanned_at:1020});await old.c.tick();assert.deepEqual(old.calls,[]);
 old.time(1025000);await old.c.tick();assert.deepEqual(old.calls,[['scan',false]]);
 const future=cadence({devices:[],scanning:false,scan_completed_at:2000,scanned_at:1000});await future.c.tick();assert.deepEqual(future.calls,[['scan',false]]);
});
test('backend-preserved in-flight rows keep their real advertisement time without becoming pending history',()=>{
 const f=projection(),row=advertisement();f.read({devices:[row],ttl_s:30,scanning:true,scan_completed_at:999});
 const found=f.rows()[0];assert.equal(found.last_seen,990);assert.equal(found.pending_review,undefined);assert.equal(found.selectable,true);assert.equal(f.c.usable(found),false);
});
test('a completed empty rediscovery removes the previous card immediately rather than calling it awaiting review',()=>{
 const f=projection();f.read({devices:[advertisement()],ttl_s:30,scanning:false,scan_completed_at:999});
 f.read({devices:[],ttl_s:30,scanning:false,scan_completed_at:1000});
 assert.deepEqual(f.rows(),[]);assert.equal(f.c.previousDisplayRows.length,0);
});
test('an empty in-flight legacy response is display-only and expires at its original deadline',()=>{
 const f=projection();f.read({devices:[advertisement()],ttl_s:30,scanning:false});
 f.read({devices:[],ttl_s:30,scanning:true});const row=f.rows()[0];assert.equal(row.last_seen,990);assert.equal(row.selectable,false);assert.equal(f.c.usable(row),false);
 f.time(1020000);assert.deepEqual(f.rows(),[]);
});
test('an error followed by a new empty scan cannot resurrect a retained card',()=>{
 const f=projection();f.read({devices:[advertisement()],ttl_s:30,scanning:false});
 f.read({devices:[],ttl_s:30,scanning:false,error:'Synthetic discovery failure'});assert.deepEqual(f.rows(),[]);
 f.read({devices:[],ttl_s:30,scanning:true});assert.deepEqual(f.rows(),[]);
});

test('only a current fresh API row in an explicitly interruptible read can be selected',()=>{
 const f=projection();f.read({devices:[advertisement()],ttl_s:30,scanning:true,discovery_interruptible:true});
 assert.equal(f.c.usable(f.rows()[0]),true);
 f.c.inventory.discovery_interruptible=false;assert.equal(f.c.usable(f.rows()[0]),false);
 f.c.inventory.discovery_interruptible=true;f.time(1020000);assert.deepEqual(f.rows(),[]);
 const cached=projection();cached.read({devices:[advertisement()],ttl_s:30,scanning:false});
 cached.read({devices:[],ttl_s:30,scanning:true,discovery_interruptible:true});assert.equal(cached.c.usable(cached.rows()[0]),false);
});

test('an explicit use during an interruptible read dispatches once and selection alone never connects',async()=>{
 const row=advertisement(),calls=[],events=[],c={using:false,consumerError:'',pendingMode:'',buttons:{preview:{disabled:false}},
  selected:()=>row,usable:null,clone,t:(zh,en)=>zh,render(){},window:{dispatchEvent:event=>events.push(event)},CustomEvent:class{constructor(name,options){this.type=name;this.detail=options.detail;}}};
 const f=projection();f.read({devices:[row],ttl_s:30,scanning:true,discovery_interruptible:true});c.usable=value=>f.c.usable(value);
 let finish;c.useHandler=(mode,selected)=>{calls.push({mode,sn:selected.sn});return new Promise(resolve=>finish=resolve);};
 vm.runInNewContext(fn('use')+'\nthis.use=use;',c);
 assert.equal(calls.length,0);const pending=c.use('preview');assert.equal(calls.length,1);assert.equal(await c.use('preview'),false);
 finish();assert.equal(await pending,true);assert.deepEqual(calls,[{mode:'preview',sn:SN}]);assert.equal(events.length,1);
});
