/* Application update controls only. The backend owns downloads and installation. */
(()=>{
 'use strict';
 const host=document.getElementById('settings-tools');if(!host||document.getElementById('workbench-update'))return;
 const source='Fairank/wuji-hand-studio',phases=new Set(['idle','checking','available','up_to_date','unpublished','downloading','ready','applying','error']);
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const card=document.createElement('section');card.id='workbench-update';card.className='wb-card workbench-update';card.setAttribute('aria-labelledby','wbu-title');
 card.innerHTML='<header class="wbu-heading"><div><h3 id="wbu-title" tabindex="-1"></h3><p id="wbu-description"></p></div><span id="wbu-state" role="status"></span></header><div class="wbu-versions"><div><span id="wbu-current-label"></span><strong id="wbu-current"></strong></div><div id="wbu-latest-row"><span id="wbu-latest-label"></span><strong id="wbu-latest"></strong></div></div><p id="wbu-message" role="status" aria-live="polite"></p><p id="wbu-error" role="alert"></p><div class="wbu-actions"><button id="wbu-check" type="button"></button><button id="wbu-prepare" type="button" class="primary"></button><button id="wbu-apply" type="button" class="primary"></button></div><p id="wbu-checked-at" class="wbu-meta"></p>';
 host.prepend(card);const $=id=>card.querySelector('#'+id);
 let data=null,fresh=false,loading=null,pending='',transportError='',actionError='',lastAttempt=0,revision=0,destroyed=false,wasVisible=false;
 const activePhases=new Set(['checking','downloading','applying']);
 const visible=()=>!document.hidden&&card.getClientRects().length>0;
 const text=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value;};
 const version=value=>typeof value==='string'&&/^\d+\.\d+\.\d+$/.test(value);
 function newer(left,right){if(!version(left)||!version(right))return false;const a=left.split('.').map(Number),b=right.split('.').map(Number);for(let i=0;i<3;i++){if(a[i]!==b[i])return a[i]>b[i];}return false;}
 function valid(value){return value&&typeof value==='object'&&!Array.isArray(value)&&value.source===source&&typeof value.current_version==='string'&&value.current_version.length>0&&value.current_version.length<81&&phases.has(value.phase)&&typeof value.busy==='boolean'&&typeof value.available==='boolean'&&typeof value.supported==='boolean'&&typeof value.can_apply==='boolean'&&(value.latest_version==null||typeof value.latest_version==='string'&&value.latest_version.length<81)&&(value.prepared==null||typeof value.prepared==='object'&&!Array.isArray(value.prepared)&&version(value.prepared.version))&&(value.error==null||typeof value.error==='string');}
 function accept(value){if(!valid(value))throw Error('Incomplete software update state');data=value;fresh=true;transportError='';actionError='';}
 function userError(error){
  const value=String(error||'').toLowerCase();
  if(/sha.?256|digest|checksum|integrity/.test(value))return t('安装包未通过完整性校验，请重新下载。','The installer did not pass integrity verification. Download it again.');
  if(/truncat|extra bytes|size limit|content-length|response size/.test(value))return t('安装包下载不完整，请重新下载。','The installer download is incomplete. Download it again.');
  if(/session|idle|device|设备|会话/.test(value))return t('请先结束设备、标定和诊断会话，再重试。','Finish device, calibration and diagnostic sessions, then retry.');
  if(/network|dns|http|urlopen|connect|timeout|timed out|fetch|abort|网络/.test(value))return t('暂时无法连接更新服务，请稍后重试。','The update service is unavailable. Try again shortly.');
  if(/permission|filesystem|directory|access|disk|磁盘|权限/.test(value))return t('暂时无法保存安装包，请检查本机存储后重试。','The installer could not be saved. Check local storage and retry.');
  return t('软件更新未完成，请重试。','The software update did not complete. Try again.');
 }
 function checkedAt(value){if(value==null)return '';const date=new Date(typeof value==='number'&&value<1e12?value*1000:value);return Number.isFinite(date.getTime())?date.toLocaleString(window.WujiLocale?.lang==='en'?'en-GB':'zh-CN'):'';}
 function render(){
  if(destroyed)return;const s=data||{},phase=fresh?s.phase:'unknown',busy=!!pending||fresh&&(s.busy||activePhases.has(phase));
  const prepared=s.prepared&&newer(s.prepared.version,s.current_version)&&(!s.latest_version||s.prepared.version===s.latest_version),ready=phase==='ready'&&prepared;
  const labels={idle:['尚未检查','Not checked'],checking:['正在检查','Checking'],available:['发现新版本','Update available'],up_to_date:['已是最新版本','Up to date'],unpublished:['尚无已发布安装包','No published installer'],downloading:['正在下载','Downloading'],ready:['安装包已就绪','Installer ready'],applying:['正在准备安装','Preparing installation'],error:['需要重试','Retry needed'],unknown:['状态待核对','Status unverified']};
  text('wbu-title',t('软件更新','Software update'));text('wbu-description',t('一并更新工作台、配套 CLI 与界面组件，保留你的用户资料。','Update the workbench, bundled CLI and interface components together. Your user data is kept.'));
  text('wbu-state',t(...labels[phase]));card.dataset.phase=phase;card.dataset.busy=String(!!busy);card.setAttribute('aria-busy',String(!!busy));
  text('wbu-current-label',t('当前版本','Current version'));text('wbu-current',s.current_version||'—');text('wbu-latest-label',t('可用版本','Available version'));text('wbu-latest',s.latest_version||'—');$('wbu-latest-row').hidden=!s.latest_version||phase==='unpublished';
  let message=!fresh?t('正在读取更新状态。','Reading update status.'):phase==='unpublished'?t('尚无已发布安装包。发布后可在这里更新。','No installer has been published yet. Updates will appear here after publication.'):phase==='checking'?t('正在检查已发布的软件版本。','Checking published software versions.'):phase==='downloading'?t('正在准备安装包；下载完成后，由你决定何时重启安装。','Preparing the installer. You choose when to restart and install after the download finishes.'):phase==='applying'?t('正在准备重启并安装更新。','Preparing to restart and install the update.'):ready&&!s.can_apply?t('安装包已就绪。请先结束设备、标定和诊断会话，再重启安装。','The installer is ready. Finish device, calibration and diagnostic sessions before restarting.'):ready?t('安装包已就绪，可重启工作台完成安装。','The installer is ready. Restart the workbench to finish installation.'):phase==='available'?t('新版本已发布。点击“下载安装包”后才会开始下载。','A new version is published. Download begins only when you choose Download installer.'):phase==='up_to_date'?t('当前软件已是最新发布版本。','You have the latest published software version.'):t('检查已发布版本，再按需下载安装。','Check published versions, then choose whether to download and install.');
  if(phase==='error')message=t('上次更新未完成，可以重新检查或下载。','The previous update did not complete. Check again or retry the download.');
  if(fresh&&s.supported===false)message=t('此环境可检查版本；请在 Windows 安装版中下载并安装更新。','You can check versions here. Download and install updates in the installed Windows app.');
  if(!fresh&&transportError)message=t('当前更新状态无法核对，恢复连接后可继续。','Update status cannot be verified. Continue when the connection recovers.');
  text('wbu-message',message);text('wbu-error',transportError||actionError?userError(transportError||actionError):fresh&&s.error?userError(s.error):'');
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
  revision++;pending=name;actionError='';render();lastAttempt=Date.now();
  try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error('Workbench connection unavailable');const state=await response.json();if(typeof state.csrf!=='string'||!state.csrf)throw Error('Workbench connection unavailable');
   const sent=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name}),signal:AbortSignal.timeout(15000)}),body=await sent.json();
   if(!sent.ok||body.ok!==true)throw Error(body.error||'Software update action failed');accept(body.workbench_update);
  }catch(error){fresh=false;actionError=error.message||'Software update action failed';}
  finally{pending='';render();}
 }
 $('wbu-check').onclick=()=>fresh?act('workbench_update_check'):refresh();$('wbu-prepare').onclick=()=>act('workbench_update_prepare');$('wbu-apply').onclick=()=>act('workbench_update_apply');
 function syncVisibility(){if(destroyed)return;const now=visible();if(now&&!wasVisible&&Date.now()-lastAttempt>1000)refresh();wasVisible=now;}
 const observer=new MutationObserver(syncVisibility);for(let node=host;node;node=node.parentElement)observer.observe(node,{attributes:true,attributeFilter:['hidden','class','style']});
 const onLanguage=()=>render();window.addEventListener('wuji-language',onLanguage);window.addEventListener('workspace-page',syncVisibility);document.addEventListener('visibilitychange',syncVisibility);
 const timer=setInterval(()=>{const busy=data?.busy||activePhases.has(data?.phase),due=Date.now()-lastAttempt>=(busy?900:5900);if(due&&(busy||visible()))refresh();},1000);
 window.WujiWorkbenchUpdate={refresh,snapshot:()=>({data,fresh}),open(){location.hash='#settings';window.WujiWorkspace?.showPage();document.getElementById('settings-tab-tools')?.click();$('wbu-title').focus({preventScroll:true});card.scrollIntoView({block:'start'});refresh();},destroy(){destroyed=true;clearInterval(timer);observer.disconnect();window.removeEventListener('wuji-language',onLanguage);window.removeEventListener('workspace-page',syncVisibility);document.removeEventListener('visibilitychange',syncVisibility);card.remove();}};
 render();refresh();
})();
