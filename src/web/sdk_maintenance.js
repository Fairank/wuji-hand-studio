/* Official stable components. Software checks never connect a device. */
(()=>{'use strict';
 const q=new URLSearchParams(location.search);if(q.get('task_view')==='1'||q.get('embedded')==='1'||parent!==window)return;
 const host=document.getElementById('official-tools');if(!host)return;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const card=document.createElement('section');card.className='sdk-maintenance';card.id='sdk-maintenance';
 card.innerHTML='<div class="sdk-maintenance-heading"><h4></h4><span id="sdk-maintenance-status" role="status" aria-live="polite"></span></div><progress class="sdk-maintenance-progress" aria-labelledby="sdk-maintenance-status" hidden></progress><p class="sdk-maintenance-note"></p><div class="sdk-maintenance-actions"><button type="button" data-action="check"></button><button type="button" data-action="update" class="primary" hidden></button></div><p class="sdk-maintenance-error" role="alert" hidden></p><details><summary></summary><pre></pre></details>';
 host.querySelector('#ot-system')?.before(card);
 let state=null,fresh=false,pending='',loading=null,failure='',failureKind='',epoch=0;
 const title=card.querySelector('h4'),status=card.querySelector('[role=status]'),note=card.querySelector('.sdk-maintenance-note'),error=card.querySelector('[role=alert]'),check=card.querySelector('[data-action=check]'),update=card.querySelector('[data-action=update]'),details=card.querySelector('details');
 function render(){
  title.textContent=t('遥操作组件','Teleoperation components');title.tabIndex=-1;
  const phase=pending==='sdk_update_prepare'?'updating':pending==='sdk_update_recover'?'verifying':pending==='sdk_release_check'?'checking':state?.phase||'unchecked',busy=!!pending||fresh&&state?.busy===true;
  const labels={unchecked:['自动检查更新','Updates are checked automatically'],changed:['控制端已改变','Controller changed'],expired:['版本记录待刷新','Version record needs refreshing'],checking:['正在检查更新…','Checking for updates…'],verifying:['正在核对上次更新…','Verifying the previous update…'],updating:['正在更新组件…','Updating components…'],up_to_date:['已是最新稳定版','Latest stable version'],available:['有可用更新','Update available'],external:['请在外部控制端更新','Update on the external controller'],unknown:['更新状态待核对','Update status unconfirmed'],error:['','']};
  const issue=failure||state?.error||'';
  status.textContent=t(...(labels[phase]||labels.unchecked))+(phase==='up_to_date'&&state?.proof?.version?' · '+state.proof.version:'');status.hidden=!!issue&&!busy;
  card.setAttribute('aria-busy',String(busy));card.querySelector('progress').hidden=!busy;
  note.hidden=phase!=='external'||!!issue;note.textContent=t('在所选控制端更新官方 SDK，或切换到内置控制组件。','Update the official SDK on the selected controller, or select the built-in components.');
  check.textContent=!fresh||failure?t('重新读取','Reload status'):state?.recovery_required?t('核对上次更新','Verify previous update'):t('检查更新','Check updates');check.disabled=busy;check.parentElement.hidden=busy;
  update.textContent=t('更新组件','Update components');update.hidden=state?.can_update!==true||!fresh||busy||!!failure||state?.recovery_required===true;update.disabled=false;
  error.hidden=!issue||busy;error.dataset.kind=state?.recovery_required?'danger':'notice';error.textContent=issue?explain(issue):'';
  details.querySelector('summary').textContent=t('版本详情','Version details');
  const lines=[];if(state?.proof?.version)lines.push(t('控制端 SDK：','Controller SDK: ')+state.proof.version);if(state?.release?.version)lines.push(t('官方稳定版：','Official stable: ')+state.release.version);if(state?.release?.offline)lines.push(t('使用有效期内的官方版本记录','Using a recent official version record'));if(state?.release?.refresh_error?.detail)lines.push(t('本次官方资料刷新未完成：','The current catalog refresh did not complete: ')+state.release.refresh_error.detail);if(issue&&state?.proof)lines.push(t('以上为上次读取结果，本次检查尚未完成。','The versions above are from the last read; this check has not completed.'));if(issue)lines.push(issue);details.querySelector('pre').textContent=lines.join('\n');details.hidden=!lines.length;
 }
 function explain(issue){
  if(state?.recovery_required)return t('上次更新的退出状态未确认，请先核对组件。','The previous update has not been verified. Verify components before continuing.');
  if(/workbench service is offline|工作台服务已离线/i.test(issue))return t('工作台服务已离线，请重新读取。','Workbench service is offline. Reload the status.');
  if(/^\[SDK_CATALOG_(LOCAL|CACHE)\]/.test(issue))return t('版本检查遇到本地文件问题，请修复软件后重试。','The version check encountered a local file problem. Repair the software and retry.');
  if(/^\[SDK_CATALOG_CERTIFICATE\]/.test(issue))return t('无法验证更新连接，请检查系统时间与证书后重试。','The update connection could not be verified. Check the system clock and certificates, then retry.');
  if(/^\[SDK_CATALOG_TLS\]/.test(issue))return t('无法建立安全更新连接，请重试；具体原因已保留在版本详情。','A secure update connection could not be established. Retry; the cause is saved in Version details.');
  if(/^\[SDK_CATALOG_METADATA\]/.test(issue))return t('官方版本资料未通过校验，请稍后重试。','The official version data did not pass validation. Retry later.');
  if(/^\[SDK_CATALOG_HTTP\]/.test(issue))return t('官方版本服务暂不可用，请稍后重试。','The official version service is unavailable. Retry later.');
  if(/^\[SDK_CATALOG_CHECK\]/.test(issue))return t('版本检查未完成，请重试；具体原因已保留在版本详情。','The version check did not complete. Retry; the cause is saved in Version details.');
  if(/internet|offline|network|timed out|联网|网络/i.test(issue))return t('暂时无法检查更新，请联网后重试。','Cannot check for updates right now. Connect to the internet and try again.');
  if(/finish|idle|busy|workspace|session|先.*结束|正在运行|有设备任务|任务.*运行/i.test(issue))return t('设备任务正在运行，结束任务后再更新。','A device task is running. Finish the task before updating.');
  if(/stopped|not.ready|not.installed|probe failed|wsl|未安装|依赖|无法加载|组件.*修复/i.test(issue))return t('控制组件暂时无法读取，请启动或修复内置组件后重试。','The controller components cannot be read. Start or repair the built-in components, then try again.');
  if(/status is unavailable|Incomplete component status|无法读取组件状态|Failed to fetch/i.test(issue))return t('暂时无法读取组件状态，请重新读取。','Component status is unavailable. Reload the status.');
  return t('本次检查未完成，请重试；详细原因已保留在版本详情。','This check did not complete. Try again; the cause is saved in Version details.');
 }
 async function load({explicit=false}={}){
  if(pending)return;if(loading)return loading;
  const version=epoch;
  const request=(async()=>{try{const r=await fetch('/api/sdk-maintenance',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error(t('无法读取组件状态','Component status is unavailable'));const s=await r.json();if(!s||typeof s.busy!=='boolean'||typeof s.phase!=='string')throw Error('Incomplete component status');if(version!==epoch)return;state=s;fresh=true;if(explicit||failureKind!=='action'){failure='';failureKind='';}}catch(e){if(version===epoch){fresh=false;failure=e.message;failureKind='load';}}finally{if(loading===request)loading=null;render();}})();loading=request;return request;
 }
 async function act(name){
  if(pending||!fresh||state?.busy===true)return;
  if(name==='sdk_update_prepare'&&(state?.can_update!==true||state?.recovery_required===true))return;
  const version=++epoch;loading=null;pending=name;failure='';failureKind='';render();
  try{const r=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('Workbench state is unavailable');const s=await r.json();if(version!==epoch)return;if(!s.csrf)throw Error('Reload workbench state');const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name}),signal:AbortSignal.timeout(12000)}),body=await response.json();if(version!==epoch)return;if(!response.ok||body.ok===false)throw Error(body.error||'Component maintenance did not complete');if(!body.sdk_maintenance||typeof body.sdk_maintenance.busy!=='boolean'||typeof body.sdk_maintenance.phase!=='string')throw Error('Incomplete component status');state=body.sdk_maintenance;fresh=true;}catch(e){if(version===epoch){fresh=false;failure=e.message;failureKind='action';}}finally{if(version===epoch){pending='';render();if(fresh)load();}}
 }
 check.onclick=()=>fresh&&!failure?act(state?.recovery_required?'sdk_update_recover':'sdk_release_check'):load({explicit:true});update.onclick=()=>act('sdk_update_prepare');
 window.WujiSDKMaintenance=Object.freeze({open(){location.hash='#settings';window.WujiWorkspace?.showPage();document.getElementById('settings-tab-devices')?.click();title.focus({preventScroll:true});card.scrollIntoView({block:'start'});load();},refresh:load});
 window.addEventListener('wuji-language',render);
 window.addEventListener('console-offline',()=>{epoch++;loading=null;pending='';fresh=false;failure=t('工作台服务已离线，请重新读取。','Workbench service is offline. Reload the status.');failureKind='load';render();});
 setInterval(()=>{if(state?.busy||card.getClientRects().length&&!document.hidden)load();},2000);
 render();load();
})();
