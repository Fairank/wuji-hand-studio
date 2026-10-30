/* Explicit uses of the shared device directory. No action runs on selection. */
(()=>{
 'use strict';
 const directory=window.WujiDeviceInventory;if(!directory)return;
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 async function state(){const r=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error(t('工作台服务未就绪','Workbench service unavailable'));return r.json();}
 let switching=false,serviceEpoch=0,serviceToken='',switchOwner=null,featureOwner=null,observedState=null,useError='',useFailure=null;
 const switchFailures=new WeakMap();
 function retire(){serviceEpoch++;switchOwner=null;switching=false;featureOwner=null;useError='';useFailure=null;}
 window.addEventListener('console-state',event=>{const token=event.detail?.csrf;if(typeof token!=='string'||!token)return;if(serviceToken&&serviceToken!==token)retire();serviceToken=token;observedState=event.detail;renderContexts();});
 window.addEventListener('console-offline',event=>{if(event.detail?.task_scope===true)return;retire();serviceToken='';observedState=null;renderContexts();});
 const serialOf=row=>row?.sn||row?.serial||'';
 const profileID=value=>typeof value==='string'&&/^hand[12]_(left|right)$/.test(value);
 const hasUser=target=>Object.hasOwn(target,'user_id')&&typeof target.user_id==='string';
 const requestID=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
 function connected(s,target){
  if(target.mode==='official'){const v=s.official_viz||{};return v.active===true&&v.phase==='running'&&serialOf(v.reported_device||v.device)===target.serial;}
  if(target.mode==='hand')return s.connection==='connected'&&s.device_id===target.serial;
  const g=s.glove||{},f=g.feedback||{},identity=f.identified_device||{};return g.busy===true&&g.connection==='receiving'&&g.glove_serial===target.serial&&(!hasUser(target)||g.user?.user_id===target.user_id)&&(!target.profile||g.profile===target.profile)&&(!target.hand_serial||f.connection==='connected'&&f.device_id===target.hand_serial&&f.stale===false&&identity.source==='sdk_identity'&&identity.serial===target.hand_serial&&identity.profile===target.profile);
 }
 function emit(progress={}){window.dispatchEvent(new CustomEvent('connection-switch-update',{detail:{pending:switching,...progress}}));}
 async function recover(requestId,checkCurrent){
  const deadline=Date.now()+75000;let attempt=null;
  while(Date.now()<deadline){
   checkCurrent();const r=await fetch('/api/connection-switch?request_id='+encodeURIComponent(requestId),{cache:'no-store',signal:AbortSignal.timeout(6000)});checkCurrent();
   if(r.status===404){await new Promise(resolve=>setTimeout(resolve,500));continue;}
   if(!r.ok)throw Error(t('无法核对本次切换，未重新发送连接请求。','This switch cannot be verified. The connection request was not resent.'));
   const value=await r.json(),progress=value.connection_switch||value;checkCurrent();
   if(progress.request_id!==requestId)throw Error(t('切换任务已变化，请查看当前连接。','The switch task changed. Review the current connection.'));
   if(attempt&&progress.attempt_id!==attempt)throw Error(t('切换任务已变化，请查看当前连接。','The switch task changed. Review the current connection.'));
   if(progress.attempt_id)attempt=progress.attempt_id;
   if(progress.busy!==true)return {ok:!progress.error,connection_switch:progress,connection_switch_receipt:progress.receipt,error:progress.error};
   await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw Error(t('切换结果仍未确认，未重新发送连接请求。请查看当前设备任务。','The switch outcome is still unconfirmed. The request was not resent. Review the current device task.'));
 }
 async function chooseProfile(before,target,checkCurrent){
  checkCurrent();const result=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':before.csrf},body:JSON.stringify({name:'connection_switch_users'}),signal:AbortSignal.timeout(30000)});checkCurrent();const value=await result.json();checkCurrent();
  if(!result.ok||value.ok===false)throw Error(value.error||t('无法读取遥操作档案，原连接保持。','Teleoperation profiles are unavailable. The original connection is retained.'));
  const current=await state(),catalog=current.connection_switch?.user_catalog||value.connection_switch?.user_catalog;checkCurrent(current);
  if(current.connection_switch?.expected_owner!==before.connection_switch.expected_owner)throw Error(t('设备连接已变化，请重新选择。','The device connection changed. Select it again.'));
  if(!catalog||catalog.busy===true||catalog.error)throw Error(catalog?.error||t('遥操作档案尚未就绪，请稍后重试。','Teleoperation profiles are not ready. Retry shortly.'));
  const users=Array.isArray(catalog.users)?catalog.users.filter(row=>typeof row?.user_id==='string'):[];
  if(!users.length)throw Error(t('没有可用的遥操作档案，请先完成手套标定。原连接保持。','No teleoperation profile is available. Calibrate the glove first. The original connection is retained.'));
  if(!window.HandWorkbenchSheet)throw Error(t('档案选择尚未就绪，请稍后重试。','Profile selection is not ready. Retry shortly.'));
  const sheet=window.HandWorkbenchSheet.create({id:'connection-switch-profile',title:t('切换到遥操作','Switch to teleoperation'),description:t('选择这只手套要使用的档案。','Choose the profile for this glove.')});
  const form=document.createElement('form'),modelRow=document.createElement('div'),modelLabel=document.createElement('label'),model=document.createElement('select'),userRow=document.createElement('div'),userLabel=document.createElement('label'),user=document.createElement('select'),actions=document.createElement('div'),confirm=document.createElement('button');
  modelRow.className=userRow.className='wb-row';actions.className='wb-actions';model.id='connection-switch-model';user.id='connection-switch-user';modelLabel.htmlFor=model.id;userLabel.htmlFor=user.id;modelLabel.textContent=t('映射型号','Hand model');userLabel.textContent=t('遥操作档案','Teleoperation profile');
  model.append(new Option(t('选择型号与左右手','Choose model and hand side'),''));for(const id of ['hand2_left','hand2_right','hand1_left','hand1_right']){const [generation,side]=id.split('_');model.append(new Option('Wuji Hand '+(generation==='hand2'?'2':'1')+' · '+t(side==='left'?'左手':'右手',side==='left'?'Left':'Right'),id));}
  const viewer=before.official_viz||{},reported=[viewer.reported_device,viewer.device].filter(row=>serialOf(row)===target.serial),sides=new Set(reported.map(row=>row.side||row.handedness).filter(side=>['left','right'].includes(side))),knownSide=sides.size===1?[...sides][0]:null,generation=before.device_profile?.generation;
  model.value=profileID(target.profile)?target.profile:knownSide&&['hand1','hand2'].includes(generation)?generation+'_'+knownSide:'';
  user.append(new Option(t('选择档案','Choose a profile'),'__choose_user__'),...users.map(row=>new Option(row.name||row.user_id||t('Default · 内置手型','Default · Built-in model'),row.user_id)));user.selectedIndex=0;confirm.type='submit';confirm.className='primary';confirm.id='connection-switch-confirm';confirm.textContent=t('切换并连接','Switch and connect');confirm.disabled=true;user.onchange=model.onchange=()=>{confirm.disabled=!profileID(model.value)||user.selectedIndex<1||!users.some(row=>row.user_id===user.value);};modelRow.append(modelLabel,model);userRow.append(userLabel,user);actions.append(confirm);form.append(modelRow,userRow,actions);sheet.body.append(form);
  return new Promise((resolve,reject)=>{let settled=false;const cleanup=()=>{window.removeEventListener('console-state',changed);window.removeEventListener('console-offline',changed);sheet.destroy();};const changed=()=>{if(settled)return;try{checkCurrent();}catch(error){settled=true;sheet.close();cleanup();reject(error);}};sheet.element.addEventListener('close',()=>{if(settled)return;settled=true;cleanup();const error=Error(t('已取消切换，原连接保持。','Switch cancelled. The original connection is retained.'));error.name='SwitchCancelled';reject(error);},{once:true});window.addEventListener('console-state',changed);window.addEventListener('console-offline',changed);form.onsubmit=event=>{event.preventDefault();changed();if(settled||user.selectedIndex<1||!users.some(row=>row.user_id===user.value)||!profileID(model.value))return;settled=true;const selected={...target,profile:model.value,user_id:user.value};sheet.close();cleanup();resolve(selected);};changed();if(!settled){sheet.open();user.focus();}});
 }
 async function switchConnection(target){
  if(switching)throw Error(t('正在切换设备，请等待本次操作完成。','A device switch is in progress. Wait for it to finish.'));
  if(!target||!['official','teleoperation','hand'].includes(target.mode)||typeof target.serial!=='string'||!target.serial)throw Error(t('请先选择要接入的设备。','Choose the device to connect first.'));
  const operationEpoch=serviceEpoch,ticket={epoch:operationEpoch};switchOwner=ticket;switching=true;emit();
  try{
   const before=await state(),context=before.connection_switch;ticket.owner_context=context?.expected_owner;ticket.before_transition={request_id:context?.request_id||null,attempt_id:context?.attempt_id||null};
   const checkCurrent=current=>{if(switchOwner!==ticket||operationEpoch!==serviceEpoch||serviceToken&&serviceToken!==before.csrf||current&&current.csrf!==before.csrf)throw Error(t('工作台连接已变化，请核对当前设备后重新选择。','The Workbench connection changed. Review current devices and select again.'));};checkCurrent(before);
   if(!before.csrf||!context||!/^[a-f0-9]{64}$/.test(context.expected_owner||''))throw Error(t('连接归属尚未核对，请稍后重试。','Connection ownership is unverified. Retry shortly.'));
   if(context.busy===true)throw Error(t('正在切换设备，请等待本次操作完成。','A device switch is in progress. Wait for it to finish.'));
   if(context.can_switch!==true)throw Error(context.blocked_reason||t('当前连接尚未核对，暂不能切换。','The current connection is unverified. Switching is unavailable.'));
   let selected={...target};
   const currentOwner=context.current_owner;
   if(selected.mode==='teleoperation'&&!hasUser(selected)&&currentOwner?.mode==='teleoperation'&&currentOwner.serial===selected.serial&&typeof currentOwner.user_id==='string'&&profileID(currentOwner.profile)&&(!selected.profile||selected.profile===currentOwner.profile)){selected.user_id=currentOwner.user_id;selected.profile=currentOwner.profile;}
   // Only the server's exact source receipt may supply a previous SDK user.
   // A name match, serial prefix, or Default is never a profile identity.
   // Native ordinary entry reads the current saved wearer server-side; an old
   // continuation or resume must not impersonate an explicit profile choice.
   const nativeSaved=context.saved_profile_supported===true,continuation=before.calibrated_teleoperation,verifiedCalibration=!nativeSaved&&continuation?.serial===selected.serial&&continuation.can_continue===true&&typeof continuation.run_id==='string'&&!!continuation.run_id&&typeof continuation.expected_revision==='string'&&!!continuation.expected_revision&&['left','right'].includes(continuation.side)&&(!selected.profile||selected.profile.endsWith('_'+continuation.side));
   if(selected.mode==='teleoperation'&&!hasUser(selected)&&verifiedCalibration&&!selected.profile&&['hand1','hand2'].includes(before.device_profile?.generation))selected.profile=before.device_profile.generation+'_'+continuation.side;
   const resume=context.resume;
   if(selected.mode==='teleoperation'&&!hasUser(selected)&&!verifiedCalibration&&resume?.serial===selected.serial&&typeof resume.user_id==='string'&&profileID(resume.profile)){if(!nativeSaved){selected.user_id=resume.user_id;selected.profile=selected.profile||resume.profile;}if(!Object.hasOwn(selected,'timeout_ms')&&Number.isInteger(resume.timeout_ms)&&resume.timeout_ms>=100&&resume.timeout_ms<=2000)selected.timeout_ms=resume.timeout_ms;}
   if(selected.mode==='teleoperation'&&!hasUser(selected)&&!verifiedCalibration&&context.saved_profile_supported!==true)selected=await chooseProfile(before,selected,checkCurrent);checkCurrent();
   const requestId=requestID(),command={name:'connection_switch',request_id:requestId,expected_owner:context.expected_owner,target:selected};let response,value,recovered=false,readingProgress=false,progressActive=true;const progressAbort=new AbortController();
   ticket.request_id=requestId;
   const progressTimer=setInterval(async()=>{if(readingProgress||!progressActive)return;readingProgress=true;try{checkCurrent();const r=await fetch('/api/connection-switch?request_id='+encodeURIComponent(requestId),{cache:'no-store',signal:AbortSignal.any([progressAbort.signal,AbortSignal.timeout(6000)])});if(r.ok){const body=await r.json(),progress=body.connection_switch||body;checkCurrent();if(progressActive&&progress.request_id===requestId)emit(progress);}}catch{}finally{readingProgress=false;}},350);
   try{
    try{response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':before.csrf},body:JSON.stringify(command),signal:AbortSignal.timeout(90000)});value=await response.json();checkCurrent();}
    catch(error){checkCurrent();if(!['AbortError','TimeoutError','TypeError'].includes(error?.name))throw error;value=await recover(requestId,checkCurrent);recovered=true;}
   }finally{progressActive=false;clearInterval(progressTimer);progressAbort.abort();}
   if(response&&!response.ok||value.ok===false)throw Error(value.error||t('设备切换未完成。','The device switch did not complete.'));
   if(value.connection_switch?.unchanged===true){const current=await state();checkCurrent(current);if(value.connection_switch.request_id!==requestId||current.connection_switch?.expected_owner!==context.expected_owner||!connected(current,selected))throw Error(t('当前连接已变化，请重新核对。','The current connection changed. Verify it again.'));return {state:current,target:selected,result:value,unchanged:true};}
   const receipt=value.connection_switch_receipt,proofTarget=receipt?.target;
   if(receipt?.version!==1||receipt.request_id!==requestId||!/^[a-f0-9]{32}$/.test(receipt.attempt_id||'')||receipt.expected_owner!==context.expected_owner||!/^[a-f0-9]{64}$/.test(receipt.after_context||'')||typeof receipt.target_task_id!=='string'||!receipt.target_task_id||proofTarget?.mode!==selected.mode||proofTarget.serial!==selected.serial||hasUser(selected)&&proofTarget.user_id!==selected.user_id||selected.profile&&proofTarget.profile!==selected.profile||selected.hand_serial&&proofTarget.hand_serial!==selected.hand_serial)throw Error(t('切换结果尚未核对，请查看当前设备任务。','The switch result is unverified. Review the current device task.'));
   let scoped=response?.connectionSwitchVerified===true?{verified:true,routed:response.connectionSwitchRouted===true}:null;
   if(recovered&&window.WujiTaskConnectionSwitch)scoped=await window.WujiTaskConnectionSwitch.accept(requestId,receipt);checkCurrent();
   if(scoped?.routed===true)return {target:selected,result:value,routed:true};
   if(!scoped?.verified){const proofResponse=await fetch('/api/task-view?task_id='+encodeURIComponent(receipt.target_task_id),{cache:'no-store',signal:AbortSignal.timeout(6000)}),proof=proofResponse.ok?await proofResponse.json():null;
    checkCurrent();if(proof?.current!==true||proof.owner_context!==receipt.after_context||proof.last_transition_receipt?.request_id!==requestId||proof.last_transition_receipt?.attempt_id!==receipt.attempt_id)throw Error(t('新连接归属尚未核对，请查看当前设备任务。','Ownership of the new connection is unverified. Review the current device task.'));}
   const attempt=receipt.attempt_id,deadline=Date.now()+60000;
   let current=await state();checkCurrent(current);
   while(!connected(current,selected)){
    const progress=current.connection_switch;
    if(attempt&&progress?.attempt_id!==attempt)throw Error(t('切换任务已变化，请查看当前连接。','The switch task changed. Review the current connection.'));
    if(progress?.error)throw Error(progress.error);
    if(progress?.busy!==true||Date.now()>deadline)throw Error(t('切换结果尚未确认，请查看当前连接状态。','The switch outcome is unconfirmed. Review the current connection.'));
    await new Promise(resolve=>setTimeout(resolve,250));checkCurrent();current=await state();checkCurrent(current);
   }
   if(attempt&&current.connection_switch?.attempt_id!==attempt)throw Error(t('切换任务已变化，请查看当前连接。','The switch task changed. Review the current connection.'));
   return {state:current,target:selected,result:value};
  }catch(error){
   const failure=['AbortError','TimeoutError'].includes(error?.name)?Error(t('切换请求超时，结果尚未确认。请核对当前设备任务。','The switch request timed out and its outcome is unconfirmed. Review the current device task.')):error;
   if(failure&&typeof failure==='object')switchFailures.set(failure,{request_id:ticket.request_id||null,owner_context:ticket.owner_context||null,before_transition:ticket.before_transition});
   throw failure;
  }finally{if(switchOwner===ticket){switchOwner=null;switching=false;emit();}}
 }
 window.WujiConnectionSwitch={run:switchConnection,busy:()=>switching};
 function chooseGlove(device){window.dispatchEvent(new CustomEvent('device-inventory-selection',{detail:{device,serial:device.sn||device.serial,kind:'glove'}}));}
 // A feature choice is explicit; hashes, history and render passes remain read-only.
 function currentOwner(value=observedState){
  const own=value?.connection_switch?.current_owner;
  if(!own||!['official','teleoperation','hand'].includes(own.mode)||!['glove','hand'].includes(own.kind)||typeof own.serial!=='string'||!own.serial)return null;
  if(own.mode==='teleoperation'&&(!Number.isSafeInteger(own.session)||own.session<=0||typeof own.user_id!=='string'||!profileID(own.profile)))return null;
  return own;
 }
 // Retain the original failure, but stop attributing it to a later verified
 // transaction. A plain refresh, a failed attempt, or our own changed context
 // is not proof that the old error has been superseded. Neither is a receipt
 // that was already present before the failed request started.
 function currentUseError(){
  if(!useError||useFailure?.retired)return '';
  const sw=observedState?.connection_switch||{},receipt=sw.receipt,owner=currentOwner(),target=receipt?.target;
  if(useFailure&&owner&&sw.busy===false&&sw.phase==='connected'&&!sw.error&&
     /^[a-f0-9]{64}$/.test(useFailure.owner_context||'')&&sw.expected_owner!==useFailure.owner_context&&
     /^[a-f0-9]{64}$/.test(sw.expected_owner||'')&&receipt?.version===1&&
     /^[a-f0-9]{32}$/.test(receipt.request_id||'')&&receipt.request_id===sw.request_id&&receipt.request_id!==useFailure.request_id&&receipt.request_id!==useFailure.before_transition?.request_id&&
     /^[a-f0-9]{32}$/.test(receipt.attempt_id||'')&&receipt.attempt_id===sw.attempt_id&&receipt.attempt_id!==useFailure.before_transition?.attempt_id&&
     /^[a-f0-9]{64}$/.test(receipt.expected_owner||'')&&/^[a-f0-9]{64}$/.test(receipt.after_context||'')&&
     target?.mode===owner.mode&&target.serial===owner.serial&&connected(observedState,target)&&
     (owner.mode!=='teleoperation'||receipt.sdk_session===owner.session&&target.user_id===owner.user_id&&target.profile===owner.profile)){
   useFailure.retired=true;return '';
  }
  return useError;
 }
 function currentCalibration(value=observedState){const c=value?.calibration||{},run=c.run||c;return run&&typeof run.id==='string'&&typeof run.serial==='string'&&(run.running===true||run.cleanup_required===true||c.cleanup_required===true)?run:null;}
 function selectedDevice(purpose,value=observedState){
  const owned=currentOwner(value),calibration=currentCalibration(value),glove=purpose==='teleoperation'||purpose==='calibration';
  if(calibration&&glove)return {serial:calibration.serial,sn:calibration.serial,kind:'glove'};
  if(owned&&(!glove||owned.kind==='glove'))return {serial:owned.serial,sn:owned.serial,kind:owned.kind};
  return directory.selection(glove?'glove':undefined);
 }
 function chooseDevice(kind='glove'){directory.show?.(kind);}
 function navigate(purpose,serial){
  if(purpose==='teleoperation'){location.hash='#glove';window.WujiWorkspace.showPage();}
  else if(purpose==='hand'){location.hash='#library';window.WujiWorkspace.showPage();}
  else{window.WujiConnectionHub.show(purpose==='calibration'?'calibration':'official');if(purpose==='official')window.WujiOfficialViz?.showDeviceMode();}
 }
 async function useFeature(purpose,device=null,options={}){
  if(!['official','teleoperation','calibration','hand'].includes(purpose)||featureOwner)return;
  const ticket={epoch:serviceEpoch,purpose,serial:serialOf(device)};featureOwner=ticket;useError='';useFailure=null;renderContexts();
  const current=()=>featureOwner===ticket&&ticket.epoch===serviceEpoch;
  try{
   const before=await state();ticket.owner_context=before.connection_switch?.expected_owner;ticket.before_transition={request_id:before.connection_switch?.request_id||null,attempt_id:before.connection_switch?.attempt_id||null};if(!current()||serviceToken&&before.csrf!==serviceToken)return;
   const chosen=device||selectedDevice(purpose,before),serial=serialOf(chosen);
   if(!serial||['teleoperation','calibration'].includes(purpose)&&chosen?.kind!=='glove'||purpose==='hand'&&chosen?.kind!=='hand'){chooseDevice(purpose==='official'?'all':'glove');return;}
   ticket.serial=serial;if(chosen.kind==='glove')chooseGlove({...chosen,sn:serial,serial});
   if(options.navigate!==false)navigate(purpose,serial);renderContexts();
   if(purpose==='calibration')return; // The final Start atomically hands over this exact glove.
   const target={mode:purpose,serial,...purpose==='official'?{theme:document.documentElement.dataset.theme==='dark'?'dark':'light'}:{}};
   if(purpose==='teleoperation'&&device?.mode==='teleoperation')for(const key of ['user_id','profile','hand_serial','timeout_ms','address'])if(Object.hasOwn(device,key))target[key]=device[key];
   const switched=await switchConnection(target);if(!current())return;if(switched.routed)return switched;
   const latest=await window.WujiConnection?.refresh?.();if(current()&&latest)observedState=latest;if(current())directory.select?.(serial);if(current()&&purpose==='hand'&&options.navigate!==false&&latest?.device_profile?.id){window.WujiDemonstration?.selectProfile(latest.device_profile.id);const select=document.getElementById('demonstration-hand');if(select){select.value=serial;select.dispatchEvent(new Event('change'));}}
   if(current()&&purpose==='official')await window.WujiOfficialViz?.selectDevice(serial);
   return {...switched,state:latest||switched.state};
  }catch(error){if(current()){useError=error.name==='SwitchCancelled'?'':error?.message||t('设备用途切换未完成，请核对当前会话。','The device switch did not complete. Review the current session.');useFailure={...(switchFailures.get(error)||{owner_context:ticket.owner_context,request_id:null,before_transition:ticket.before_transition}),retired:false};}throw error;}
  finally{if(current()){featureOwner=null;renderContexts();}}
 }
 const contexts=[];
 function renderContexts(){
  const error=currentUseError();
  for(const item of contexts){
   const owner=currentOwner(),calibration=currentCalibration(),selected=selectedDevice(item.purpose),serial=featureOwner?.serial||serialOf(selected),sw=observedState?.connection_switch||{};
   item.name.textContent=serial||(item.purpose==='teleoperation'?t('尚未选择手套','No glove selected'):t('尚未选择设备','No device selected'));
   const pending=featureOwner||sw.busy===true;
   if(item.calibrate){item.calibrate.textContent=t('前往标定','Open calibration');item.calibrate.hidden=!error||!/已标定|标定|calibrat/i.test(error);}
   item.status.textContent=error||(pending?sw.phase==='stopping'?t('正在结束原会话…','Ending the previous session…'):sw.phase==='connecting'?t('正在接入所选功能…','Opening the selected feature…'):t('正在准备切换…','Preparing the switch…'):calibration?t('当前用途：手型标定','Current use: calibration'):owner?t('当前用途：','Current use: ')+(owner.mode==='official'?'CLI':owner.mode==='teleoperation'?t('遥操作','Teleoperation'):t('灵巧手反馈','Hand feedback')):sw.owner_error?t('会话归属待核对','Session ownership unverified'):serial?t('已选择，进入功能时接入','Selected; connects when a feature is chosen'):t('先在设备页选择本次设备','Choose the device on the Devices page'));
   item.status.dataset.error=String(!!error||!!sw.owner_error);item.change.textContent=serial?t('更换设备','Change device'):t('选择设备','Choose device');item.change.disabled=!!pending;
  }
  window.dispatchEvent(new CustomEvent('device-use-update',{detail:{pending:!!featureOwner,error}}));
 }
 function mountContext(host,purpose){
  if(!host)return;const row=document.createElement('section');row.className='device-use-context';const name=document.createElement('strong'),status=document.createElement('span'),change=document.createElement('button'),calibrate=purpose==='teleoperation'?document.createElement('a'):null;status.setAttribute('role','status');status.setAttribute('aria-live','polite');change.type='button';change.onclick=()=>chooseDevice(purpose==='teleoperation'?'glove':'all');row.append(name,status,change);if(calibrate){calibrate.className='device-use-calibration';calibrate.href='#connection';calibrate.textContent=t('前往标定','Open calibration');calibrate.hidden=true;calibrate.onclick=event=>{event.preventDefault();useFeature('calibration').catch(()=>{});};row.append(calibrate);}host.prepend(row);contexts.push({purpose,name,status,change,calibrate});
 }
 window.WujiDeviceUse={open:useFeature,choose:chooseDevice,current:()=>currentOwner(),selected:purpose=>selectedDevice(purpose),snapshot:()=>({pending:!!featureOwner,error:currentUseError()})};
 const enterTeleoperation=event=>{if(event.defaultPrevented)return;event.preventDefault();useFeature('teleoperation').catch(()=>{});};
 for(const link of document.querySelectorAll('.directory [data-page="glove"]'))link.addEventListener('click',enterTeleoperation);
 mountContext(document.getElementById('hub-official'),'official');mountContext(document.querySelector('#page-glove .glove-controls'),'teleoperation');
 window.addEventListener('connection-switch-update',renderContexts);window.addEventListener('device-inventory-selection',renderContexts);window.addEventListener('device-inventory-update',renderContexts);window.addEventListener('wuji-language',renderContexts);renderContexts();

 directory.setUseHandler(async(mode,device)=>{
  // Re-read after the click. Retained cards are never sufficient authority.
  await directory.refresh();const current=directory.snapshot(),serial=device.sn||device.serial;
  const found=current?.devices?.find(row=>(row.sn||row.serial)===serial);
  if(!found?.selectable||serialOf(directory.selection())!==serial)throw Error(t('设备列表或所选设备已改变，请重新选择后继续。','The device list or selected device changed. Select it again to continue.'));
  if(mode==='official'){
   if(!found.official_available)throw Error(t('请先完成官方设备发现','Run official discovery first'));
   await useFeature('official',found,{navigate:found.kind!=='glove'||currentOwner()?.mode==='official'&&currentOwner()?.serial===serial});return;
  }
  if(mode==='hand'){
   if(found.kind!=='hand')throw Error(t('所选设备不是灵巧手','Selected device is not a hand'));
   // Exact serial identity selects the backend's original discovered route.
   await useFeature('hand',found,{navigate:currentOwner()?.mode==='hand'&&currentOwner()?.serial===serial});return;
  }
  if(found.kind!=='glove')throw Error(t('请先选择手套','Select a glove first'));
  chooseGlove(found);
  if(mode==='calibration'){await useFeature('calibration',found);return;}
  if(mode!=='preview')throw Error('Unknown device workflow');
  // This button is the explicit purpose choice. The shared coordinator ends
  // only its exact source owner and opens the SDK; following remains separate.
  await useFeature('teleoperation',found);return;
 });
})();
