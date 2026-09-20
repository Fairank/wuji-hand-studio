// Exercise the shared UI command path without a DOM, SDK or hardware.
const {readFileSync}=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=readFileSync(path.join(__dirname,'../src/web/app.js'),'utf8');
const start=source.indexOf('window.WujiConnection='),end=source.indexOf("  el('connect').addEventListener",start);
assert(start>0&&end>start);
function setup(hardware,stopSucceeds=true,confirmed=true){
  const calls=[],errors=[];let clock=0;
  const c={window:{},state:{hardware},service:true,pending:false,Date:{now:()=>clock+=3000},setTimeout:fn=>fn(),
    action:async cmd=>{calls.push(cmd);return cmd.name!=='hardware_stop'||stopSucceeds;},
    freshState:async()=>{c.state.hardware={active:!confirmed,stop_confirmed:confirmed};},showError:e=>errors.push(e)};
  vm.runInNewContext(source.slice(start,end),c);return {api:c.window.WujiConnection,calls,errors};
}
(async()=>{
  let t=setup({active:false});assert(await t.api.connect('',''));assert.equal(t.calls[0].auto_detect,true);assert.equal(t.calls[0].address,'');
  t=setup({active:false});assert(await t.api.disconnect());assert.deepEqual(t.calls.map(c=>c.name),['disconnect']);
  t=setup({active:true});assert(await t.api.disconnect());assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop','disconnect']);
  t=setup({active:true},false);assert.equal(await t.api.disconnect(),false);assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop']);
  t=setup({active:true},true,false);assert.equal(await t.api.disconnect(),false);assert.deepEqual(t.calls.map(c=>c.name),['hardware_stop']);assert.equal(t.errors.length,1);
  console.log('5 connection flow cases passed; no hardware used');
})().catch(e=>{console.error(e);process.exitCode=1;});
