/* Independent-window frames belong to one service across both identity reads. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),assert=require('node:assert/strict');
const helpers=path.join(__dirname,'test_view_frame_latency_0272.cjs'),m=new Module(helpers,module);m.filename=helpers;m.paths=module.paths;m._compile(fs.readFileSync(helpers,'utf8').split("test('main frame lane")[0]+'\nmodule.exports={fixture};',helpers);
async function fixture(options={}){
 const f=await m.exports.fixture('viewer',{neverDecode:true,frameAge:5,frameTimeout:250,...options});
 f.state.csrf='service-A';const fetch=f.ctx.fetch;
 f.ctx.fetch=async(url,init)=>{
  const issuedService=f.state.csrf,response=await fetch(url,init);
  if(url!=='/api/view')return response;
  const data=await response.json();data.service_token=Object.hasOwn(options,'serviceToken')?options.serviceToken:issuedService;
  return{ok:true,json:async()=>data};
 };
 return f;
}
test('same-service frame still displays and the next frame preserves the original cadence',async()=>{const f=await fixture();f.start();await f.advance(10);await f.resolveFirst();await f.advance(20);assert.equal(f.draws.length,1);await f.advance(110);assert.deepEqual(f.reads.filter(r=>r.url==='/api/view').map(r=>r.at),[0,100]);});
test('service change during decoding rejects the old image and the next explicit display cycle recovers',async()=>{const f=await fixture();f.start();await f.advance(10);f.state.csrf='service-B';await f.resolveFirst();await f.advance(20);assert.equal(f.draws.length,0);assert.equal(f.get('stale').hidden,false);await f.advance(110);f.decoders[1].resolve();await f.advance(120);assert.equal(f.draws.length,1);assert.equal(f.draws[0].value,'data:synthetic-frame-1');});
test('parallel state-B and old frame-A responses cannot be accepted as one source',async()=>{const f=await fixture({serviceToken:'service-A'});f.state.csrf='service-B';f.start();await f.advance(10);assert.equal(f.decoders.length,0);assert.equal(f.draws.length,0);assert.equal(f.get('stale').hidden,false);});
for(const token of [undefined,null,'',false,7])test('missing or invalid frame service identity is unavailable: '+String(token),async()=>{const f=await fixture({serviceToken:token});f.start();await f.advance(10);assert.equal(f.decoders.length,0);assert.equal(f.draws.length,0);assert.equal(f.get('stale').hidden,false);});
test('post-decode identity service change cannot make an old image ready',async()=>{const f=await fixture({fetchMs:(url,n)=>url==='/api/state'&&n%2===1?40:0});f.start();await f.advance(10);await f.resolveFirst();f.state.csrf='service-B';await f.advance(60);assert.equal(f.draws.length,0);assert.equal(f.get('stale').hidden,false);});
