/* Shared task-frame host. Layout never owns a device connection. */
(()=>{'use strict';
 window.WujiTaskFrames={create({frames=new Map(),embedded=false,translate,locale}={}){
 const taskFrames=frames,t=translate||((zh,en)=>window.WujiLocale?.lang==='en'?en:zh),L=locale||window.WujiLocale;
 const taskPages=new Set(['home','library','feedback','parameters','connection','glove','records','devices','official-viz','calibration']);
 const notifyFrame=(frame,visible)=>frame.contentWindow?.postMessage({type:'wb-visibility',visible},new URL(frame.src).origin);
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
 function taskDispose(id){const item=taskFrames.get(id);if(!item)return;taskSuspend(id);taskVisibility(id,false);item.frame.remove();taskFrames.delete(id);}
 function taskRoute(id,page){const item=taskFrames.get(id);if(!item||!item.allowed||!taskPages.has(page)||['official-viz','calibration'].includes(page)&&!item.allowNative)return false;item.page=page;if(item.ready)item.frame.contentWindow?.postMessage({type:'wb-task-route',task_id:id,page},item.origin);return true;}
 window.addEventListener('message',event=>{if(embedded||event.data?.type!=='wb-task-scope')return;const id=event.data.task_id,item=taskFrames.get(id);if(!item||event.source!==item.frame.contentWindow||event.origin!==item.origin||event.data.owner_revision!==item.revision)return;const value=event.data;item.scopeVerified=item.allowed&&value.current===true&&typeof value.owner_context==='string'&&/^[a-f0-9]{64}$/.test(value.owner_context);item.ownerContext=item.scopeVerified?value.owner_context:null;if(item.allowed)item.reason=item.scopeVerified?'':typeof value.reason==='string'?value.reason.slice(0,500):taskScopeReason();taskApplyVisibility(item);window.dispatchEvent(new CustomEvent('task-view-scope',{detail:{task_id:id}}));});

 const routedSwitches=new Set(),pendingSwitches=new Map();
 const stableSwitch=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
 window.addEventListener('message',event=>{
  const value=event.data;if(embedded||value?.type!=='wb-task-switch-pending'||!/^[a-f0-9]{32}$/.test(value.request_id||''))return;
  const item=taskFrames.get(value.task_id);if(!item||event.source!==item.frame.contentWindow||event.origin!==item.origin)return;
  const old=pendingSwitches.get(value.request_id);
  if(value.pending===true){if(old||!item.allowed||!item.scopeVerified||item.ownerContext!==value.owner_context||!/^[a-f0-9]{64}$/.test(value.expected_owner||''))return;pendingSwitches.set(value.request_id,{source:value.task_id,token:window.TaskWorkspace?.beginSwitch(value.task_id),processing:false});}
  else if(value.pending===false&&old?.source===value.task_id&&!old.processing){pendingSwitches.delete(value.request_id);window.TaskWorkspace?.endSwitch(value.task_id);}
 });
 window.addEventListener('message',async event=>{
  if(embedded||event.data?.type!=='wb-task-switch')return;
  const sourceId=event.data.task_id,item=taskFrames.get(sourceId),receipt=event.data.receipt;
  if(!item||event.source!==item.frame.contentWindow||event.origin!==item.origin||receipt?.version!==1||receipt.source_task_id!==sourceId||!/^[a-f0-9]{32}$/.test(receipt.request_id||'')||!/^[a-f0-9]{32}$/.test(receipt.attempt_id||'')||!/^[a-f0-9]{64}$/.test(receipt.expected_owner||'')||!/^[a-f0-9]{64}$/.test(receipt.after_context||'')||routedSwitches.has(receipt.request_id))return;
  const pending=pendingSwitches.get(receipt.request_id);if(pending)pending.processing=true;
  const token=pending?.token||window.TaskWorkspace?.beginSwitch(sourceId);
  try{
   await window.TaskWorkspace?.refresh();const snapshot=window.TaskWorkspace?.snapshot(),target=snapshot?.registryTasks.find(task=>task.task_id===receipt.target_task_id),serial=receipt.target?.serial;
   if(!target||target.endpoint_id!==item.endpoint||target.view?.origin!==item.origin||!taskCurrent(target)||!serial||![taskDeviceSN(target.devices?.glove),taskDeviceSN(target.devices?.hand)].includes(serial))return;
   const r=await fetch('/api/task-view?task_id='+encodeURIComponent(target.task_id),{cache:'no-store',signal:AbortSignal.timeout(6000)}),proof=r.ok?await r.json():null;
   if(proof?.current!==true||proof.owner_context!==receipt.after_context||stableSwitch(proof.last_transition_receipt)!==stableSwitch(receipt))return;
   routedSwitches.add(receipt.request_id);await window.TaskWorkspace?.finishSwitch(sourceId,target,receipt,token);
  }catch{/* Keep the previous view frozen if ownership cannot be proved. */}
  finally{pendingSwitches.delete(receipt.request_id);window.TaskWorkspace?.endSwitch(sourceId);}
 });
 window.addEventListener('wuji-language',()=>{for(const [id,item]of taskFrames)if(item.ready)item.frame.contentWindow?.postMessage({type:'wb-task-language',task_id:id,language:L.lang},item.origin);});
 const api={mount:mountTaskFrame,update:taskUpdate,current:taskCurrent,suspend:taskSuspend,dispose:taskDispose,setVisible:taskVisibility,route:taskRoute,
 inspect:id=>{const item=taskFrames.get(id);return item?{endpoint:item.endpoint,origin:item.origin,loads:item.loads,visible:item.visible,allowNative:item.allowNative,verified:item.scopeVerified,blockedReason:item.reason,primary:item.primary,revision:item.revision}:null;}};
 return api;
 }};
})();
