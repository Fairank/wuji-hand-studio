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
 const releases=document.createElement('div');releases.className='ot-cli-release';
 releases.innerHTML='<div class="ot-cli-release-row"><span id="ot-cli-release-state" role="status"></span><div class="ot-cli-release-actions"><button id="ot-cli-check" type="button"></button><button id="ot-cli-update" type="button"></button><button id="ot-cli-rollback" type="button"></button></div></div><div id="ot-cli-auto-row" class="ot-cli-auto-row" hidden><div><label id="ot-cli-auto-label" for="ot-cli-auto"></label><p id="ot-cli-auto-note"></p></div><button id="ot-cli-auto" class="ot-cli-switch" type="button" role="switch" aria-checked="false" aria-labelledby="ot-cli-auto-label" aria-describedby="ot-cli-auto-note"><span></span></button></div><div id="ot-cli-progress-wrap" hidden><div class="ot-cli-progress-heading"><span id="ot-cli-progress-label"></span><span id="ot-cli-progress-detail"></span></div><progress id="ot-cli-progress" max="100" aria-labelledby="ot-cli-progress-label"></progress></div><p id="ot-cli-release-error" role="alert"></p><details><summary id="ot-cli-release-details-label"></summary><p id="ot-cli-release-details"></p></details>';
 card.querySelector('#ot-system').before(releases);host.prepend(card);
 // Daily version controls stay first. Environment repair keeps the same
 // controls and listeners in a disclosure rather than another dashboard.
 const environment=document.createElement('details');environment.id='ot-environment';environment.className='ot-environment';
 const environmentSummary=document.createElement('summary'),environmentTitle=document.createElement('span');environmentTitle.id='ot-environment-title';environmentSummary.append(environmentTitle,$('ot-state'));environment.append(environmentSummary);
 for(const selector of ['#ot-description','.ot-table','.ot-runtime','#ot-checked-at','#ot-status','#ot-error','.ot-actions'])environment.append(card.querySelector(selector));
 card.append(environment);
 let data=null,fresh=false,pending='',pollPromise=null,transportError='',actionError='',systemMessage='',lastRefresh=0,lastEnvironmentIssue='',environmentEpoch=0;
 let cliData=null,cliFresh=false,cliPending='',cliPromise=null,cliTransportError='',cliActionError='',cliLastRefresh=0,cliUnconfirmedPreference=null,cliEpoch=0;
 function version(value){return typeof value==='string'&&value.trim()?value:t('未检查','Not checked');}
 function timestamp(value){
  if(value===null||value===undefined)return t('尚未检查','Not checked yet');
  const date=new Date(typeof value==='number'&&value<1e12?value*1000:value);
  return Number.isFinite(date.getTime())?date.toLocaleString(window.WujiLocale?.lang==='en'?'en-GB':'zh-CN'):t('时间不可用','Time unavailable');
 }
 function render(){
  const r=data?.runtime||{},check=data?.check||{},target=data?.target||{},busy=!!pending||fresh&&(!!r.busy||!!check.busy);
  const installed=!!r.installed,mismatch=check.status==='ready'&&!check.compatible,selected=['wsl','macvm'].includes(window.WujiInstallation?.snapshot()?.bridge?.mode);
  const labels={'ot-title':['控制组件','Control components'],'ot-subtitle':['CLI 与遥操作 SDK','CLI and teleoperation SDK'],'ot-environment-title':['控制环境','Controller environment'],
   'ot-description':['内置环境用于设备连接、标定与遥操作。','The built-in environment supports connections, calibration and teleoperation.'],
   'ot-component-label':['组件','Component'],'ot-checked-label':['实际检查版本','Checked version'],'ot-target-label':['内置基线','Bundled baseline'],
   'ot-runtime-label':['内置控制组件','Built-in control components'],'ot-system-title':['Windows 系统支持','Windows system support'],
   'ot-system-note':['首次使用可能需要安装 Windows 系统组件。若系统要求重启，请重启后返回这里继续安装。','First use may require Windows components. If Windows requests a restart, return here afterwards to continue setup.'],
   'ot-system-install':['安装 Windows 系统组件','Install Windows components']};
  for(const [id,text] of Object.entries(labels))$(id).textContent=t(...text);
  $('ot-sdk').parentElement.hidden=true;
  $('ot-cli').textContent=version(check.cli_version);$('ot-sdk').textContent=version(check.sdk_version);
  $('ot-cli-target').textContent=target.cli_version||'—';$('ot-sdk-target').textContent=target.sdk_version||'—';
  $('ot-checked-at').textContent=t('检查时间：','Checked: ')+timestamp(check.checked_at);
  const phaseLabels={checking:['正在校验组件','Verifying components'],importing:['正在安装控制组件','Installing control components'],upgrading:['正在更新至配套版本','Updating to the bundled version'],verifying:['正在验证安装','Verifying installation']};
  $('ot-runtime-state').textContent=!fresh?t('状态待核对','Status unconfirmed'):r.busy?t(...(phaseLabels[r.stage]||['正在准备组件','Preparing components'])):r.ready?t('配套环境已安装','Bundled environment installed'):installed?t('已有环境，待更新','Environment installed; update required'):t('尚未安装','Not installed');
  $('ot-runtime-note').textContent=t('安装状态来自环境记录；上方版本只显示实际软件检查结果。','Installation status comes from environment records. Versions above come only from an actual software check.');
  const status=!fresh?t('状态待核对','Status unconfirmed'):pending==='control_tools_check'||check.busy?t('正在检查','Checking'):check.status==='ready'?(check.compatible?t('配套版本已确认','Bundled versions verified'):t('检查已完成','Check completed')):check.status==='failed'?t('检查未通过','Check failed'):t('尚未检查','Not checked');
  $('ot-state').textContent=status;card.dataset.status=!fresh?'unknown':pending==='control_tools_check'||check.busy?'checking':check.status||'unchecked';
  $('ot-state').hidden=fresh&&check.status==='ready'&&check.compatible===true&&!busy;
  $('ot-status').textContent=!fresh?(data?t('状态暂不可用，请重新读取。','Status unavailable. Reload to retry.'):t('正在读取组件状态。','Loading component status.')):check.busy?t('正在检查软件版本，不连接设备。','Checking software versions without connecting devices.'):check.status==='ready'&&!check.compatible?t('内置环境需要修复。','The built-in environment needs repair.'):'';
  $('ot-error').textContent=transportError||actionError||check.error||r.error||'';
  $('ot-check').textContent=!fresh?t('重新读取状态','Reload status'):check.busy||pending==='control_tools_check'?t('正在检查…','Checking…'):check.status==='failed'?t('重新检查内置组件','Retry component check'):t('检查内置组件','Check built-in components');
  $('ot-check').disabled=busy;
  $('ot-install').hidden=!r.supported||fresh&&r.ready&&!mismatch&&!r.busy;$('ot-install').textContent=r.busy?t('正在准备…','Preparing…'):r.ready&&!mismatch?t('配套组件已安装','Bundled components installed'):installed?t('更新至配套版本','Update to bundled version'):t('安装内置控制组件','Install built-in components');
  $('ot-install').disabled=!fresh||busy||!!r.ready&&!mismatch;
  $('ot-select').hidden=!r.supported||!r.ready||fresh&&selected;$('ot-select').textContent=selected?t('已使用内置组件','Built-in components selected'):t('使用内置组件','Use built-in components');$('ot-select').disabled=!fresh||busy||selected;
  const environmentIssue=!!pending||r.busy===true||check.busy===true||!!transportError||!!actionError||!!check.error||!!r.error||fresh&&(mismatch||r.supported&&!r.ready);
  const issueKey=environmentIssue?JSON.stringify([pending,r.busy,r.stage,check.busy,check.status,check.compatible,transportError,actionError,check.error,r.error,r.ready]):'';
  if(issueKey&&issueKey!==lastEnvironmentIssue)environment.open=true;
  lastEnvironmentIssue=issueKey;
  $('ot-system').hidden=!r.supported||r.mode==='macvm';$('ot-system-install').disabled=busy;$('ot-system-status').textContent=systemMessage;renderCli();
 }
 function renderCli(){
  const u=cliData?.update||{},current=cliData?.current||{},previous=cliData?.previous||{},busy=!!cliPending||!!pending||cliFresh&&(!!cliData?.busy||!!u.checking||!!u.preparing);
  const managed=u.update_method==='managed_native',phase=cliPending==='official_cli_check'?'checking':cliPending==='official_cli_update'?'checking':cliPending==='official_cli_rollback'?'verifying':u.phase;
  const bundledUpdate=cliFresh&&u.update_method==='workbench'&&u.newer_cli_available===true;
  const phaseLabels={checking:['正在检查更新','Checking for updates'],downloading:['正在下载更新','Downloading update'],verifying:['正在校验更新','Verifying update'],preparing:['正在准备更新','Preparing update'],waiting_idle:['设备使用中，空闲后更新','Waiting for devices to be idle'],ready:['更新已完成','Update complete']};
  let label=!cliFresh?t('版本状态暂不可用','Version status unavailable'):phaseLabels[phase]?t(...phaseLabels[phase]):u.checking?t('正在检查更新','Checking for updates'):u.preparing?t('正在准备更新','Preparing update'):u.error?t('更新未完成','Update incomplete'):u.available?t('可更新至 ','Update available: ')+(u.latest_version||'—'):u.checked_at!=null?t('检查已完成','Check completed'):t('尚未检查更新','Updates not checked');
  if(cliFresh&&phase!=='ready'&&u.checked_at!=null&&!busy&&!u.error&&!u.available&&u.latest_version&&u.latest_version===current.version)label=t('已是最新版本','Up to date');
  if(bundledUpdate&&!busy&&!u.error)label=t('随工作台更新 · ','Updates with workbench · ')+(u.latest_version||'—');
  $('ot-cli-release-state').textContent='CLI '+version(current.version)+' · '+label;
  $('ot-cli-check').textContent=!cliFresh?t('重新读取','Reload status'):phase==='checking'?t('正在检查…','Checking…'):t('检查更新','Check updates');$('ot-cli-update').textContent=busy&&phase!=='checking'?t('正在更新…','Updating…'):bundledUpdate?t('软件更新','Software update'):phase==='error'?t('重试更新','Retry update'):t('更新 CLI','Update CLI');$('ot-cli-rollback').textContent=t('切换至 ','Switch to ')+version(previous.version);
  $('ot-cli-check').disabled=busy;$('ot-cli-update').hidden=!bundledUpdate&&!u.available&&!u.preparing&&cliPending!=='official_cli_update';$('ot-cli-update').disabled=!cliFresh||busy||!bundledUpdate&&u.available!==true;$('ot-cli-rollback').hidden=!previous.version;$('ot-cli-rollback').disabled=!cliFresh||busy||!previous.version;
  $('ot-cli-auto-row').hidden=!managed;$('ot-cli-auto-label').textContent=t('自动更新 CLI','Update CLI automatically');$('ot-cli-auto-note').textContent=cliPending==='official_cli_auto_update'?t('正在保存…','Saving…'):t('使用官方稳定版，设备空闲时安装。','Install official stable releases when devices are idle.');
  $('ot-cli-auto').setAttribute('aria-checked',String(u.auto_update===true));$('ot-cli-auto').disabled=!cliFresh||!!cliPending;
  $('ot-cli-auto').setAttribute('aria-busy',String(cliPending==='official_cli_auto_update'));
  const failure=cliTransportError||cliActionError||(phase!=='waiting_idle'?u.error:'')||'';
  $('ot-cli-release-error').textContent=failure?(cliTransportError?t('暂时无法读取更新状态，请重新读取。','Update status is unavailable. Reload to retry.'):cliActionError?cliActionError:t('更新未完成，当前版本仍可使用。请重试或查看版本详情。','Update did not complete. The current version remains available. Retry or see version details.')):'';
  const progress=u.progress||{},showProgress=cliFresh&&['checking','downloading','verifying','preparing'].includes(phase),bar=$('ot-cli-progress'),received=progress.received_bytes,total=progress.total_bytes;
  $('ot-cli-progress-wrap').hidden=!showProgress;$('ot-cli-progress-label').textContent=phaseLabels[phase]?t(...phaseLabels[phase]):'';
  const known=phase==='downloading'&&Number.isFinite(received)&&Number.isFinite(total)&&total>0&&received>=0&&received<=total,mb=value=>(value/1048576).toFixed(1);
  if(known){const percent=Math.floor(received/total*100);bar.value=percent;$('ot-cli-progress-detail').textContent=percent+'% · '+mb(received)+' / '+mb(total)+' MB';}
  else{bar.removeAttribute('value');$('ot-cli-progress-detail').textContent=phase==='downloading'&&Number.isFinite(received)&&received>=0?mb(received)+' MB':'';}
  releases.dataset.phase=cliFresh?phase||'unchecked':'unknown';
  $('ot-cli-release-details-label').textContent=t('版本详情','Version details');
  $('ot-cli-release-details').textContent=[t('当前：','Current: ')+version(current.version)+(current.commit?' · '+String(current.commit).slice(0,12):''),t('最新：','Latest: ')+version(u.latest_version),t('上次检查：','Last checked: ')+timestamp(u.checked_at),t('上一版：','Previous: ')+version(previous.version),u.update_method==='workbench'?t('原版 CLI 随工作台软件更新，设备与标定共用同一版本。','The original CLI updates with the workbench. Discovery and calibration use the same version.'):u.auto_update===true?t('自动更新已开启','Automatic updates enabled'):t('自动更新未开启','Automatic updates not enabled'),u.error||''].filter(Boolean).join('\n');
 }
 async function loadCli({afterAction=false}={}){
  if(cliPending&&!afterAction)return;if(cliPromise)return cliPromise;const own=cliEpoch;
  const request=(async()=>{try{const response=await fetch('/api/official-cli',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!response.ok)throw Error(t('无法读取 CLI 版本状态。','CLI version status is unavailable.'));const next=await response.json();if(!next||!next.update||!next.current)throw Error(t('CLI 版本状态不完整。','CLI version status is incomplete.'));if(own!==cliEpoch)return;cliData=next;cliFresh=true;cliTransportError='';cliLastRefresh=Date.now();if(cliUnconfirmedPreference!==null&&next.update.auto_update===cliUnconfirmedPreference){cliUnconfirmedPreference=null;cliActionError='';}}catch(error){if(own===cliEpoch){cliFresh=false;cliTransportError=error.message;}}finally{if(cliPromise===request)cliPromise=null;renderCli();}})();cliPromise=request;return request;
 }
 async function cliAction(name,extra={}){
  if(cliPending||!cliFresh)return;const update=cliData?.update||{},busy=cliData?.busy===true||update.checking===true||update.preparing===true;
  if(name==='official_cli_auto_update'){if(update.update_method!=='managed_native'||typeof extra.enabled!=='boolean')return;}
  else if(pending||busy||name==='official_cli_update'&&update.available!==true||name==='official_cli_rollback'&&!cliData?.previous?.version)return;
  const own=++cliEpoch;cliPromise=null;if(name!=='official_cli_auto_update'){environmentEpoch++;pollPromise=null;}cliPending=name;cliActionError='';cliUnconfirmedPreference=null;let rejected=false;renderCli();
  try{const sr=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!sr.ok)throw Error(t('工作台服务未就绪','Workbench service is not ready'));const s=await sr.json();if(own!==cliEpoch)return;if(!s.csrf)throw Error(t('请重新读取工作台状态','Reload the workbench state'));const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name,...extra}),signal:AbortSignal.timeout(15000)}),body=await response.json();if(own!==cliEpoch)return;if(!response.ok||body.ok===false){rejected=true;throw Error(body.error||t('CLI 版本操作未完成','CLI version action did not complete'));}await loadCli({afterAction:true});if(own!==cliEpoch)return;if(name==='official_cli_auto_update'&&(!cliFresh||cliData.update.auto_update!==extra.enabled)){cliUnconfirmedPreference=extra.enabled;cliActionError=t('暂无法确认设置是否保存，请重新读取。','Unable to confirm whether the setting was saved. Reload status.');}if(name!=='official_cli_auto_update')await load();}
  catch(error){if(own!==cliEpoch)return;await loadCli({afterAction:true});if(own!==cliEpoch)return;const confirmed=name==='official_cli_auto_update'&&cliFresh&&cliData.update.auto_update===extra.enabled;if(name==='official_cli_auto_update'&&!confirmed&&!rejected)cliUnconfirmedPreference=extra.enabled;cliActionError=name==='official_cli_auto_update'?(confirmed?'':rejected?t('自动更新设置未保存，请重试。','Automatic update setting was not saved. Try again.'):t('暂无法确认设置是否保存，请重新读取。','Unable to confirm whether the setting was saved. Reload status.')):error.message;}finally{if(own===cliEpoch){cliPending='';renderCli();}}
 }
 async function load({afterAction=false}={}){
  if(pending&&!afterAction)return;if(pollPromise)return pollPromise;const own=environmentEpoch;
  const request=(async()=>{
   try{
    const response=await fetch('/api/control-tools',{cache:'no-store',signal:AbortSignal.timeout(6000)});
    if(!response.ok)throw Error(t('无法读取 CLI / SDK 状态，请重试。','CLI / SDK status is unavailable. Try again.'));
    const next=await response.json();if(!next||!next.runtime||!next.check||!next.target)throw Error(t('组件状态不完整，请重试。','Component status is incomplete. Try again.'));
    if(own!==environmentEpoch)return;data=next;fresh=true;transportError='';lastRefresh=Date.now();
   }catch(error){if(own===environmentEpoch){fresh=false;transportError=error.message||t('组件服务暂不可用','Component service unavailable');}}
   finally{if(pollPromise===request)pollPromise=null;render();}
  })();
  pollPromise=request;return request;
 }
 async function act(name){
  if(pending||!fresh)return;
  const own=++environmentEpoch;pollPromise=null;pending=name;actionError='';render();
  try{
   const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});
   if(!response.ok)throw Error(t('工作台服务尚未就绪','Workbench service is not ready'));
   const state=await response.json();if(own!==environmentEpoch)return;if(!state.csrf)throw Error(t('请重新读取工作台状态','Reload the workbench status'));
   const result=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name}),signal:AbortSignal.timeout(15000)});
   const body=await result.json();if(own!==environmentEpoch)return;if(!result.ok||body.ok===false)throw Error(body.error||t('操作未完成，请重试','Operation failed; try again'));
   await load({afterAction:true});if(own!==environmentEpoch)return;
   if(name!=='control_tools_check')await window.WujiInstallation?.refresh();
  }catch(error){if(own===environmentEpoch)actionError=error.message||t('操作未完成，请重试','Operation failed; try again');}
  finally{if(own===environmentEpoch){pending='';render();}}
 }
 function open(){location.hash='#settings';ws.showPage();$('settings-tab-devices')?.click();$('ot-title').focus({preventScroll:true});card.scrollIntoView({block:'start'});load();loadCli();}
 window.WujiOfficialTools={open,refresh:()=>Promise.all([load(),loadCli()])};
 $('ot-check').onclick=()=>fresh?act('control_tools_check'):load();
 $('ot-cli-check').onclick=()=>cliFresh?cliAction('official_cli_check'):loadCli();$('ot-cli-update').onclick=()=>{if(cliFresh&&cliData?.update?.update_method==='workbench'&&cliData.update.newer_cli_available===true){window.WujiWorkbenchUpdate?.open();return;}cliAction('official_cli_update');};$('ot-cli-rollback').onclick=()=>cliAction('official_cli_rollback');
 $('ot-cli-auto').onclick=()=>cliAction('official_cli_auto_update',{enabled:cliData?.update?.auto_update!==true});
 $('ot-install').onclick=()=>act('runtime_install');$('ot-select').onclick=()=>act('runtime_select');
 $('ot-system-install').onclick=async()=>{
  if(pending)return;const own=++environmentEpoch;pollPromise=null;pending='system';render();
  try{if(!window.pywebview?.api?.install_runtime_components)throw Error(t('请在 Windows 软件窗口中操作','Open the Windows application to use this action'));
   await window.pywebview.api.install_runtime_components();if(own!==environmentEpoch)return;systemMessage=t('已请求安装 Windows 组件。若系统要求重启，重启后返回这里继续。','Windows component setup requested. Return here after any required restart.');
  }catch(error){if(own===environmentEpoch)systemMessage=error.message;}finally{if(own===environmentEpoch){pending='';render();}}
 };
 window.addEventListener('installation-state',render);window.addEventListener('wuji-language',render);
 window.addEventListener('console-offline',()=>{environmentEpoch++;cliEpoch++;pollPromise=null;cliPromise=null;pending='';cliPending='';fresh=false;cliFresh=false;transportError=cliTransportError=t('工作台服务已离线，请重新读取。','Workbench service is offline. Reload the status.');render();});
 window.addEventListener('workspace-page',event=>{if(event.detail==='settings'&&Date.now()-lastRefresh>1000)load();});
 setInterval(()=>{const visible=card.getClientRects().length>0&&!document.hidden;if(data?.runtime?.busy||data?.check?.busy||(visible&&Date.now()-lastRefresh>6000))load();if(cliData?.busy||cliData?.update?.checking||cliData?.update?.preparing||(visible&&Date.now()-cliLastRefresh>6000))loadCli();},2000);
 render();load();loadCli();
})();
