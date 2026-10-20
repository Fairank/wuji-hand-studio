/* Software components only. Device sessions remain on the existing command paths. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),ws=window.WujiWorkspace;
 const host=$('settings-devices');if(!host||!ws)return;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const card=document.createElement('section');card.id='official-tools';card.className='wb-card official-tools';
 card.setAttribute('aria-labelledby','ot-title');
 card.innerHTML=`<header class="ot-heading"><div><h3 id="ot-title" tabindex="-1"></h3><p id="ot-subtitle"></p></div><span id="ot-state" class="ot-badge"></span></header>
 <p id="ot-description"></p><div class="ot-table" role="table" aria-labelledby="ot-title"><div class="ot-table-head" role="row"><span id="ot-component-label" role="columnheader"></span><span id="ot-checked-label" role="columnheader"></span><span id="ot-target-label" role="columnheader"></span></div>
 <div class="ot-component" role="row"><strong role="rowheader">CLI</strong><span id="ot-cli" role="cell"></span><span id="ot-cli-target" role="cell"></span></div>
 <div class="ot-component" role="row"><strong role="rowheader">SDK</strong><span id="ot-sdk" role="cell"></span><span id="ot-sdk-target" role="cell"></span></div></div>
 <div class="ot-runtime"><div><strong id="ot-runtime-label"></strong><span id="ot-runtime-state"></span></div><p id="ot-runtime-note"></p></div>
 <p id="ot-checked-at" class="ot-meta"></p><p id="ot-status" role="status" aria-live="polite"></p><p id="ot-error" role="alert"></p>
 <div class="ot-actions"><button id="ot-check" type="button" class="primary"></button><button id="ot-install" type="button"></button><button id="ot-select" type="button"></button></div>
 <details id="ot-system"><summary id="ot-system-title"></summary><p id="ot-system-note"></p><button id="ot-system-install" type="button"></button><p id="ot-system-status" role="status"></p></details>`;
 host.prepend(card);
 let data=null,fresh=false,pending='',pollPromise=null,transportError='',actionError='',systemMessage='',lastRefresh=0;
 function version(value){return typeof value==='string'&&value.trim()?value:t('未检查','Not checked');}
 function timestamp(value){
  if(value===null||value===undefined)return t('尚未检查','Not checked yet');
  const date=new Date(typeof value==='number'&&value<1e12?value*1000:value);
  return Number.isFinite(date.getTime())?date.toLocaleString(window.WujiLocale?.lang==='en'?'en-GB':'zh-CN'):t('时间不可用','Time unavailable');
 }
 function render(){
  const r=data?.runtime||{},check=data?.check||{},target=data?.target||{},busy=!!pending||!!r.busy||!!check.busy;
  const installed=!!r.installed,mismatch=check.status==='ready'&&!check.compatible,selected=['wsl','macvm'].includes(window.WujiInstallation?.snapshot()?.bridge?.mode);
  const labels={'ot-title':['官方 CLI / SDK','Official CLI / SDK'],'ot-subtitle':['内置控制组件','Built-in control components'],
   'ot-description':['用于工作台的设备连接、手套标定与遥操作。Windows 的内置 Linux 控制组件由工作台管理。','Used for device connections, glove calibration and teleoperation. Workbench manages the built-in Linux control components on Windows.'],
   'ot-component-label':['组件','Component'],'ot-checked-label':['实际检查版本','Checked version'],'ot-target-label':['配套目标版本','Bundled target'],
   'ot-runtime-label':['内置控制组件','Built-in control components'],'ot-system-title':['Windows 系统支持','Windows system support'],
   'ot-system-note':['首次使用可能需要安装 Windows 系统组件。若系统要求重启，请重启后返回这里继续安装。','First use may require Windows components. If Windows requests a restart, return here afterwards to continue setup.'],
   'ot-system-install':['安装 Windows 系统组件','Install Windows components']};
  for(const [id,text] of Object.entries(labels))$(id).textContent=t(...text);
  $('ot-cli').textContent=version(check.cli_version);$('ot-sdk').textContent=version(check.sdk_version);
  $('ot-cli-target').textContent=target.cli_version||'—';$('ot-sdk-target').textContent=target.sdk_version||'—';
  $('ot-checked-at').textContent=t('检查时间：','Checked: ')+timestamp(check.checked_at);
  const phaseLabels={checking:['正在校验组件','Verifying components'],importing:['正在安装控制组件','Installing control components'],upgrading:['正在更新至配套版本','Updating to the bundled version'],verifying:['正在验证安装','Verifying installation']};
  $('ot-runtime-state').textContent=!fresh?t('状态待核对','Status unconfirmed'):r.busy?t(...(phaseLabels[r.stage]||['正在准备组件','Preparing components'])):r.ready?t('配套环境已安装','Bundled environment installed'):installed?t('已有环境，待更新','Environment installed; update required'):t('尚未安装','Not installed');
  $('ot-runtime-note').textContent=t('安装状态来自环境记录；上方版本只显示实际软件检查结果。','Installation status comes from environment records. Versions above come only from an actual software check.');
  const status=!fresh?t('状态待核对','Status unconfirmed'):check.busy?t('正在检查','Checking'):check.status==='ready'?(check.compatible?t('配套版本已确认','Bundled versions verified'):t('检查已完成','Check completed')):check.status==='failed'?t('检查未通过','Check failed'):t('尚未检查','Not checked');
  $('ot-state').textContent=status;card.dataset.status=!fresh?'unknown':check.busy?'checking':check.status||'unchecked';
  $('ot-status').textContent=!fresh?(data?t('当前状态读取失败，保留上次检查结果。重新读取后可重试。','Current status is unavailable. Previous check results remain visible; reload to retry.'):t('正在读取组件状态。','Loading component status.')):check.busy?t('只检查软件版本与导入，不连接设备。','Checking software versions and imports only; no devices are connected.'):check.status==='ready'&&!check.compatible?t('实际版本与配套目标不同。更新至配套版本后可重新检查。','Checked versions differ from the bundled target. Update to the bundled version, then check again.'):t('安装、检查和选用控制组件均不会启动机械手。','Installing, checking and selecting control components do not start hand motion.');
  $('ot-error').textContent=transportError||actionError||check.error||r.error||'';
  $('ot-check').textContent=!fresh?t('重新读取状态','Reload status'):check.busy||pending==='control_tools_check'?t('正在检查…','Checking…'):check.status==='failed'?t('重新检查 CLI / SDK','Retry CLI / SDK check'):t('检查 CLI / SDK','Check CLI / SDK');
  $('ot-check').disabled=busy;
  $('ot-install').hidden=!r.supported;$('ot-install').textContent=r.busy?t('正在准备…','Preparing…'):r.ready&&!mismatch?t('配套组件已安装','Bundled components installed'):installed?t('更新至配套版本','Update to bundled version'):t('安装内置控制组件','Install built-in components');
  $('ot-install').disabled=!fresh||busy||!!r.ready&&!mismatch;
  $('ot-select').hidden=!r.supported||!r.ready;$('ot-select').textContent=selected?t('已使用内置组件','Built-in components selected'):t('使用内置组件','Use built-in components');$('ot-select').disabled=!fresh||busy||selected;
  $('ot-system').hidden=!r.supported||r.mode==='macvm';$('ot-system-install').disabled=busy;$('ot-system-status').textContent=systemMessage;
 }
 async function load(){
  if(pollPromise)return pollPromise;
  pollPromise=(async()=>{
   try{
    const response=await fetch('/api/control-tools',{cache:'no-store',signal:AbortSignal.timeout(6000)});
    if(!response.ok)throw Error(t('无法读取 CLI / SDK 状态，请重试。','CLI / SDK status is unavailable. Try again.'));
    const next=await response.json();if(!next||!next.runtime||!next.check||!next.target)throw Error(t('组件状态不完整，请重试。','Component status is incomplete. Try again.'));
    data=next;fresh=true;transportError='';lastRefresh=Date.now();
   }catch(error){fresh=false;transportError=error.message||t('组件服务暂不可用','Component service unavailable');}
   finally{pollPromise=null;render();}
  })();
  return pollPromise;
 }
 async function act(name){
  if(pending)return;
  pending=name;actionError='';render();
  try{
   const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});
   if(!response.ok)throw Error(t('工作台服务尚未就绪','Workbench service is not ready'));
   const state=await response.json();if(!state.csrf)throw Error(t('请重新读取工作台状态','Reload the workbench status'));
   const result=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name}),signal:AbortSignal.timeout(15000)});
   const body=await result.json();if(!result.ok||body.ok===false)throw Error(body.error||t('操作未完成，请重试','Operation failed; try again'));
   await load();
   if(name!=='control_tools_check')await window.WujiInstallation?.refresh();
  }catch(error){actionError=error.message||t('操作未完成，请重试','Operation failed; try again');}
  finally{pending='';render();}
 }
 function open(){location.hash='#settings';ws.showPage();$('settings-tab-devices')?.click();$('ot-title').focus({preventScroll:true});card.scrollIntoView({block:'start'});load();}
 window.WujiOfficialTools={open};
 $('ot-check').onclick=()=>fresh?act('control_tools_check'):load();
 $('ot-install').onclick=()=>act('runtime_install');$('ot-select').onclick=()=>act('runtime_select');
 $('ot-system-install').onclick=async()=>{
  if(pending)return;pending='system';render();
  try{if(!window.pywebview?.api?.install_runtime_components)throw Error(t('请在 Windows 软件窗口中操作','Open the Windows application to use this action'));
   await window.pywebview.api.install_runtime_components();systemMessage=t('已请求安装 Windows 组件。若系统要求重启，重启后返回这里继续。','Windows component setup requested. Return here after any required restart.');
  }catch(error){systemMessage=error.message;}finally{pending='';render();}
 };
 window.addEventListener('installation-state',render);window.addEventListener('wuji-language',render);
 window.addEventListener('workspace-page',event=>{if(event.detail==='settings'&&Date.now()-lastRefresh>1000)load();});
 setInterval(()=>{if(data?.runtime?.busy||data?.check?.busy||(!card.closest('.workspace-page')?.hidden&&!card.parentElement.hidden&&Date.now()-lastRefresh>6000))load();},2000);
 render();load();
})();
