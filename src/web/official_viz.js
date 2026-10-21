/* One unmodified official Viewer. Device/process ownership remains in the backend. */
(()=>{
 'use strict';
 let mounted=null;
 const text=(value,max=2000)=>typeof value==='string'?value.slice(0,max):'';
 function mount(host){
  if(mounted){if(mounted.host===host)return mounted.api;throw Error('Official visualization is already mounted');}
  if(!host||host.nodeType!==1)throw TypeError('Official visualization requires a host element');
  const root=document.createElement('section');root.className='ov-root';root.setAttribute('aria-labelledby','ov-title');
  root.innerHTML='<header class="ov-heading"><div><h3 id="ov-title"></h3><p id="ov-description"></p></div><span id="ov-state" role="status"></span></header><div class="ov-toolbar"><label class="ov-device-field"><span id="ov-device-label"></span><select id="ov-device"></select></label><button id="ov-scan" type="button"></button><button id="ov-start" class="primary" type="button"></button><button id="ov-stop" type="button"></button><div class="ov-display-actions"><button id="ov-reload" type="button"></button><button id="ov-focus" type="button" aria-pressed="false"></button></div></div><p id="ov-start-note" class="ov-note"></p><div class="ov-session"><span id="ov-identity"></span><span id="ov-version"></span></div><p id="ov-message" role="status" aria-live="polite"></p><p id="ov-error" role="alert"></p><div class="ov-stage"><div id="ov-empty"><strong id="ov-empty-title"></strong><p id="ov-empty-message"></p></div></div><span id="ov-frame-status" role="status"></span><details class="ov-logs"><summary id="ov-logs-label"></summary><pre id="ov-logs"></pre></details>';
  host.append(root);const $=id=>root.querySelector('#'+id),stage=root.querySelector('.ov-stage'),logs=root.querySelector('details');
  const modes=document.createElement('div');modes.className='ov-modes hub-tabs';modes.setAttribute('role','tablist');
  modes.innerHTML='<button id="ov-mode-offline" role="tab" type="button"></button><button id="ov-mode-device" role="tab" type="button"></button>';
  root.querySelector('.ov-heading').after(modes);
  const deviceControls=root.querySelector('.ov-toolbar'),displayActions=root.querySelector('.ov-display-actions');deviceControls.after(displayActions);displayActions.prepend($('ov-stop'));
  const sourceInfo=document.createElement('div');sourceInfo.className='ov-source-info';
  const connectDevice=document.createElement('button');connectDevice.type='button';connectDevice.id='ov-connect-device';connectDevice.className='primary';
  sourceInfo.append($('ov-message'),connectDevice);displayActions.after(sourceInfo);
  const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
  let state=null,fresh=false,loading=null,pending='',sequence=0,transportError='',actionError='',viewerError='',frame=null,frameUrl='',frameLoaded=false,focus=false,destroyed=false,lastVisible=null,optionKey='',savedScroll=0;
  let viewMode='offline',offline=null,offlineError='',offlineLoading=null,frameReady=false,consoleState=null,wantedSerial='';
  const previewOnly=s=>s?.connection==='disconnected'&&s?.hardware?.active===false&&s?.glove?.busy===true&&!s.glove.feedback?.device_id&&s.glove.hardware?.active!==true&&s.glove.hardware?.active!==null&&!(s.unconfirmed_devices||[]).length;
  const theme=()=>document.documentElement.dataset.theme==='dark'?'dark':'light';
  const visible=()=>!document.hidden&&host.getClientRects().length>0&&getComputedStyle(host).visibility!=='hidden';
  function setText(id,value){if($(id).textContent!==value)$(id).textContent=value;}
  function setFocus(value){const next=!!value,changed=next!==focus,area=host.closest('.page-area');if(changed&&next)savedScroll=area?.scrollTop||0;focus=next;document.body.dataset.officialVizFocus=String(focus);root.dataset.focus=String(focus);if(changed&&area)area.scrollTop=focus?0:savedScroll;$('ov-focus').setAttribute('aria-pressed',String(focus));setText('ov-focus',focus?t('恢复布局','Restore layout'):t('专注查看','Focus view'));}
  function detach(){if(frame){frame.removeAttribute('src');frame.remove();frame=null;}frameUrl='';frameLoaded=false;frameReady=false;root.dataset.rendererReady='false';}
  function checkedUrl(value){
   if(typeof value!=='string'||!value.trim())return '';
   const u=new URL(value,location.origin),loopback=['127.0.0.1','localhost','[::1]'].includes(u.hostname);
   if(u.username||u.password||u.protocol!=='http:'||!loopback&&u.origin!==location.origin)throw Error(t('可视化地址无效，请重新读取状态。','Invalid Viewer address. Reload the status.'));
   return u.href;
  }
  function syncFrame(){
   if(destroyed)return;
   const blank=viewMode==='offline',show=visible()&&(blank?offline?.available===true:fresh&&state?.active===true&&state.phase==='running');let url='';
   if(show)try{url=checkedUrl(blank?offline.url+'?hide_welcome_screen&persist=0&notebook=0&theme='+theme():state.viewer_url);}catch(error){viewerError=error.message;}
   if(!show||!url){detach();$('ov-empty').hidden=false;$('ov-frame-status').hidden=true;return;}
   if(frame&&frameUrl===url)return;
   detach();viewerError='';frameUrl=url;frame=document.createElement('iframe');frame.id='ov-frame';frame.title=t('Wuji CLI 原版可视化','Original Wuji CLI visualization');frame.referrerPolicy='no-referrer';frame.allow='fullscreen';
   const current=frame;frame.addEventListener('load',()=>{if(frame!==current)return;frameLoaded=true;renderFrameStatus();});
   frame.addEventListener('error',()=>{if(frame!==current)return;viewerError=t('画面载入失败，可重新载入；CLI 会话仍需单独停止。','Viewer loading failed. Reload the view; stop the CLI session separately.');render();});
   frame.src=url;stage.append(frame);$('ov-empty').hidden=true;renderFrameStatus();
  }
  function renderFrameStatus(){root.dataset.rendererReady=String(frameReady);setText('ov-frame-status',(viewMode==='offline'?frameReady:frameLoaded)?(viewMode==='offline'?t('原版空窗口 · 未接入设备数据','Original empty Viewer · No device data'):t('页面已载入；CLI 未报告数据新鲜度','Page loaded; data freshness is not reported by the CLI')):t('正在载入原版画面…','Loading the original Viewer…'));$('ov-frame-status').hidden=!frame;}
  function renderLogs(){if(logs.open)setText('ov-logs',(Array.isArray(state?.logs)?state.logs:[]).filter(line=>typeof line==='string').slice(-80).map(line=>line.slice(0,2000)).join('\n')||t('暂无日志','No log messages'));}
  function render(){
   if(destroyed)return;const s=state||{},busy=!!pending||!!s.busy,active=s.active===true,phase=s.phase;
   setText('ov-title',t('官方可视化','Official visualization'));setText('ov-description',t('通过 Wuji CLI 打开完整原版 Viewer。','Open the complete original Viewer through Wuji CLI.'));
   const blank=viewMode==='offline';root.dataset.viewMode=viewMode;setText('ov-mode-offline',t('空窗口','Empty Viewer'));setText('ov-mode-device',t('实时设备','Live device'));modes.setAttribute('aria-label',t('可视化来源','Viewer source'));
   for(const key of ['offline','device']){$('ov-mode-'+key).setAttribute('aria-selected',String(key===viewMode));$('ov-mode-'+key).tabIndex=key===viewMode?0:-1;}
   deviceControls.hidden=blank;root.querySelector('.ov-session').hidden=blank;
   const phases={idle:['尚未打开','Not open'],starting:['CLI 正在启动','CLI starting'],running:['CLI 运行中','CLI running'],stopping:['CLI 正在停止','CLI stopping'],error:['CLI 需要处理','CLI needs attention'],exited:['CLI 已退出','CLI exited']};
   setText('ov-state',blank?(offline?.available?t('空窗口','Empty Viewer'):t('正在准备界面','Preparing Viewer')):!fresh?t('状态未核对','Status unverified'):s.scanning?t('正在查找设备','Finding devices'):phases[phase]?t(...phases[phase]):t('状态未知','Status unknown'));root.dataset.phase=fresh?text(phase,40):'unknown';
   setText('ov-device-label',t('手套或灵巧手','Glove or hand'));setText('ov-scan',s.scanning?t('正在查找…','Finding…'):previewOnly(consoleState)?t('关闭手套预览并查找','Close glove preview and find'):t('查找设备','Find devices'));setText('ov-start',pending==='official_viz_start'?t('正在打开…','Opening…'):t('启动实时画面','Start live view'));setText('ov-stop',pending==='official_viz_stop'?t('正在停止…','Stopping…'):t('停止可视化','Stop visualization'));setText('ov-reload',t('重新载入画面','Reload view'));setFocus(focus);
   const devices=(Array.isArray(s.devices)?s.devices:[]).filter(d=>d&&typeof (d.sn||d.serial)==='string'&&(d.sn||d.serial)).map(d=>({...d,sn:d.sn||d.serial})),key=JSON.stringify([devices,window.WujiLocale?.lang]);
   if(key!==optionKey){optionKey=key;const selected=wantedSerial||$('ov-device').value;$('ov-device').replaceChildren(new Option(t('选择设备','Select a device'),''),...devices.map(d=>new Option([text(d.model||d.device_type,100),d.sn,(d.handedness||d.side)==='left'?t('左','Left'):(d.handedness||d.side)==='right'?t('右','Right'):''].filter(Boolean).join(' · '),d.sn)));if(devices.some(d=>d.sn===selected))$('ov-device').value=selected;}
   $('ov-device').disabled=busy||active||!!s.scanning;$('ov-scan').disabled=!fresh||busy||active||!!s.reserved||!!s.scanning||s.supported!==true;
   $('ov-start').disabled=!fresh||busy||active||!!s.scanning||s.supported!==true||s.can_start!==true||!$('ov-device').value;
   $('ov-stop').disabled=pending==='official_viz_stop'||!active&&!s.reserved&&!['starting','running','stopping'].includes(phase);
   $('ov-focus').disabled=!frame;$('ov-reload').disabled=blank?!!offlineLoading:!fresh||!active||phase!=='running';$('ov-stop').hidden=!active&&!s.reserved&&!['starting','running','stopping'].includes(phase);
   $('ov-start-note').hidden=blank;$('ov-message').hidden=false;connectDevice.hidden=!blank;connectDevice.textContent=t('接入设备','Use a live device');
   setText('ov-start-note',t('跳过触觉清零，不使能电机。手套可视化会设置数据流频率。','Skips tactile zeroing and does not enable motors. Glove visualization configures stream rates.'));
   const d=s.device||{},identity=d.sn?[text(d.model,100),text(d.sn,160),d.handedness==='left'?t('左手','Left'):d.handedness==='right'?t('右手','Right'):''].filter(Boolean).join(' · '):'';
   setText('ov-identity',identity?(active?t('启动报告：','Startup report: '):t('上次启动：','Previous startup: '))+identity:'');setText('ov-version','CLI '+(text(s.cli_version,80)||t('版本未核对','version unverified'))+(s.backend==='windows_native'?t(' · Windows 原生',' · Native Windows'):''));
   setText('ov-message',!fresh?t('当前状态未核对，重新读取后再操作。','Current state is unverified. Reload it before starting.'):text(s.blocked_reason)||(active?t('显示 CLI 进程状态；画面保留不代表反馈在线。','Shows CLI process state; a retained image does not establish live feedback.'):t('选择设备后打开；不会自动连接其他设备。','Select a device to open; another device is never selected automatically.')));
   if(blank){
    setText('ov-description',t('原版 Viewer 可以独立打开；插上设备后，仍需选择并启动实时画面。','The original Viewer opens independently. After plugging in a device, select it and start the live view.'));
    setText('ov-start-note',t('原版 Rerun Viewer · 无需启动设备或 Linux 控制端。','Original Rerun Viewer · No device or Linux controller required.'));
    setText('ov-message',active||s.reserved?t('当前显示空窗口，尚未接入数据；已有设备会话仍在运行，可切到“实时设备”查看，或单独停止。','This empty Viewer has no device data. A device session is still active; switch to Live device to view it, or stop it separately.'):t('当前是空窗口，尚未接入设备数据。点击“接入设备”，再查找并选择手套或灵巧手。','This is an empty Viewer with no device data. Choose Use a live device, then find and select a glove or hand.'));
   }
   setText('ov-error',transportError||actionError||text(s.error)||viewerError);setText('ov-empty-title',!fresh?t('状态未核对','Status unverified'):s.supported===false?t('当前环境不支持','Not supported in this environment'):active?t('等待画面','Waiting for the Viewer'):t('原版 Viewer','Original Viewer'));
   setText('ov-empty-message',!fresh?t('重新读取状态后可恢复画面。','The view can resume after status is verified.'):active?t('CLI 可能仍在运行，停止会话请使用上方按钮。','The CLI may still be running. Use the button above to stop its session.'):t('查找并选择设备，原版界面将在此处显示。','Find and select a device. The original interface will appear here.'));
   if(blank){setText('ov-error',offlineError||viewerError);setText('ov-empty-title',t('正在打开原版 Viewer','Opening original Viewer'));setText('ov-empty-message',offlineError||t('本地资源正在准备，无需连接设备。','Preparing local resources. No device connection needed.'));}
   setText('ov-logs-label',t('详细日志','Detailed log'));renderLogs();syncFrame();renderFrameStatus();$('ov-focus').disabled=!frame;
  }
  async function refresh(){
   if(destroyed||loading)return loading;
   loading=(async()=>{try{const r=await fetch('/api/official-viz?theme='+theme(),{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error(t('无法读取可视化状态，请重试。','Visualization status is unavailable. Retry.'));const next=await r.json();if(!next||typeof next.active!=='boolean'||typeof next.supported!=='boolean'||typeof next.phase!=='string')throw Error(t('可视化状态不完整。','Incomplete visualization state.'));state=next;fresh=true;transportError='';}
    catch(error){fresh=false;transportError=error.message;}
    finally{loading=null;if(!destroyed)render();}})();return loading;
  }
  async function loadOffline(){
   if(offlineLoading)return offlineLoading;
   offlineLoading=(async()=>{try{const r=await fetch('/api/official-viewer',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error(t('无法打开本地 Viewer','Local Viewer unavailable'));const value=await r.json();if(!value.available||!value.url)throw Error(value.error||t('原版 Viewer 资源未安装，请更新工作台。','Original Viewer resources are missing. Update the workbench.'));offline=value;offlineError='';}
   catch(error){offlineError=error.message;}finally{offlineLoading=null;if(!destroyed)render();}})();return offlineLoading;
  }
  async function act(name,extra={},endPreview=false){
   if(pending&&name!=='official_viz_stop')return;const own=++sequence;pending=name;actionError='';render();
   try{const sr=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!sr.ok)throw Error(t('工作台服务未就绪','Workbench service is not ready'));let s=await sr.json();if(!s.csrf)throw Error(t('请重新读取工作台状态','Reload the workbench state'));
    if(endPreview&&name==='official_viz_scan'&&s.glove?.busy){
     if(!previewOnly(s))throw Error(t('设备会话已改变，请先在原工作区结束。','The device session changed. End it in its owning workspace.'));
     const close=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name:'glove_disconnect'}),signal:AbortSignal.timeout(15000)}),closed=await close.json();if(!close.ok||closed.ok===false)throw Error(closed.error||t('手套预览未关闭','Glove preview did not close'));
     for(let i=0;i<40&&s.glove?.busy;i++){await new Promise(resolve=>setTimeout(resolve,250));const poll=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!poll.ok)throw Error(t('无法确认预览退出','Cannot confirm preview exit'));s=await poll.json();}
     if(s.glove?.busy)throw Error(t('手套预览仍在退出，请稍后重试。','Glove preview is still closing. Retry shortly.'));
    }
    const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name,...extra}),signal:AbortSignal.timeout(name==='official_viz_stop'?45000:15000)}),body=await r.json();if(!r.ok||body.ok===false)throw Error(body.error||t('操作未完成','Operation did not complete'));await refresh();}
   catch(error){if(own===sequence)actionError=error.message;}
   finally{if(own===sequence)pending='';if(!destroyed)render();}
  }
  function syncVisibility(){if(destroyed)return;const now=visible();if(!now){setFocus(false);detach();}if(now!==lastVisible){lastVisible=now;if(now){refresh();if(viewMode==='offline'&&!offline&&!offlineError)loadOffline();}}syncFrame();}
  $('ov-scan').onclick=()=>act('official_viz_scan',{},previewOnly(consoleState));$('ov-start').onclick=()=>act('official_viz_start',{serial:$('ov-device').value,theme:theme()});$('ov-stop').onclick=()=>act('official_viz_stop');$('ov-device').onchange=()=>{wantedSerial=$('ov-device').value;window.WujiDeviceInventory?.select(wantedSerial);render();};
  function selectMode(value){viewMode=value;setFocus(false);render();if(value==='offline'&&!offline)loadOffline();}
  function showDeviceMode(){selectMode('device');($('ov-scan').disabled?$('ov-mode-device'):$('ov-scan')).focus();}
  connectDevice.onclick=showDeviceMode;
  $('ov-mode-offline').onclick=()=>selectMode('offline');$('ov-mode-device').onclick=()=>selectMode('device');
  modes.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();selectMode(event.key==='Home'?'offline':event.key==='End'?'device':viewMode==='offline'?'device':'offline');$('ov-mode-'+viewMode).focus();});
  $('ov-focus').onclick=()=>setFocus(!focus);$('ov-reload').onclick=()=>{detach();viewerError='';if(viewMode==='offline'&&!offline)loadOffline();else syncFrame();render();};logs.addEventListener('toggle',renderLogs);
  const onEscape=e=>{if(e.key==='Escape'&&focus){setFocus(false);$('ov-focus').focus();}},onLanguage=()=>render(),onTheme=()=>{render();if(visible()||state?.active)refresh();};
  const onReady=event=>{if(frame&&frameUrl&&event.source===frame.contentWindow&&event.origin===new URL(frameUrl).origin&&event.data==='READY'){frameReady=true;renderFrameStatus();}};
  window.addEventListener('message',onReady);
  const onConsole=event=>{consoleState=event.detail;render();};window.addEventListener('console-state',onConsole);
  document.addEventListener('keydown',onEscape);document.addEventListener('visibilitychange',syncVisibility);window.addEventListener('workspace-page',syncVisibility);window.addEventListener('hashchange',syncVisibility);window.addEventListener('wuji-language',onLanguage);
  const observer=new MutationObserver(syncVisibility);for(let node=host;node;node=node.parentElement)observer.observe(node,{attributes:true,attributeFilter:['hidden','class','style','data-page','data-hub-tab']});
  const themeObserver=new MutationObserver(onTheme);themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  const sizeObserver=new ResizeObserver(syncVisibility);sizeObserver.observe(host);const timer=setInterval(()=>{syncVisibility();if(visible()||state?.active)refresh();},1000);
  function selectDevice(serial){wantedSerial=serial;optionKey='';render();}
  const onSelection=event=>{if(event.detail?.device?.sn)selectDevice(event.detail.device.sn);};
  window.addEventListener('device-inventory-selection',onSelection);
  const api={refresh,syncVisibility,showDeviceMode,selectDevice,destroy(){destroyed=true;clearInterval(timer);observer.disconnect();themeObserver.disconnect();sizeObserver.disconnect();document.removeEventListener('keydown',onEscape);document.removeEventListener('visibilitychange',syncVisibility);window.removeEventListener('workspace-page',syncVisibility);window.removeEventListener('hashchange',syncVisibility);window.removeEventListener('wuji-language',onLanguage);window.removeEventListener('message',onReady);window.removeEventListener('console-state',onConsole);window.removeEventListener('device-inventory-selection',onSelection);setFocus(false);detach();root.remove();mounted=null;}};
  mounted={host,api};render();syncVisibility();return api;
 }
 window.WujiOfficialViz={mount,showDeviceMode(){mounted?.api.showDeviceMode();},async selectDevice(serial){await mounted?.api.refresh();mounted?.api.selectDevice(serial);}};
})();
