import {cameraGesture} from './camera_gesture.mjs';
const $=id=>document.getElementById(id);let state=null,camera=null,pointer=null,dirty=false,busy=false;
const text=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
let lastMeta=null;
function viewMessage(meta){
 if(meta.mode==='demo'){
  const playback=meta.playback||{};
  return `${text('仿真动作预览','Motion preview')} · ${window.WujiLocale?.playbackLabel?.(playback.label,playback.action)||playback.label||''} · ${text(playback.running?'播放中':'已暂停或完成',playback.running?'Playing':'Paused or complete')}`;
 }
 const messages={
  '实机同步 · 等待真实关节反馈':['实机同步 · 等待真实关节反馈','Hardware sync · waiting for measured joint feedback'],
  '动作预览 · 请先选择并播放动作':['动作预览 · 请先选择并播放动作','Motion preview · select and play an action'],
  '模型预览 · 等待连接与关节对应核对':['模型预览 · 等待连接与关节对应核对','Model preview · waiting for connection and joint mapping check'],
  '三维渲染已中断，画面不再代表当前姿态':['三维渲染已中断，画面不再代表当前姿态','3D rendering stopped; this image no longer represents the current pose'],
  '正在载入原生左手模型':['正在载入原生左手模型','Loading the native left-hand model']
 };
 const pair=messages[meta.message];return pair?text(...pair):meta.message||'';
}
function renderNotice(meta){
 const source=meta.mode==='demo'?text('编排预览','Scripted preview'):meta.source_seq==null?text('模型预览','Model preview'):`${text('实测帧','Measured frame')} ${meta.source_seq}`;
 $('notice').textContent=`${source} · ${Number(meta.render_hz||0).toFixed(1)} fps · ${viewMessage(meta)}`;
}
function applyLanguage(){
 const english=window.WujiLocale?.lang==='en';document.documentElement.lang=english?'en':'zh-CN';
 document.title=english?'MuJoCo · Hand Viewer':'MuJoCo · 灵巧手查看器';
 $('viewer-title').textContent=english?'MuJoCo · Hand Viewer':'MuJoCo · 灵巧手查看器';
 const labels=english?['Front','Side','Reset']:['正面','侧面','复位'];
 document.querySelectorAll('[data-view]').forEach((button,index)=>button.textContent=labels[index]);
 document.querySelector('.viewer-nav').setAttribute('aria-label',text('相机视角','Camera views'));
 $('screen').setAttribute('aria-label',text('MuJoCo 三维查看器','MuJoCo 3D viewer'));
 $('image').alt=text('手部实时姿态','Live hand pose');
 $('viewer-help').textContent=text('拖动旋转 · 滚轮缩放 · Shift拖动平移。此窗口不持有电机控制会话。','Drag to orbit · Wheel to zoom · Shift-drag to pan. This window does not own a motor-control session.');
}
function applyTheme(){
 let choice='system';try{choice=localStorage.getItem('wuji-workbench-theme')||'system';}catch{}
 const dark=choice==='dark'||(choice==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);
 document.documentElement.dataset.theme=dark?'dark':'light';
}
function applyTextSize(){
 let choice='100';try{const saved=localStorage.getItem('wuji-font-scale');if(['90','100','110'].includes(saved))choice=saved;}catch{}
 document.documentElement.dataset.uiFontScale=choice;
}
applyLanguage();applyTheme();applyTextSize();
window.addEventListener('wuji-language',applyLanguage);
window.addEventListener('wuji-language',()=>{if(lastMeta)renderNotice(lastMeta);});
window.addEventListener('wuji-locale-catalog',()=>{if(lastMeta)renderNotice(lastMeta);});
window.addEventListener('storage',event=>{
 if(event.key==='wuji-language'&&['zh','en'].includes(event.newValue))window.WujiLocale?.setLanguage(event.newValue);
 if(event.key==='wuji-workbench-theme')applyTheme();
 if(event.key==='wuji-font-scale')applyTextSize();
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',applyTheme);
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
 if(!s.ok||!v.ok)throw Error(text('本机服务未连接','Local service unavailable'));state=await s.json();const data=await v.json();if(!pointer&&!busy&&!dirty)camera=state.camera;
 if(!data.meta.ready||!data.image)throw Error(data.meta.message||text('没有可用的实时画面','No live image available'));
 const im=new Image();im.src=data.image;await im.decode();$('image').src=data.image;$('stale').hidden=true;
 lastMeta=data.meta;renderNotice(lastMeta);
 }catch(e){$('stale').hidden=false;$('stale').textContent=e.message;}
 finally{setTimeout(poll,100);}}
fetch('/api/catalog',{cache:'force-cache'}).then(r=>r.ok?r.json():null).then(catalog=>{if(catalog?.actions)window.WujiLocale?.registerActionLabels?.(catalog.actions);}).catch(()=>{});
poll();
