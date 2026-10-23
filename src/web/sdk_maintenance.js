/* Official stable components. Software checks never connect a device. */
(()=>{'use strict';
 const q=new URLSearchParams(location.search);if(q.get('task_view')==='1'||q.get('embedded')==='1'||parent!==window)return;
 const host=document.getElementById('official-tools');if(!host)return;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const card=document.createElement('section');card.className='sdk-maintenance';card.id='sdk-maintenance';
 card.innerHTML='<div class="sdk-maintenance-heading"><h4></h4><span role="status" aria-live="polite"></span></div><p class="sdk-maintenance-note"></p><div class="sdk-maintenance-actions"><button type="button" data-action="check"></button><button type="button" data-action="update" class="primary" hidden></button></div><p class="sdk-maintenance-error" role="alert" hidden></p><details><summary></summary><pre></pre></details>';
 host.querySelector('#ot-system')?.before(card);
 let state=null,fresh=false,pending=false,loading=null,failure='',epoch=0;
 const title=card.querySelector('h4'),status=card.querySelector('[role=status]'),note=card.querySelector('.sdk-maintenance-note'),error=card.querySelector('[role=alert]'),check=card.querySelector('[data-action=check]'),update=card.querySelector('[data-action=update]'),details=card.querySelector('details');
 function render(){
  title.textContent=t('遥操作组件','Teleoperation components');title.tabIndex=-1;
  const phase=state?.phase||'unchecked',busy=pending||state?.busy===true;
  const labels={unchecked:['启动遥操作时自动核对','Verified when starting teleoperation'],changed:['控制端已改变，请重新检查','Controller changed; check again'],checking:['正在核对…','Checking…'],verifying:['正在核验…','Verifying…'],updating:['正在更新…','Updating…'],up_to_date:['已使用官方稳定版','Official stable version verified'],available:['有可用更新','Update available'],external:['控制端需要更新','Controller update required'],unknown:['更新状态待核对','Update status unconfirmed'],error:['检查未完成','Check incomplete']};
  status.textContent=t(...(labels[phase]||labels.unchecked));
  note.textContent=phase==='external'?t('请在对应控制端更新 SDK，或使用内置控制组件。','Update the SDK on the selected controller, or use the built-in components.'):t('使用官方公开稳定版，在空闲时更新。','Uses the official public stable release. Updates run while devices are idle.');
  check.textContent=!fresh?t('重新读取','Reload status'):busy?t('处理中…','Working…'):state?.recovery_required?t('核对组件','Verify components'):t('检查更新','Check updates');check.disabled=busy;
  update.textContent=t('更新组件','Update components');update.hidden=state?.can_update!==true;update.disabled=!fresh||busy||state?.recovery_required===true;
  const issue=failure||state?.error||'';error.hidden=!issue;error.textContent=issue?t('操作未完成，请查看版本详情。','The operation did not complete. See version details.'):'';
  details.querySelector('summary').textContent=t('版本详情','Version details');
  const lines=[];if(state?.proof?.version)lines.push(t('控制端 SDK：','Controller SDK: ')+state.proof.version);if(state?.release?.version)lines.push(t('官方稳定版：','Official stable: ')+state.release.version);if(state?.release?.offline)lines.push(t('使用有效期内的官方版本记录','Using a recent official version record'));if(issue)lines.push(issue);details.querySelector('pre').textContent=lines.join('\n');details.hidden=!lines.length;
 }
 async function load(){
  if(loading)return loading;
  const version=epoch;
  loading=(async()=>{try{const r=await fetch('/api/sdk-maintenance',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error(t('无法读取组件状态','Component status is unavailable'));const s=await r.json();if(!s||typeof s.busy!=='boolean'||typeof s.phase!=='string')throw Error('Incomplete component status');if(version!==epoch)return;state=s;fresh=true;failure='';}catch(e){if(version===epoch){fresh=false;failure=e.message;}}finally{loading=null;render();}})();return loading;
 }
 async function act(name){
  if(pending)return;pending=true;epoch++;failure='';render();
  try{const r=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('Workbench state is unavailable');const s=await r.json();if(!s.csrf)throw Error('Reload workbench state');const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name}),signal:AbortSignal.timeout(12000)}),body=await response.json();if(!response.ok||body.ok===false)throw Error(body.error||'Component maintenance did not complete');state=body.sdk_maintenance;fresh=true;}catch(e){fresh=false;failure=e.message;}finally{pending=false;render();if(fresh)load();}
 }
 check.onclick=()=>fresh?act(state?.recovery_required?'sdk_update_recover':'sdk_release_check'):load();update.onclick=()=>act('sdk_update_prepare');
 window.WujiSDKMaintenance=Object.freeze({open(){location.hash='#settings';window.WujiWorkspace?.showPage();document.getElementById('settings-tab-devices')?.click();title.focus({preventScroll:true});card.scrollIntoView({block:'start'});load();},refresh:load});
 window.addEventListener('wuji-language',render);
 setInterval(()=>{if(state?.busy||card.getClientRects().length&&!document.hidden)load();},2000);
 render();load();
})();
