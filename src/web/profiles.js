(function(){
 const $=id=>document.getElementById(id),ws=window.WujiWorkspace;if(!ws)return;
 const control=document.createElement('div');control.className='device-profile-control';
 const label=document.createElement('label'),select=document.createElement('select'),note=document.createElement('p');
 select.id='device-profile';label.htmlFor=select.id;note.className='hint';note.setAttribute('role','status');control.append(label,select,note);
 document.querySelector('.page-area').prepend(control);let info=null,selected='hand2_left',busy=false,lastSideText='';
 const text=(zh,en)=>document.documentElement.lang==='en'?en:zh;
 function render(){
  label.textContent=text('设备型号','Device');select.setAttribute('aria-label',label.textContent);
  if(!info)return;
  const value=selected;select.replaceChildren(...info.profiles.map(p=>new Option(text(p.zh,p.en),p.id)));select.value=value;
  const first=selected.startsWith('hand1');
  note.textContent=first?text('一代：原生模型 · 官方张开/握拳适配 · LowPass 5 Hz / 200 Hz发送；其他动作仅预览，Kp/Kd不适用。','Hand 1: native model · official open/curl adaptation · LowPass 5 Hz / 200 Hz commands. Other actions are preview-only; Kp/Kd do not apply.') :text('二代：原生左右手模型与各自官方录制 · 实机跟随须连接并核对对应设备。','Hand 2: native left/right models and separate official recordings. Connect and verify the matching device for feedback.');
  const heading=$('pose-console')?.querySelector('h2');if(heading)heading.textContent='MuJoCo · '+text(info.profiles.find(p=>p.id===selected).zh,info.profiles.find(p=>p.id===selected).en);
  updateLabels();
 }
 function updateLabels(){
  const first=selected.startsWith('hand1');
  const caption=first?text('对指候选 · 仅预览','Opposition candidate · preview only'):text('官方二代'+(selected.endsWith('left')?'左':'右')+'手对指录制','Official Hand 2 '+(selected.endsWith('left')?'left':'right')+' recording');
  for(const id of ['studio-action','trial-action','play-action']){const option=$(id)?.querySelector('option[value="official_opposition"]');if(option&&option.textContent!==caption)option.textContent=caption;}
  if($('pose-image')&&info){const p=info.profiles.find(p=>p.id===selected);$('pose-image').alt=text(p.zh+'原生模型',p.en+' native model');}
 }
 window.addEventListener('console-state',updateLabels);
 async function load(){info=await(await fetch('/api/installation')).json();selected=info.selected_profile;render();}
 select.addEventListener('change',async()=>{busy=true;select.disabled=true;try{const s=await(await fetch('/api/state')).json();const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name:'device_profile_select',profile:select.value})});const j=await r.json();if(!r.ok)throw Error(j.error);selected=j.device_profile.id;render();}catch(e){select.value=selected;note.textContent=e.message;}finally{busy=false;}});
 window.addEventListener('console-state',e=>{const s=e.detail;select.disabled=busy||s.connection!=='disconnected'||s.hardware.active!==false;if(s.device_profile?.id&&s.device_profile.id!==selected){selected=s.device_profile.id;render();}const first=selected.startsWith('hand1'),unsupported=first&&!['open','fist'].includes($('studio-action')?.value);if(unsupported){if($('trial-start'))$('trial-start').disabled=true;const n=document.querySelector('.action-selection-note');if(n)n.textContent=text('一代候选动作仅供画面预览，请选择画面预览播放','Hand 1 candidate: use Preview; hardware action is unavailable');}const h=$('pose-console')?.querySelector('h2');if(h&&info){const p=info.profiles.find(p=>p.id===selected);h.textContent='MuJoCo · '+text(p.zh,p.en);}});
 window.addEventListener('wuji-language',render);load().catch(()=>note.textContent='无法加载设备型号 / Could not load device profiles');
})();
