/* Full original Ubuntu Studio, with an independently versioned component. */
(()=>{
 'use strict';
 const ws=window.WujiWorkspace,$=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 if(!ws||new URLSearchParams(location.search).get('embedded')==='1')return;
 const page=document.createElement('section');page.id='page-official-studio';page.className='workspace-page';page.hidden=true;
 page.innerHTML=`<header class="page-heading"><div><h2 tabindex="-1">Wuji Studio</h2><p id="os-subtitle"></p></div><div class="os-heading-actions"><span id="os-state" role="status"></span><button id="os-settings" type="button"></button></div></header>
 <div class="os-session-bar"><span id="os-session-note"></span><div><button id="os-offline" type="button"></button><button id="os-focus" type="button"></button><button id="os-reconnect" type="button"></button><button id="os-close" type="button"></button></div></div>
 <div class="os-stage"><div id="os-empty"><svg viewBox="0 0 48 48" aria-hidden="true"><rect x="5" y="8" width="38" height="30" rx="6"/><path d="M5 16h38M13 12h1m4 0h1M18 23l-5 5 5 5m12-10 5 5-5 5"/></svg><h3 id="os-empty-title"></h3><p id="os-empty-text"></p><button id="os-open" class="primary" type="button"></button><p id="os-progress" role="status"></p></div><iframe id="os-frame" title="Official Ubuntu Wuji Studio" referrerpolicy="no-referrer" hidden></iframe></div>
 <p id="os-error" role="alert"></p>`;
 document.querySelector('.page-area').prepend(page);ws.views['official-studio']=page.querySelector('.os-stage');
 const nav=document.createElement('a');nav.href='#official-studio';nav.dataset.page='official-studio';
 nav.innerHTML='<svg viewBox="0 0 26 24" aria-hidden="true"><rect x="3" y="4" width="20" height="16" rx="3"/><path d="M3 9h20m-15-3h1m3 0h1"/></svg><span>Wuji Studio</span>';
 document.querySelector('.directory').insertBefore(nav,document.querySelector('.nav-advanced'));
 const settings=document.createElement('dialog');settings.id='os-settings-dialog';settings.className='symbol-dialog os-settings-dialog';
 settings.innerHTML=`<header class="os-dialog-heading"><h3 id="os-settings-title"></h3><button id="os-settings-close" type="button" aria-label="Close">×</button></header><p id="os-version-summary"></p><div class="os-component-info"><strong id="os-program-label"></strong><span id="os-program-version">—</span><strong id="os-data-label"></strong><span id="os-data-note"></span></div><label for="os-version" id="os-version-label"></label><select id="os-version"></select><div class="os-version-actions"><button id="os-check" type="button"></button><button id="os-probe" type="button"></button></div><div class="os-install-actions"><button id="os-install" class="primary" type="button"></button><button id="os-select" type="button"></button><button id="os-rollback" type="button"></button></div><p id="os-update-note"></p><p id="os-maintenance"></p><p id="os-dialog-status" role="status"></p>`;
 document.body.append(settings);
 const close=document.createElement('dialog');close.className='symbol-dialog os-close-dialog';close.id='os-close-dialog';
 close.innerHTML='<h3 id="os-close-title"></h3><p id="os-close-note"></p><div class="button-row"><button id="os-close-cancel" type="button"></button><button id="os-close-confirm" class="primary" type="button"></button></div>';document.body.append(close);
 let state=null,pending=false,viewerSession='',viewLoading=false,optionsKey='',actionError='',viewerError='',autoProbed=false,inactive=false;
 const frame=$('os-frame');
 function hideViewer(){frame.hidden=true;frame.removeAttribute('src');viewerSession='';viewLoading=false;page.dataset.viewer='disconnected';}
 async function act(name,values={}){
  pending=true;actionError='';render();
  try{
   const token=(await(await fetch('/api/state')).json()).csrf;
   const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({name,...values})});
   const value=await r.json();if(!r.ok)throw Error(value.error);
   if(value.studio)state=value.studio;
   if(value.viewer_url)attach(value.viewer_url);
   await poll();return true;
  }catch(e){actionError=e.message;return false;}
  finally{pending=false;render();}
 }
 function attach(url){
  const parsed=new URL(url,location.origin);if(parsed.origin!==location.origin||parsed.pathname!=='/official_studio_viewer.html')throw Error('Invalid Studio viewer');
  viewerSession=new URLSearchParams(parsed.hash.slice(1)).get('session');viewerError='';frame.src=url;frame.hidden=false;viewLoading=false;page.dataset.viewer='connecting';
 }
 async function loadView(){if(viewLoading||!state?.active||state.phase!=='running')return;viewLoading=true;await act('official_studio_view');viewLoading=false;}
 function render(){
  if(!state)return;
  const busy=pending||state.busy,active=state.active,installed=!!state.current;
  $('os-subtitle').textContent=t('Ubuntu 官方原版 · 在工作台内运行','Original Ubuntu application · Inside your workbench');
  const phases={unchecked:t('待检查','Not checked'),checking:t('正在检查','Checking'),not_installed:t('未安装','Not installed'),installed:t('已就绪','Ready'),downloading:t('正在下载','Downloading'),dependencies:t('准备图形组件','Preparing graphics'),installing:t('正在安装','Installing'),preparing:t('正在准备','Preparing'),starting:t('正在打开','Opening'),running:t('正在运行','Running'),closing:t('正在关闭','Closing'),error:t('需要处理','Needs attention'),external_session:t('已有会话','Existing session'),needs_check:t('需要核对','Needs verification'),checking_updates:t('检查版本','Checking versions'),switching:t('切换版本','Switching version')};
  $('os-state').textContent=phases[state.phase]||state.phase;
  $('os-session-note').textContent=active?(state.offline?t('离线查看 · 已隔离设备网络','Offline view · Device network isolated'):t('设备由官方 Studio 管理，工作台控制已暂停','Official Studio owns devices; workbench controls are suspended')):t('原版连接、可视化与标定界面。用户数据独立保留。','Original connection, visualization and calibration. User data is retained separately.');
  $('os-settings').textContent=t('组件设置','Component settings');$('os-settings-title').textContent=t('Studio 组件','Studio component');
  $('os-empty-title').textContent='Wuji Studio';$('os-empty-text').textContent=installed?t('打开完整原版界面，继续使用官方操作流程。','Open the complete original interface and use the official workflow.'):t('首次使用安装官方程序及图形组件，以后可独立更新。','Install the official program and graphics once; update independently later.');
  $('os-open').textContent=busy?(phases[state.phase]||t('请稍候','Please wait')):installed?t('打开 Studio','Open Studio'):t('安装组件','Install component');$('os-open').disabled=busy||active||!state.supported;
  $('os-offline').textContent=t('离线查看','Offline view');$('os-offline').disabled=busy||active||!installed;
  $('os-close').textContent=t('关闭 Studio','Close Studio');$('os-close').disabled=pending||!(active||state.phase==='starting');
  $('os-reconnect').textContent=t('重新载入画面','Reload view');$('os-reconnect').disabled=pending||!active||state.phase==='external_session';
  $('os-focus').textContent=document.body.dataset.studioFocus==='true'?t('退出专注','Exit focus'):t('专注显示','Focus view');$('os-focus').disabled=!active;
  $('os-empty').hidden=active&&!frame.hidden;
  $('os-progress').textContent=state.phase==='downloading'&&state.download_total?t('已下载 ','Downloaded ')+Math.round(100*state.download_bytes/state.download_total)+'%':busy?(phases[state.phase]||''):'';
  $('os-program-label').textContent=t('程序版本','Program version');$('os-program-version').textContent=state.current||t('未安装','Not installed');
  $('os-data-label').textContent=t('用户数据','User data');$('os-data-note').textContent=t('与程序分开保存，不随切换版本删除','Stored separately; version switching does not delete it');
  $('os-version-summary').textContent=t('单独管理官方 Studio，无需重装工作台。','Manage official Studio independently of the workbench.');
  $('os-version-label').textContent=t('官方版本','Official version');
  const installedVersions=new Set(state.inventory?.versions?.map(v=>v.version)||[]),all=[...new Set([...installedVersions,...(state.catalog||[]).map(v=>v.version),state.current].filter(Boolean))];
  const key=JSON.stringify([all,state.current,window.WujiLocale?.lang]);if(key!==optionsKey){optionsKey=key;const old=$('os-version').value;$('os-version').replaceChildren(...all.map(v=>new Option(v+(v===state.current?t(' · 当前',' · Current'):installedVersions.has(v)?t(' · 已安装',' · Installed'):t(' · 可安装',' · Available')),v)));$('os-version').value=all.includes(old)?old:state.current||all[0]||'';}
  $('os-check').textContent=t('检查官方版本','Check official versions');$('os-probe').textContent=t('检查组件','Verify component');
  $('os-install').textContent=t('安装所选版本','Install selected');$('os-select').textContent=t('使用所选版本','Use selected');$('os-rollback').textContent=t('回到上一版本','Previous version');
  for(const id of ['os-check','os-probe','os-install','os-select','os-version','os-rollback'])$(id).disabled=busy||active;
  $('os-select').disabled=busy||active||!installedVersions.has($('os-version').value)||$('os-version').value===state.current;
  $('os-rollback').disabled=busy||active||!state.previous;
  $('os-update-note').textContent=t('只从官方 GitHub 下载并核对 SHA-256。保留旧程序；回退只切换程序，不自动降级用户档案。','Downloads only from official GitHub with SHA-256 verification. Previous programs stay installed; rollback changes the program, not the user-data format.');
  $('os-maintenance').textContent=t('官方最终版本为 2026.9.22，已停止维护。此处保留完整原程序。','The final official release is 2026.9.22 and is no longer maintained. This is the complete original program.');
  $('os-close-title').textContent=t('关闭官方 Studio？','Close official Studio?');$('os-close-note').textContent=t('请先在 Studio 内停止动作并断开设备。工作台不能读取它内部的电机状态，关闭窗口不代表已确认设备停用。','Stop motion and disconnect devices inside Studio first. Workbench cannot observe its internal motor state; closing the window does not confirm motor disable.');
  $('os-close-cancel').textContent=t('返回 Studio','Back to Studio');$('os-close-confirm').textContent=t('关闭 Studio','Close Studio');
  $('os-error').textContent=state.error||actionError||viewerError;$('os-dialog-status').textContent=state.error||actionError||state.update_warning||'';
  if(!active&&frame.hasAttribute('src'))hideViewer();
  if(active&&!page.hidden&&!frame.hasAttribute('src'))loadView();
 }
 async function poll(){
  if(inactive)return;
  try{
   const r=await fetch('/api/official-studio',{cache:'no-store'});if(!r.ok)return;state=await r.json();
   if(!state.supported){inactive=true;nav.remove();page.remove();settings.remove();close.remove();delete ws.views['official-studio'];clearInterval(timer);if(location.hash==='#official-studio')location.hash='#connection';return;}
   render();
   if(!autoProbed&&!page.hidden&&state.phase==='unchecked'&&!pending){autoProbed=true;await act('official_studio_probe');}
  }catch{}
 }
 $('os-open').onclick=()=>act(state?.current?'official_studio_open':'official_studio_install');
 $('os-offline').onclick=()=>act('official_studio_open',{offline:true});
 $('os-focus').onclick=()=>{document.body.dataset.studioFocus=String(document.body.dataset.studioFocus!=='true');render();};
 $('os-reconnect').onclick=()=>{hideViewer();loadView();};$('os-close').onclick=()=>close.showModal();
 $('os-close-cancel').onclick=()=>close.close();$('os-close-confirm').onclick=async()=>{close.close();await act('official_studio_close');};
 $('os-settings').onclick=()=>{settings.showModal();poll();};$('os-settings-close').onclick=()=>settings.close();
 $('os-check').onclick=()=>act('official_studio_check_updates');$('os-probe').onclick=()=>act('official_studio_probe');
 $('os-install').onclick=()=>act('official_studio_install',$('os-version').value?{version:$('os-version').value}:{});
 $('os-select').onclick=()=>act('official_studio_select',{version:$('os-version').value});$('os-rollback').onclick=()=>act('official_studio_select',{version:state.previous});
 $('os-version').onchange=render;
 window.addEventListener('message',e=>{if(e.origin===location.origin&&e.source===frame.contentWindow&&e.data?.type==='official-studio-viewer'&&e.data.session===viewerSession){page.dataset.viewer=e.data.phase;if(e.data.phase==='error')viewerError=e.data.detail;render();}});
 window.addEventListener('workspace-page',e=>{if(e.detail==='official-studio'){poll();if(state?.phase==='unchecked'&&!autoProbed){autoProbed=true;act('official_studio_probe');}}else{hideViewer();document.body.dataset.studioFocus='false';}});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.dataset.studioFocus==='true'){document.body.dataset.studioFocus='false';render();}});
 window.addEventListener('wuji-language',render);
 const timer=setInterval(()=>{if(!page.hidden||settings.open||state?.busy||state?.active)poll();},1000);
 poll();ws.showPage();
})();
