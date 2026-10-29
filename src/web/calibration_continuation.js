/* Continue the verified current official result; never infer a device or user. */
(()=>{
 'use strict';
 let state=null,pending=false,requestedRun=null,requestedMode='virtual',entryHash='',error='',host=null,button=null,realButton=null,cancel=null,note=null;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 function loadedSession(){
  const c=state?.calibrated_teleoperation,g=state?.glove,p=g?.model_provenance;
  return !!c&&!!g&&Number.isInteger(c.sdk_session)&&/^[a-f0-9]{32}$/.test(c.run_id||'')
   &&/^[a-f0-9]{64}$/.test(c.model_sha256||'')&&typeof g.user?.user_id==='string'&&!!g.user.user_id
   &&g.session_id===c.sdk_session&&g.busy===true&&g.connection==='receiving'
   &&g.glove_serial===c.serial&&p?.source_run_id===c.run_id&&p?.model_sha256===c.model_sha256
   &&p?.owner_user_id===g.user?.user_id&&g.profile===state.device_profile?.id;
 }
 function mount(){
  const result=document.querySelector('.oc-result');if(!result)return false;
  if(host?.isConnected)return true;
  host=document.createElement('div');host.className='calibration-continuation';
  button=document.createElement('button');button.type='button';button.className='primary';
  realButton=document.createElement('button');realButton.type='button';
  cancel=document.createElement('button');cancel.type='button';
  note=document.createElement('p');note.setAttribute('role','status');
  host.append(button,realButton,cancel,note);result.append(host);
  const begin=async mode=>{
   const context=state?.calibrated_teleoperation;if(!context||pending)return;
   if(context.connected===true||loadedSession()){navigate(mode);return;}
   if(context.can_continue!==true||!context.run_id||!context.expected_revision)return;
   pending=true;requestedRun=context.run_id;requestedMode=mode;entryHash=location.hash;error='';render();
   try{acceptReply(await request('calibrated_teleoperation_continue',{run_id:context.run_id,expected_revision:context.expected_revision}),context.run_id,context.attempt_id,true);}
   catch(e){if(state?.calibrated_teleoperation?.run_id===context.run_id&&(!state.calibrated_teleoperation.attempt_id||state.calibrated_teleoperation.attempt_id===context.attempt_id))error=requestError(e);requestedRun=null;}finally{pending=false;render();}
  };
  button.onclick=()=>begin('virtual');realButton.onclick=()=>begin('real');
  cancel.onclick=async()=>{if(pending)return;const context=state?.calibrated_teleoperation;if(!context?.run_id||!context?.attempt_id)return;pending=true;requestedRun=null;error='';render();try{acceptReply(await request('calibrated_teleoperation_cancel',{run_id:context.run_id,attempt_id:context.attempt_id}),context.run_id,context.attempt_id);}catch(e){if(state?.calibrated_teleoperation?.run_id===context.run_id&&state?.calibrated_teleoperation?.attempt_id===context.attempt_id)error=requestError(e);}finally{pending=false;render();}};
  return true;
 }
 function acceptReply(value,runId,attemptId,starting=false){
  const next=value?.calibrated_teleoperation;
  const current=state?.calibrated_teleoperation;
  const sameAttempt=starting?(!current?.attempt_id||current.attempt_id===attemptId||current.attempt_id===next?.attempt_id):(current?.attempt_id===attemptId&&next?.attempt_id===attemptId);
  if(current?.run_id===runId&&next?.run_id===runId&&sameAttempt)state={...state,calibrated_teleoperation:next};
 }
 function requestError(value){
  if(['AbortError','TimeoutError'].includes(value?.name))return t('请求超时，结果尚未确认；请核对当前任务状态。','The request timed out; its outcome is unconfirmed. Review the current task state.');
  if(value?.name==='TypeError')return t('连接中断，结果尚未确认；请核对当前任务状态。','Connection interrupted; its outcome is unconfirmed. Review the current task state.');
  return value?.message||t('接续未完成','Continuation incomplete');
 }
 async function request(name,payload={}){
  if(!state?.csrf)throw Error(t('请恢复本机服务后重试','Reconnect to the local service first'));
  const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name,...payload}),signal:AbortSignal.timeout(20000)});
  const value=await r.json();if(!r.ok||value.ok!==true)throw Error(value.error||t('接续未完成','Continuation incomplete'));return value;
 }
 async function navigate(mode='virtual'){
  if(window.WujiTeleoperation?.openMode){window.WujiTeleoperation.openMode(mode);return;}
  location.hash=mode==='real'?'#glove':'#virtual-glove';window.WujiWorkspace?.showPage();
 }
 function render(){
  if(!mount())return;const c=state?.calibrated_teleoperation;
  host.hidden=!c||!c.run_id||(!c.can_continue&&!c.busy&&!c.error&&!c.connected);
  const loaded=loadedSession();
  button.hidden=c?.busy===true&&!loaded;button.disabled=pending||!state||!(c?.can_continue===true||c?.connected===true||loaded);
  realButton.hidden=button.hidden;realButton.disabled=button.disabled;
  button.textContent=t('虚拟遥操作','Virtual teleoperation');realButton.textContent=t('遥操作','Teleoperation');
  cancel.hidden=c?.busy!==true||loaded;cancel.disabled=pending||!state||!c?.attempt_id;cancel.textContent=t('取消接续','Cancel continuation');
  const phases={validating:['正在接续本次标定…','Continuing this calibration…'],exporting:['正在沿用已保存的手型…','Loading the saved hand model…'],connecting:['正在打开手套预览…','Opening glove preview…'],cancelling:['正在结束接续…','Ending continuation…']};
  const fault=error||c?.error;
  note.textContent=fault?(window.WujiLocale?.lang==='en'?fault.split(' / ').at(-1):fault.split(' / ')[0]):(pending?t('正在加载已保存的标定…','Loading saved calibration…'):c?.busy?(phases[c.phase]?.[window.WujiLocale?.lang==='en'?1:0]||t('正在接续…','Continuing…')):'');note.hidden=!note.textContent;
  if((c?.connected===true||loaded)&&requestedRun===c.run_id){requestedRun=null;if(location.hash===entryHash)navigate(requestedMode);}
 }
 window.addEventListener('console-state',event=>{state=event.detail;render();});
 window.addEventListener('console-offline',()=>{state=null;requestedRun=null;render();});
 window.addEventListener('wuji-language',render);
})();
