/* Execute the production frame lanes with deterministic transport/decode clocks. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const web=path.join(__dirname,'web');
function section(source,start,end){const at=source.indexOf(start);assert(at>=0,start);const to=source.indexOf(end,at);assert(to>at,end);return source.slice(at,to);}
async function fixture(kind='main',options={}){
 const source=fs.readFileSync(path.join(web,kind==='main'?'visual.js':'viewer.js'),'utf8');
 let now=0,id=0,visible=true,fetchFailures=options.failures||0;const timers=new Map(),reads=[],draws=[],decoders=[],failures=[],nodes=new Map();
 const state={csrf:'synthetic-view-service',connection:'disconnected',view_source:'glove',teleoperation_view_mode:'target',device_profile:{id:'hand2_right'},glove:{busy:true,connection:'receiving',session_id:7,glove_serial:'WG_SYNTHETIC_VIEW',profile:'hand2_right',user:{user_id:'synthetic-wearer'},stream:{fresh:true,seq:1},feedback:{}}};
 function node(){return{hidden:false,style:{display:'',removeProperty(){}},classList:{toggle(){},remove(){}},removeAttribute(){},replaceChildren(){},append(){},textContent:''};}
 function get(key){if(!nodes.has(key)){const n=node();if(key==='pose-image'||key==='image')Object.defineProperty(n,'src',{set(value){draws.push({at:now,value});},get(){return'';}});nodes.set(key,n);}return nodes.get(key);}
 const timeout=(fn,ms)=>{timers.set(++id,{at:now+Math.max(0,ms),fn});return id;};
 const ctx={AbortController,AbortSignal,performance:{now:()=>now},state,sourceScope:'source-A',viewRevision:0,serviceOffline:false,viewPolling:false,viewRequest:null,viewTimer:0,decodedImage:null,hasImage:false,lastImageScope:null,lastImageMode:null,viewStatus:{kind:'loading'},camera:null,pointer:null,busy:false,dirty:false,
  setTimeout:timeout,clearTimeout:key=>timers.delete(key),visible:()=>visible,syncView(){},text:(zh,en)=>en,english:()=>true,sourceKind:mode=>mode==='glove_constrained_unavailable'?'glove_constrained':mode,
  demonstrationProfile:()=>null,demonstrationMode:()=>'',virtualTeleoperation:()=>true,$:get,byId:get,cells:[],fmt:v=>String(v),document:{createElement:node},renderStatus(){},
  discardImage(){ctx.hasImage=false;},clearVisual(message,kind='error',meta=null){failures.push({at:now,message});ctx.viewStatus={kind,message,meta};},
  Image:class{
   set src(value){this.value=value;}
   decode(){const d={image:this,resolve:null};decoders.push(d);return new Promise(resolve=>{d.resolve=resolve;if(!options.neverDecode)timeout(resolve,options.decodeMs||0);});}
  },
  fetch:async(url,init={})=>{
   const ordinal=reads.filter(r=>r.url===url).length;reads.push({at:now,url});
   const delay=typeof options.fetchMs==='function'?options.fetchMs(url,ordinal):options.fetchMs||0;
   await new Promise((resolve,reject)=>{let finished=false;const finish=fn=>{if(finished)return;finished=true;ctx.clearTimeout(timer);init.signal?.removeEventListener('abort',abort);fn();};const abort=()=>finish(()=>reject(Object.assign(Error('synthetic aborted request'),{name:'AbortError'})));const timer=timeout(()=>finish(resolve),delay);if(init.signal?.aborted)abort();else init.signal?.addEventListener('abort',abort,{once:true});});
   if(url==='/api/state')return{ok:true,json:async()=>structuredClone(state)};
   if(fetchFailures-->0)return{ok:false,status:503,json:async()=>({error:'Synthetic frame unavailable'})};
   const meta={ready:true,mode:options.defaultDisplay?'preview':'glove_constrained',device_profile:state.device_profile.id,view_source:'glove',glove_seq:state.glove.stream.seq,collision_preview:{profile:state.glove.profile,bridge_session_id:state.glove.session_id,glove_serial:state.glove.glove_serial},feedback:{},joints:[],render_hz:20};
   if(options.frameAge!==undefined)meta.frame_age_ms=options.frameAge;if(options.frameTimeout!==undefined)meta.frame_timeout_ms=options.frameTimeout;
   return{ok:true,json:async()=>({meta,image:'data:synthetic-frame-'+ordinal})};
  }
 };
 let code;
 if(kind==='main'){
  const helpers=source.includes('  function frameDeadline(')?section(source,'  function frameDeadline(','  async function showFrame('):'';
  code=section(source,'  function payloadMatches(','  function discardImage(')+helpers+section(source,'  async function showFrame(','  function mappingForm(')+section(source,'  function scheduleView(','  function syncView(')+section(source,'  async function pollView(','  const updateVisibility=')+'\nthis.start=pollView;';
 }else{
  const helpers=source.includes('function frameDeadline(')?section(source,'function frameDeadline(','async function poll(){'):'';
  code=section(source,'function stateScope(','const fallback=')+helpers+source.slice(source.indexOf('async function poll(){'),source.lastIndexOf('\npoll();'))+'\nthis.start=poll;';
 }
 vm.createContext(ctx);vm.runInContext(code,ctx);
 async function flush(){for(let n=0;n<40;n++)await Promise.resolve();}
 async function advance(to){await flush();let count=0;while(true){const tasks=[...timers].filter(([,v])=>v.at<=to).sort((a,b)=>a[1].at-b[1].at||a[0]-b[0]);if(!tasks.length)break;assert(++count<5000,'bounded frame loop');const [key,task]=tasks[0];timers.delete(key);now=task.at;task.fn();await flush();}now=to;await flush();}
 return{start:()=>{ctx.start();},advance,reads,draws,failures,decoders,get,state,ctx,
  changeSource(){ctx.sourceScope='source-B';ctx.viewRevision++;ctx.viewRequest?.abort();},
  hide(){visible=false;ctx.viewRevision++;ctx.viewRequest?.abort();},
  resolveFirst:async()=>{decoders[0].resolve();await flush();}};
}
test('main frame lane schedules from request start instead of waiting another50ms after decoding',async()=>{const f=await fixture('main',{fetchMs:15,decodeMs:10});f.start();await f.advance(210);assert.deepEqual(f.reads.map(r=>r.at),[0,50,100,150,200]);assert.equal(f.ctx.viewStatus.kind,'ready');});
test('independent viewer keeps its100ms start cadence while preserving both identity reads',async()=>{const f=await fixture('viewer',{fetchMs:15,decodeMs:10});f.start();await f.advance(240);assert.deepEqual(f.reads.filter(r=>r.url==='/api/view').map(r=>r.at),[0,100,200]);assert.equal(f.reads.filter(r=>r.url==='/api/state').length,6);assert.equal(f.draws.length,3);});
test('source expiry during decoding withholds the late image and permits a new request',async()=>{for(const kind of ['main','viewer']){const f=await fixture(kind,{frameAge:10,frameTimeout:100,decodeMs:120});f.start();await f.advance(210);assert.equal(f.draws.length,0,kind);assert(f.reads.filter(r=>r.url!=='/api/state').length>=2,kind);}});
test('viewer source expiry while waiting for post-decode identity cannot mark the old frame ready',async()=>{const f=await fixture('viewer',{frameAge:10,frameTimeout:100,decodeMs:10,fetchMs:(url,n)=>url==='/api/state'&&n%2===1?180:0});f.start();await f.advance(230);assert.equal(f.draws.length,0);assert(f.reads.filter(r=>r.url==='/api/view').length>=2);assert.equal(f.get('stale').hidden,false);});
test('a never-settling decoder is bounded and its late success cannot publish after retry',async()=>{for(const kind of ['main','viewer']){const f=await fixture(kind,{defaultDisplay:true,neverDecode:true});f.start();await f.advance(2700);assert(f.reads.filter(r=>r.url!=='/api/state').length>=2,kind);await f.resolveFirst();assert.equal(f.draws.length,0,kind);}});
test('page/source retirement during decoding cannot publish an old image or stop a device',async()=>{for(const change of ['changeSource','hide']){const f=await fixture('main',{decodeMs:150});f.start();await f.advance(20);f[change]();await f.advance(220);assert(!f.draws.some(d=>d.value==='data:synthetic-frame-0'));assert(f.reads.every(r=>r.url==='/api/view?teleoperation=preview'));}});
test('fast request failure can retry at normal cadence and missing time fields preserve default displays',async()=>{const f=await fixture('main',{defaultDisplay:true,failures:1,decodeMs:5});f.start();await f.advance(110);assert.equal(f.failures.length,1);assert(f.draws.length>=1);assert.equal(f.ctx.viewStatus.kind,'ready');assert.deepEqual(f.reads.map(r=>r.at),[0,50,100]);});
test('malformed or incomplete source timing never impersonates an unlimited fresh frame',async()=>{for(const fields of [{frameAge:-1,frameTimeout:100},{frameAge:'1',frameTimeout:100},{frameAge:0},{frameTimeout:100},{frameAge:0,frameTimeout:false}]){for(const kind of ['main','viewer']){const f=await fixture(kind,fields);f.start();await f.advance(60);assert.equal(f.draws.length,0,kind+JSON.stringify(fields));}}});
test('viewer post-decode owner change withholds the old image while preserving an active camera gesture',async()=>{const f=await fixture('viewer',{decodeMs:10,fetchMs:(url,n)=>url==='/api/state'&&n%2===1?30:0});f.ctx.pointer={id:2};f.ctx.camera={azimuth:42};f.start();await f.advance(15);f.state.glove.session_id=8;await f.advance(50);assert.equal(f.draws.length,0);assert.equal(f.ctx.camera.azimuth,42);await f.advance(150);assert.equal(f.draws.length,1);assert.equal(f.draws[0].value,'data:synthetic-frame-1');});
test('newer target sequence in the same SDK scope does not starve a still-timely certified image',async()=>{const f=await fixture('main',{frameAge:5,frameTimeout:250,decodeMs:30});f.start();await f.advance(10);f.state.glove.stream.seq=50;await f.advance(40);assert.equal(f.draws.length,1);assert.equal(f.ctx.viewStatus.kind,'ready');});
