/* Real shared feature coordinator; local owner viewing never opens another use. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const prelude=fs.readFileSync(path.join(__dirname,'test_device_connect_navigation_0267.cjs'),'utf8').split('for(const [kind,mode,viewHash]')[0],context={require,__dirname,process,structuredClone,console,AbortSignal,AbortController,setTimeout,setInterval,clearInterval,URL};vm.runInNewContext(prelude+'\nthis.fixture=fixture;',context);const fixture=context.fixture;
const count=f=>f.calls.filter(c=>c.body?.name==='connection_switch').length;
function home(f){f.location.hash='#home';}
test('same-SN SDK owner View preserves session/wearer/profile and sends no new switch or control',async()=>{const f=fixture();await f.use('preview');const owner=structuredClone(f.state().connection_switch.current_owner),before=count(f);home(f);await f.use('official');assert.equal(f.location.hash,'#glove');assert.deepEqual(JSON.parse(JSON.stringify(f.state().connection_switch.current_owner)),owner);assert.equal(count(f),before);});
test('CLI View remains on its exact current purpose without another connection request',async()=>{const f=fixture();await f.use('official');home(f);const before=count(f);await f.use('official');assert.equal(f.location.hash,'#connection');assert.equal(count(f),before);});
test('explicit CLI choice still transfers SDK purpose and Hand Connect keeps original navigation',async()=>{const f=fixture();await f.use('preview');await f.window.WujiDeviceUse.open('official',{kind:'glove',serial:f.sn});assert.equal(f.state().connection_switch.current_owner.mode,'official');assert.equal(count(f),2);const h=fixture('hand');await h.use('hand');assert.equal(h.location.hash,'#home');assert.equal(h.state().connection_switch.current_owner.mode,'hand');});
test('a changed owner/session before View verification rejects instead of reconnecting the captured purpose',async()=>{const f=fixture();await f.use('preview');home(f);const before=count(f),p=f.use('official');p.catch(()=>{});f.state().glove.session_id=9;f.state().connection_switch.current_owner.session=9;await assert.rejects(p,/changed|变化/);assert.equal(f.location.hash,'#home');assert.equal(count(f),before);});
test('offline or service replacement retires a pending View and its old response cannot navigate',async()=>{for(const replace of [false,true]){const f=fixture();await f.use('preview');home(f);const before=count(f),p=f.use('official');p.catch(()=>{});f.window.dispatchEvent({type:'console-offline'});if(replace){f.state().csrf='new-view-service';f.window.dispatchEvent({type:'console-state',detail:structuredClone(f.state())});}await p.catch(()=>{});assert.equal(f.location.hash,'#home');assert.equal(count(f),before);}});

test('the real directory renderer labels both verified glove purposes View and does not require discovery for existing ownership',()=>{const inventory=fs.readFileSync(path.join(__dirname,'web/device_inventory.js'),'utf8'),start=inventory.indexOf('  const connectedOwner=sessionOwner()'),end=inventory.indexOf('  setText(moreLabel',start),disabled=inventory.match(/buttons\.official\.disabled=([^;]+);/)[1];for(const mode of ['official','teleoperation']){const c={sessionOwner:()=>({kind:'glove',mode,serial:'WG_VIEW'}),row:{kind:'glove',sn:'WG_VIEW',official_available:false},sn:r=>r.sn,canUse:true,buttons:{official:{}}};vm.runInNewContext(inventory.slice(start,end)+'\nbuttons.official.disabled='+disabled+';this.view=gloveView;',c);assert.equal(c.view,true);assert.equal(c.buttons.official.disabled,false);}});

test('leaving the page while View is being verified retires only that read-only navigation',async()=>{const f=fixture();await f.use('preview');home(f);const before=count(f),p=f.use('official');p.catch(()=>{});f.location.hash='#settings';f.window.dispatchEvent({type:'workspace-page'});await p.catch(()=>{});assert.equal(f.location.hash,'#settings');assert.equal(count(f),before);});

test('Hand View opens the retained verified hand without discovery or another switch',async()=>{
 const f=fixture('hand');await f.use('hand');home(f);
 f.window.WujiDeviceInventory.snapshot=()=>({devices:[]});
 let discovery=0,profile=null;
 f.window.WujiDeviceInventory.refresh=async()=>{discovery++;};
 f.window.WujiDemonstration={selectProfile:value=>{profile=value;}};
 const before=count(f);
 await f.use('hand');
 assert.equal(discovery,0);
 assert.equal(count(f),before);
 assert.equal(f.location.hash,'#library');
 assert.equal(profile,'hand2_right');
});

test('Hand View rejects a changed side, stale session or unverified identity before navigating',async()=>{
 const changes=[
  value=>{value.device_profile={id:'hand2_left',generation:'hand2',side:'left'};value.identified_device.profile='hand2_left';},
  value=>{value.session_generation++;value.connection_switch.current_owner.session++;},
  value=>{value.identified_device.source='unverified';},
 ];
 for(const change of changes){
  const f=fixture('hand');await f.use('hand');home(f);
  const before=count(f);
  change(f.state());
  await assert.rejects(f.use('hand'),/changed|变化/);
  assert.equal(f.location.hash,'#home');
  assert.equal(count(f),before);
 }
});
