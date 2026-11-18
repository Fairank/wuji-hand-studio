// Exercise the shared UI command path without a DOM, SDK or hardware.
const {readFileSync}=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=readFileSync(path.join(__dirname,'../src/web/app.js'),'utf8');
const start=source.indexOf('window.WujiConnection='),end=source.indexOf("  el('connect').addEventListener",start);
assert(start>0&&end>start);
function setup(hardware,stopSucceeds=true,confirmed=true,stillPending=false){
  const calls=[],errors=[];let clock=0;
  const c={window:{},state:{hardware},service:true,pending:false,Date:{now:()=>clock+=3000},setTimeout:fn=>fn(),
    action:async cmd=>{calls.push(cmd);return cmd.name!=='hardware_stop'||stopSucceeds;},
    freshState:async()=>{c.state.hardware={active:!confirmed,stop_confirmed:confirmed,start_pending:stillPending};},showError:e=>errors.push(e)};
  vm.runInNewContext(source.slice(start,end),c);return {api:c.window.WujiConnection,calls,errors};
}
(async()=>{
  let t=setup({active:false});assert(await t.api.connect('',''));assert.equal(t.calls[0].auto_detect,true);assert.equal(t.calls[0].address,'');
  t=setup({active:false});assert(await t.api.disconnect());assert.deepEqual(t.calls.map(c=>c.name),['disconnect']);
  t=setup({active:true});assert(await t.api.disconnect());assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop','disconnect']);
  t=setup({active:true},false);assert.equal(await t.api.disconnect(),false);assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop']);
  t=setup({active:true},true,false);assert.equal(await t.api.disconnect(),false);assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop']);assert.equal(t.errors.length,1);
  t=setup({active:null,stop_confirmed:false});assert(await t.api.disconnect());assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop','disconnect']);
  t=setup({active:null,stop_confirmed:false},true,false);assert.equal(await t.api.disconnect(),false);assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop']);
  t=setup({active:false,stop_confirmed:true,start_pending:true});assert(await t.api.disconnect());assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop','disconnect']);
  t=setup({active:false,stop_confirmed:true,start_pending:true},true,true,true);assert.equal(await t.api.disconnect(),false);assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop']);assert.equal(t.errors.length,1);
  t=setup({active:false,stop_confirmed:false});assert(await t.api.disconnect());assert.deepEqual(t.calls.map(c=>c.name),['disconnect']);
  const listenerStart=source.indexOf("  el('disconnect').addEventListener"),listenerEnd=source.indexOf('\n',listenerStart);
  assert(listenerStart>end&&listenerEnd>listenerStart);
  let click,disconnects=0;
  vm.runInNewContext(source.slice(listenerStart,listenerEnd),{el:id=>{assert.equal(id,'disconnect');return {addEventListener:(event,handler)=>{assert.equal(event,'click');click=handler;}};},window:{WujiConnection:{disconnect:()=>{disconnects++;}}}});
  click();assert.equal(disconnects,1);
  console.log('11 connection flow cases passed; no hardware used');
})().catch(e=>{console.error(e);process.exitCode=1;});
