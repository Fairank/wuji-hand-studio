// Verify the real timing renderer against current and legacy worker DTOs.
const {readFileSync}=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const {test}=require('node:test');
const source=readFileSync(path.join(__dirname,'web/playback.js'),'utf8');
const start=source.indexOf('  function renderMotionPanel('),end=source.indexOf('    const config=',start);
assert(start>=0&&end>start);
function render(timing){
 const output={textContent:''},context={get:id=>{assert.equal(id,'command-timing');return output;}};
 vm.createContext(context);vm.runInContext(source.slice(start,end)+'}',context);
 context.renderMotionPanel({active:false,command_timing:timing},true);return output.textContent;
}
test('Hand 1 requested_hz renders its target without inventing measured feedback',()=>{
 const text=render({requested_hz:200});assert.match(text,/指令目标 200 Hz/);
 assert.match(text,/实测发布 — Hz/);assert.match(text,/最长间隔 — ms/);assert.doesNotMatch(text,/undefined/);
});
test('current target_hz takes precedence over legacy values and keeps measurements',()=>{
 const text=render({target_hz:500,requested_hz:200,host_publish_hz:498.5,max_interval_ms:3.25});
 assert.match(text,/指令目标 500 Hz/);assert.match(text,/实测发布 498.5 Hz/);assert.match(text,/最长间隔 3.25 ms/);
});
test('missing target uses the unavailable marker',()=>{
 const text=render({});assert.match(text,/指令目标 — Hz/);assert.doesNotMatch(text,/undefined/);
});
