import {cameraGesture} from './camera_gesture.mjs';

const screen=document.querySelector('.pose-screen');
const toolbar=document.createElement('div');toolbar.className='view-toolbar';
toolbar.innerHTML=`<div class="view-sources" role="group" aria-label="画面来源"><button data-source="feedback" aria-pressed="true">跟随实机</button><button data-source="preview" aria-pressed="false">动作预览</button></div><div class="camera-presets" role="group" aria-label="观看角度"><button data-view="front">正面</button><button data-view="back">背面</button><button data-view="side">侧面</button><button data-view="reset">复位</button></div><p>拖动旋转 · 滚轮/双指缩放 · 右键或 Shift 拖动平移</p><p id="camera-notice" role="status"></p>`;
screen.before(toolbar);
const lens=document.createElement('div');lens.className='camera-lens';
lens.append(toolbar.querySelector('.camera-presets'));screen.append(lens);
const stageStop=document.createElement('button');stageStop.textContent='停止实机并停用电机';stageStop.className='stage-stop';stageStop.hidden=true;toolbar.append(stageStop);
stageStop.addEventListener('click',async()=>{try{await post({name:'hardware_stop'});notice.textContent='已请求停止，等待实机状态';}catch(e){notice.textContent=e.message;}});
screen.tabIndex=0;screen.setAttribute('role','group');screen.setAttribute('aria-label','三维视角，拖动旋转，滚轮缩放，方向键旋转，加减键缩放，R复位');
document.getElementById('pose-image').draggable=false;
let state=null,camera=null,revision=-1,dirty=false,busy=false,timer=0,lastGesture=0;
const pointers=new Map();
const notice=document.getElementById('camera-notice');
const clone=c=>({...c,lookat:[...c.lookat]});
const defaults=()=>({azimuth:90,elevation:-5,distance:.38,lookat:[0,0,.115]});
async function post(body){
  if(!state?.csrf)throw Error('本机服务未连接');
  const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify(body),signal:AbortSignal.timeout(2000)});
  const value=await r.json();if(!r.ok||!value.ok)throw Error(value.error||'操作失败');return value;
}
function schedule(){dirty=true;clearTimeout(timer);timer=setTimeout(flush,50);}
async function flush(){
  if(busy||!dirty||!camera||!state)return;
  busy=true;dirty=false;const sent=clone(camera);
  try{const r=await post({name:'view_camera',camera:sent});revision=Math.max(revision,r.camera_revision);notice.textContent='';}
  catch(e){notice.textContent=e.message;}
  finally{busy=false;if(dirty)timer=setTimeout(flush,50);}
}
function gesture(kind,dx,dy){
  if(!camera||!state)return;
  camera=cameraGesture(camera,kind,dx,dy);lastGesture=performance.now();schedule();
}
function preset(view){
  camera=defaults();if(view==='back')camera.azimuth=270;if(view==='side')camera.azimuth=0;
  lastGesture=performance.now();schedule();
}
for(const b of lens.querySelectorAll('[data-view]'))b.addEventListener('click',()=>preset(b.dataset.view));
for(const b of toolbar.querySelectorAll('[data-source]'))b.addEventListener('click',async()=>{
  try{await post({name:'view_source',source:b.dataset.source});notice.textContent='';}
  catch(e){notice.textContent=e.message;}
});
screen.addEventListener('contextmenu',e=>e.preventDefault());
screen.addEventListener('pointerdown',e=>{
  if(e.target.closest('button'))return;
  if(!state)return;e.preventDefault();screen.focus({preventScroll:true});
  screen.setPointerCapture(e.pointerId);pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
});
screen.addEventListener('pointermove',e=>{
  const old=pointers.get(e.pointerId);if(!old)return;
  const rect=screen.getBoundingClientRect();
  if(pointers.size===2){
    const other=[...pointers.entries()].find(([id])=>id!==e.pointerId)[1];
    const before=Math.hypot(old.x-other.x,old.y-other.y),after=Math.hypot(e.clientX-other.x,e.clientY-other.y);
    if(before>2&&after>2)gesture('zoom',0,Math.log(before/after));
  }else gesture(e.shiftKey||(e.buttons&2)?'pan':'orbit',(e.clientX-old.x)/rect.width,(e.clientY-old.y)/rect.height);
  pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
});
for(const name of ['pointerup','pointercancel','lostpointercapture'])screen.addEventListener(name,e=>pointers.delete(e.pointerId));
screen.addEventListener('wheel',e=>{
  if(!state)return;e.preventDefault();const pixels=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?screen.clientHeight:1);gesture('zoom',0,Math.max(-1,Math.min(1,pixels*.002)));
},{passive:false});
screen.addEventListener('keydown',e=>{
  const keys={ArrowLeft:[-.05,0],ArrowRight:[.05,0],ArrowUp:[0,-.05],ArrowDown:[0,.05]};
  if(keys[e.key]){e.preventDefault();gesture('orbit',...keys[e.key]);}
  else if(['+','=','-'].includes(e.key)){e.preventDefault();gesture('zoom',0,e.key==='-'?.12:-.12);}
  else if(e.key.toLowerCase()==='r'){e.preventDefault();preset('reset');}
});
window.addEventListener('console-state',e=>{
  state=e.detail;
  if(!camera||(!busy&&!dirty&&performance.now()-lastGesture>350&&state.camera_revision>=revision)){
    camera=clone(state.camera);revision=state.camera_revision;
  }
  for(const b of toolbar.querySelectorAll('button'))b.disabled=false;
  stageStop.hidden=!state.glove?.busy&&(state.hardware?.active===false||state.hardware?.active===undefined);
  for(const b of toolbar.querySelectorAll('[data-source]')){
    b.setAttribute('aria-pressed',String(b.dataset.source===state.view_source));
    b.disabled=!!state.hardware?.active&&b.dataset.source==='preview';
  }
});
window.addEventListener('console-offline',()=>{state=null;dirty=false;clearTimeout(timer);pointers.clear();for(const b of toolbar.querySelectorAll('button'))b.disabled=true;});
