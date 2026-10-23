/* One unmodified official Viewer. Device/process ownership remains in the backend. */
(()=>{
 'use strict';
 let mounted=null;
 const text=(value,max=2000)=>typeof value==='string'?value.slice(0,max):'';
 function mount(host,options={}){
  if(mounted){if(mounted.host===host)return mounted.api;throw Error('Official visualization is already mounted');}
  if(!host||host.nodeType!==1)throw TypeError('Official visualization requires a host element');
  const root=document.createElement('section');root.className='ov-root';root.setAttribute('aria-labelledby','ov-title');
  root.innerHTML='<header class="ov-heading"><div><h3 id="ov-title"></h3><p id="ov-description"></p></div><span id="ov-state" role="status"></span></header><div class="ov-toolbar"><label class="ov-device-field"><span id="ov-device-label"></span><select id="ov-device"></select></label><button id="ov-scan" type="button"></button><div class="ov-task-choice rt-segments" role="group"><button id="ov-task-view" type="button" aria-pressed="true"></button><button id="ov-task-calibration" type="button" aria-pressed="false"></button></div><button id="ov-start" class="primary" type="button"></button><button id="ov-stop" type="button"></button><div class="ov-display-actions"><button id="ov-reload" type="button"></button><button id="ov-focus" type="button" aria-pressed="false"></button></div></div><p id="ov-start-note" class="ov-note"></p><div class="ov-session"><span id="ov-identity"></span><span id="ov-version"></span></div><p id="ov-message" role="status" aria-live="polite"></p><p id="ov-error" role="alert"></p><div id="ov-shared-stage" class="ov-stage" data-task="view"><div id="ov-view-stage"><div id="ov-empty"><strong id="ov-empty-title"></strong><p id="ov-empty-message"></p></div></div></div><span id="ov-frame-status" role="status"></span><details class="ov-logs"><summary id="ov-logs-label"></summary><pre id="ov-logs"></pre></details>';
  host.append(root);const $=id=>root.querySelector('#'+id),stage=root.querySelector('.ov-stage'),viewStage=$('ov-view-stage'),logs=root.querySelector('details');
  const modes=document.createElement('div');modes.className='ov-source-actions';
  modes.innerHTML='<button id="ov-mode-device" type="button" hidden></button><button id="ov-mode-offline" type="button"></button>';
  root.querySelector('.ov-heading').after(modes);
  const deviceControls=root.querySelector('.ov-toolbar'),displayActions=root.querySelector('.ov-display-actions');deviceControls.after(displayActions);displayActions.prepend($('ov-stop'));
  const calibrate=document.createElement('button');calibrate.type='button';calibrate.id='ov-calibrate';calibrate.className='primary';deviceControls.append(calibrate);calibrate.onclick=()=>{if(!calibrate.disabled)options.onCalibrate?.({start:true});};
  const sourceInfo=document.createElement('div');sourceInfo.className='ov-source-info';
  const connectDevice=document.createElement('button');connectDevice.type='button';connectDevice.id='ov-connect-device';connectDevice.className='primary';
  sourceInfo.append($('ov-message'),connectDevice);displayActions.after(sourceInfo);
  displayActions.prepend(root.querySelector('.ov-session'));root.querySelector('.ov-session').append($('ov-state'));
  const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
  let state=null,fresh=false,loading=null,pending='',sequence=0,transportError='',actionError='',viewerError='',frame=null,frameUrl='',frameLoaded=false,focus=false,destroyed=false,lastVisible=null,optionKey='',savedScroll=0;
  let viewMode='device',task='view',calibrationLayer=null,calibrationMeta={},offline=null,offlineError='',offlineLoading=null,frameReady=false,consoleState=null,wantedSerial='',calibrationRunning=false,calibrationBusy=false,calibrationVerified=true;
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
   frame.src=url;viewStage.append(frame);$('ov-empty').hidden=true;renderFrameStatus();
  }
  function renderFrameStatus(){root.dataset.rendererReady=String(frameReady);setText('ov-frame-status',(viewMode==='offline'?frameReady:frameLoaded)?(viewMode==='offline'?t('查看器已打开 · 未接入设备数据，不用于标定','Viewer open · No device data; not a calibration session'):t('页面已载入；CLI 未报告数据新鲜度','Page loaded; data freshness is not reported by the CLI')):t('正在载入原版画面…','Loading the original Viewer…'));$('ov-frame-status').hidden=task==='calibration'||!frame||root.dataset.liveOwned==='true'&&frameLoaded&&!viewerError;}
  function renderLogs(){if(logs.open){const detail=[$('ov-version').textContent,viewMode==='device'&&frameLoaded?t('页面已载入；CLI 未报告数据新鲜度。','Page loaded; data freshness is not reported by the CLI.'):null,...(Array.isArray(state?.logs)?state.logs:[])];setText('ov-logs',detail.filter(line=>typeof line==='string'&&line).slice(-82).map(line=>line.slice(0,2000)).join('\n')||t('暂无日志','No log messages'));}}
  function render(){
   if(destroyed)return;const s=state||{},busy=!!pending||!!s.busy,active=s.active===true,phase=s.phase;
   setText('ov-title',t('官方 CLI','Official CLI'));setText('ov-description',t('选好设备，在同一工作区查看或标定。','Choose a device to view or calibrate in this workspace.'));
   const calibrating=task==='calibration';root.dataset.task=task;root.dataset.guided=String(calibrationMeta.guided===true);stage.dataset.task=task;viewStage.hidden=calibrating;if(calibrationLayer)calibrationLayer.hidden=!calibrating;
   setText('ov-task-view',t('查看','View'));setText('ov-task-calibration',t('标定','Calibrate'));root.querySelector('.ov-task-choice').setAttribute('aria-label',t('官方任务','Official task'));$('ov-task-view').setAttribute('aria-pressed',String(!calibrating));$('ov-task-calibration').setAttribute('aria-pressed',String(calibrating));
   $('ov-start').hidden=calibrating;calibrate.hidden=!calibrating;modes.hidden=calibrating;logs.hidden=calibrating;$('ov-start-note').hidden=calibrating;$('ov-reload').hidden=calibrating;$('ov-frame-status').hidden=calibrating;
   const blank=viewMode==='offline';root.dataset.viewMode=viewMode;setText('ov-mode-offline',blank?t('返回实时设备','Return to live device'):t('无设备查看器','Viewer without a device'));setText('ov-mode-device',t('实时设备','Live device'));
   $('ov-mode-offline').setAttribute('aria-pressed',String(blank));
   deviceControls.hidden=false;root.querySelector('.ov-session').hidden=blank;
   const phases={idle:['尚未打开','Not open'],starting:['CLI 正在启动','CLI starting'],running:['CLI 运行中','CLI running'],stopping:['CLI 正在停止','CLI stopping'],error:['CLI 需要处理','CLI needs attention'],exited:['CLI 已退出','CLI exited']};
   setText('ov-state',blank?(offline?.available?t('无设备查看器','Viewer only'):t('正在准备界面','Preparing Viewer')):!fresh?t('状态未核对','Status unverified'):s.scanning?t('正在查找设备','Finding devices'):phases[phase]?t(...phases[phase]):t('状态未知','Status unknown'));root.dataset.phase=fresh?text(phase,40):'unknown';
   setText('ov-device-label',t('本次使用的设备','Device for this task'));setText('ov-scan',s.scanning?t('正在查找…','Finding…'):previewOnly(consoleState)?t('关闭手套预览并查找','Close glove preview and find'):t('查找设备','Find devices'));setText('ov-start',pending==='official_viz_start'?t('正在打开…','Opening…'):t('查看画面','View device'));setText('ov-calibrate',t('准备标定','Prepare calibration'));setText('ov-stop',pending==='official_viz_stop'?t('正在停止…','Stopping…'):t('停止画面会话','Stop Viewer session'));setText('ov-reload',t('重新载入画面','Reload view'));setFocus(focus);
   const devices=(Array.isArray(s.devices)?s.devices:[]).filter(d=>d&&typeof (d.sn||d.serial)==='string'&&(d.sn||d.serial)).map(d=>({...d,sn:d.sn||d.serial}));
   const ownedDevice=s.reported_device||s.device,ownedSN=active&&phase==='running'&&typeof (ownedDevice?.sn||ownedDevice?.serial)==='string'?(ownedDevice.sn||ownedDevice.serial):'';
   // A mounted task already has a verified owner. Show that existing session
   // even when its earlier discovery advertisement has expired; never start it.
   if(ownedSN&&!devices.some(d=>d.sn===ownedSN))devices.push({...ownedDevice,sn:ownedSN});
   const key=JSON.stringify([devices,ownedSN,window.WujiLocale?.lang]);
   if(key!==optionKey){optionKey=key;const selected=ownedSN||wantedSerial||$('ov-device').value;$('ov-device').replaceChildren(new Option(t('选择设备','Select a device'),''),...devices.map(d=>new Option([text(d.model||d.device_type,100),d.sn,(d.handedness||d.side)==='left'?t('左','Left'):(d.handedness||d.side)==='right'?t('右','Right'):''].filter(Boolean).join(' · '),d.sn)));if(devices.some(d=>d.sn===selected))$('ov-device').value=selected;}
   // Reduce routine chrome only for the exact, verified session we own.
   // Unknown state, another owner and every reported error keep their context.
   const liveOwned=fresh&&s.supported===true&&calibrationVerified&&!blank&&!calibrating&&active&&phase==='running'&&ownedSN===$('ov-device').value&&!!ownedSN&&/^[a-f0-9]{32}$/.test(s.session_id||'')&&!busy&&!calibrationRunning&&!calibrationBusy&&!s.blocked_reason&&!s.error&&!transportError&&!actionError&&!viewerError;
   root.dataset.liveOwned=String(liveOwned);
   $('ov-device').disabled=busy||active||!!s.scanning||calibrationRunning||calibrationBusy;$('ov-scan').disabled=!fresh||busy||active||!!s.reserved||!!s.scanning||s.supported!==true||calibrationRunning||calibrationBusy;
   $('ov-start').disabled=!fresh||busy||active||!!s.scanning||s.supported!==true||s.can_start!==true||!$('ov-device').value||calibrationRunning||calibrationBusy||!calibrationVerified;
   calibrate.disabled=!!pending||calibrationBusy||calibrationMeta.ready!==true;calibrate.setAttribute('aria-controls','ov-shared-stage');setText('ov-calibrate',calibrationBusy?t('正在准备…','Preparing…'):calibrationMeta.actionLabel||t('开始标定','Start calibration'));setText('ov-start',pending==='official_viz_start'?t('正在接入…','Connecting…'):t('接入并查看','Connect and view'));setText('ov-stop',pending==='official_viz_stop'?t('正在断开…','Disconnecting…'):t('断开设备画面','Disconnect device view'));
   $('ov-stop').disabled=pending==='official_viz_stop'||!active&&!s.reserved&&!['starting','running','stopping'].includes(phase);
   $('ov-focus').disabled=!frame;$('ov-reload').disabled=blank?!!offlineLoading:!fresh||!active||phase!=='running';$('ov-stop').hidden=!active&&!s.reserved&&!['starting','running','stopping'].includes(phase);
   $('ov-start-note').hidden=blank||calibrating||liveOwned;$('ov-message').hidden=false;connectDevice.hidden=!blank||calibrating;connectDevice.textContent=t('接入设备','Use a live device');
   setText('ov-start-note',calibrationMeta.guided?t('接入后查看三维与传感数据；手型标定请切到“标定”。不使能电机；手套画面会设置数据流频率。','Connect to view 3D and sensor data. For hand-model calibration, choose Calibrate. Motors are not enabled; glove viewing configures stream rates.'):t('查看模式显示三维与传感数据；手型标定请切到“标定”，按官方终端的菜单与图示操作，无需编写代码。跳过触觉清零、不使能电机；手套画面会设置数据流频率。','View mode shows 3D and sensor data. For hand-model calibration, choose Calibrate and follow the official terminal menus and illustrations; no coding is needed. Skips tactile zeroing and does not enable motors; glove visualization configures stream rates.'));
   const d=s.device||{},identity=d.sn?[text(d.model,100),text(d.sn,160),d.handedness==='left'?t('左手','Left'):d.handedness==='right'?t('右手','Right'):''].filter(Boolean).join(' · '):'';
   setText('ov-identity',identity?(liveOwned?'':active?t('启动报告：','Startup report: '):t('上次启动：','Previous startup: '))+identity:'');setText('ov-version','CLI '+(text(s.cli_version,80)||t('版本未核对','version unverified'))+(s.backend==='windows_native'?t(' · Windows 原生',' · Native Windows'):''));
   setText('ov-message',calibrationRunning?t('标定会话正在使用设备；取消或完成后才能打开实时画面。','Calibration owns the device. Finish or cancel it before opening a live view.'):!calibrationVerified&&!active?t('标定会话状态尚未核对，请刷新官方状态后再打开实时画面。','Calibration session state is unverified. Refresh official status before opening a live view.'):!fresh?t('当前状态未核对，重新读取后再操作。','Current state is unverified. Reload it before starting.'):text(s.blocked_reason)||(active?t('显示 CLI 进程状态；画面保留不代表反馈在线。','Shows CLI process state; a retained image does not establish live feedback.'):t('选择设备后查看画面，或准备手套标定。不会自动启动、切换或停止设备会话。','Select a device to view, or prepare glove calibration. Device sessions never start, switch or stop automatically.')));
   if(blank){
    setText('ov-description',t('官方查看器可以离线打开；实际设备画面和标定仍由上方任务按钮明确启动。','The official Viewer can open offline. Use the task buttons above to explicitly start a device view or calibration.'));
    setText('ov-start-note',t('原版 Rerun Viewer · 无需启动设备或 Linux 控制端。','Original Rerun Viewer · No device or Linux controller required.'));
    setText('ov-message',active||s.reserved?t('当前查看器没有接入数据；已有设备会话仍在运行，请切回“实时设备”查看或单独停止。','This viewer has no device data. The existing device session is still running; return to Live device to view it or stop it separately.'):t('无设备时可先检查查看器能否打开。查看手套或灵巧手，请点击“接入设备”，再查找并选择实际设备。','Use this to check that the viewer opens without hardware. For glove or hand data, choose Use a live device, then find and select your device.'));
   }
   setText('ov-error',transportError||actionError||text(s.error)||viewerError);setText('ov-empty-title',!fresh?t('状态未核对','Status unverified'):s.supported===false?t('当前环境不支持','Not supported in this environment'):active?t('等待画面','Waiting for the Viewer'):t('选择设备，打开实时画面','Select a device for live visualization'));
   setText('ov-empty-message',!fresh?t('重新读取状态后可恢复画面。','The view can resume after status is verified.'):active?t('CLI 可能仍在运行，停止会话请使用上方按钮。','The CLI may still be running. Use the button above to stop its session.'):t('查找并选择设备，原版界面将在此处显示。','Find and select a device. The original interface will appear here.'));
   if(blank){setText('ov-error',offlineError||viewerError);setText('ov-empty-title',t('正在打开原版 Viewer','Opening original Viewer'));setText('ov-empty-message',offlineError||t('本地资源正在准备，无需连接设备。','Preparing local resources. No device connection needed.'));}
   if(calibrating){setText('ov-description',t('选好设备，在同一工作区查看或标定。','Choose a device to view or calibrate in this workspace.'));setText('ov-state',text(calibrationMeta.status)||t('标定','Calibration'));setText('ov-message',text(calibrationMeta.reason,2000));setText('ov-version',text(calibrationMeta.runtime,200));root.querySelector('.ov-session').hidden=false;setText('ov-identity','');}
   if(liveOwned)setText('ov-message','');sourceInfo.hidden=!$('ov-message').textContent&&connectDevice.hidden;
   setText('ov-logs-label',t('详细日志','Detailed log'));renderLogs();syncFrame();renderFrameStatus();$('ov-focus').disabled=calibrating?calibrationMeta.terminal!==true:!frame;
  }
  async function refresh(){
   if(destroyed||loading)return loading;
   loading=(async()=>{try{const r=await fetch('/api/official-viz?theme='+theme(),{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error(t('无法读取可视化状态，请重试。','Visualization status is unavailable. Retry.'));const next=await r.json();if(!next||typeof next.active!=='boolean'||typeof next.supported!=='boolean'||typeof next.phase!=='string')throw Error(t('可视化状态不完整。','Incomplete visualization state.'));state=next;fresh=true;transportError='';}
    catch(error){fresh=false;transportError=error.message;}
    finally{loading=null;if(!destroyed){render();window.dispatchEvent(new CustomEvent('official-viz-update',{detail:{state,fresh}}));}}})();return loading;
  }
  async function loadOffline(){
   if(offlineLoading)return offlineLoading;
   offlineLoading=(async()=>{try{const r=await fetch('/api/official-viewer',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error(t('无法打开本地 Viewer','Local Viewer unavailable'));const value=await r.json();if(!value.available||!value.url)throw Error(value.error||t('原版 Viewer 资源未安装，请更新工作台。','Original Viewer resources are missing. Update the workbench.'));offline=value;offlineError='';}
   catch(error){offlineError=error.message;}finally{offlineLoading=null;if(!destroyed)render();}})();return offlineLoading;
  }
  async function act(name,extra={},endPreview=false){
   if(pending&&name!=='official_viz_stop')return;const own=++sequence;pending=name;actionError='';render();window.dispatchEvent(new CustomEvent('official-viz-update'));
   try{const sr=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!sr.ok)throw Error(t('工作台服务未就绪','Workbench service is not ready'));let s=await sr.json();if(!s.csrf)throw Error(t('请重新读取工作台状态','Reload the workbench state'));
    if(endPreview&&name==='official_viz_scan'&&s.glove?.busy){
     if(!previewOnly(s))throw Error(t('设备会话已改变，请先在原工作区结束。','The device session changed. End it in its owning workspace.'));
     const close=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name:'glove_disconnect'}),signal:AbortSignal.timeout(15000)}),closed=await close.json();if(!close.ok||closed.ok===false)throw Error(closed.error||t('手套预览未关闭','Glove preview did not close'));
     for(let i=0;i<40&&s.glove?.busy;i++){await new Promise(resolve=>setTimeout(resolve,250));const poll=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!poll.ok)throw Error(t('无法确认预览退出','Cannot confirm preview exit'));s=await poll.json();}
     if(s.glove?.busy)throw Error(t('手套预览仍在退出，请稍后重试。','Glove preview is still closing. Retry shortly.'));
    }
    const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name,...extra}),signal:AbortSignal.timeout(name==='official_viz_stop'?45000:15000)}),body=await r.json();if(!r.ok||body.ok===false)throw Error(body.error||t('操作未完成','Operation did not complete'));await refresh();if(name==='official_viz_scan')options.onDiscovery?.();}
   catch(error){if(own===sequence)actionError=error.message;}
   finally{if(own===sequence)pending='';if(!destroyed){render();window.dispatchEvent(new CustomEvent('official-viz-update'));}}
  }
  function syncVisibility(){if(destroyed)return;const now=visible();if(!now){setFocus(false);detach();}if(now!==lastVisible){lastVisible=now;if(now){refresh();if(viewMode==='offline'&&!offline&&!offlineError)loadOffline();}}syncFrame();}
  $('ov-scan').onclick=()=>act('official_viz_scan',{},previewOnly(consoleState));$('ov-start').onclick=()=>{if($('ov-start').disabled)return;selectMode('device');act('official_viz_start',{serial:$('ov-device').value,theme:theme()});};$('ov-stop').onclick=()=>act('official_viz_stop');$('ov-device').onchange=()=>{wantedSerial=$('ov-device').value;window.WujiDeviceInventory?.select(wantedSerial);options.onDeviceChange?.(wantedSerial,selection().device);render();};
  function selectMode(value){viewMode=value;setFocus(false);render();if(value==='offline'&&!offline)loadOffline();}
  function setTask(value){const next=value==='calibration'?'calibration':'view',changed=next!==task;task=next;if(changed)setFocus(false);render();if(changed)options.onTaskChange?.(task);}
  function showDeviceMode(){setTask('view');selectMode('device');($('ov-device').disabled?$('ov-stop').hidden?$('ov-start'):$('ov-stop'):$('ov-device')).focus();}
  $('ov-task-view').onclick=()=>setTask('view');$('ov-task-calibration').onclick=()=>setTask('calibration');
  root.querySelector('.ov-task-choice').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();setTask(event.key==='Home'?'view':event.key==='End'?'calibration':task==='view'?'calibration':'view');$(task==='view'?'ov-task-view':'ov-task-calibration').focus();});
  connectDevice.onclick=showDeviceMode;
  $('ov-mode-offline').onclick=()=>selectMode(viewMode==='offline'?'device':'offline');$('ov-mode-device').onclick=()=>selectMode('device');
  $('ov-focus').onclick=()=>setFocus(!focus);$('ov-reload').onclick=()=>{detach();viewerError='';if(viewMode==='offline'&&!offline)loadOffline();else syncFrame();render();};logs.addEventListener('toggle',renderLogs);
  const onEscape=e=>{if(e.key==='Escape'&&focus){setFocus(false);$('ov-focus').focus();}},onLanguage=()=>render(),onTheme=()=>{render();if(visible()||state?.active)refresh();};
  const onReady=event=>{if(frame&&frameUrl&&event.source===frame.contentWindow&&event.origin===new URL(frameUrl).origin&&event.data==='READY'){frameReady=true;renderFrameStatus();}};
  window.addEventListener('message',onReady);
  const onConsole=event=>{consoleState=event.detail;render();};window.addEventListener('console-state',onConsole);
  document.addEventListener('keydown',onEscape);document.addEventListener('visibilitychange',syncVisibility);window.addEventListener('workspace-page',syncVisibility);window.addEventListener('hashchange',syncVisibility);window.addEventListener('wuji-language',onLanguage);
  const observer=new MutationObserver(syncVisibility);for(let node=host;node;node=node.parentElement)observer.observe(node,{attributes:true,attributeFilter:['hidden','class','style','data-page','data-hub-tab']});
  const themeObserver=new MutationObserver(onTheme);themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  const sizeObserver=new ResizeObserver(syncVisibility);sizeObserver.observe(host);const timer=setInterval(()=>{syncVisibility();if(visible()||state?.active)refresh();},1000);
  function selection(){const serial=$('ov-device').value;return {serial,device:(Array.isArray(state?.devices)?state.devices:[]).find(d=>(d.sn||d.serial)===serial)||null};}
  function selectDevice(serial){wantedSerial=serial||'';optionKey='';render();options.onDeviceChange?.(wantedSerial,selection().device);}
  function setCalibrationState(value){const running=value?.running===true,busy=value?.busy===true,verified=value?.verified===true,meta={ready:value?.ready===true,human:value?.human===true,reason:text(value?.reason),runtime:text(value?.runtime,200),status:text(value?.status,100),terminal:value?.terminal===true,actionLabel:text(value?.actionLabel,80),guided:value?.guided===true};if(running===calibrationRunning&&busy===calibrationBusy&&verified===calibrationVerified&&JSON.stringify(meta)===JSON.stringify(calibrationMeta))return;calibrationRunning=running;calibrationBusy=busy;calibrationVerified=verified;calibrationMeta=meta;render();}
  const onSelection=event=>{const serial=event.detail?.device?.sn||event.detail?.device?.serial||'';selectDevice(serial);};
  window.addEventListener('device-inventory-selection',onSelection);
  const api={refresh,syncVisibility,showDeviceMode,selectDevice,selection,setFocus,setTask,attachCalibration(node){calibrationLayer=node;stage.append(node);render();},setCalibrationState,snapshot:()=>({state,fresh,pending,task}),destroy(){destroyed=true;clearInterval(timer);observer.disconnect();themeObserver.disconnect();sizeObserver.disconnect();document.removeEventListener('keydown',onEscape);document.removeEventListener('visibilitychange',syncVisibility);window.removeEventListener('workspace-page',syncVisibility);window.removeEventListener('hashchange',syncVisibility);window.removeEventListener('wuji-language',onLanguage);window.removeEventListener('message',onReady);window.removeEventListener('console-state',onConsole);window.removeEventListener('device-inventory-selection',onSelection);setFocus(false);detach();root.remove();mounted=null;}};
  mounted={host,api};render();syncVisibility();return api;
 }
 window.WujiOfficialViz={mount,showDeviceMode(){mounted?.api.showDeviceMode();},async selectDevice(serial){await mounted?.api.refresh();mounted?.api.selectDevice(serial);}};
})();
