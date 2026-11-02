/* The main frame lane validates the service envelope as well as owner metadata. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),assert=require('node:assert/strict');
const helper=path.join(__dirname,'test_view_frame_latency_0272.cjs'),m=new Module(helper,module);m.filename=helper;m.paths=module.paths;m._compile(fs.readFileSync(helper,'utf8').split("test('main frame lane")[0]+'\nmodule.exports={fixture};',helper);
async function fixture(options={}){
 const f=await m.exports.fixture('main',{decodeMs:5,frameAge:5,frameTimeout:250,...options});f.state.csrf='service-B';const fetch=f.ctx.fetch;let ordinal=0;
 f.ctx.fetch=async(url,init)=>{const issued=f.state.csrf,response=await fetch(url,init),data=await response.json();data.service_token=typeof options.token==='function'?options.token(ordinal++,issued):Object.hasOwn(options,'token')?options.token:issued;return{ok:true,json:async()=>data};};
 return f;
}
test('main frame lane preserves current-service rendering and the existing50ms cadence',async()=>{const f=await fixture();f.start();await f.advance(70);assert.equal(f.ctx.viewStatus.kind,'ready');assert.deepEqual(f.reads.map(r=>r.at),[0,50]);assert.equal(f.draws.length,2);});
test('main current-state B cannot display a foreign service A frame, then normal B recovers',async()=>{const f=await fixture({token:(n,current)=>n===0?'service-A':current});f.start();await f.advance(20);assert.equal(f.draws.length,0);assert.equal(f.ctx.viewStatus.kind,'unavailable');assert.equal(f.decoders.length,0);await f.advance(70);assert.equal(f.draws.length,1);assert.equal(f.draws[0].value,'data:synthetic-frame-1');assert.equal(f.ctx.viewStatus.kind,'ready');});
test('main frame cannot publish after only the service identity changes during decoding',async()=>{const f=await fixture({neverDecode:true});f.start();await f.advance(10);f.state.csrf='service-C';await f.resolveFirst();await f.advance(20);assert.equal(f.draws.length,0);assert.equal(f.ctx.viewStatus.kind,'unavailable');});
test('main frame requires a nonempty exact current-service token for every source',async()=>{for(const token of [undefined,null,'',false,7]){const f=await fixture({token,defaultDisplay:true});f.start();await f.advance(20);assert.equal(f.draws.length,0);assert.equal(f.decoders.length,0);assert.equal(f.ctx.viewStatus.kind,'unavailable');}});
