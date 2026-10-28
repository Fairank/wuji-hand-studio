/* Application update controls only. The backend owns downloads and installation. */
(()=>{
 'use strict';
 const host=document.getElementById('settings-tools');if(!host||document.getElementById('workbench-update'))return;
 const source='Fairank/wuji-hand-studio',phases=new Set(['idle','checking','available','up_to_date','unpublished','downloading','ready','applying','completed','error']);
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const card=document.createElement('section');card.id='workbench-update';card.className='wb-card workbench-update';card.setAttribute('aria-labelledby','wbu-title');
 card.innerHTML='<header class="wbu-heading"><div><h3 id="wbu-title" tabindex="-1"></h3><p id="wbu-description"></p></div><span id="wbu-state" role="status"></span></header><div class="wbu-versions"><div><span id="wbu-current-label"></span><strong id="wbu-current"></strong></div><div id="wbu-latest-row"><span id="wbu-latest-label"></span><strong id="wbu-latest"></strong></div></div><p id="wbu-completed" role="status" hidden></p><p id="wbu-message" role="status" aria-live="polite"></p><p id="wbu-error" role="alert"></p><div id="wbu-progress-wrap" hidden><div class="wbu-progress-heading"><span id="wbu-progress-label"></span><span id="wbu-progress-detail"></span></div><progress id="wbu-progress" max="100" aria-labelledby="wbu-progress-label"></progress></div><div class="wbu-actions"><button id="wbu-check" type="button"></button><button id="wbu-prepare" type="button" class="primary"></button><button id="wbu-apply" type="button" class="primary"></button></div><p id="wbu-checked-at" class="wbu-meta"></p>';
 host.prepend(card);const updateEntry=window.parent===window&&!new URLSearchParams(location.search).has('task_view')?document.createElement('button'):null;if(updateEntry){updateEntry.id='desktop-software-update';updateEntry.type='button';updateEntry.className='desktop-menu-row';updateEntry.onclick=open;document.getElementById('desktop-settings')?.after(updateEntry);}const $=id=>card.querySelector('#'+id);
 let data=null,fresh=false,loading=null,pending='',transportError='',actionError='',actionErrorStage='',lastAttempt=0,revision=0,destroyed=false,wasVisible=false,sheet=null,applyUnconfirmed=false;
 const activePhases=new Set(['checking','downloading','applying']);
 const visible=()=>!document.hidden&&card.getClientRects().length>0;
 const text=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value;};
 const version=value=>typeof value==='string'&&/^\d+\.\d+\.\d+$/.test(value);
 function newer(left,right){if(!version(left)||!version(right))return false;const a=left.split('.').map(Number),b=right.split('.').map(Number);for(let i=0;i<3;i++){if(a[i]!==b[i])return a[i]>b[i];}return false;}
 function valid(value){return value&&typeof value==='object'&&!Array.isArray(value)&&value.source===source&&typeof value.current_version==='string'&&value.current_version.length>0&&value.current_version.length<81&&phases.has(value.phase)&&typeof value.busy==='boolean'&&typeof value.available==='boolean'&&typeof value.supported==='boolean'&&typeof value.can_apply==='boolean'&&(value.latest_version==null||typeof value.latest_version==='string'&&value.latest_version.length<81)&&(value.prepared==null||typeof value.prepared==='object'&&!Array.isArray(value.prepared)&&version(value.prepared.version))&&(value.error==null||typeof value.error==='string');}
 function accept(value){if(!valid(value))throw Error('Incomplete software update state');data=value;fresh=true;transportError='';actionError='';applyUnconfirmed=false;}
 function userError(error,state={}){
  const value=String(error||'').toLowerCase();
  if(state.cancelled===true||/cancelled|canceled|取消/.test(value))return t('本次更新已取消。需要时可重新开始。','This update was cancelled. You can start again when ready.');
  if(/rate.?limit|too many requests|(?:http(?: error)?|status)\s*[:=]?\s*(?:403|429)\b|限流/.test(value))return t('更新服务暂时限制请求，请稍后重试。','The update service is limiting requests. Please try again later.');
  if(/sha.?256|digest|checksum|integrity/.test(value))return t('安装包未通过完整性校验，请重新下载。','The installer did not pass integrity verification. Download it again.');
  if(/truncat|extra bytes|size limit|content-length|response size/.test(value))return t('安装包下载不完整，请重新下载。','The installer download is incomplete. Download it again.');
  if(/session|idle|device|设备|会话/.test(value))return t('请先结束设备、标定和诊断会话，再重试。','Finish device, calibration and diagnostic sessions, then retry.');
  if((state.error_stage==='check'||/metadata/.test(value))&&/timed out|timeout|\bstall(?:ed|ing)?\b/.test(value))return t('检查版本时等待响应超时，请重新检查。','The version check timed out while waiting for a response. Check again.');
  if(/preparation timed out|download.*(?:timed out|timeout|deadline|time limit)|(?:deadline|time limit).*download/.test(value))return t('下载用时超过限制，已停止。可重新下载安装包。','The download exceeded its time limit and stopped. Download the installer again.');
  if(/\bstall(?:ed|ing)?\b|no (?:download )?progress|无进展/.test(value))return t('下载长时间没有收到新数据，已停止。请重试。','The download stopped because no new data arrived. Please retry.');
  if(state.error_stage==='install'||/updat(?:e|er) helper|installer (?:failed|exit)|安装助手/.test(value))return t('安装未完成，当前版本仍可使用。请重新检查后重试安装。','Installation did not finish. Your current version remains usable. Check again and retry installation.');
  if(state.error_stage==='download'&&/timeout|timed out/.test(value))return t('下载等待数据超时，已停止。可重新下载安装包。','The download stopped while waiting for data. Download the installer again.');
  if(/network|dns|http|urlopen|connect|timeout|timed out|fetch|abort|网络/.test(value))return t('暂时无法连接更新服务，请稍后重试。','The update service is unavailable. Try again shortly.');
  if(/permission|filesystem|directory|access|disk|磁盘|权限/.test(value))return t('暂时无法保存安装包，请检查本机存储后重试。','The installer could not be saved. Check local storage and retry.');
  return t('软件更新未完成，请重试。','The software update did not complete. Try again.');
 }
 function checkedAt(value){if(value==null)return '';const date=new Date(typeof value==='number'&&value<1e12?value*1000:value);return Number.isFinite(date.getTime())?date.toLocaleString(window.WujiLocale?.lang==='en'?'en-GB':'zh-CN'):'';}
 function progress(s,phase){
  const p=s.progress||{},bytes=Number.isSafeInteger(p.received_bytes)&&p.received_bytes>=0?p.received_bytes:null,total=Number.isSafeInteger(p.total_bytes)&&p.total_bytes>0?p.total_bytes:null;
  const failed=phase==='error'&&['downloading','verifying'].includes(p.stage)&&bytes!==null&&bytes>0;
  const downloading=phase==='downloading'&&p.stage!=='verifying',determinate=(downloading||failed)&&bytes!==null&&total!==null&&bytes<=total;
  const active=activePhases.has(phase),bar=$('wbu-progress');$('wbu-progress-wrap').hidden=!active&&!failed;bar.hidden=failed&&!determinate;
  const label=failed?s.cancelled===true?t('下载已取消','Download cancelled'):p.stage==='verifying'?t('安装包校验未通过','Installer verification failed'):t('下载已中断','Download interrupted'):phase==='checking'?t('正在检查新版本','Checking for updates'):phase==='applying'?t('正在重启并安装','Restarting to install'):p.stage==='verifying'?t('正在校验安装包','Verifying installer'):t('正在下载安装包','Downloading installer');
  text('wbu-progress-label',label);text('wbu-progress-detail',determinate?Math.floor(bytes/total*100)+'% · '+(bytes/1048576).toFixed(1)+' / '+(total/1048576).toFixed(1)+' MB':(downloading||failed)&&bytes!==null&&bytes>0?(bytes/1048576).toFixed(1)+' MB':'');
  if(determinate){bar.value=bytes/total*100;bar.setAttribute('aria-valuetext',Math.floor(bytes/total*100)+'%');}else{bar.removeAttribute('value');bar.removeAttribute('aria-valuetext');}
 }
 function render(){
  if(destroyed)return;const s=data||{},phase=pending==='workbench_update_check'?'checking':pending==='workbench_update_prepare'?'downloading':pending==='workbench_update_apply'?'applying':applyUnconfirmed?'applying':fresh?s.phase:s.phase==='applying'?'applying':'unknown',busy=!!pending||activePhases.has(phase)||fresh&&s.busy;
  const prepared=s.prepared&&newer(s.prepared.version,s.current_version)&&(!s.latest_version||s.prepared.version===s.latest_version),ready=phase==='ready'&&prepared;
  const labels={idle:['尚未检查','Not checked'],checking:['正在检查','Checking'],available:['发现新版本','Update available'],up_to_date:['已是最新版本','Up to date'],unpublished:['尚无正式版更新','No stable release'],downloading:['正在下载','Downloading'],ready:['安装包已就绪','Installer ready'],applying:['正在准备安装','Preparing installation'],completed:['更新已完成','Update complete'],error:['需要重试','Retry needed'],unknown:['状态待核对','Status unverified']};
  if(updateEntry)updateEntry.textContent=t('软件更新','Software update');if(sheet)sheet.setLabels({title:t('软件更新','Software update'),closeLabel:t('关闭','Close')});text('wbu-title',t('软件更新','Software update'));text('wbu-description',t('一并更新工作台、配套 CLI 与界面组件，保留你的用户资料。','Update the workbench, bundled CLI and interface components together. Your user data is kept.'));
  text('wbu-state',applyUnconfirmed?t('正在核对安装状态','Verifying installation status'):t(...labels[phase]));card.dataset.phase=phase;card.dataset.busy=String(!!busy);card.setAttribute('aria-busy',String(!!busy));
  text('wbu-current-label',t('当前版本','Current version'));text('wbu-current',s.current_version||'—');text('wbu-latest-label',t('可用版本','Available version'));text('wbu-latest',s.latest_version||'—');$('wbu-latest-row').hidden=!s.latest_version||phase==='unpublished';
  let message=applyUnconfirmed?t('安装请求的结果正在核对。请等待工作台重新打开；不会重复启动安装。','Verifying the installation request. Wait for the workbench to reopen; installation will not be sent again.'):phase==='applying'?t('正在重启并安装更新，安装窗口将显示后续进度。完成后工作台会重新打开。','Restarting to install. The installer window will show progress, then reopen the workbench.'):phase==='completed'?t('已更新到 ','Updated to ')+s.current_version:!fresh&&!pending?t('正在读取更新状态。','Reading update status.'):phase==='unpublished'?t('尚无正式版安装包。正式发布后可在这里更新。','No stable installer is available yet. Updates will appear here after a stable release.'):phase==='checking'?t('正在检查已发布的软件版本。','Checking published software versions.'):phase==='downloading'?t('正在准备安装包；下载完成后，由你决定何时重启安装。','Preparing the installer. You choose when to restart and install after the download finishes.'):phase==='applying'?t('正在准备重启并安装更新。','Preparing to restart and install the update.'):ready&&!s.can_apply?t('安装包已就绪。请先结束设备、标定和诊断会话，再重启安装。','The installer is ready. Finish device, calibration and diagnostic sessions before restarting.'):ready?t('安装包已就绪，可重启工作台完成安装。','The installer is ready. Restart the workbench to finish installation.'):phase==='available'?t('新版本已发布。点击“下载安装包”后才会开始下载。','A new version is published. Download begins only when you choose Download installer.'):phase==='up_to_date'?t('当前软件已是最新发布版本。','You have the latest published software version.'):t('检查已发布版本，再按需下载安装。','Check published versions, then choose whether to download and install.');
  if(phase==='error')message=t('上次更新未完成，可以重新检查或下载。','The previous update did not complete. Check again or retry the download.');
  if(fresh&&s.supported===false)message=t('此环境可检查版本；请在 Windows 安装版中下载并安装更新。','You can check versions here. Download and install updates in the installed Windows app.');
  if(!fresh&&transportError&&phase!=='applying')message=t('当前更新状态无法核对，恢复连接后可继续。','Update status cannot be verified. Continue when the connection recovers.');
  const completed=fresh&&phase==='up_to_date'&&!s.available&&!s.error&&!newer(s.latest_version,s.current_version)&&version(s.completed_version)&&s.completed_version===s.current_version;
  $('wbu-completed').hidden=!completed;text('wbu-completed',completed?t('更新完成 · ','Update complete · ')+s.current_version:'');text('wbu-message',message);text('wbu-error',phase==='applying'&&!fresh?'':transportError?userError(transportError):actionError?userError(actionError,{error_stage:actionErrorStage}):fresh&&s.error?userError(s.error,s):'');progress(pending?{}:s,phase);
  text('wbu-check',!fresh?t('重新读取状态','Reload status'):phase==='checking'||pending==='workbench_update_check'?t('正在检查…','Checking…'):t('检查更新','Check updates'));
  text('wbu-prepare',phase==='downloading'||pending==='workbench_update_prepare'?t('正在下载…','Downloading…'):t('下载安装包','Download installer'));
  text('wbu-apply',phase==='applying'||pending==='workbench_update_apply'?t('正在准备安装…','Preparing installation…'):t('重启并安装','Restart and install'));
  $('wbu-check').disabled=!!busy;$('wbu-prepare').hidden=phase==='ready'||phase==='applying'||!s.available&&phase!=='downloading';$('wbu-prepare').disabled=!fresh||!!busy||s.supported!==true||s.available!==true||phase==='ready'||phase==='applying';
  $('wbu-apply').hidden=!ready&&phase!=='applying';$('wbu-apply').disabled=!fresh||!!busy||s.supported!==true||!ready||s.can_apply!==true;
  const stamp=checkedAt(s.checked_at);text('wbu-checked-at',stamp?t('上次检查：','Last checked: ')+stamp:t('尚未检查已发布版本','Published versions not checked yet'));
 }
 async function refresh(){
  if(destroyed||pending)return;if(loading)return loading;const own=revision;lastAttempt=Date.now();
  loading=(async()=>{try{const response=await fetch('/api/workbench-update',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!response.ok)throw Error('Update status HTTP '+response.status);const next=await response.json();if(own===revision)accept(next);}
   catch(error){if(own===revision){fresh=false;transportError=error.message||'Update state unavailable';}}
   finally{loading=null;render();}})();return loading;
 }
 async function act(name){
  if(pending||!fresh)return;
  if(name==='workbench_update_prepare'&&($('wbu-prepare').hidden||$('wbu-prepare').disabled))return;
  if(name==='workbench_update_apply'&&($('wbu-apply').hidden||$('wbu-apply').disabled))return;
  if(name==='workbench_update_check'&&$('wbu-check').disabled)return;
  revision++;pending=name;actionError='';actionErrorStage=name==='workbench_update_apply'?'install':name==='workbench_update_prepare'?'download':'check';render();lastAttempt=Date.now();let dispatched=false,rejected=false;
  try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error('Workbench connection unavailable');const state=await response.json();if(typeof state.csrf!=='string'||!state.csrf)throw Error('Workbench connection unavailable');
   dispatched=true;const sent=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name}),signal:AbortSignal.timeout(15000)});rejected=!sent.ok;const body=await sent.json();
   if(!sent.ok||body.ok!==true){rejected=true;throw Error(body.error||'Software update action failed');}accept(body.workbench_update);
  }catch(error){fresh=false;applyUnconfirmed=name==='workbench_update_apply'&&dispatched&&!rejected;actionError=applyUnconfirmed?'':error.message||'Software update action failed';}
  finally{pending='';render();}
 }
 $('wbu-check').onclick=()=>fresh?act('workbench_update_check'):refresh();$('wbu-prepare').onclick=()=>act('workbench_update_prepare');$('wbu-apply').onclick=()=>act('workbench_update_apply');
 function open(){
  if(destroyed)return;
  if(!sheet){
   if(!window.HandWorkbenchSheet)return;
   sheet=window.HandWorkbenchSheet.create({id:'workbench-update-sheet',title:t('软件更新','Software update')});sheet.element.classList.add('wbu-sheet');
   sheet.element.addEventListener('close',()=>{if(!sheet.element.open&&!destroyed){host.prepend(card);syncVisibility();}});
  }
  document.getElementById('desktop-menu')?.close();sheet.body.append(card);sheet.open();render();refresh();
 }
 function syncVisibility(){if(destroyed)return;const now=visible();if(now&&!wasVisible&&Date.now()-lastAttempt>1000)refresh();wasVisible=now;}
 const observer=new MutationObserver(syncVisibility);for(let node=host;node;node=node.parentElement)observer.observe(node,{attributes:true,attributeFilter:['hidden','class','style']});
 const onLanguage=()=>render();window.addEventListener('wuji-language',onLanguage);window.addEventListener('workspace-page',syncVisibility);document.addEventListener('visibilitychange',syncVisibility);
 const timer=setInterval(()=>{const busy=data?.busy||activePhases.has(data?.phase),due=Date.now()-lastAttempt>=(busy?900:5900);if(due&&(busy||visible()))refresh();},1000);
 window.WujiWorkbenchUpdate={refresh,snapshot:()=>({data,fresh}),open,destroy(){destroyed=true;clearInterval(timer);observer.disconnect();window.removeEventListener('wuji-language',onLanguage);window.removeEventListener('workspace-page',syncVisibility);document.removeEventListener('visibilitychange',syncVisibility);sheet?.destroy();updateEntry?.remove();card.remove();}};
 render();refresh();
})();
