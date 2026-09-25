/* One shared Windows/macOS interface. Extra device workspaces keep their own live pages. */
(()=>{
 'use strict';
 const ws=window.WujiWorkspace,L=window.WujiLocale,$=id=>document.getElementById(id),t=(zh,en)=>L.lang==='en'?en:zh;
 const embedded=new URLSearchParams(location.search).get('embedded')==='1';
 document.documentElement.dataset.embedded=String(embedded);
 let state=null,retarget=null;
 const speeds=[.1,.25,.5,.75,1,1.25,1.5,1.75,2,2.5,3,4];
 for(const id of ['trial-speed','play-speed']){
  const select=$(id),previous=select?.value;if(!select)continue;
  select.replaceChildren(...speeds.map(rate=>new Option(rate+'×',String(rate))));select.value=previous||'1';
 }
 function addPage(key,zh,en,description){
  const page=document.createElement('section');page.id='page-'+key;page.className='workspace-page';page.hidden=true;
  page.innerHTML='<header class="page-heading"><h2></h2><p></p></header><div class="page-body"></div>';
  document.querySelector('.page-area').append(page);ws.views[key]=page.querySelector('.page-body');
  const link=document.createElement('a');link.href='#'+key;link.dataset.page=key;link.innerHTML='<svg viewBox="0 0 26 24" aria-hidden="true"><circle cx="13" cy="12" r="8"/><circle cx="13" cy="12" r="2"/></svg><span></span>';
  document.querySelector('.directory').append(link);
  const label=()=>{page.querySelector('h2').textContent=link.lastElementChild.textContent=t(zh,en);page.querySelector('header p').textContent=description[L.lang]};
  label();window.addEventListener('wuji-language',label);return page.querySelector('.page-body');
 }
 const settings=addPage('settings','设置','Settings',{zh:'让工作台适合你的使用习惯。',en:'Make the workbench feel right for you.'});
 const devices=embedded?null:addPage('devices','多手演示','Hand ensemble',{zh:'选择参与的手，一起播放或双手衔接。',en:'Choose participating hands for same-action playback or a paired routine.'});
 settings.innerHTML=`<div class="wb-card"><h3 id="wb-appearance-title"></h3><p id="wb-appearance-note"></p><div class="wb-row"><label id="wb-theme-label" for="wb-theme"></label><select id="wb-theme"><option value="system"></option><option value="light"></option><option value="dark"></option></select></div><div class="wb-row"><label id="wb-auto-hand-label"><input type="checkbox" id="wb-auto-hand"><span></span></label></div><div class="wb-row"><label id="wb-auto-glove-label"><input type="checkbox" id="wb-auto-glove"><span></span></label></div></div>
 <div class="wb-card"><h3 id="wb-retarget-title"></h3><p id="wb-retarget-note"></p><div class="wb-row"><label for="wb-smoothing" id="wb-smoothing-label"></label><input id="wb-smoothing" type="number" min="0" max="1000" step="10" value="0"><span>ms</span></div><div id="wb-retarget-grid" class="wb-list"></div><div class="wb-actions"><button id="wb-retarget-default"></button><button id="wb-retarget-save" class="primary"></button></div><p id="wb-retarget-status" class="wb-status" role="status"></p></div>`;
 if(devices)devices.innerHTML=`<div class="wb-card"><h3 id="wb-device-title"></h3><p id="wb-device-note"></p><div class="wb-row"><label for="wb-device-name" id="wb-device-name-label"></label><input id="wb-device-name" maxlength="40"><label for="wb-device-profile" id="wb-device-profile-label"></label><select id="wb-device-profile"><option value="hand2_left">Hand 2 · Left</option><option value="hand2_right">Hand 2 · Right</option><option value="hand1_left">Hand 1 · Left</option><option value="hand1_right">Hand 1 · Right</option></select><button id="wb-device-add" class="primary"></button></div><p id="wb-device-status" role="status" class="wb-status"></p><div id="wb-device-list" class="wb-list"></div></div>`;
 function request(name,payload={}){
  if(!state?.csrf)return Promise.reject(Error(t('连接本机服务后重试','Connect to the local service first')));
  return fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name,...payload})})
   .then(async r=>{const value=await r.json();if(!r.ok||!value.ok)throw Error(value.error||'Request failed');return value});
 }
 const frames=new Map();let active='main',fleet=[];
 function themeApply(value){
  const theme=value==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):value;
  document.documentElement.dataset.theme=theme;
  $('wb-theme').value=value;
  if(!embedded)for(const frame of frames.values())frame.contentWindow?.postMessage({type:'wb-theme',value},'*');
 }
 let themeChoice=localStorage.getItem('wuji-workbench-theme')||'system';
 const fromParent=new URLSearchParams(location.search).get('theme');if(embedded&&['system','light','dark'].includes(fromParent))themeChoice=fromParent;
 themeApply(themeChoice);
 $('wb-theme').onchange=()=>{themeChoice=$('wb-theme').value;localStorage.setItem('wuji-workbench-theme',themeChoice);themeApply(themeChoice)};
 matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>themeApply(themeChoice));
 window.addEventListener('message',event=>{if(embedded&&document.referrer&&event.origin===new URL(document.referrer).origin&&event.data?.type==='wb-theme')themeApply(event.data.value)});
 const autoHand=$('wb-auto-hand'),autoGlove=$('wb-auto-glove');
 autoHand.checked=localStorage.getItem('wuji-auto-hand')==='true';autoGlove.checked=localStorage.getItem('wuji-auto-glove')==='true';
 autoHand.onchange=()=>localStorage.setItem('wuji-auto-hand',String(autoHand.checked));
 autoGlove.onchange=()=>localStorage.setItem('wuji-auto-glove',String(autoGlove.checked));
 let lastHand=0,lastGlove=0,openedGlove=false;
 setInterval(()=>{
  const now=Date.now();
  if(autoHand.checked&&!autoGlove.checked&&!state?.glove?.busy&&['disconnected','error'].includes(state?.connection)&&now-lastHand>15000){lastHand=now;request('connect',{address:'',auto_detect:true}).catch(()=>{})}
  if(autoGlove.checked&&state?.connection==='disconnected'&&!state?.glove?.busy&&now-lastGlove>15000){lastGlove=now;request('glove_scan').catch(()=>{})}
  const g=state?.glove||{};
  if(autoGlove.checked&&g.connection==='ready'&&g.devices?.length===1&&!openedGlove){openedGlove=true;
   request('glove_open',{serial:g.devices[0].serial,user_id:g.user?.user_id||'',timeout_ms:250}).catch(()=>{openedGlove=false});
  }
  if(g.connection==='disconnected')openedGlove=false;
 },1000);
 document.addEventListener('click',event=>{if(event.target?.id==='disconnect'||event.target?.id==='connection-toggle'&&state?.connection==='connected'){
  autoHand.checked=false;localStorage.setItem('wuji-auto-hand','false');
 }},true);
 const fingerNames=()=>L.lang==='en'?['Thumb','Index','Middle','Ring','Little']:['拇指','食指','中指','无名指','小指'];
 let outputGrid=null,outputKind='gain';
 const kindSelect=document.createElement('select');kindSelect.id='wb-output-kind';kindSelect.setAttribute('aria-label',t('调整项','Adjustment'));
 kindSelect.append(new Option(t('幅度系数','Gain'),'gain'),new Option(t('角度偏移 °','Offset °'),'offset_deg'));
 $('wb-retarget-grid').before(kindSelect);
 const outputRows=()=>fingerNames().map((label,i)=>({key:String(i),label}));
 const outputCols=()=>[1,2,3,4].map(i=>({key:String(i),label:t('关节 ','Joint ')+i}));
 function retargetRead(){
  if(!retarget)return;
  if(outputGrid)retarget[outputKind]=outputGrid.read().flat();
  const raw=$('wb-smoothing').value.trim();if(!raw||!Number.isFinite(Number(raw)))throw Error(t('请填写输出平滑','Enter output smoothing'));
  retarget.smoothing_ms=Number(raw);
 }
 kindSelect.onchange=()=>{try{retargetRead();outputKind=kindSelect.value;retargetRender()}catch(e){kindSelect.value=outputKind;$('wb-retarget-status').textContent=e.message}};
 function retargetRender(replace=true){if(!retarget)return;
  kindSelect.options[0].textContent=t('幅度系数','Gain');kindSelect.options[1].textContent=t('角度偏移 °','Offset °');
  kindSelect.setAttribute('aria-label',t('调整项','Adjustment'));
  const matrix=Array.from({length:5},(_,i)=>retarget[outputKind].slice(i*4,i*4+4));
  if(!outputGrid)outputGrid=NumericGrid.mount($('wb-retarget-grid'),{rows:outputRows(),columns:outputCols(),value:matrix,language:L.lang,onChange:value=>{retarget[outputKind]=value.flat()}});
  else{outputGrid.setLabels(outputRows(),outputCols());outputGrid.setLanguage(L.lang);if(replace)outputGrid.setValue(matrix)}
  if(replace)$('wb-smoothing').value=String(retarget.smoothing_ms);
 }
 let mappingContext=null;
 const mappingCard=$('wb-retarget-title').closest('.wb-card');
 const bindingNote=document.createElement('p');bindingNote.className='wb-note';bindingNote.id='wb-mapping-binding';$('wb-retarget-note').after(bindingNote);
 const presetRow=document.createElement('div');presetRow.className='wb-row';
 const presetSelect=document.createElement('select');presetSelect.setAttribute('aria-label',t('映射预设','Mapping preset'));
 const presetName=document.createElement('input');presetName.maxLength=48;presetName.placeholder=t('预设名称','Preset name');presetName.setAttribute('aria-label',t('预设名称','Preset name'));
 const loadPreset=document.createElement('button'),savePreset=document.createElement('button'),deletePreset=document.createElement('button'),applyMapping=document.createElement('button');
 for(const button of [loadPreset,savePreset,deletePreset,applyMapping])button.type='button';
 presetRow.append(presetSelect,loadPreset,presetName,savePreset,deletePreset);mappingCard.append(presetRow);$('wb-retarget-save').after(applyMapping);
 function showContext(value,replace=true){mappingContext=value;if(replace){retarget=value.settings;retargetRender()}
  const b=value.binding;bindingNote.textContent=`${b.generation} · ${b.side} · ${t('手套','Glove')} ${b.glove_serial||'—'} → ${t('机械手','Hand')} ${b.hand_serial||t('连接时选择','Select on connect')} · ${t('用户','User')} ${b.sdk_user||'—'} · v${value.revision}`;
  const choice=presetSelect.value;presetSelect.replaceChildren(new Option(t('选择映射预设','Select a mapping preset'),''),...(value.presets||[]).map(p=>new Option(p.name,p.name)));presetSelect.value=choice;
 }
 async function mappingRefresh(){try{const response=await fetch('/api/retarget/context');if(!response.ok)throw Error(t('无法读取映射配置','Cannot load mapping configuration'));showContext(await response.json())}catch(error){$('wb-retarget-status').textContent=error.message}}
 window.addEventListener('retarget-binding-changed',mappingRefresh);mappingRefresh();
 $('wb-smoothing').oninput=()=>{if(retarget)retarget.smoothing_ms=Number($('wb-smoothing').value)};
 $('wb-retarget-default').onclick=()=>{retarget={gain:Array(20).fill(1),offset_deg:Array(20).fill(0),smoothing_ms:0};retargetRender()};
 $('wb-retarget-save').onclick=async()=>{if(!retarget)return;
  try{retargetRead();const out=await request('retarget_save',{values:retarget,revision:mappingContext?.revision});showContext(out.mapping);$('wb-retarget-status').textContent=t('已保存到当前设备配对。可应用到手套预览，或重新连接后生效。','Saved for this pairing. Apply in glove preview, or reconnect.')}catch(error){$('wb-retarget-status').textContent=error.message}
 };
 loadPreset.onclick=()=>{const preset=mappingContext?.presets?.find(p=>p.name===presetSelect.value);if(preset){retarget=structuredClone(preset.settings);retargetRender();$('wb-retarget-status').textContent=t('已填入预设；保存后生效。','Preset loaded; save to use it.')}};
 savePreset.onclick=async()=>{try{retargetRead();const out=await request('retarget_preset_save',{preset:presetName.value,values:retarget});showContext(out.mapping,false);$('wb-retarget-status').textContent=t('预设已保存','Preset saved')}catch(e){$('wb-retarget-status').textContent=e.message}};
 deletePreset.onclick=async()=>{try{const out=await request('retarget_preset_delete',{preset:presetSelect.value});showContext(out.mapping,false)}catch(e){$('wb-retarget-status').textContent=e.message}};
 applyMapping.onclick=async()=>{try{await request('retarget_apply');$('wb-retarget-status').textContent=t('已发送应用请求，请核对下方控制端状态。','Apply requested; check controller status below.')}catch(e){$('wb-retarget-status').textContent=e.message}};
 const mappingLive=document.createElement('p');mappingLive.className='wb-note';mappingCard.append(mappingLive);
 window.addEventListener('console-state',e=>{const g=e.detail?.glove||{};applyMapping.disabled=g.connection!=='receiving'||!!g.feedback?.device_id;
  const equal=JSON.stringify(g.stream?.retarget)===JSON.stringify(mappingContext?.settings);
  mappingLive.textContent=g.connection==='receiving'?(equal?t('控制端正在使用已保存的映射','Controller is using the saved mapping'):t('控制端映射与保存值不同；应用预览或重新连接','Controller mapping differs; apply in preview or reconnect')):t('连接手套后显示控制端实际使用的映射','Connect a glove to inspect the applied mapping');
 });
 const gloveLink=document.createElement('a');gloveLink.href='#settings';gloveLink.className='wb-note';gloveLink.id='wb-glove-settings-link';
 $('page-glove')?.querySelector('.glove-controls')?.append(gloveLink);
 // Parent device switch keeps all loopback child frames mounted and their leases alive.
 const selector=document.createElement('select');selector.id='wb-device-switch';selector.setAttribute('aria-label',t('切换设备工作区','Switch device workspace'));
 if(!embedded)document.querySelector('.top-actions').prepend(selector);
 function switchTo(id){active=id;document.body.classList.toggle('wb-child-active',id!=='main');
  for(const [key,frame] of frames)frame.hidden=key!==id;
  if(id!=='main'&&!frames.has(id)){
   const d=fleet.find(x=>x.id===id);if(!d)return;
   const frame=document.createElement('iframe');frame.id='wb-device-frame-'+id;frame.className='wb-device-frame';frame.title=d.label;
   frame.src=`http://127.0.0.1:${d.port}/?embedded=1&theme=${encodeURIComponent(themeChoice)}#library`;
   document.body.append(frame);frames.set(id,frame);
  }
  selector.value=id;
 }
 selector.onchange=()=>switchTo(selector.value);
 window.HandSessions={show:switchTo,refresh:()=>fleetRefresh()};
 let fleetBusy=false,fleetViewKey='',fleetChoicesKey='';
 const fleetStop=document.createElement('button');fleetStop.type='button';fleetStop.className='danger';
 if(devices){fleetStop.textContent=t('停止所有工作区动作','Stop motion in all workspaces');$('wb-device-status').before(fleetStop);
  fleetStop.onclick=async()=>{fleetStop.disabled=true;try{const out=await request('fleet_stop');$('wb-device-status').textContent=out.errors?.length?JSON.stringify(out.errors):t('已向全部工作区请求停止','Stop requested in every workspace')}catch(error){$('wb-device-status').textContent=error.message}finally{fleetStop.disabled=false}};
 }
 async function fleetRefresh(){if(embedded||fleetBusy)return;fleetBusy=true;try{
  const result=await (await fetch('/api/fleet')).json();fleet=result.devices||[];
  const choicesKey=JSON.stringify([fleet.map(({id,label})=>({id,label})),L.lang]);
  if(choicesKey!==fleetChoicesKey){fleetChoicesKey=choicesKey;
   selector.replaceChildren(new Option(t('主工作区','Main workspace'),'main'),...fleet.map(x=>new Option(x.label,x.id)));
   selector.value=fleet.some(x=>x.id===active)?active:'main';
  }
  if(active!=='main'&&!fleet.some(x=>x.id===active))switchTo('main');
  const viewKey=JSON.stringify([fleet.map(({id,label,profile,connection,hardware,glove,hand_serial,glove_serial,following,stale})=>({id,label,profile,connection,hardware,glove,hand_serial,glove_serial,following,stale})),L.lang]);
  if(viewKey===fleetViewKey)return;fleetViewKey=viewKey;
  $('wb-device-list')?.replaceChildren(...fleet.map(x=>{
   const row=document.createElement('article');row.className='wb-device-card';
   const name=document.createElement('strong');name.textContent=x.label;
   const detail=document.createElement('p');detail.textContent=`${x.profile} · ${x.following===true?t('手套跟随中','Glove following'):x.hardware===true?t('动作中','Moving'):x.connection==='connected'?t('反馈在线','Feedback online'):x.glove==='receiving'?t('手套预览','Glove preview'):t('未连接','Disconnected')}`;
   const pair=document.createElement('p');pair.textContent=t('机械手：','Hand: ')+(x.hand_serial||'—')+' · '+t('手套：','Glove: ')+(x.glove_serial||'—');
   const actions=document.createElement('div');actions.className='wb-actions';
   const button=document.createElement('button');button.textContent=t('打开','Open');button.onclick=()=>switchTo(x.id);
   const remove=document.createElement('button');remove.textContent=t('移除','Remove');
   remove.disabled=x.hardware===true||x.following===true||x.program?.active===true;
   const rename=document.createElement('button');rename.textContent=t('重命名','Rename');rename.onclick=async()=>{const label=prompt(t('工作区名称','Workspace name'),x.label);if(!label)return;try{await request('fleet_rename',{id:x.id,label});await fleetRefresh()}catch(error){$('wb-device-status').textContent=error.message}};
   remove.onclick=async()=>{try{const result=await request('fleet_remove',{id:x.id});if(!result.removed)throw Error(result.error||'Workspace is active');
    frames.get(x.id)?.remove();frames.delete(x.id);if(active===x.id)switchTo('main');await fleetRefresh();
   }catch(error){$('wb-device-status').textContent=error.message}};
   actions.append(button,rename,remove);row.append(name,detail,pair,actions);return row;
  }));
 }catch(error){if($('wb-device-status'))$('wb-device-status').textContent=error.message}finally{fleetBusy=false}}
 if(devices){$('wb-device-add').onclick=async()=>{const name=$('wb-device-name').value.trim();try{
  const response=await request('fleet_create',{label:name,profile:$('wb-device-profile').value});$('wb-device-name').value='';
  await fleetRefresh();switchTo(response.device.id);$('wb-device-status').textContent='';
 }catch(error){$('wb-device-status').textContent=error.message}};fleetRefresh();setInterval(fleetRefresh,2500);}
 let fleetBeating=false;
 if(!embedded)setInterval(async()=>{if(fleetBeating||!fleet.length)return;fleetBeating=true;
  try{await request('fleet_keepalive')}catch{}finally{fleetBeating=false}
 },250);
 function labels(){
  presetSelect.setAttribute('aria-label',t('映射预设','Mapping preset'));presetName.placeholder=t('预设名称','Preset name');presetName.setAttribute('aria-label',t('预设名称','Preset name'));
  loadPreset.textContent=t('填入预设','Load preset');savePreset.textContent=t('保存为预设','Save preset');deletePreset.textContent=t('删除预设','Delete preset');applyMapping.textContent=t('应用到手套预览','Apply to glove preview');
  fleetStop.textContent=t('停止所有工作区动作','Stop motion in all workspaces');
  selector.setAttribute('aria-label',t('切换设备工作区','Switch device workspace'));
  $('wb-appearance-title').textContent=t('外观','Appearance');
  $('wb-appearance-note').textContent=t('使用系统原生磨砂；不采集桌面、不绘制外部折射。','Native frosted material. No desktop capture or external refraction.');
  $('wb-theme-label').textContent=t('显示主题','Theme');
  for(const [id,zh,en] of [['system','跟随系统','System'],['light','白色','Light'],['dark','黑色','Dark']])$('wb-theme').querySelector(`[value=${id}]`).textContent=t(zh,en);
  $('wb-auto-hand-label').querySelector('span').textContent=t('自动发现并连接单只机械手（只读反馈）','Find and connect a single hand automatically (feedback only)');
  $('wb-auto-glove-label').querySelector('span').textContent=t('自动发现并连接单只手套（仅预览）','Find and connect a single glove automatically (preview only)');
  $('wb-retarget-title').textContent=t('映射输出调整','Mapper output adjustment');
  $('wb-retarget-note').textContent=t('所选求解器将 21 个关键点映射为 20 个关节角，再应用下方幅度、角度偏移和平滑。配置按左右手、代际、手套、机械手与标定用户分别保存。默认保持官方输出。','The selected solver maps 21 landmarks to 20 joint angles, followed by gain, offset and smoothing. Settings are separate for side, generation, glove, hand and SDK user. Defaults preserve SDK output.');
  $('wb-smoothing-label').textContent=t('输出平滑','Output smoothing');$('wb-retarget-default').textContent=t('恢复默认','Reset defaults');$('wb-retarget-save').textContent=t('保存映射','Save mapping');
  $('wb-glove-settings-link').textContent=t('调整手套映射 →','Adjust retargeting →');
  if(devices){$('wb-device-title').textContent=t('独立设备会话','Independent hand sessions');$('wb-device-note').textContent=t('多手可加入同一个工作区，也可分开使用。','Hands can share a workspace or work independently.');$('wb-device-name-label').textContent=t('名称','Name');$('wb-device-profile-label').textContent=t('型号','Model');$('wb-device-add').textContent=t('添加工作区','Add workspace')}
  retargetRender(false);
 }
 window.addEventListener('console-state',event=>{state=event.detail;});
 window.addEventListener('wuji-language',()=>{labels();fleetRefresh()});
 labels();ws.showPage();
})();
