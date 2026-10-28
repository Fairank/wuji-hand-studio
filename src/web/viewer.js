import {cameraGesture} from './camera_gesture.mjs';
const $=id=>document.getElementById(id);let state=null,camera=null,pointer=null,dirty=false,busy=false,lastImageMode=null,lastImageScope=null,sourceScope=null,decodedImage=null;
const text=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
const sourceKind=mode=>mode==='glove_constrained_unavailable'?'glove_constrained':mode;
function stateScope(value){
 if(!value)return null;const g=value.glove||{},feedback=g.feedback||{};
 return JSON.stringify([value.view_source||'feedback',value.teleoperation_view_mode||'automatic',value.device_profile?.id||null,
  value.device_id||null,g.profile||null,g.session_id??null,g.glove_serial||null,g.user?.user_id??null,
  feedback.device_id||null,feedback.connection==='disconnected',g.model_provenance?.model_sha256||null,
  value.view_source==='glove'?g.connection==='disconnected':value.view_source!=='preview'&&value.connection==='disconnected']);
}
function discardImage(){lastImageMode=null;lastImageScope=null;$('image').hidden=true;$('image').style.display='none';$('image').removeAttribute('src');}
function syncSource(next){const key=stateScope(next);state=next;if(key===sourceScope)return;sourceScope=key;discardImage();if(decodedImage)decodedImage.src='';$('notice').textContent=text('正在准备画面','Preparing view');}
function payloadMatches(meta){
 if(!state)return false;const g=state.glove||{},proof=meta.collision_preview||{},kind=sourceKind(meta.mode);
 if(kind?.startsWith('glove_')&&g.connection==='disconnected')return false;
 if(kind==='glove_live'&&(g.feedback?.connection==='disconnected'||meta.feedback?.connection==='disconnected'))return false;
 if(kind==='live'&&state.connection==='disconnected')return false;
 const matches=(value,current)=>value==null||value===current;
 return matches(meta.device_profile,state.device_profile?.id)&&matches(meta.view_source,state.view_source)
  &&matches(proof.profile,g.profile)&&matches(proof.bridge_session_id,g.session_id)&&matches(proof.glove_serial,g.glove_serial)
  &&matches(meta.feedback?.device_id,kind==='glove_live'?g.feedback?.device_id:state.device_id);
}
function imageScope(meta){const proof=meta?.collision_preview||{};return JSON.stringify([sourceScope,sourceKind(meta?.mode),
 meta?.device_profile??null,meta?.view_source??null,proof.profile??null,proof.bridge_session_id??null,proof.glove_serial??null,meta?.feedback?.device_id??null]);}
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
 if(!s.ok||!v.ok)throw Error('Local service unavailable / 本机服务未连接');syncSource(await s.json());const data=await v.json();if(!pointer&&!busy&&!dirty)camera=state.camera;
 const scope=imageScope(data.meta);
 if(!payloadMatches(data.meta)){discardImage();throw Error(text('设备已切换，等待新画面','Device changed; waiting for a new view'));}
 if(lastImageScope!==scope)discardImage();
 const mode=data.meta.mode,constrained=['glove_constrained','glove_constrained_unavailable'].includes(mode),glove=constrained||mode==='glove_preview'||mode==='glove_live';
 const source=constrained?text('映射预览 · 碰撞约束','Mapping preview · Collision constrained'):mode==='glove_preview'?text('映射预览','Mapping preview'):mode==='glove_live'?text('实机反馈','Hand feedback'):mode==='demo'?'SCRIPTED PREVIEW / 编排预览':data.meta.source_seq==null?'MODEL PREVIEW / 模型预览':`MEASURED FRAME / 实测帧 ${data.meta.source_seq}`;
 $('notice').title=constrained?text('显示姿态经过官方模型的几何约束；SDK 原始目标与实机指令未修改，不代表实物避碰。','The displayed pose uses geometry constraints from the official model. Original SDK targets and hardware commands are unchanged; this does not establish collision avoidance on hardware.') : '';
 if(!data.meta.ready||!data.image||mode==='glove_live'&&(data.meta.feedback?.stale!==false||data.meta.source_seq==null)){
  $('notice').textContent=source+' · '+text('暂不可用','Unavailable');throw Error(data.meta.message||text('画面暂不可用','View unavailable'));
 }
 const im=new Image();decodedImage=im;im.src=data.image;try{await im.decode();}finally{if(decodedImage===im)decodedImage=null;}
 // This independent page has no console-state event stream. Re-read the
 // source after decoding so a late A frame cannot land in a new B session.
 const confirmed=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(2500)});
 if(!confirmed.ok)throw Error('Local service unavailable / 本机服务未连接');syncSource(await confirmed.json());
 if(scope!==imageScope(data.meta)||!payloadMatches(data.meta)){discardImage();throw Error(text('设备已切换，等待新画面','Device changed; waiting for a new view'));}
 $('image').src=data.image;$('image').hidden=false;$('image').style.removeProperty('display');$('stale').hidden=true;lastImageMode=mode;lastImageScope=scope;
 $('notice').textContent=`${source} · ${Number(data.meta.render_hz||0).toFixed(1)} fps`+(glove?'':` · ${data.meta.message||''}`);
 }catch(e){$('stale').hidden=false;$('stale').textContent=e.message;}
 finally{setTimeout(poll,100);}}
poll();
