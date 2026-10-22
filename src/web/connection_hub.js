(()=>{
 'use strict';
 const $=id=>document.getElementById(id),ws=window.WujiWorkspace;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const body=$('page-connection')?.querySelector('.page-body');
 if(!body)return;
 const readingCanvas=document.querySelector('.page-area'),footer=readingCanvas?.querySelector(':scope > footer');
 if(footer)readingCanvas.append(footer);
 const old=[...body.children];
 const tabs=document.createElement('div');tabs.className='hub-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label',t('连接与校准分区','Connection sections'));
 const sections={};
 const definitions=[['device','设备连接','Devices'],['official','官方 CLI','Official CLI'],['retarget','映射调校','Mapping'],['visual','遥操作预览','Teleoperation']];
 for(const [key,zh,en] of definitions){
  const button=document.createElement('button');button.type='button';button.id='hub-tab-'+key;button.dataset.hubTab=key;button.setAttribute('role','tab');button.setAttribute('aria-controls','hub-'+key);button.textContent=t(zh,en);tabs.append(button);
  const section=document.createElement('section');section.className='hub-section';section.id='hub-'+key;section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby',button.id);section.hidden=true;sections[key]=section;
 }
 body.replaceChildren(tabs,...Object.values(sections));
 const officialViz=window.WujiOfficialViz?.mount(sections.official,{onCalibrate:()=>startCalibration(),onTaskChange:task=>{if(task==='calibration')refresh(true);},onDeviceChange:selectSharedDevice,onDiscovery:()=>refresh(true)});
 sections.device.append(...old);
 const glovePage=$('page-glove'),gloveBody=glovePage?.querySelector('.page-body');
 if(gloveBody)sections.visual.append(...[...gloveBody.children]);
 const retarget=$('wb-retarget-title')?.closest('.wb-card');if(retarget)sections.retarget.append(retarget);
 const oldGloveNav=document.querySelector('.directory [data-page="glove"]');if(oldGloveNav)oldGloveNav.hidden=true;
 const mappingLink=$('wb-glove-settings-link');if(mappingLink)mappingLink.href='#connection';
 mappingLink?.addEventListener('click',()=>select('retarget'));

 const intro=document.createElement('div');intro.className='hub-intro';
 intro.innerHTML='<p id="hub-intro-note"></p><div class="hub-badges"><span id="hub-hand-badge"></span><span id="hub-glove-badge"></span></div>';
 sections.device.prepend(intro);
 const visualIntro=document.createElement('div');visualIntro.className='hub-intro';
 visualIntro.innerHTML='<h3 id="hub-visual-title"></h3><p id="hub-visual-note"></p><div class="hub-legend"><span id="hub-real-label"></span><span id="hub-preview-label"></span></div>';
 sections.visual.prepend(visualIntro);
 const calib=document.createElement('div');calib.className='hub-calibration wb-card';
 calib.innerHTML=`<header class="hub-calibration-heading"><div><h3 id="hub-calib-title"></h3><p id="hub-calib-description"></p><span id="hub-calib-runtime"></span></div><div class="hub-calibration-tools"><span id="hub-calib-cli" class="hub-calib-availability"></span><button type="button" id="hub-calib-refresh"></button><a href="https://docs.wuji.tech/docs/en/wuji-cli/latest/hand-model-calibration/" target="_blank" rel="noopener" id="hub-calib-doc"></a></div></header>
 <div class="hub-calibration-workspace"><div class="hub-calibration-preparation">
 <div class="hub-calibration-setup"><div class="hub-calib-setup-caption"><strong id="hub-calib-setup-label"></strong><span id="hub-calib-setup-summary"></span></div><div class="hub-calibration-form">
 <section class="hub-calib-field hub-calib-device-field"><div class="hub-calib-field-heading"><label for="hub-calib-device"><span class="hub-calib-number" aria-hidden="true">1</span><span id="hub-glove-label"></span></label><button type="button" id="hub-calib-scan"></button></div><select id="hub-calib-device" aria-describedby="hub-calib-device-note"></select><p id="hub-calib-device-note" class="hub-field-note"></p></section>
 <section class="hub-calib-field hub-calib-profile-field"><div class="hub-calib-field-heading"><label for="hub-profile"><span class="hub-calib-number" aria-hidden="true">2</span><span id="hub-profile-label"></span></label><button type="button" id="hub-profile-new"></button></div><div class="hub-profile-current"><select id="hub-profile" aria-describedby="hub-current-profile"></select><button type="button" id="hub-profile-switch"></button></div><p id="hub-current-profile" class="hub-field-note"></p>
 <div class="hub-profile-create"><p id="hub-new-profile-title"></p><div class="hub-profile-current"><label><span id="hub-new-profile-label" class="hub-sr-only"></span><input id="hub-new-profile" maxlength="32" autocomplete="off" aria-describedby="hub-profile-name-hint"></label><button type="button" id="hub-profile-create"></button></div><p id="hub-profile-name-hint" class="hub-field-note"></p></div></section>
 <section class="hub-calib-field hub-calib-side-field"><label for="hub-side" class="hub-calib-field-heading"><span class="hub-calib-number" aria-hidden="true">3</span><span id="hub-side-label"></span></label><div class="hub-calib-side-row"><select id="hub-side" aria-describedby="hub-calib-models"><option value=""></option><option value="left"></option><option value="right"></option></select><p id="hub-calib-models" class="hub-field-note"></p></div><label class="hub-replace"><input type="checkbox" id="hub-replace"><span id="hub-replace-label"></span></label></section>
 </div></div>
 <div class="hub-calib-readiness" role="status" aria-live="polite"><div><strong id="hub-calib-ready-title"></strong><p id="hub-calib-ready-note"></p></div><button type="button" id="hub-calib-next"></button></div>
 <div class="wb-actions hub-calibration-actions"><button type="button" id="hub-calib-start" class="primary" aria-describedby="hub-calib-ready-note"></button><button type="button" id="hub-calib-cancel"></button></div>
 </div><div class="hub-calibration-session"><div id="hub-calibration-guide"></div></div></div>`;
 const calibrationPanel=document.createElement('section');calibrationPanel.id='hub-calibration';calibrationPanel.className='hub-cli-calibration';calibrationPanel.setAttribute('aria-label',t('官方标定工作区','Official calibration workspace'));
 const calibrationSummary=document.createElement('div');calibrationSummary.hidden=true;calibrationSummary.innerHTML='<span id="hub-cli-calibration-label"></span><span id="hub-cli-calibration-state"></span>';
 calibrationPanel.append(calibrationSummary,calib);officialViz?.attachCalibration(calibrationPanel);
 const deviceField=calib.querySelector('.hub-calib-device-field');deviceField.classList.add('hub-cli-device-context');
 const deviceSummary=document.createElement('strong');deviceSummary.id='hub-calib-selected-device';deviceField.querySelector('select').before(deviceSummary);
 $('hub-calib-device').hidden=true;$('hub-calib-device').tabIndex=-1;$('hub-calib-scan').hidden=true;
 const calibrationSession=calib.querySelector('.hub-calibration-session');calibrationSession.prepend(calib.querySelector('.hub-calib-readiness'),calib.querySelector('.hub-calibration-actions'));
 // The original CLI terminal needs its full width; preparation keeps its compact layout.
 calib.append($('hub-calibration-guide'));
 const endPreview=document.createElement('button');endPreview.id='hub-calib-end-preview';endPreview.type='button';endPreview.hidden=true;calib.querySelector('.hub-calibration-actions').append(endPreview);
 // Both tasks share the same toolbar and stage. Hidden legacy callbacks retain IDs.
 const sharedActions=sections.official.querySelector('.ov-display-actions');sharedActions?.prepend($('hub-calib-cancel'),$('hub-calib-refresh'),$('hub-calib-doc'));
 $('hub-calib-start').hidden=true;
 const previewOnly=s=>s?.glove?.busy===true&&s?.connection==='disconnected'&&s?.hardware?.active===false&&!s.glove.feedback?.device_id&&s.glove.hardware?.active!==true&&s.glove.hardware?.active!==null&&!(s.unconfirmed_devices||[]).length;
 const guideHost=$('hub-calibration-guide');
 const guide=window.OfficialCalibration?.mount(guideHost,{language:()=>window.WujiLocale?.lang||'zh'});
 window.WujiConnectionHub={show(key){location.hash='#connection';ws.showPage();select(key)}};
 let active='device',calibration=null,calibrationFresh=false,consoleState=null,busy=false,refreshPending=false,scanPending=false,calibrationReadAt=0,actionError='',refreshError='',wantedGlove='',newProfileOpen=false,nextAction=null,lastActiveProfile='',replacementContext='';
 function selectSharedDevice(serial,row){if(serial!==wantedGlove)$('hub-side').value=sideOf(row);wantedGlove=serial||'';renderCalibration();}
 window.addEventListener('device-inventory-selection',event=>{const row=event.detail?.device;if(row)selectSharedDevice(row.sn||row.serial,row);});
 window.addEventListener('device-inventory-update',()=>{renderCalibration();});
 window.addEventListener('official-viz-update',()=>renderCalibration());
 function calibrationVisible(){return active==='official'&&officialViz?.snapshot()?.task==='calibration';}
 function openCalibration(locate=false){officialViz?.setTask('calibration');window.WujiDeviceInventory?.refresh();refresh(true);if(locate)requestAnimationFrame(()=>{$('ov-device')?.scrollIntoView({block:'nearest',behavior:'instant'});$('ov-task-calibration')?.focus({preventScroll:true});});}
 function select(key){const prepare=key==='calibration';if(prepare)key='official';if(!sections[key])key='device';const changed=active!==key;active=key;for(const [name,section] of Object.entries(sections)){
   section.hidden=name!==key;const button=$('hub-tab-'+name);button.setAttribute('aria-selected',String(name===key));button.tabIndex=name===key?0:-1;
  }
  if(key==='visual'&&window.wujiFloating?.isFloating()!==true)$('glove-viewer-dock')?.append($('pose-console'));
  else if(window.wujiFloating?.isFloating()!==true)ws.viewerHome()?.append($('pose-console'));
  if(key==='official'){window.WujiDeviceInventory?.refresh();refresh(true);}
  document.body.dataset.hubTab=key;
  officialViz?.syncVisibility();
  // A different task opens at its first step. Polling and same-tab refreshes
  // leave scroll, focus and unfinished form values alone.
  if(changed&&location.hash==='#connection'){
   readingCanvas?.scrollTo({top:0,behavior:'instant'});
   if(window.innerWidth<=900)tabs.scrollIntoView({block:'start',behavior:'instant'});
  }
  if(prepare)openCalibration(true);
 }
 tabs.addEventListener('click',event=>{const key=event.target.closest('[data-hub-tab]')?.dataset.hubTab;if(key)select(key)});
 tabs.addEventListener('keydown',event=>{if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;const index=definitions.findIndex(x=>x[0]===active),next=event.key==='Home'?0:event.key==='End'?definitions.length-1:(index+(event.key==='ArrowRight'?1:-1)+definitions.length)%definitions.length;event.preventDefault();const key=definitions[next][0];select(key);$('hub-tab-'+key).focus()});
 window.addEventListener('hashchange',()=>{if(location.hash==='#glove'){location.hash='#connection';select('visual')}});
 window.addEventListener('workspace-page',event=>{if(event.detail==='connection')select(active)});
 if(location.hash==='#glove'){location.hash='#connection';active='visual'}

 function renderStatus(){const hand=consoleState?.connection,glove=consoleState?.glove?.connection;
  $('hub-hand-badge').textContent=hand==='connected'?(consoleState?.stale?t('反馈过期','Stale feedback'):t('已连接','Connected')):hand==='connecting'?t('连接中','Connecting'):hand==='disconnected'?t('未连接','Not connected'):hand==='error'?t('连接未完成','Connection incomplete'):t('状态未知','Unknown');
  $('hub-glove-badge').textContent=glove==='receiving'?t('接收中','Receiving'):glove==='ready'?t('已发现','Discovered'):glove==='connecting'?t('连接中','Connecting'):glove==='disconnected'?t('未连接','Not connected'):t('状态未知','Unknown');
  $('hub-hand-badge').dataset.state=hand==='connected'&&!consoleState?.stale?'ready':hand==='connecting'?'pending':hand==='error'?'error':'idle';
  $('hub-glove-badge').dataset.state=glove==='receiving'?'ready':glove==='connecting'?'pending':'idle';
 }
 const object=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
 const write=(id,value)=>{const element=$(id);if(element.textContent!==value)element.textContent=value;};
 function options(element,items,value){const signature=JSON.stringify(items);if(element.dataset.options!==signature){element.replaceChildren(...items.map(([label,key,disabled])=>{const option=new Option(label,key);option.disabled=disabled===true;return option;}));element.dataset.options=signature;}element.value=items.some(item=>item[1]===value)?value:'';}
 const milliseconds=value=>{if(typeof value==='number'&&Number.isFinite(value))return value<1e12?value*1000:value;if(typeof value==='string'){const parsed=Date.parse(value);return Number.isFinite(parsed)?parsed:0;}return 0;};
 function gloveFresh(row,c,strict=false){const now=Date.now(),expires=milliseconds(row?.expires_at),seen=milliseconds(row?.last_seen||row?.observed_at)||(!strict&&(milliseconds(c.metadata_checked_at)||calibrationReadAt));return !!row&&row.stale!==true&&row.selectable!==false&&seen>0&&seen<=now&&now-seen<30000&&(!expires||now<expires);}
 const sideOf=row=>{const side=String(row?.handedness||row?.side||'').toLowerCase().replace(/^handedness\./,'');return ['left','right'].includes(side)?side:'';};
 function sessionBlock(){
  if(!consoleState)return t('工作台服务离线，恢复后再操作。','Workbench service is offline. Wait for reconnection.');
  if((consoleState.unconfirmed_devices||[]).length)return t('有设备的停用结果尚未确认，请先在设备页面核对。','A device stop is unconfirmed. Review its session in Devices.');
  const viewer=officialViz?.snapshot();if(!viewer?.fresh)return t('官方画面会话状态尚未核对，请刷新官方状态后继续。','The official Viewer session state is unverified. Refresh it before continuing.');
  if(viewer.pending||viewer.state?.scanning)return t('官方任务正在处理，请等待本次操作完成后再标定。','An official task is in progress. Wait for it to finish before calibration.');
  if(viewer.state?.active||viewer.state?.reserved||['starting','running','stopping'].includes(viewer.state?.phase))return t('官方画面会话仍在使用设备。请先明确停止画面，再开始标定。','The official Viewer session still owns the device. Stop that view explicitly before calibration.');
  if(consoleState.connection!=='disconnected')return t('灵巧手反馈仍在连接，请先在原会话中断开。','Hand feedback is connected. Disconnect it in its owning session first.');
  if(consoleState.hardware?.active!==false)return t('灵巧手电机状态尚未确认，暂不能标定。','The hand motor state is unconfirmed. Calibration is unavailable.');
  if(consoleState.glove?.busy)return t('手套预览或遥操作正在使用设备，请先结束原会话。','Glove preview or teleoperation is using the device. End that session first.');
  if(consoleState.program?.active||consoleState.group?.active||consoleState.recording?.active||consoleState.parameter_sync?.busy)return t('当前工作区仍有任务运行，请先结束后再标定。','A workspace task is still running. Finish it before calibration.');
  return '';
 }
 async function refresh(force=false){if(busy||refreshPending||(!force&&!calibrationVisible()))return;refreshPending=true;renderCalibration();try{
  const response=await fetch('/api/calibration'+(force?'?refresh=1':''),{cache:'no-store',signal:AbortSignal.timeout(8000)});
  if(!response.ok)throw Error(t('无法读取官方校准状态','Calibration status unavailable'));
  const next=await response.json();if(!object(next)||typeof next.available!=='boolean'||!Array.isArray(next.users)||!Array.isArray(next.devices)||next.current!==null&&!object(next.current)||!object(next.run)||typeof next.run.running!=='boolean')throw Error(t('官方标定信息不完整，请刷新后重试。','Official calibration data is incomplete. Refresh and retry.'));
  calibration=next;calibrationReadAt=Date.now();calibrationFresh=true;refreshError='';
 }catch(error){calibrationFresh=false;refreshError=error.message;}finally{refreshPending=false;renderCalibration()}}
 async function action(name,extra={}){if(busy)return;busy=true;actionError='';renderCalibration();try{
   const token=consoleState?.csrf;if(!token)throw Error(t('工作台服务未就绪','Workbench service not ready'));
   const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({name,...extra})});const data=await response.json();if(!response.ok||data.ok===false)throw Error(data.error||'Action failed');
   if(object(data.calibration)){calibration=data.calibration;calibrationReadAt=Date.now();calibrationFresh=true;}if(name==='calibration_start')$('hub-replace').checked=false;
   if(name==='calibration_profile_create'||name==='calibration_profile_switch')$('hub-profile').value='';
   if(name==='calibration_profile_create'){newProfileOpen=false;$('hub-new-profile').value='';}
  }catch(error){actionError=error.message}finally{busy=false;renderCalibration();refresh(true)}}
 async function scanGloves(){
  if(scanPending||busy||calibration?.run?.running)return;scanPending=true;actionError='';renderCalibration();
  try{
   const inventory=window.WujiDeviceInventory;if(!inventory)throw Error(t('设备发现尚未就绪，请稍后重试。','Device discovery is not ready. Retry shortly.'));
   await inventory.refresh();const before=inventory.snapshot();if(before?.scan_allowed!==true)throw Error(before?.blocked_reason||t('当前会话暂不能扫描，请先结束设备任务。','Discovery is unavailable. Finish active device sessions first.'));
   const scanned=await inventory.scan();await inventory.refresh();let after=inventory.snapshot();
   if(!scanned)throw Error(after?.error||after?.blocked_reason||t('手套扫描尚未完成，请重试。','Glove discovery did not complete. Retry.'));
   const deadline=Date.now()+20000;
   while(after?.scanning===true){
    if(document.hidden||active!=='official'||location.hash!=='#connection')return;
    if(Date.now()>=deadline)throw Error(t('扫描尚未结束，请稍后重新查找。','Discovery is still running. Try again shortly.'));
    await new Promise(resolve=>setTimeout(resolve,300));
    if(document.hidden||active!=='official'||location.hash!=='#connection')return;
    await inventory.refresh();after=inventory.snapshot();
   }
   if(after?.error)throw Error(after.error);
  }catch(error){actionError=error.message}finally{scanPending=false;if(!document.hidden&&active==='official'&&location.hash==='#connection')await refresh(true);renderCalibration()}
 }
 function renderCalibration(){
  const c=object(calibration)?calibration:{},run=object(c.run)?c.run:{},human=c.human_tty===true,cleanup=c.cleanup_required===true,metadataFresh=calibrationFresh&&c.metadata_current===true,available=(human?calibrationFresh:metadataFresh)&&c.available===true;
  calibrationPanel.dataset.humanTty=String(human);calib.querySelector('.hub-calibration-preparation').hidden=c.human_tty!==false;calib.querySelector('.hub-calibration-heading').hidden=true;
  const users=Array.isArray(c.users)?c.users.filter(u=>object(u)&&typeof u.name==='string'):[],devices=Array.isArray(c.devices)?c.devices.filter(d=>object(d)&&typeof(d.sn||d.serial)==='string'):[];
  const current=object(c.current)?c.current:{},profile=typeof current.name==='string'?current.name:'',old=$('hub-profile').value;
  const defaultUser=!profile||profile.toLowerCase()==='default'||current.is_default===true||users.some(u=>u.name===profile&&u.is_default===true);
  options($('hub-profile'),[[t('选择标定用户','Choose calibration user'),''],...users.map(u=>[u.name+(u.is_default&&u.name.toLowerCase()!=='default'?t(' · 默认',' · Default'):''),u.name])],old&&old!==lastActiveProfile&&users.some(u=>u.name===old)?old:profile);
  lastActiveProfile=profile;
  const chosenProfile=$('hub-profile').value,pendingProfile=!!chosenProfile&&chosenProfile!==profile;
  $('hub-profile').closest('.hub-profile-current').hidden=defaultUser&&users.length===1&&!pendingProfile;calib.querySelector('.hub-calib-profile-field').dataset.default=String(defaultUser);
  const serial=officialViz?.selection()?.serial||'';
  options($('hub-calib-device'),[[devices.length?t('选择手套','Choose a glove'):t('尚未发现手套','No glove found'), ''],...devices.map(d=>{const side=sideOf(d);return[(d.sn||d.serial)+(side?' · '+t(side==='left'?'左手':'右手',side==='left'?'Left':'Right'):t(' · 左右待核对',' · Side unverified'))+(gloveFresh(d,c)?'':t(' · 已过期',' · Stale')),d.sn||d.serial,!gloveFresh(d,c)];})],serial);
  const shared=officialViz?.selection(),sharedGlove=shared?.device&&/glove/i.test([shared.device.kind,shared.device.device_type,shared.device.model].filter(Boolean).join(' '))?shared.device:null;
  const selected=devices.find(d=>(d.sn||d.serial)===serial)||(human?sharedGlove:null),side=$('hub-side').value,reportedSide=sideOf(selected),sideMismatch=!!reportedSide&&!!side&&reportedSide!==side;
  write('hub-calib-selected-device',selected?[serial,reportedSide?t(reportedSide==='left'?'左手':'右手',reportedSide==='left'?'Left':'Right'):t('左右待核对','Side unverified')].join(' · '):shared?.serial?shared.serial:t('尚未选择手套','No glove selected'));
  const model=object(current[side+'_hand'])?current[side+'_hand']:{},modelKnown=typeof model.calibrated==='boolean',hasModel=model.calibrated===true;
  const replacementKey=JSON.stringify([profile,chosenProfile,side,$('hub-calib-device').value,hasModel]);
  if(replacementContext!==replacementKey||!hasModel)$('hub-replace').checked=false;replacementContext=replacementKey;
  const inventory=window.WujiDeviceInventory?.snapshot(),block=sessionBlock(),inventoryBlock=inventory?.scan_allowed===false?(inventory.blocked_reason||t('设备会话暂不可用，请先核对连接状态。','Device sessions are unavailable. Review connection status.')):'';
  const mutating=busy||refreshPending||scanPending||run.running===true||cleanup;
  write('hub-calib-cli',refreshPending?t('正在核对…','Checking…'):!metadataFresh?t('信息待刷新','Refresh required'):c.available?c.calibration_supported?t('官方 CLI 已就绪','Official CLI ready'):t('仅可查看状态','Status only'):t('CLI 未就绪','CLI unavailable'));
  $('hub-calib-cli').dataset.state=available&&c.calibration_supported?'ready':'unknown';
  const runtime=typeof c.runtime==='string'?c.runtime:object(c.runtime)?[c.runtime.label||c.runtime.name,c.runtime.version].filter(x=>typeof x==='string').join(' · '):typeof c.backend==='string'?c.backend:'';
  write('hub-calib-runtime',runtime);$('hub-calib-runtime').hidden=!runtime;
  write('hub-calib-setup-summary',run.running?(run.user||profile||'')+' · '+(run.serial||''):t('设备、用户与侧别确认后开始','Confirm glove, user and hand side'));
  write('hub-current-profile',run.running&&run.source==='official_tty'?t('启动时档案：'+(run.user||profile||'—')+'。官方终端可切换用户，以终端内显示为准。','Profile at launch: '+(run.user||profile||'—')+'. The official terminal can switch users; its display is authoritative.'):!metadataFresh?t('用户信息尚未核对，请刷新。','User information is unverified. Refresh first.'):pendingProfile?t('已选择 '+chosenProfile+'，点击“使用此用户”后生效。','Selected '+chosenProfile+'. Choose Use this user to make it active.'):defaultUser?t('Default 无法保存个人标定，请先创建命名用户。','Default cannot save personal calibration. Create a named user first.'):t('标定将保存到 '+profile+' 的个人档案。','Calibration will be saved to '+profile+"’s profile."));
  const userName=$('hub-new-profile').value.trim(),nameValid=/^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}$/.test(userName),nameExists=users.some(u=>u.name===userName);
  calib.querySelector('.hub-profile-create').hidden=!(defaultUser||newProfileOpen);
  $('hub-profile-new').hidden=defaultUser;$('hub-profile-new').disabled=mutating||!available||!!block||!!inventoryBlock;
  write('hub-profile-name-hint',userName&&!nameValid?t('名称限 1–32 个字母、数字、_ 或 -，不能以 - 开头。','Use 1–32 letters, digits, _ or -. Do not start with -.'):nameExists?t('此用户已存在，请在上方选择并使用。','This user exists. Select and use it above.'):t('1–32 个字母、数字、_ 或 -','1–32 letters, digits, _ or -'));
  $('hub-new-profile').setAttribute('aria-invalid',String(!!userName&&(!nameValid||nameExists)));
  write('hub-calib-device-note',scanPending||inventory?.scanning?t('正在查找手套，只读取设备身份。','Finding gloves; reading device identity only.'):c.devices_error||(!devices.length?t('先接好电源与数据线，再点“扫描手套”。无需先连接预览。','Connect power and the data cable, then scan. No preview connection is needed.'):selected?reportedSide?t('设备报告：'+(reportedSide==='left'?'左手':'右手')+'。开始时还会核对设备身份。','Device reports '+(reportedSide==='left'?'left':'right')+'. Its identity is rechecked on start.'):t('扫描未报告左右，请在第 3 步选择；开始时由 SDK 核对实际身份。','Discovery did not report a side. Choose it in step 3; the SDK verifies the device on start.'):t('按序列号选择本次要标定的一只手套。','Select the glove for this calibration by serial number.')));
  write('hub-calib-models',!side?t('左右手分别保存标定','Left and right models are saved separately'):!metadataFresh||!modelKnown?t('已有标定尚未核对','Existing calibration is unverified'):hasModel?t('此侧已有个人标定','A personal model exists for this side'):t('此侧尚未标定','This side is not calibrated'));
  $('hub-replace').closest('label').hidden=!hasModel;
  for(const id of ['hub-profile','hub-new-profile','hub-calib-device','hub-side','hub-replace'])$(id).disabled=mutating;
  $('hub-profile-switch').hidden=!pendingProfile;$('hub-profile-switch').disabled=mutating||!available||!pendingProfile||!!block||!!inventoryBlock;
  $('hub-profile-create').disabled=mutating||!available||!nameValid||nameExists||!!block||!!inventoryBlock;
  $('hub-calib-refresh').disabled=busy||refreshPending||scanPending||run.running===true;
  $('hub-calib-scan').disabled=mutating||!!block||inventory?.scanning===true||inventory?.scan_allowed===false;
  write('hub-calib-scan',scanPending||inventory?.scanning?t('扫描中…','Scanning…'):t('扫描手套','Scan gloves'));
  $('hub-calib-scan').title=block||inventoryBlock||t('只查找设备，不开始预览或标定','Find devices only; does not start preview or calibration');
  endPreview.hidden=!previewOnly(consoleState);endPreview.disabled=busy||refreshPending||scanPending||run.running===true;write('hub-calib-end-preview',t('结束预览，准备标定','End preview to prepare calibration'));
  let reason='',nextLabel='',next=null;
  if(cleanup){reason=c.cleanup_error||t('上次官方终端的退出尚未核对。请先结束并核对该会话。','The previous official terminal exit is unverified. End and verify that session first.');nextLabel=t('核对会话','Review session');next=()=>$('hub-calib-cancel').focus();}
  else if(refreshPending||scanPending){reason=scanPending?t('正在查找设备，完成后重新核对准备状态。','Finding devices; preparation is checked again when discovery finishes.'):t('正在核对官方用户与标定信息。','Checking official user and calibration data.');}
  else if(!calibrationFresh||!human&&!metadataFresh||!c.available){reason=refreshError||(!human&&c.metadata_error)||c.error||t('官方标定信息尚未核对，请刷新后继续。','Official calibration state is unverified. Refresh to continue.');nextLabel=t('刷新状态','Refresh status');next=()=>refresh(true);}
  else if(c.calibration_supported!==true){reason=c.error||t('当前 CLI 环境无法启动标定，请检查连接设置或更新官方工具。','This CLI environment cannot start calibration. Review connection settings or update the official tools.');nextLabel=t('连接设置','Connection settings');next=()=>{window.WujiDeviceInventory?.show('glove');window.WujiDeviceInventory?.showSettings('glove');};}
  else if(block){reason=block;if(!officialViz?.snapshot()?.fresh){nextLabel=t('刷新官方状态','Refresh official status');next=async()=>{await officialViz?.refresh();await refresh(true);};}else if(officialViz?.snapshot()?.state?.active||officialViz?.snapshot()?.state?.reserved){nextLabel=t('查看画面会话','Review Viewer session');next=()=>$('ov-stop')?.focus();}else if(previewOnly(consoleState)){nextLabel=t('查看预览交接','Review preview handover');next=()=>endPreview.focus();}else{nextLabel=t('查看设备','Review devices');next=()=>window.WujiDeviceInventory?.show('all');}}
  else if(inventoryBlock){reason=inventoryBlock;nextLabel=t('刷新状态','Refresh status');next=async()=>{await window.WujiDeviceInventory?.refresh();await refresh(true);};}
  else if(!human&&pendingProfile){reason=t('所选用户尚未启用，请先点击“使用此用户”。','The selected user is not active. Choose Use this user first.');nextLabel=t('使用此用户','Use this user');next=()=>$('hub-profile-switch').click();}
  else if(!human&&defaultUser){reason=t('先创建一个命名用户，标定结果才能保存到你的档案。','Create a named user so calibration can be saved to your profile.');nextLabel=t('填写用户名称','Enter a user name');next=()=>$('hub-new-profile').focus();}
  else if(c.devices_error){reason=c.devices_error;nextLabel=t('重新扫描','Scan again');next=scanGloves;}
  else if(!selected){reason=shared?.serial?t('当前所选设备不是可标定的手套，或发现记录尚未核对。请在上方选择手套。','The selected device is not a calibration-ready glove, or its discovery is unverified. Choose a glove above.'):devices.length?t('请在上方选择本次要标定的手套。','Choose the glove to calibrate above.'):t('尚未发现可用手套。接好设备后扫描，列表会显示序列号和左右信息。','No usable glove was found. Connect it and scan to see its serial number and hand side.');nextLabel=devices.length||shared?.serial?t('选择手套','Choose a glove'):t('扫描手套','Scan gloves');next=devices.length||shared?.serial?()=>$('ov-device').focus():scanGloves;}
  else if(!gloveFresh(selected,c,human)){reason=t('所选手套的发现记录已过期或时间未核对，请重新扫描后再开始。','Discovery for this glove is stale or its time is unverified. Scan again before starting.');nextLabel=t('重新扫描','Scan again');next=scanGloves;}
  else if(!human&&!['left','right'].includes(side)){reason=t('请选择这只手套的左手或右手，开始时会再次核对。','Choose left or right for this glove. Its identity is checked again on start.');nextLabel=t('选择左右手','Choose hand side');next=()=>$('hub-side').focus();}
  else if(!human&&sideMismatch){reason=t('所选手套报告为'+(reportedSide==='left'?'左手':'右手')+'，与标定侧不一致。','The glove reports '+(reportedSide==='left'?'left':'right')+', which does not match the selected side.');nextLabel=t('检查标定侧','Review hand side');next=()=>$('hub-side').focus();}
  else if(!human&&!modelKnown){reason=t('此侧已有模型的状态未知，请刷新核对后再开始。','The existing model status for this side is unknown. Refresh before starting.');nextLabel=t('刷新状态','Refresh status');next=()=>refresh(true);}
  else if(!human&&hasModel&&!$('hub-replace').checked){reason=t('此侧已有个人标定。重新标定前，请确认覆盖。','A personal model exists for this side. Confirm replacement to recalibrate.');nextLabel=t('确认覆盖','Review replacement');next=()=>$('hub-replace').focus();}
  const ready=!reason&&!busy&&run.running!==true;
  calib.querySelector('.hub-calib-readiness').dataset.state=run.running?'running':ready?'ready':'blocked';
  write('hub-calib-ready-title',cleanup?t('等待会话清理核对','Session cleanup needs verification'):run.running?run.source==='official_tty'?t('官方终端会话运行中','Official terminal session running'):run.status==='starting'?t('正在启动并核对身份','Starting and verifying identity'):t('官方标定正在进行','Official calibration in progress'):busy?t('正在提交…','Submitting…'):ready?t('准备完成','Ready to calibrate'):t('开始前还需完成','Before you start'));
  write('hub-calib-ready-note',cleanup?reason:run.running?run.source==='official_tty'?t('请在下方官方终端按提示操作；结束会话不会被当作标定成功。','Follow the original terminal below. Ending the session does not establish calibration success.'):t('请跟随下方官方当前步骤，取消会结束本次采集。','Follow the current official step below. Cancel ends this capture.'):reason||actionError||t('佩戴好手套后开始。工作台会先核对身份，再进入官方采集流程。','Put on the glove and start. The Workbench verifies identity before official capture.'));
  nextAction=next;$('hub-calib-next').hidden=run.running===true||!nextLabel;write('hub-calib-next',nextLabel);$('hub-calib-next').disabled=mutating;
  $('hub-calib-start').disabled=!ready;$('hub-calib-cancel').disabled=busy||run.running!==true&&!cleanup;$('hub-calib-cancel').hidden=run.running!==true&&!cleanup;
  $('hub-calib-refresh').hidden=!calibrationVisible();$('hub-calib-doc').hidden=!calibrationVisible();
  write('hub-calib-start',t('开始标定','Start calibration'));write('hub-calib-cancel',cleanup||run.source==='official_tty'?t('结束并核对会话','End and verify session'):t('取消标定','Cancel calibration'));
  calib.dataset.running=String(run.running===true);calibrationPanel.dataset.running=String(run.running===true);
  write('hub-cli-calibration-state',cleanup?t('等待核对','Verification required'):run.running?t('进行中','In progress'):ready?t('已就绪','Ready'):t('准备设备与用户','Prepare device and user'));
  const status=cleanup?t('等待核对','Verification required'):run.running?run.status==='starting'?t('正在核对身份','Verifying identity'):run.status==='cancelling'?t('正在结束会话','Ending session'):t('官方会话运行中','Official session running'):run.id&&run.status==='session_ended'?t('官方会话已结束','Official session ended'):run.id&&run.status==='stopped'?t('会话已停止','Session stopped'):ready?t('标定已就绪','Ready to calibrate'):t('标定待就绪','Calibration unavailable');
  officialViz?.setCalibrationState({running:run.running===true,busy:busy||scanPending||cleanup,verified:calibrationFresh&&typeof run.running==='boolean',ready,human,runtime,status,reason:reason||actionError||(run.running?t('按下方官方终端提示操作。结束会话不会被当作标定成功。','Follow the official terminal below. Ending the session does not establish calibration success.'):human?t('由官方 CLI 完成用户、左右手与覆盖确认。','The official CLI handles profile, hand side and replacement confirmation.'):t('确认下方准备项后开始。','Review the preparation below, then start.')),terminal:run.source==='official_tty'&&!!run.id});
  guide?.render(c,{fresh:calibrationFresh,error:!consoleState?block:[actionError,refreshError,c.error].filter(Boolean).join(' · '),notice:reason||actionError||(human?t('点击上方“开始标定”，在这里直接使用完整官方流程。','Choose Start calibration above to use the complete official journey here.'):t('开始后，在这里查看官方当前步骤与采集反馈。','Start calibration to see the current official step and capture feedback here.'))});
 }
 $('hub-calib-refresh').onclick=()=>refresh(true);$('hub-calib-scan').onclick=scanGloves;$('hub-calib-next').onclick=()=>nextAction?.();
 $('hub-profile-new').onclick=()=>{newProfileOpen=!newProfileOpen;renderCalibration();if(newProfileOpen)$('hub-new-profile').focus();};
 $('hub-profile-switch').onclick=()=>action('calibration_profile_switch',{profile:$('hub-profile').value});$('hub-profile-create').onclick=()=>action('calibration_profile_create',{profile:$('hub-new-profile').value.trim()});
 function startCalibration(){renderCalibration();if($('hub-calib-start').disabled)return;const serial=officialViz?.selection()?.serial||'';action('calibration_start',calibration?.human_tty===true?{serial}:{side:$('hub-side').value,serial,replace:calibration?.current?.[$('hub-side').value+'_hand']?.calibrated===true&&$('hub-replace').checked});}
 $('hub-calib-start').onclick=startCalibration;$('hub-calib-cancel').onclick=()=>action('calibration_cancel');
 endPreview.onclick=async()=>{try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error(t('无法核对设备状态','Cannot verify device state'));const state=await response.json();consoleState=state;if(!previewOnly(state))throw Error(t('设备会话已改变，请在原工作区结束。','The device session changed. End it in its owning workspace.'));await action('glove_disconnect');}catch(error){actionError=error.message;renderCalibration();}};
 $('hub-calib-device').addEventListener('input',()=>{wantedGlove=$('hub-calib-device').value;const row=(Array.isArray(calibration?.devices)?calibration.devices:[]).find(d=>(d.sn||d.serial)===wantedGlove);$('hub-side').value=sideOf(row);window.WujiDeviceInventory?.select(wantedGlove);});
 for(const id of ['hub-new-profile','hub-side','hub-replace','hub-profile','hub-calib-device'])$(id).addEventListener('input',renderCalibration);
 window.addEventListener('console-state',event=>{consoleState=event.detail;renderStatus();renderCalibration()});
 window.addEventListener('console-offline',()=>{consoleState=null;actionError='';renderStatus();renderCalibration()});
 function labels(){for(const [key,zh,en] of definitions)$('hub-tab-'+key).textContent=t(zh,en);
  $('hub-intro-note').textContent=t('灵巧手与手套独立连接，也可以只使用其中一种。','Connect a hand, a glove, or both. Each connection is independent.');
  $('hub-visual-title').textContent=t('映射与灵巧手预览','Mapping and hand preview');
  $('hub-visual-note').textContent=t('此处显示工作台独有的映射目标与灵巧手反馈；手套骨架和触觉数据请使用官方可视化。','View workbench mapping targets and hand feedback here. Use the original visualization for glove skeleton and tactile data.');
  $('hub-real-label').textContent=t('三维：灵巧手 / 映射预览','3D: hand / mapped preview');$('hub-preview-label').textContent='';
  $('hub-calib-title').textContent=t('手型标定','Hand-model calibration');
  $('hub-calib-setup-label').textContent=t('准备标定','Calibration setup');
  $('hub-calib-description').textContent=t('选择手套与个人档案，跟随官方流程完成采集。','Choose a glove and personal profile, then follow official capture.');
  $('hub-profile-label').textContent=t('标定用户','Calibration user');$('hub-profile-new').textContent=t('新建用户','New user');$('hub-new-profile-title').textContent=t('创建你的个人标定档案','Create your personal calibration profile');$('hub-new-profile-label').textContent=t('用户名称','User name');$('hub-new-profile').placeholder=t('填写名称，例如 Alex','Name, e.g. Alex');$('hub-profile-switch').textContent=t('使用此用户','Use this user');$('hub-profile-create').textContent=t('创建并使用','Create and use');$('hub-calib-refresh').textContent=t('刷新','Refresh');$('hub-calib-refresh').setAttribute('aria-label',t('刷新官方标定状态','Refresh official calibration status'));
  $('hub-cli-calibration-label').textContent=t('手套标定','Glove calibration');$('hub-glove-label').textContent=t('本次使用的手套','Glove for this task');$('hub-side-label').textContent=t('确认左右手','Confirm hand side');$('hub-side').options[0].textContent=t('选择左右手','Choose hand side');$('hub-side').options[1].textContent=t('左手','Left');$('hub-side').options[2].textContent=t('右手','Right');$('hub-replace-label').textContent=t('覆盖这一侧已有标定','Replace existing model for this side');$('hub-calib-start').textContent=t('开始标定','Start calibration');$('hub-calib-cancel').textContent=t('取消标定','Cancel calibration');$('hub-calib-doc').textContent=t('说明 ↗','Guide ↗');$('hub-calib-doc').setAttribute('aria-label',t('官方手型标定说明（新窗口）','Official hand-model calibration guide (opens in a new window)'));$('hub-calib-doc').href='https://docs.wuji.tech/docs/'+(window.WujiLocale?.lang==='en'?'en':'zh')+'/wuji-cli/latest/hand-model-calibration/';
  renderStatus();renderCalibration();
 }
 window.addEventListener('wuji-language',labels);labels();select(active);setInterval(()=>{if(active==='official'&&calibration?.run?.running)refresh(false)},500);
})();
