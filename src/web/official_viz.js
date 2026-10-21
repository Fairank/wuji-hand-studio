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
  const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
  let state=null,fresh=false,loading=null,pending='',sequence=0,transportError='',actionError='',viewerError='',frame=null,frameUrl='',frameLoaded=false,focus=false,destroyed=false,lastVisible=null,optionKey='',savedScroll=0;
  const theme=()=>document.documentElement.dataset.theme==='dark'?'dark':'light';
  const visible=()=>!document.hidden&&host.getClientRects().length>0&&getComputedStyle(host).visibility!=='hidden';
  function setText(id,value){if($(id).textContent!==value)$(id).textContent=value;}
  function setFocus(value){const next=!!value,changed=next!==focus,area=host.closest('.page-area');if(changed&&next)savedScroll=area?.scrollTop||0;focus=next;document.body.dataset.officialVizFocus=String(focus);root.dataset.focus=String(focus);if(changed&&area)area.scrollTop=focus?0:savedScroll;$('ov-focus').setAttribute('aria-pressed',String(focus));setText('ov-focus',focus?t('恢复布局','Restore layout'):t('专注查看','Focus view'));}
  function detach(){if(frame){frame.removeAttribute('src');frame.remove();frame=null;}frameUrl='';frameLoaded=false;}
  function checkedUrl(value){
   if(typeof value!=='string'||!value.trim())return '';
   const u=new URL(value,location.origin),loopback=['127.0.0.1','localhost','[::1]'].includes(u.hostname);
   if(u.username||u.password||u.protocol!=='http:'||!loopback&&u.origin!==location.origin)throw Error(t('可视化地址无效，请重新读取状态。','Invalid Viewer address. Reload the status.'));
   return u.href;
  }
  function syncFrame(){
   if(destroyed)return;
   const show=fresh&&visible()&&state?.active===true&&state.phase==='running';let url='';
   if(show)try{url=checkedUrl(state.viewer_url);}catch(error){viewerError=error.message;}
   if(!show||!url){detach();$('ov-empty').hidden=false;$('ov-frame-status').hidden=true;return;}
   if(frame&&frameUrl===url)return;
   detach();viewerError='';frameUrl=url;frame=document.createElement('iframe');frame.id='ov-frame';frame.title=t('Wuji CLI 原版可视化','Original Wuji CLI visualization');frame.referrerPolicy='no-referrer';frame.allow='fullscreen';
   const current=frame;frame.addEventListener('load',()=>{if(frame!==current)return;frameLoaded=true;renderFrameStatus();});
   frame.addEventListener('error',()=>{if(frame!==current)return;viewerError=t('画面载入失败，可重新载入；CLI 会话仍需单独停止。','Viewer loading failed. Reload the view; stop the CLI session separately.');render();});
   frame.src=url;stage.append(frame);$('ov-empty').hidden=true;renderFrameStatus();
  }
  function renderFrameStatus(){setText('ov-frame-status',frameLoaded?t('页面已载入；CLI 未报告数据新鲜度','Page loaded; data freshness is not reported by the CLI'):t('正在载入原版画面…','Loading the original Viewer…'));$('ov-frame-status').hidden=!frame;}
  function renderLogs(){if(logs.open)setText('ov-logs',(Array.isArray(state?.logs)?state.logs:[]).filter(line=>typeof line==='string').slice(-80).map(line=>line.slice(0,2000)).join('\n')||t('暂无日志','No log messages'));}
  function render(){
   if(destroyed)return;const s=state||{},busy=!!pending||!!s.busy,active=s.active===true,phase=s.phase;
   setText('ov-title',t('官方可视化','Official visualization'));setText('ov-description',t('通过 Wuji CLI 打开完整原版 Viewer。','Open the complete original Viewer through Wuji CLI.'));
   const phases={idle:['尚未打开','Not open'],starting:['CLI 正在启动','CLI starting'],running:['CLI 运行中','CLI running'],stopping:['CLI 正在停止','CLI stopping'],error:['CLI 需要处理','CLI needs attention'],exited:['CLI 已退出','CLI exited']};
   setText('ov-state',!fresh?t('状态未核对','Status unverified'):s.scanning?t('正在查找设备','Finding devices'):phases[phase]?t(...phases[phase]):t('状态未知','Status unknown'));root.dataset.phase=fresh?text(phase,40):'unknown';
   setText('ov-device-label',t('设备','Device'));setText('ov-scan',s.scanning?t('正在查找…','Finding…'):t('查找设备','Find devices'));setText('ov-start',pending==='official_viz_start'?t('正在打开…','Opening…'):t('打开可视化','Open visualization'));setText('ov-stop',pending==='official_viz_stop'?t('正在停止…','Stopping…'):t('停止可视化','Stop visualization'));setText('ov-reload',t('重新载入画面','Reload view'));setFocus(focus);
   const devices=(Array.isArray(s.devices)?s.devices:[]).filter(d=>d&&typeof d.sn==='string'&&d.sn),key=JSON.stringify([devices,window.WujiLocale?.lang]);
   if(key!==optionKey){optionKey=key;const selected=$('ov-device').value;$('ov-device').replaceChildren(new Option(t('选择设备','Select a device'),''),...devices.map(d=>new Option([text(d.model,100),d.sn,d.handedness==='left'?t('左','Left'):d.handedness==='right'?t('右','Right'):''].filter(Boolean).join(' · '),d.sn)));if(devices.some(d=>d.sn===selected))$('ov-device').value=selected;}
   $('ov-device').disabled=busy||active||!!s.scanning;$('ov-scan').disabled=!fresh||busy||active||!!s.reserved||!!s.scanning||s.supported!==true;
   $('ov-start').disabled=!fresh||busy||active||!!s.scanning||s.supported!==true||s.can_start!==true||!$('ov-device').value;
   $('ov-stop').disabled=pending==='official_viz_stop'||!active&&!s.reserved&&!['starting','running','stopping'].includes(phase);
   $('ov-focus').disabled=!frame;$('ov-reload').disabled=!fresh||!active||phase!=='running';
   setText('ov-start-note',t('跳过触觉清零，不使能电机。手套可视化会设置数据流频率。','Skips tactile zeroing and does not enable motors. Glove visualization configures stream rates.'));
   const d=s.device||{},identity=d.sn?[text(d.model,100),text(d.sn,160),d.handedness==='left'?t('左手','Left'):d.handedness==='right'?t('右手','Right'):''].filter(Boolean).join(' · '):'';
   setText('ov-identity',identity?(active?t('启动报告：','Startup report: '):t('上次启动：','Previous startup: '))+identity:'');setText('ov-version','CLI '+(text(s.cli_version,80)||t('版本未核对','version unverified')));
   setText('ov-message',!fresh?t('当前状态未核对，重新读取后再操作。','Current state is unverified. Reload it before starting.'):text(s.blocked_reason)||(active?t('显示 CLI 进程状态；画面保留不代表反馈在线。','Shows CLI process state; a retained image does not establish live feedback.'):t('选择设备后打开；不会自动连接其他设备。','Select a device to open; another device is never selected automatically.')));
   setText('ov-error',transportError||actionError||text(s.error)||viewerError);setText('ov-empty-title',!fresh?t('状态未核对','Status unverified'):s.supported===false?t('当前环境不支持','Not supported in this environment'):active?t('等待画面','Waiting for the Viewer'):t('原版 Viewer','Original Viewer'));
   setText('ov-empty-message',!fresh?t('重新读取状态后可恢复画面。','The view can resume after status is verified.'):active?t('CLI 可能仍在运行，停止会话请使用上方按钮。','The CLI may still be running. Use the button above to stop its session.'):t('查找并选择设备，原版界面将在此处显示。','Find and select a device. The original interface will appear here.'));
   setText('ov-logs-label',t('详细日志','Detailed log'));renderLogs();syncFrame();renderFrameStatus();$('ov-focus').disabled=!frame;
  }
  async function refresh(){
   if(destroyed||loading)return loading;
   loading=(async()=>{try{const r=await fetch('/api/official-viz?theme='+theme(),{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error(t('无法读取可视化状态，请重试。','Visualization status is unavailable. Retry.'));const next=await r.json();if(!next||typeof next.active!=='boolean'||typeof next.supported!=='boolean'||typeof next.phase!=='string')throw Error(t('可视化状态不完整。','Incomplete visualization state.'));state=next;fresh=true;transportError='';}
    catch(error){fresh=false;transportError=error.message;}
    finally{loading=null;if(!destroyed)render();}})();return loading;
  }
  async function act(name,extra={}){
   if(pending&&name!=='official_viz_stop')return;const own=++sequence;pending=name;actionError='';render();
   try{const sr=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!sr.ok)throw Error(t('工作台服务未就绪','Workbench service is not ready'));const s=await sr.json();if(!s.csrf)throw Error(t('请重新读取工作台状态','Reload the workbench state'));const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name,...extra}),signal:AbortSignal.timeout(name==='official_viz_stop'?45000:15000)}),body=await r.json();if(!r.ok||body.ok===false)throw Error(body.error||t('操作未完成','Operation did not complete'));await refresh();}
   catch(error){if(own===sequence)actionError=error.message;}
   finally{if(own===sequence)pending='';if(!destroyed)render();}
  }
  function syncVisibility(){if(destroyed)return;const now=visible();if(!now){setFocus(false);detach();}if(now!==lastVisible){lastVisible=now;if(now)refresh();}syncFrame();}
  $('ov-scan').onclick=()=>act('official_viz_scan');$('ov-start').onclick=()=>act('official_viz_start',{serial:$('ov-device').value,theme:theme()});$('ov-stop').onclick=()=>act('official_viz_stop');$('ov-device').onchange=render;
  $('ov-focus').onclick=()=>setFocus(!focus);$('ov-reload').onclick=()=>{detach();viewerError='';syncFrame();render();};logs.addEventListener('toggle',renderLogs);
  const onEscape=e=>{if(e.key==='Escape'&&focus){setFocus(false);$('ov-focus').focus();}},onLanguage=()=>render(),onTheme=()=>{render();if(visible()||state?.active)refresh();};
  document.addEventListener('keydown',onEscape);document.addEventListener('visibilitychange',syncVisibility);window.addEventListener('workspace-page',syncVisibility);window.addEventListener('hashchange',syncVisibility);window.addEventListener('wuji-language',onLanguage);
  const observer=new MutationObserver(syncVisibility);for(let node=host;node;node=node.parentElement)observer.observe(node,{attributes:true,attributeFilter:['hidden','class','style','data-page','data-hub-tab']});
  const themeObserver=new MutationObserver(onTheme);themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  const sizeObserver=new ResizeObserver(syncVisibility);sizeObserver.observe(host);const timer=setInterval(()=>{syncVisibility();if(visible()||state?.active)refresh();},1000);
  const api={refresh,syncVisibility,destroy(){destroyed=true;clearInterval(timer);observer.disconnect();themeObserver.disconnect();sizeObserver.disconnect();document.removeEventListener('keydown',onEscape);document.removeEventListener('visibilitychange',syncVisibility);window.removeEventListener('workspace-page',syncVisibility);window.removeEventListener('hashchange',syncVisibility);window.removeEventListener('wuji-language',onLanguage);setFocus(false);detach();root.remove();mounted=null;}};
  mounted={host,api};render();syncVisibility();return api;
 }
 window.WujiOfficialViz={mount};
})();
