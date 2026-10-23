/* One shared Windows/macOS interface. Extra device workspaces keep their own live pages. */
(()=>{
 'use strict';
 const ws=window.WujiWorkspace,L=window.WujiLocale,$=id=>document.getElementById(id),t=(zh,en)=>L.lang==='en'?en:zh;
 const embedded=new URLSearchParams(location.search).get('embedded')==='1';
 const taskView=embedded&&new URLSearchParams(location.search).get('task_view')==='1';
 const taskViewId=taskView?new URLSearchParams(location.search).get('task_id'):null;let taskNative=taskView&&new URLSearchParams(location.search).get('task_native')==='1';
 document.documentElement.dataset.embedded=String(embedded);
 document.documentElement.dataset.taskView=String(taskView);
 document.documentElement.dataset.taskNative=String(taskNative);
 // A task frame is a view of one authoritative owner, not a reusable endpoint.
 // Every command (including keepalive/stop) is scoped again by the server.
 let taskScope=null;
 if(taskView){
  const rawFetch=window.fetch.bind(window),app=document.querySelector('.app');
  const switchRequests=new Map();
  const stable=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
  async function verifySwitch(requestId,receipt){
   const command=switchRequests.get(requestId),target=command?.target;
   if(!command||receipt?.version!==1||receipt.request_id!==requestId||!/^[a-f0-9]{32}$/.test(receipt.attempt_id||'')||receipt.source_task_id!==taskViewId||receipt.expected_owner!==command.expected_owner||!/^[a-f0-9]{64}$/.test(receipt.after_context||'')||!/^[A-Za-z0-9_-]{1,80}$/.test(receipt.target_task_id||'')||receipt.target?.mode!==target.mode||receipt.target?.serial!==target.serial||Object.hasOwn(target,'user_id')&&receipt.target.user_id!==target.user_id||target.profile&&receipt.target.profile!==target.profile)throw Error(t('切换回执与本次任务不符，请核对当前设备。','The switch receipt does not match this task. Review the current device.'));
   const r=await rawFetch('/api/task-view?task_id='+encodeURIComponent(receipt.target_task_id),{cache:'no-store',signal:AbortSignal.timeout(6000)}),proof=r.ok?await r.json():null;
   if(proof?.current!==true||proof.owner_context!==receipt.after_context||stable(proof.last_transition_receipt)!==stable(receipt))throw Error(t('新任务归属尚未核对，未采用切换回执。','The new task owner is unverified. The switch receipt was not accepted.'));
   if(receipt.target_task_id!==taskViewId){
    invalidate(t('连接已切换，正在打开新的设备任务。','The connection switched. Opening its new device task.'));
    if(parentOrigin&&window.parent!==window)window.parent.postMessage({type:'wb-task-switch',task_id:taskViewId,receipt},parentOrigin);
    return {verified:true,routed:true};
   }
   await refreshScope(true);const deadline=performance.now()+6000;
   while(!current||!parentCurrent||!stateReady||context!==receipt.after_context){if(performance.now()>deadline)throw Error(t('新连接正在核对，请查看当前任务。','The new connection is being verified. Review the current task.'));await new Promise(resolve=>setTimeout(resolve,25));}
   return {verified:true,routed:false};
  }
  window.WujiTaskConnectionSwitch={accept:(requestId,receipt)=>verifySwitch(requestId,receipt)};
  const notice=document.createElement('p');notice.id='task-scope-notice';notice.setAttribute('role','status');
  Object.assign(notice.style,{position:'fixed',inset:'16px 16px auto',zIndex:'1000',margin:'0',padding:'14px 16px',borderRadius:'12px',background:'var(--wb-card,#f5f5f7)',color:'var(--wb-ink,#1d1d1f)',border:'1px solid var(--wb-line,#ddd)'});document.body.append(notice);
  let current=false,parentCurrent=false,parentRevision=null,context=null,epoch=0,reading=null,checkedAt=0,reason='',lastPublished='',stateReady=false,clearing=false;
  const stateProof=Symbol('task-state-owner');
  const ownedReads=new Set(['/api/state','/api/view','/api/workspace-view','/api/official-viz','/api/calibration','/api/workspaces','/api/retarget/context']);
  const parentOrigin=(()=>{try{return document.referrer?new URL(document.referrer).origin:null;}catch{return null;}})();
  function publish(){const allowed=current&&parentCurrent&&stateReady,key=allowed?'current':'blocked';if(document.documentElement.dataset.taskScope!==key)document.documentElement.dataset.taskScope=key;if(app&&app.inert===allowed)app.inert=!allowed;if(notice.hidden!==allowed)notice.hidden=allowed;const message=reason||t('任务归属正在核对，此视图暂不可操作。','Verifying this task owner. This view is temporarily read-only.');if(notice.textContent!==message)notice.textContent=message;
   const payload={type:'wb-task-scope',task_id:taskViewId,owner_revision:parentRevision,current:allowed,owner_context:allowed?context:null,reason:allowed?'':message},signature=JSON.stringify(payload);
   if(parentOrigin&&window.parent!==window&&signature!==lastPublished){lastPublished=signature;window.parent.postMessage(payload,parentOrigin);}
  }
  function invalidate(message){if(current||stateReady){epoch++;stateReady=false;clearConsent();}current=false;reason=message;publish();}
  function clearConsent(){for(const box of document.querySelectorAll('#glove-clear,#trial-clear,#probe-clear,#pe-clear,#hub-replace,.hg-clearbox'))box.checked=false;clearing=true;try{window.dispatchEvent(new Event('console-offline'));}finally{clearing=false;}}
  async function refreshScope(force=false){
   if(reading)return reading;if(!force&&Date.now()-checkedAt<750)return;
   const requestedRevision=parentRevision;reading=(async()=>{try{const r=await rawFetch('/api/task-view?task_id='+encodeURIComponent(taskViewId||''),{cache:'no-store',signal:AbortSignal.timeout(3000)});if(!r.ok)throw Error(t('无法核对任务归属。','Task ownership is unavailable.'));const value=await r.json();if(requestedRevision!==parentRevision)return;
     if(value?.task_id!==taskViewId||value.current!==true||typeof value.owner_context!=='string'||!/^[a-f0-9]{64}$/.test(value.owner_context)){invalidate(t('此任务已断开或归属已变化，请打开当前设备任务。','This task is disconnected or its owner changed. Open the current device task.'));return;}
     if(context!==value.owner_context){epoch++;stateReady=false;clearConsent();context=value.owner_context;}
     current=true;reason='';checkedAt=Date.now();publish();
    }catch{invalidate(t('任务归属尚未确认，此视图暂不可操作。','Task ownership is unverified. This view is temporarily read-only.'));}
    finally{reading=null;if(requestedRevision!==parentRevision)queueMicrotask(()=>refreshScope(true));}})();return reading;
  }
  window.fetch=async(input,init)=>{
   const request=new Request(input,init),url=new URL(request.url),api=url.origin===location.origin&&url.pathname.startsWith('/api/');
   if(!api)return rawFetch(request);
   if(url.pathname==='/api/task-view')throw Error(t('任务归属查询由当前视图管理。','Task ownership is managed by this view.'));
   const write=!['GET','HEAD'].includes(request.method),owned=write||ownedReads.has(url.pathname);
   if(!owned)return rawFetch(request);
   await refreshScope(write);if(!current||!parentCurrent||(write&&!stateReady))throw Error(t('此任务归属未确认，操作未发送。','This task owner is unverified. No operation was sent.'));
   let switchCommand=null;if(write&&url.pathname==='/api/action')try{const command=await request.clone().json();if(command?.name==='connection_switch'&&/^[a-f0-9]{32}$/.test(command.request_id||'')){switchCommand=command;switchRequests.set(command.request_id,command);if(switchRequests.size>32)switchRequests.delete(switchRequests.keys().next().value);}}catch{}
   const sentContext=context,sentEpoch=epoch,headers=new Headers(request.headers);headers.set('X-Task-Id',taskViewId);headers.set('X-Task-Context',sentContext);
   const response=await rawFetch(new Request(request,{headers}));
   if(!write||response.ok)await refreshScope(true);
   let switchVerified=false;
   if(switchCommand&&response.ok){const value=await response.clone().json();if(value?.ok===true&&value.connection_switch?.unchanged!==true){if(stable(value.task_transition_receipt)!==stable(value.connection_switch_receipt))throw Error(t('切换回执尚未核对，请查看当前设备任务。','The switch receipt is unverified. Review the current device task.'));const checked=await verifySwitch(switchCommand.request_id,value.task_transition_receipt);switchVerified=checked.verified;Object.defineProperties(response,{connectionSwitchVerified:{value:true},connectionSwitchRouted:{value:checked.routed}});if(checked.routed)return response;}}
   if(!current||!parentCurrent||sentContext!==context||sentEpoch!==epoch){
    let acceptedTransition=switchVerified;
    if(!acceptedTransition&&write&&response.ok)try{
     const value=await response.clone().json(),receipt=value?.task_receipt;
     if(value?.ok===true&&receipt&&typeof receipt==='object'&&!Array.isArray(receipt)&&Object.keys(receipt).length===3&&receipt.task_id===taskViewId&&receipt.before_context===sentContext&&typeof receipt.after_context==='string'&&/^[a-f0-9]{64}$/.test(receipt.after_context)){
      // Only the server's receipt for this exact accepted command can bridge
      // an owner transition. A fresh read must still verify its destination.
      await refreshScope(true);if(!current&&parentCurrent)await refreshScope(true);
      acceptedTransition=current&&parentCurrent&&receipt.after_context===context;
      if(acceptedTransition){
       const destination=context,destinationEpoch=epoch,deadline=performance.now()+3500;
       while(!stateReady||!current){
        if(!parentCurrent||context!==destination||epoch!==destinationEpoch||performance.now()>deadline){acceptedTransition=false;break;}
        await new Promise(resolve=>setTimeout(resolve,25));
       }
      }
     }
    }catch{}
    if(!acceptedTransition)throw Error(t('任务会话已变化，旧响应未采用。','The task session changed. The old response was discarded.'));
   }
   // The normalized console event keeps enumerable symbols. Unlock only after
   // this exact owner epoch's validated state has reached the existing renderer.
   if(!write&&url.pathname==='/api/state'&&response.ok){
    const read=response.json.bind(response);
    response.json=async()=>{const value=await read();if(value&&typeof value==='object'&&!Array.isArray(value))Object.defineProperty(value,stateProof,{value:{context:sentContext,epoch:sentEpoch},enumerable:true});return value;};
   }
   return response;
  };
  window.addEventListener('console-state',event=>{const proof=event.detail?.[stateProof];if(!proof||proof.context!==context||proof.epoch!==epoch||!current||!parentCurrent){event.stopImmediatePropagation();return;}queueMicrotask(()=>{if(proof.context===context&&proof.epoch===epoch&&current&&parentCurrent){stateReady=true;reason='';publish();}});},true);
  window.addEventListener('console-offline',()=>{if(clearing)return;const wasReady=stateReady;stateReady=false;if(wasReady)reason=t('本机服务暂不可用，恢复后继续。','The local service is unavailable. Continue after reconnection.');publish();},true);
  for(const name of ['click','pointerdown','keydown','submit'])document.addEventListener(name,event=>{if((!current||!parentCurrent||!stateReady)&&!(name==='keydown'&&['Tab','Escape'].includes(event.key))){event.preventDefault();event.stopImmediatePropagation();}},true);
  taskScope={setParent(value,revision){if(!Number.isSafeInteger(revision)||revision<1)return;if(parentRevision!==revision){parentRevision=revision;current=false;checkedAt=0;}if(parentCurrent&&value!==true){epoch++;stateReady=false;clearConsent();}parentCurrent=value===true;publish();if(parentCurrent)refreshScope(true);},refresh:()=>refreshScope(true),snapshot:()=>({current:current&&parentCurrent&&stateReady,owner_context:context,epoch})};
  publish();refreshScope(true);setInterval(()=>refreshScope(true),1000);
  window.addEventListener('wuji-language',publish);
 }
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
 const devices=embedded&&!new URLSearchParams(location.search).has('task_group')?null:addPage('devices','多手演示','Hand ensemble',{zh:'选择参与的手，一起播放或双手衔接。',en:'Choose participating hands for same-action playback or a paired routine.'});
 settings.innerHTML=`<div class="wb-card"><h3 id="wb-appearance-title"></h3><p id="wb-appearance-note"></p><div class="wb-row"><label id="wb-theme-label" for="wb-theme"></label><select id="wb-theme"><option value="system"></option><option value="light"></option><option value="dark"></option></select></div></div>
 <div class="wb-card"><h3 id="wb-retarget-title"></h3><p id="wb-retarget-note"></p><div class="wb-row"><label for="wb-smoothing" id="wb-smoothing-label"></label><input id="wb-smoothing" type="number" min="0" max="1000" step="10" value="0"><span>ms</span></div><div id="wb-retarget-grid" class="wb-list"></div><div class="wb-actions"><button id="wb-retarget-default"></button><button id="wb-retarget-save" class="primary"></button></div><p id="wb-retarget-status" class="wb-status" role="status"></p></div>`;
 if(devices)devices.innerHTML=`<div class="wb-card"><h3 id="wb-device-title"></h3><p id="wb-device-note"></p><div class="wb-row"><label for="wb-device-name" id="wb-device-name-label"></label><input id="wb-device-name" maxlength="40"><label for="wb-device-profile" id="wb-device-profile-label"></label><select id="wb-device-profile"><option value="hand2_left">Hand 2 · Left</option><option value="hand2_right">Hand 2 · Right</option><option value="hand1_left">Hand 1 · Left</option><option value="hand1_right">Hand 1 · Right</option></select><button id="wb-device-add" class="primary"></button></div><p id="wb-device-status" role="status" class="wb-status"></p><div id="wb-device-list" class="wb-list"></div></div>`;
 function request(name,payload={}){
  if(!state?.csrf)return Promise.reject(Error(t('连接本机服务后重试','Connect to the local service first')));
  return fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name,...payload})})
   .then(async r=>{const value=await r.json();if(!r.ok||!value.ok)throw Error(value.error||'Request failed');return value});
 }
 const frames=new Map(),taskFrames=new Map();let active='main',fleet=[];
 function themeApply(value){
  const theme=value==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):value;
  document.documentElement.dataset.theme=theme;
  $('wb-theme').value=value;
  if(!embedded){for(const frame of frames.values())frame.contentWindow?.postMessage({type:'wb-theme',value},new URL(frame.src).origin);for(const item of taskFrames.values())if(item.ready)item.frame.contentWindow?.postMessage({type:'wb-theme',value},item.origin);}
 }
 let themeChoice='system';try{const saved=localStorage.getItem('wuji-workbench-theme');if(['system','light','dark'].includes(saved))themeChoice=saved;}catch{}
 const fromParent=new URLSearchParams(location.search).get('theme');if(embedded&&['system','light','dark'].includes(fromParent))themeChoice=fromParent;
 themeApply(themeChoice);
 $('wb-theme').onchange=()=>{themeChoice=$('wb-theme').value;try{localStorage.setItem('wuji-workbench-theme',themeChoice);}catch{}themeApply(themeChoice)};
 matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>themeApply(themeChoice));
 window.addEventListener('message',event=>{if(embedded&&document.referrer&&event.origin===new URL(document.referrer).origin&&event.data?.type==='wb-theme')themeApply(event.data.value)});
 const taskPages=new Set(['home','library','feedback','parameters','connection','glove','records','devices','official-viz','calibration']);
 window.addEventListener('message',event=>{
  if(!taskView||event.source!==window.parent||!document.referrer||event.origin!==new URL(document.referrer).origin||event.data?.task_id!==taskViewId)return;
  const value=event.data;
  if(value.type==='wb-task-route'&&taskPages.has(value.page)){
   if(['official-viz','calibration'].includes(value.page)){if(taskNative)window.WujiConnectionHub?.show(value.page==='calibration'?'calibration':'official');}
   else{location.hash='#'+value.page;ws.showPage();}
  }else if(value.type==='wb-task-language'&&['zh','en'].includes(value.language))window.WujiLocale?.setLanguage(value.language);
  else if(value.type==='wb-task-capabilities'&&typeof value.allow_native==='boolean'){taskNative=value.allow_native;document.documentElement.dataset.taskNative=String(taskNative);}
  else if(value.type==='wb-task-owner'&&typeof value.current==='boolean')taskScope?.setParent(value.current,value.owner_revision);
 });
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
 function renderMappingBinding(){if(!mappingContext)return;
  const b=mappingContext.binding;bindingNote.textContent=`${b.generation} · ${b.side} · ${t('手套','Glove')} ${b.glove_serial||'—'} → ${t('灵巧手','Hand')} ${b.hand_serial||t('连接时选择','Select on connect')} · ${t('用户','User')} ${b.sdk_user||'—'} · v${mappingContext.revision}`;
 }
 function showContext(value,replace=true){mappingContext=value;if(replace){retarget=value.settings;retargetRender()}
  renderMappingBinding();
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
 const sessionBar=document.createElement('nav');sessionBar.id='wb-session-bar';sessionBar.className='wb-session-bar';sessionBar.hidden=true;
 const sessionBack=document.createElement('button'),sessionName=document.createElement('strong');sessionBack.type='button';sessionBack.id='wb-session-back';sessionName.id='wb-session-name';
 sessionBar.append(sessionBack,sessionName);if(!embedded)document.body.append(sessionBar);
 function sessionLayout(){if(embedded||sessionBar.hidden)return;const top=document.querySelector('.top');document.documentElement.style.setProperty('--wb-session-top',Math.ceil(top.getBoundingClientRect().bottom+8)+'px');}
 function sessionLabels(){const d=fleet.find(x=>x.id===active);sessionBar.hidden=active==='main';sessionBar.setAttribute('aria-label',t('当前设备工作区','Current hand workspace'));sessionBack.textContent=t('‹ 返回多手总览','‹ All hands');sessionName.textContent=d?.label||active;sessionLayout();}
 function notifyFrame(frame,visible){frame.contentWindow?.postMessage({type:'wb-visibility',visible},new URL(frame.src).origin);}
 function switchTo(id){
  if(id!=='main'&&!fleet.some(x=>x.id===id))return;
  active=id;document.body.classList.toggle('wb-child-active',id!=='main');
  for(const [key,frame] of frames){frame.hidden=key!==id;notifyFrame(frame,key===id);}
  if(id!=='main'&&!frames.has(id)){
   const d=fleet.find(x=>x.id===id);if(!d)return;
   const frame=document.createElement('iframe');frame.id='wb-device-frame-'+id;frame.className='wb-device-frame';frame.title=d.label;
   frame.src=`http://127.0.0.1:${d.port}/?embedded=1&theme=${encodeURIComponent(themeChoice)}#library`;
   frame.addEventListener('load',()=>notifyFrame(frame,active===id));
   document.body.append(frame);frames.set(id,frame);
  }
  selector.value=id;sessionLabels();window.dispatchEvent(new Event('workbench-session-visibility'));
 }
 sessionBack.onclick=()=>{switchTo('main');location.hash='#devices';ws.showPage();document.querySelector('.wsx-ws')?.focus();};
 window.addEventListener('resize',sessionLayout);if(!embedded)new ResizeObserver(sessionLayout).observe(document.querySelector('.top'));
 selector.onchange=()=>switchTo(selector.value);
 function taskFrameSpec(task){
  if(!task||typeof task.task_id!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(task.task_id)||!task.view||typeof task.view.url!=='string')throw Error(t('任务画面地址尚未核对','Task view address is unverified'));
  const url=new URL(task.view.url),declared=task.view.origin;
  if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password||url.origin!==declared||!url.port||Number(url.port)<1024||Number(url.port)>65535)throw Error(t('任务画面必须来自已登记的本机工作区','Task views must use their registered loopback workspace'));
  if(task.view.page&&!taskPages.has(task.view.page))throw Error(t('任务页面不受支持','Unsupported task page'));
  const allowNative=task.endpoint_id==='main'&&task.view.allow_native===true;
  if(['official-viz','calibration'].includes(task.view.page)&&!allowNative)throw Error(t('官方任务需由主工作台核对后进入','Official tasks require the main workbench preflight'));
  url.searchParams.set('embedded','1');url.searchParams.set('task_view','1');url.searchParams.set('task_id',task.task_id);if(allowNative)url.searchParams.set('task_native','1');else url.searchParams.delete('task_native');if(task.view.page)url.hash=['official-viz','calibration'].includes(task.view.page)?'connection':task.view.page;
  return {url:url.href,origin:url.origin,page:task.view.page||null,allowNative};
 }
 const taskDeviceSN=value=>typeof value==='string'?value:value?.serial||value?.sn||'';
 function taskPrimary(task){
  if(task.kind==='ensemble'){const hands=Array.isArray(task.devices?.hands)?task.devices.hands.map(taskDeviceSN):[];return hands.length>=2&&hands.every(Boolean)?JSON.stringify(['ensemble',...hands.slice().sort(),task.intent?.group_id,task.intent?.members]):'';}
  const glove=taskDeviceSN(task.devices?.glove),hand=taskDeviceSN(task.devices?.hand);return glove?'glove:'+glove:hand?'hand:'+hand:'';
 }
 function taskCurrent(task){
  if(!taskPrimary(task)||['disconnected','offline','unknown','unconfirmed','cleanup_required','conflict','error'].includes(task.status))return false;
  if(task.status==='calibrated')return task.observed?.continuation_verified===true;
  if(['calibrating','connecting'].includes(task.status))return true;
  return task.observed?.stale!==true&&task.observed?.hardware_active!==null;
 }
 const taskScopeReason=()=>t('此任务已断开、反馈过期或归属待核对；请使用当前设备任务。','This task is disconnected, stale or unverified. Use the current device task.');
 function taskApplyVisibility(item){const show=item.visible&&item.allowed&&item.scopeVerified;if(item.frame.hidden===show)item.frame.hidden=!show;if(item.ready&&item.lastVisible!==show){item.lastVisible=show;notifyFrame(item.frame,show);}}
 function taskNotifyOwner(item,id){if(item.ready)item.frame.contentWindow?.postMessage({type:'wb-task-owner',task_id:id,owner_revision:item.revision,current:item.allowed},item.origin);}
 function mountTaskFrame(task,host){
  if(embedded||!host)throw Error('Task frame host unavailable');
  const spec=taskFrameSpec(task),old=taskFrames.get(task.task_id);
  if(old){if(old.origin!==spec.origin||old.endpoint!==task.endpoint_id||old.primary!==taskPrimary(task))throw Error(t('任务所属设备已变化，请核对原会话','Task device owner changed; review the original session'));if(old.host!==host)throw Error('A live task frame cannot be reparented');return old.frame;}
  if(!taskCurrent(task))throw Error(taskScopeReason());
  const frame=document.createElement('iframe');frame.className='tw-frame';frame.id='tw-frame-'+task.task_id;frame.title=task.label||t('设备任务','Device task');frame.dataset.taskId=task.task_id;
  frame.hidden=true;const item={frame,host,origin:spec.origin,endpoint:task.endpoint_id,primary:taskPrimary(task),revision:task.revision,allowNative:spec.allowNative,allowed:true,scopeVerified:false,ownerContext:null,reason:t('正在核对任务归属。','Verifying task ownership.'),visible:false,page:spec.page,loads:0,ready:false};taskFrames.set(task.task_id,item);
  frame.addEventListener('load',()=>{item.loads++;item.ready=true;taskNotifyOwner(item,task.task_id);taskApplyVisibility(item);frame.contentWindow?.postMessage({type:'wb-task-language',task_id:task.task_id,language:L.lang},item.origin);frame.contentWindow?.postMessage({type:'wb-task-capabilities',task_id:task.task_id,allow_native:item.allowNative},item.origin);if(item.page)frame.contentWindow?.postMessage({type:'wb-task-route',task_id:task.task_id,page:item.page},item.origin);});
  frame.src=spec.url;host.append(frame);return frame;
 }
 function taskVisibility(id,visible){const item=taskFrames.get(id);if(!item)return;item.visible=visible===true;taskApplyVisibility(item);}
 function taskUpdate(task){const item=taskFrames.get(task.task_id);if(!item)return false;const spec=taskFrameSpec(task);
  if(item.origin!==spec.origin||item.endpoint!==task.endpoint_id||item.primary!==taskPrimary(task)){const notify=item.allowed;item.allowed=false;item.scopeVerified=false;item.reason=t('任务所属设备或成员已变化，原视图已冻结。','The task device or members changed. The original view is frozen.');if(notify)taskNotifyOwner(item,task.task_id);taskApplyVisibility(item);return false;}
  const changed=item.allowNative!==spec.allowNative,revisionChanged=item.revision!==task.revision,wasAllowed=item.allowed;item.allowNative=spec.allowNative;item.allowed=taskCurrent(task);
  if(revisionChanged){item.revision=task.revision;item.scopeVerified=false;item.ownerContext=null;}
  if(!item.allowed){item.scopeVerified=false;item.reason=taskScopeReason();}else if(revisionChanged)item.reason=t('正在核对新的任务会话。','Verifying the new task session.');
  if(revisionChanged||wasAllowed!==item.allowed)taskNotifyOwner(item,task.task_id);if(changed&&item.ready)item.frame.contentWindow?.postMessage({type:'wb-task-capabilities',task_id:task.task_id,allow_native:item.allowNative},item.origin);taskApplyVisibility(item);return item.allowed;
 }
 function taskSuspend(id){const item=taskFrames.get(id);if(!item)return;const changed=item.allowed;item.allowed=false;item.scopeVerified=false;item.reason=taskScopeReason();if(changed)taskNotifyOwner(item,id);taskApplyVisibility(item);}
 function taskRoute(id,page){const item=taskFrames.get(id);if(!item||!item.allowed||!taskPages.has(page)||['official-viz','calibration'].includes(page)&&!item.allowNative)return false;item.page=page;if(item.ready)item.frame.contentWindow?.postMessage({type:'wb-task-route',task_id:id,page},item.origin);return true;}
 window.addEventListener('message',event=>{if(embedded||event.data?.type!=='wb-task-scope')return;const id=event.data.task_id,item=taskFrames.get(id);if(!item||event.source!==item.frame.contentWindow||event.origin!==item.origin||event.data.owner_revision!==item.revision)return;const value=event.data;item.scopeVerified=item.allowed&&value.current===true&&typeof value.owner_context==='string'&&/^[a-f0-9]{64}$/.test(value.owner_context);item.ownerContext=item.scopeVerified?value.owner_context:null;if(item.allowed)item.reason=item.scopeVerified?'':typeof value.reason==='string'?value.reason.slice(0,500):taskScopeReason();taskApplyVisibility(item);window.dispatchEvent(new CustomEvent('task-view-scope',{detail:{task_id:id}}));});
 window.HandSessions={show(id){if(window.TaskWorkspace?.openEndpoint?.(id))return;switchTo(id);},refresh:()=>fleetRefresh(),taskFrames:{mount:mountTaskFrame,update:taskUpdate,current:taskCurrent,suspend:taskSuspend,setVisible:taskVisibility,route:taskRoute,inspect:id=>{const item=taskFrames.get(id);return item?{endpoint:item.endpoint,origin:item.origin,loads:item.loads,visible:item.visible,allowNative:item.allowNative,verified:item.scopeVerified,blockedReason:item.reason,primary:item.primary,revision:item.revision}:null;}}};
 const routedSwitches=new Set();
 const stableSwitch=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
 window.addEventListener('message',async event=>{
  if(embedded||event.data?.type!=='wb-task-switch')return;
  const sourceId=event.data.task_id,item=taskFrames.get(sourceId),receipt=event.data.receipt;
  if(!item||event.source!==item.frame.contentWindow||event.origin!==item.origin||receipt?.version!==1||receipt.source_task_id!==sourceId||!/^[a-f0-9]{32}$/.test(receipt.request_id||'')||!/^[a-f0-9]{32}$/.test(receipt.attempt_id||'')||!/^[a-f0-9]{64}$/.test(receipt.expected_owner||'')||!/^[a-f0-9]{64}$/.test(receipt.after_context||'')||routedSwitches.has(receipt.request_id))return;
  try{
   await window.TaskWorkspace?.refresh();const snapshot=window.TaskWorkspace?.snapshot(),target=snapshot?.tasks.find(task=>task.task_id===receipt.target_task_id),serial=receipt.target?.serial;
   if(!target||target.endpoint_id!==item.endpoint||target.view?.origin!==item.origin||!taskCurrent(target)||!serial||![taskDeviceSN(target.devices?.glove),taskDeviceSN(target.devices?.hand)].includes(serial))return;
   const r=await fetch('/api/task-view?task_id='+encodeURIComponent(target.task_id),{cache:'no-store',signal:AbortSignal.timeout(6000)}),proof=r.ok?await r.json():null;
   if(proof?.current!==true||proof.owner_context!==receipt.after_context||stableSwitch(proof.last_transition_receipt)!==stableSwitch(receipt))return;
   routedSwitches.add(receipt.request_id);
   // A later close or navigation is newer human intent. Keep its task tab,
   // without reopening a view the user just dismissed or replacing another task.
   if(snapshot.closed.includes(sourceId)||snapshot.selected!==sourceId||location.hash!=='#tasks')return;
   window.TaskWorkspace.reopen(target.task_id);
  }catch{/* Leave the source frozen; a later explicit task open rechecks its owner. */}
 });
 window.addEventListener('wuji-language',()=>{for(const [id,item]of taskFrames)if(item.ready)item.frame.contentWindow?.postMessage({type:'wb-task-language',task_id:id,language:L.lang},item.origin);});
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
  sessionLabels();
  const viewKey=JSON.stringify([fleet.map(({id,label,profile,connection,hardware,glove,hand_serial,glove_serial,following,stale})=>({id,label,profile,connection,hardware,glove,hand_serial,glove_serial,following,stale})),L.lang]);
  if(viewKey===fleetViewKey)return;fleetViewKey=viewKey;
  $('wb-device-list')?.replaceChildren(...fleet.map(x=>{
   const row=document.createElement('article');row.className='wb-device-card';
   const name=document.createElement('strong');name.textContent=x.label;
   const detail=document.createElement('p');detail.textContent=`${x.profile} · ${x.following===true?t('手套跟随中','Glove following'):x.hardware===true?t('动作中','Moving'):x.connection==='connected'?t('反馈在线','Feedback online'):x.glove==='receiving'?t('手套预览','Glove preview'):t('未连接','Disconnected')}`;
   const pair=document.createElement('p');pair.textContent=t('灵巧手：','Hand: ')+(x.hand_serial||'—')+' · '+t('手套：','Glove: ')+(x.glove_serial||'—');
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
  sessionLabels();
  $('wb-appearance-title').textContent=t('外观','Appearance');
  $('wb-appearance-note').textContent=t('使用系统原生磨砂；不采集桌面、不绘制外部折射。','Native frosted material. No desktop capture or external refraction.');
  $('wb-theme-label').textContent=t('显示主题','Theme');
  for(const [id,zh,en] of [['system','跟随系统','System'],['light','白色','Light'],['dark','黑色','Dark']])$('wb-theme').querySelector(`[value=${id}]`).textContent=t(zh,en);
  $('wb-retarget-title').textContent=t('映射输出调整','Mapper output adjustment');
  $('wb-retarget-note').textContent=t('所选求解器将 21 个关键点映射为 20 个关节角，再应用下方幅度、角度偏移和平滑。配置按左右手、代际、手套、灵巧手与标定用户分别保存。默认保持官方输出。','The selected solver maps 21 landmarks to 20 joint angles, followed by gain, offset and smoothing. Settings are separate for side, generation, glove, hand and SDK user. Defaults preserve SDK output.');
  $('wb-smoothing-label').textContent=t('输出平滑','Output smoothing');$('wb-retarget-default').textContent=t('恢复默认','Reset defaults');$('wb-retarget-save').textContent=t('保存映射','Save mapping');
  $('wb-glove-settings-link').textContent=t('调整手套映射 →','Adjust retargeting →');
  if(devices){$('wb-device-title').textContent=t('独立设备会话','Independent hand sessions');$('wb-device-note').textContent=t('多手可加入同一个工作区，也可分开使用。','Hands can share a workspace or work independently.');$('wb-device-name-label').textContent=t('名称','Name');$('wb-device-profile-label').textContent=t('型号','Model');$('wb-device-add').textContent=t('添加工作区','Add workspace')}
  retargetRender(false);
  renderMappingBinding();
 }
 window.addEventListener('console-state',event=>{state=event.detail;});
 window.addEventListener('wuji-language',()=>{labels();fleetRefresh()});
 labels();ws.showPage();
})();
