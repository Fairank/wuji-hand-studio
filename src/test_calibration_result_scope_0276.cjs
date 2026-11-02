/* Actual preparation renderer; no SDK, device, native process or network. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'web/connection_hub.js'),'utf8');
const start=source.indexOf(' function renderGuidedCalibration(c){'),end=source.indexOf(' function renderCalibration(){',start);assert(start>=0&&end>start);
const SN='WG_SYNTHETIC_RESULT_SCOPE',ID='a'.repeat(32),PROFILE='Morgan';
function fixture(){
 const fields=new Map(),parts=new Map();let viewer={fresh:true,state:{active:false,phase:'idle',session_id:null}},lastGuide,lastMeta;
 const node=()=>({hidden:false,disabled:false,checked:false,value:'',dataset:{},textContent:'',options:[{},{}],append(child){child.parentElement=this;},closest(){return this;}});
 const field=id=>{if(!fields.has(id))fields.set(id,node());return fields.get(id);};
 const calib=node(),preparation=node(),previous=node(),guideHost=node(),advanced=node(),calibrationPanel=node();
 calib.querySelector=selector=>{if(selector==='.hub-calibration-preparation')return preparation;if(!parts.has(selector))parts.set(selector,node());return parts.get(selector);};
 field('hub-profile').value=PROFILE;field('hub-side').value='right';field('hub-calib-interface').value='guided';
 const device={sn:SN,device_type:'wuji_glove',side:'right'};
 const person={name:PROFILE,right_hand:{calibrated:true},left_hand:{calibrated:false}};
 const data={available:true,calibration_supported:true,guided_native:true,metadata_current:true,current:person,users:[person],run:{id:ID,serial:SN,user:PROFILE,side:'right',running:false,status:'completed',source:'official_jsonl_native',interface:'guided',exit_code:0,cleanup_confirmed:true}};
 const ctx={consoleState:{csrf:'A',calibrated_teleoperation:{run_id:ID,can_continue:true}},calibrationFresh:true,busy:false,cancelStartPending:false,calibrationStartPending:false,startCancelRequested:false,refreshPending:false,actionError:'',refreshError:'',replacementContext:'',lastStartedRunId:ID,lastStartedContext:null,previousResultRunId:'',nextAction:null,
  object:v=>!!v&&typeof v==='object'&&!Array.isArray(v),t:(zh,en)=>en,$:field,calib,previous,guideHost,advanced,calibrationPanel,
  officialViz:{snapshot:()=>viewer,selection:()=>({serial:SN,device}),setCalibrationState:value=>lastMeta=value},
  sideOf:row=>row?.side||row?.handedness||'',calibrationSdkOwner:()=>false,gloveFresh:()=>true,sessionBlock:()=>'',calibrationVisible:()=>true,
  write:(id,value)=>field(id).textContent=value,options:(element,items,value)=>element.value=value,
  guide:{render:(value,context)=>lastGuide={data:value,context}}};
 vm.createContext(ctx);vm.runInContext(source.slice(start,end)+'\nthis.render=renderGuidedCalibration;',ctx);
 return {ctx,data,field,calib,preparation,previous,guideHost,parts,render:()=>ctx.render(data),viewer:value=>viewer=value,guide:()=>lastGuide,meta:()=>lastMeta,newViewer:()=>viewer={fresh:true,state:{active:true,phase:'running',session_id:'b'.repeat(32),reported_device:device}}};
}
test('same service complete then stale SDK then a new exact CLI session moves the old result into history',()=>{
 const f=fixture();f.render();assert.equal(f.calib.dataset.currentResult,'true');assert.equal(f.previous.hidden,true);
 f.ctx.consoleState.calibrated_teleoperation={run_id:ID,connected:true,busy:false};f.render();assert.equal(f.calib.dataset.currentResult,'true');
 f.ctx.consoleState.calibrated_teleoperation={run_id:ID,connected:false,busy:false,phase:'connecting'};f.render();
 f.newViewer();f.render();assert.equal(f.calib.dataset.currentResult,'false');assert.equal(f.previous.hidden,false);assert.equal(f.guideHost.parentElement,f.previous);assert.equal(f.preparation.hidden,false);
 assert.equal(f.field('hub-profile').value,PROFILE);assert.equal(f.field('hub-calib-interface').value,'guided');assert.match(f.field('hub-calib-ready-note').textContent,/Confirm replacement/);
});
test('a continuation offer cannot attribute an old run to a new Viewer even without a local start receipt',()=>{
 const f=fixture();f.ctx.lastStartedRunId='';f.newViewer();f.render();assert.equal(f.calib.dataset.currentResult,'false');assert.equal(f.guide().context.previous,true);
});
test('current guided completion without a replacement Viewer remains in the result area',()=>{
 const f=fixture();f.render();assert.equal(f.calib.dataset.currentResult,'true');assert.equal(f.previous.hidden,true);assert.equal(f.guideHost.parentElement,f.calib);assert.equal(f.guide().context.previous,false);
});
test('running original terminal and unconfirmed cleanup stay current and keep the chosen interface',()=>{
 for(const cleanup of [false,true]){const f=fixture();f.field('hub-calib-interface').value='terminal';Object.assign(f.data.run,{source:'official_tty',interface:'terminal',running:!cleanup,status:cleanup?'session_ended':'terminal',user:null});f.data.cleanup_required=cleanup;f.render();assert.equal(f.previous.hidden,true);assert.equal(f.guide().context.previous,false);assert.equal(f.preparation.hidden,true);assert.equal(f.field('hub-calib-interface').value,'terminal');assert.equal(f.parts.get('.hub-calib-profile-field').hidden,true);}
});
test('terminal completion followed by exact CLI reconnect preserves terminal choice and wearer while keeping the old terminal historical',()=>{
 const f=fixture();f.field('hub-calib-interface').value='terminal';Object.assign(f.data.run,{source:'official_tty',interface:'terminal',status:'session_ended',user:null});f.newViewer();f.render();assert.equal(f.previous.hidden,false);assert.equal(f.preparation.hidden,false);assert.equal(f.field('hub-calib-interface').value,'terminal');assert.equal(f.field('hub-profile').value,PROFILE);assert.equal(f.meta().human,true);
});
test('late old run reads remain historical after new CLI preparation, while the next real run becomes current',()=>{
 const f=fixture();f.newViewer();f.render();f.render();assert.equal(f.guide().context.previous,true);
 Object.assign(f.data.run,{id:'c'.repeat(32),running:true,status:'collecting'});f.viewer({fresh:true,state:{active:false,phase:'idle',session_id:null}});f.render();assert.equal(f.previous.hidden,true);assert.equal(f.guide().context.previous,false);assert.equal(f.preparation.hidden,true);
});
test('a successor Viewer closing or becoming unknown cannot revive the prior completed result',()=>{
 const f=fixture();f.newViewer();f.render();assert.equal(f.guide().context.previous,true);
 for(const viewer of [{fresh:true,state:{active:false,phase:'idle',session_id:null}},{fresh:false,state:{active:true,phase:'running',session_id:'b'.repeat(32)}},{fresh:true,state:{active:false,phase:'error',session_id:null}}]){f.viewer(viewer);f.render();assert.equal(f.calib.dataset.currentResult,'false');assert.equal(f.previous.hidden,false);assert.equal(f.guide().context.previous,true);}
 f.newViewer();f.render();assert.equal(f.guide().context.previous,true);assert.equal(f.field('hub-profile').value,PROFILE);
});
test('retiring an old result does not retire a different new run or hide its cleanup',()=>{
 const f=fixture();f.newViewer();f.render();f.viewer({fresh:true,state:{active:false,phase:'idle',session_id:null}});
 Object.assign(f.data.run,{id:'c'.repeat(32),running:true,status:'collecting'});f.ctx.lastStartedRunId=f.data.run.id;f.render();assert.equal(f.guide().context.previous,false);
 f.data.run.running=false;f.data.run.status='completed';f.render();assert.equal(f.calib.dataset.currentResult,'true');assert.equal(f.guide().context.previous,false);
 f.data.cleanup_required=true;f.data.run.id=ID;f.render();assert.equal(f.guide().context.previous,false);assert.equal(f.preparation.hidden,true);
});
