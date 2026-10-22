/* Continue the verified current official result; never infer a device or user. */
(()=>{
 'use strict';
 let state=null,pending=false,requestedRun=null,error='',host=null,button=null,cancel=null,note=null;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 function mount(){
  const result=document.querySelector('.oc-result');if(!result)return false;
  if(host?.isConnected)return true;
  host=document.createElement('div');host.className='calibration-continuation';
  button=document.createElement('button');button.type='button';button.className='primary';
  cancel=document.createElement('button');cancel.type='button';
  note=document.createElement('p');note.setAttribute('role','status');
  host.append(button,cancel,note);result.append(host);
  button.onclick=async()=>{
   const context=state?.calibrated_teleoperation;if(!context||pending)return;
   if(context.connected===true){navigate();return;}
   if(context.can_continue!==true||!context.run_id||!context.expected_revision)return;
   pending=true;requestedRun=context.run_id;error='';render();
   try{acceptReply(await request('calibrated_teleoperation_continue',{run_id:context.run_id,expected_revision:context.expected_revision}),context.run_id);}
   catch(e){error=requestError(e);requestedRun=null;}finally{pending=false;render();}
  };
  cancel.onclick=async()=>{if(pending)return;const runId=state?.calibrated_teleoperation?.run_id;pending=true;requestedRun=null;error='';render();try{acceptReply(await request('calibrated_teleoperation_cancel'),runId);}catch(e){error=requestError(e);}finally{pending=false;render();}};
  return true;
 }
 function acceptReply(value,runId){
  const next=value?.calibrated_teleoperation;
  if(state?.calibrated_teleoperation?.run_id===runId&&next?.run_id===runId)state={...state,calibrated_teleoperation:next};
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
 async function navigate(){
  if(window.TaskWorkspace){await window.TaskWorkspace.refresh();const task=window.TaskWorkspace.snapshot().tasks.find(row=>row.devices?.glove===state?.calibrated_teleoperation?.serial);if(task){window.TaskWorkspace.select(task.task_id);return;}}
  location.hash='#glove';window.WujiWorkspace?.showPage();
 }
 function render(){
  if(!mount())return;const c=state?.calibrated_teleoperation;
  host.hidden=!c||!c.run_id||(!c.can_continue&&!c.busy&&!c.error&&!c.connected);
  button.hidden=c?.busy===true;button.disabled=pending||!state||!(c?.can_continue===true||c?.connected===true);
  button.textContent=c?.connected?t('进入遥操作','Open teleoperation'):t('继续使用这只手套','Continue with this glove');
  cancel.hidden=c?.busy!==true;cancel.disabled=pending||!state;cancel.textContent=t('取消接续','Cancel continuation');
  const phases={validating:['正在接续本次标定…','Continuing this calibration…'],exporting:['正在沿用已保存的手型…','Loading the saved hand model…'],connecting:['正在打开手套预览…','Opening glove preview…'],cancelling:['正在结束接续…','Ending continuation…']};
  note.textContent=error||c?.error||(pending?t('正在提交请求，等待工作台确认…','Submitting the request; waiting for the workbench…'):c?.busy?(phases[c.phase]?.[window.WujiLocale?.lang==='en'?1:0]||t('正在接续…','Continuing…')):'');note.hidden=!note.textContent;
  if(c?.connected===true&&requestedRun===c.run_id){requestedRun=null;navigate();}
 }
 window.addEventListener('console-state',event=>{state=event.detail;render();});
 window.addEventListener('console-offline',()=>{state=null;requestedRun=null;render();});
 window.addEventListener('wuji-language',render);
})();
