import {cameraGesture} from './camera_gesture.mjs';
const $=id=>document.getElementById(id);let state=null,camera=null,pointer=null,dirty=false,busy=false;
const fallback=()=>({azimuth:90,elevation:-5,distance:.38,lookat:[0,0,.115]});
async function flush(){if(busy||!dirty||!state?.csrf)return;busy=true;dirty=false;try{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name:'view_camera',camera}),signal:AbortSignal.timeout(2000)});if(!r.ok)throw Error('Camera request failed');}catch(e){$('notice').textContent=e.message;}finally{busy=false;if(dirty)setTimeout(flush,50);}}
function move(kind,dx,dy){const r=$('screen').getBoundingClientRect();camera=cameraGesture(camera||fallback(),kind,kind==='zoom'?0:dx/Math.max(1,r.width),kind==='zoom'?dy*.001:dy/Math.max(1,r.height));dirty=true;setTimeout(flush,40);}
const screen=$('screen');screen.addEventListener('contextmenu',e=>e.preventDefault());
screen.addEventListener('pointerdown',e=>{if(pointer)return;pointer={id:e.pointerId,x:e.clientX,y:e.clientY,pan:e.shiftKey||e.button===2};screen.setPointerCapture(e.pointerId);});
screen.addEventListener('pointermove',e=>{if(pointer?.id!==e.pointerId)return;const dx=e.clientX-pointer.x,dy=e.clientY-pointer.y;pointer.x=e.clientX;pointer.y=e.clientY;move(pointer.pan?'pan':'orbit',dx,dy);});
for(const type of ['pointerup','pointercancel','lostpointercapture'])screen.addEventListener(type,e=>{if(pointer?.id===e.pointerId)pointer=null;});
screen.addEventListener('wheel',e=>{e.preventDefault();move('zoom',0,e.deltaY);},{passive:false});
screen.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();move('orbit',e.key==='ArrowLeft'?-15:e.key==='ArrowRight'?15:0,e.key==='ArrowUp'?-15:e.key==='ArrowDown'?15:0);}});
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{camera=fallback();if(b.dataset.view==='side')camera.azimuth=0;dirty=true;flush();}));
async function poll(){try{
 const [s,v]=await Promise.all([fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(2500)}),fetch('/api/view',{cache:'no-store',signal:AbortSignal.timeout(2500)})]);
 if(!s.ok||!v.ok)throw Error('Local service unavailable / 本机服务未连接');state=await s.json();const data=await v.json();if(!pointer&&!busy&&!dirty)camera=state.camera;
 if(!data.meta.ready||!data.image)throw Error(data.meta.message||'No live image');
 const im=new Image();im.src=data.image;await im.decode();$('image').src=data.image;$('stale').hidden=true;
 const source=data.meta.mode==='demo'?'SCRIPTED PREVIEW / 编排预览':data.meta.source_seq==null?'MODEL PREVIEW / 模型预览':`MEASURED FRAME / 实测帧 ${data.meta.source_seq}`;
 $('notice').textContent=`${source} · ${Number(data.meta.render_hz||0).toFixed(1)} fps · ${data.meta.message||''}`;
 }catch(e){$('stale').hidden=false;$('stale').textContent=e.message;}
 finally{setTimeout(poll,100);}}
poll();
