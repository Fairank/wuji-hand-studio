/* Shared discovery surface. Discovery never starts a device stream or motion. */
(()=>{
 'use strict';
 const host=document.getElementById('hub-device');if(!host)return;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const make=(tag,cls,parent)=>{const element=document.createElement(tag);if(cls)element.className=cls;if(parent)parent.append(element);return element;};
 const anchor=document.createComment('shared-device-inventory');host.prepend(anchor);
 const root=make('section','di-root');root.id='device-inventory';root.setAttribute('aria-labelledby','di-title');anchor.after(root);
 const heading=make('header','di-heading',root),headingCopy=make('div','',heading),title=make('h3','',headingCopy);title.id='di-title';
 const description=make('p','di-description',headingCopy),scanControls=make('div','di-scan-controls',heading),discoveryState=make('span','di-discovery-state',scanControls),scanButton=make('button','di-refresh',scanControls);discoveryState.id='di-discovery-state';discoveryState.setAttribute('role','status');discoveryState.setAttribute('aria-live','polite');scanButton.id='di-scan';scanButton.type='button';
 const scanIcon=make('span','di-refresh-icon',scanButton);scanIcon.setAttribute('aria-hidden','true');scanIcon.innerHTML='<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M15.9 6.9A6.25 6.25 0 1 0 16.1 12M15.9 6.9V2.8M15.9 6.9h-4.1" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"/></svg>';const scanText=make('span','di-refresh-text',scanButton);
 const stats=make('dl','di-stats',root),statRows=[['hand','left'],['hand','right'],['glove','left'],['glove','right']].map(([kind,side])=>{const row=make('div','',stats),value=make('dd','',row),label=make('dt','',row);return{kind,side,value,label};});
 const toolbar=make('div','di-list-toolbar',root),filters=make('div','di-filters',toolbar);filters.setAttribute('role','group');
 const filterButtons=['all','hand','glove'].map(kind=>{const button=make('button','',filters);button.type='button';button.dataset.filter=kind;button.onclick=()=>{filter=kind;render();};return button;});
 const freshness=make('span','di-freshness',toolbar);freshness.id='di-freshness';
 const notice=make('p','di-notice',root);notice.id='di-notice';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
 const list=make('div','di-list',root);list.id='di-devices';list.setAttribute('role','radiogroup');
 const empty=make('div','di-empty',root),emptyTitle=make('strong','',empty),emptyNote=make('p','',empty);
 const selectedArea=make('section','di-selected',root),selectionCopy=make('div','di-selection-copy',selectedArea),selectionTitle=make('strong','',selectionCopy),selectionNote=make('span','',selectionCopy),actions=make('div','di-actions',selectedArea);
 const buttons={};for(const mode of ['official','preview','hand','calibration']){const button=make('button',mode==='official'?'primary':'',actions);button.type='button';button.dataset.use=mode;button.onclick=()=>use(mode);buttons[mode]=button;}
 const more=make('details','di-more',actions),moreLabel=make('summary','',more),moreActions=make('div','di-more-actions',more);more.id='di-device-more';moreActions.id='di-maintenance-actions';moreActions.append(buttons.official);
 const useStatus=make('p','di-use-status',selectedArea);useStatus.setAttribute('role','status');useStatus.setAttribute('aria-live','polite');
 const advanced=make('details','di-advanced'),advancedSummary=make('summary','',advanced),advancedLabel=make('strong','',advancedSummary),advancedNote=make('span','',advancedSummary),advancedBody=make('div','di-advanced-body',advanced);
 advanced.id='di-workspace-settings';
 for(const element of [...host.children])if(element.matches('.cr-device-grid,.cr-next'))advancedBody.append(element);
 document.getElementById('settings-devices')?.append(advanced);const oldIntro=host.querySelector(':scope > .hub-intro');if(oldIntro)oldIntro.hidden=true;
 let inventory=null,previousDisplayRows=[],scanFocus='',online=false,loading=null,scanning=false,using=false,pendingMode='',filter='all',activeSerial='',useHandler=null,timer=0,lastRead=0,backoffUntil=0,lastErrorKey='',localError='',consumerError='',consoleState=null,destroyed=false,discoveryWanted=true,sessionBaseline=new Set(),sessionCount=0,pausedForSession=false,serviceRevision=0;
 const selectedByKind=new Map(),cards=new Map(),discoveryHolds=new Set();
 const getTime=value=>{if(typeof value==='number'&&Number.isFinite(value))return value<1e12?value*1000:value;if(typeof value==='string'){const parsed=Date.parse(value);return Number.isFinite(parsed)?parsed:0;}return 0;};
 const sessionOwner=()=>window.WujiDeviceUse?.current();
 const isOwned=row=>{const owner=sessionOwner();return !!owner&&owner.serial===(row?.sn||row?.serial)&&owner.kind===row?.kind;};
 const rows=()=>{const current=Array.isArray(inventory?.devices)?inventory.devices.filter(d=>d&&typeof(d.sn||d.serial)==='string'):[],observed=online&&inventory?.scanning===true&&!inventory.error&&!localError&&!current.length?previousDisplayRows:current;const visible=observed.filter(fresh),owner=sessionOwner();if(online&&owner&&!visible.some(row=>(row.sn||row.serial)===owner.serial))visible.push({sn:owner.serial,serial:owner.serial,kind:owner.kind,selectable:true,in_use:true,session_verified:true,...owner.kind==='hand'&&/^hand[12]_(left|right)$/.test(owner.profile||'')?{generation:owner.profile.split('_')[0],side:owner.profile.split('_')[1]}:{}});return visible;};
 const sn=row=>row?.sn||row?.serial||'';
 const fresh=row=>{const seen=getTime(row?.last_seen),ttl=Number(inventory?.ttl_s),age=Date.now()-seen;return online&&row?.stale!==true&&seen>0&&age>=0&&age<((Number.isFinite(ttl)&&ttl>0?ttl:30)*1000);};
 const usable=row=>!scanning&&inventory?.scanning!==true&&!!row&&['hand','glove'].includes(row.kind)&&row.selectable===true&&(fresh(row)||isOwned(row));
 const visible=()=>!document.hidden&&!root.closest('[hidden]')&&root.getClientRects().length>0&&getComputedStyle(root).visibility!=='hidden';
 const selected=()=>rows().find(row=>sn(row)===activeSerial)||null;
 const clone=row=>row?JSON.parse(JSON.stringify(row)):null;
 const setText=(element,value)=>{if(element.textContent!==value)element.textContent=value;};
 const sideLabel=row=>row.side==='left'?t('左','Left'):row.side==='right'?t('右','Right'):t('左右待确认','Side unknown');
 function deviceName(row){const kind=row.kind==='glove'?'Wuji Glove':row.kind==='hand'?'Wuji Hand':t('待识别设备','Unidentified device');const generation=row.generation==='hand2'?' 2':row.generation==='hand1'?' 1':row.generation==='glove1'?' 1':'';return kind+generation;}
 function brief(row){return deviceName(row)+' · '+sideLabel(row);}
 function ownerLabel(row){
  if(!online||!row)return '';const owner=sessionOwner();if(owner?.serial===sn(row))return owner.purpose==='teleoperation'?t('遥操作','Teleoperation'):owner.purpose==='cli_view'?'CLI':t('灵巧手反馈','Hand feedback');const serial=sn(row),state=consoleState||{},calibration=state.calibration||{},run=calibration.run||calibration;
  if(serial&&run.serial===serial&&typeof run.id==='string'&&run.id&&(run.running===true||run.cleanup_required===true||calibration.cleanup_required===true))return run.running===true?run.status==='starting'?t('标定准备中','Preparing calibration'):run.status==='solving'?t('正在生成手型','Creating model'):run.status==='cancelling'?t('正在结束标定','Ending calibration'):t('标定中','Calibrating'):t('会话待核对','Session unverified');
  const viewer=state.official_viz||{},device=viewer.reported_device||viewer.device;if(serial&&sn(device)===serial&&viewer.active===true&&viewer.phase==='running'&&viewer.session_id)return t('画面会话','Viewer session');
  return row.in_use===true?t('会话占用','Session in use'):'';
 }
 function informSelection(){const row=selected();window.dispatchEvent(new CustomEvent('device-inventory-selection',{detail:{device:clone(row),serial:sn(row),kind:row?.kind||null}}));}
 function select(serial){if(!serial){const changed=!!activeSerial;activeSerial='';selectedByKind.clear();consumerError='';render();if(changed)informSelection();return true;}const row=rows().find(d=>sn(d)===serial);if(!usable(row))return false;const changed=activeSerial!==serial;activeSerial=serial;selectedByKind.set(row.kind,serial);consumerError='';render();if(changed)informSelection();return true;}
 async function use(mode){
  const row=selected();if(using||!usable(row)||buttons[mode]?.disabled)return false;
  consumerError='';using=true;pendingMode=mode;render();
  try{
   if(typeof useHandler!=='function')throw Error(t('此工作区入口尚未就绪，请稍后重试。','This workspace entry is not ready. Retry shortly.'));
   window.dispatchEvent(new CustomEvent('device-inventory-use',{detail:{mode,device:clone(row)}}));
   await useHandler(mode,clone(row));return true;
  }catch(error){consumerError=error.message||t('操作未完成','The operation did not complete');return false;}
  finally{using=false;pendingMode='';render();}
 }
 function createCard(serial){
  const card=make('label','di-device'),radio=make('input','',card);radio.type='radio';radio.name='inventory-device';radio.value=serial;radio.onchange=()=>select(serial);
  const icon=make('span','di-device-icon',card);icon.setAttribute('aria-hidden','true');
  const copy=make('span','di-device-copy',card),name=make('strong','',copy),meta=make('span','di-device-meta',copy),identity=make('span','di-device-serial',copy),status=make('span','di-device-state',card);
  const value={card,radio,icon,name,meta,identity,status};cards.set(serial,value);return value;
 }
 // Ownership and fresh connection evidence are different. Stale feedback
 // keeps its owner; it must never silently resume discovery as a disconnect.
 function connectionSnapshot(){
  const owners=new Set(),success=new Set(),s=consoleState||{},g=s.glove||{},v=s.official_viz||{},identity=s.identified_device||{};
  const key=(serial,fallback)=>typeof serial==='string'&&serial?'device:'+serial:'session:'+fallback;
  const handSerial=s.device_id||identity.serial;
  if(s.connection&&s.connection!=='disconnected')owners.add(key(handSerial,'hand'));
  if(s.connection==='connected'&&s.stale===false&&identity.source==='sdk_identity'&&identity.serial&&identity.serial===s.device_id&&/^hand[12]_(left|right)$/.test(identity.profile||'')&&identity.profile===s.device_profile?.id)success.add(key(identity.serial,'hand'));
  if(g.busy===true||['connecting','receiving'].includes(g.connection))owners.add(key(g.glove_serial||g.device_id,'glove'));
  if(g.connection==='receiving'&&g.stream?.fresh===true&&typeof g.glove_serial==='string'&&g.glove_serial)success.add(key(g.glove_serial,'glove'));
  const officialSerial=v.device?.sn||v.device?.serial;
  const readOnlyScan=v.scanning===true&&v.active!==true&&v.reserved!==true&&!v.session_id&&!v.cleanup_required&&!v.error&&!['starting','running','stopping'].includes(v.phase);
  if(!readOnlyScan&&(v.active===true||v.reserved===true||v.busy===true||['starting','running','stopping'].includes(v.phase)))owners.add(key(officialSerial,'official'));
  if(v.active===true&&v.phase==='running'&&typeof officialSerial==='string'&&officialSerial)success.add(key(officialSerial,'official'));
  const calibration=s.calibration||{},run=calibration.run||calibration,calibrationOwned=run.running===true||run.cleanup_required===true||calibration.cleanup_required===true;
  if(calibrationOwned)owners.add(key(run.serial,'calibration'));
  // This is session ownership, never proof of streaming or a fresh sensor frame.
  if(run.running===true&&typeof run.id==='string'&&run.id&&typeof run.serial==='string'&&run.serial&&['collecting','solving','terminal'].includes(run.status))success.add(key(run.serial,'calibration'));
  if(online)for(const row of inventory?.devices||[]){
   const serial=sn(row);if(row?.in_use===true)owners.add(key(serial,'inventory'));
   if(!serial||!Array.isArray(row.observations))continue;
   if(row.observations.some(observation=>observation&&(observation.sn||observation.serial)===serial&&['sdk_identity','glove_session_identity'].includes(observation.source)&&observation.identity_verified===true&&observation.stale===false&&observation.in_use===true))success.add(key(serial,'inventory'));
  }
  for(const serial of success)owners.add(serial);
  return {owners,success,allGone:online&&!!consoleState&&!!inventory&&!scanning&&inventory.scanning!==true&&!inventory.error&&!(inventory.workspace_errors||[]).length&&s.connection==='disconnected'&&g.busy!==true&&v.active!==true&&!owners.size&&!(s.unconfirmed_devices||[]).length};
 }
 function syncDiscoveryIntent(){
  const sessions=connectionSnapshot();
  for(const serial of sessionBaseline)if(!sessions.owners.has(serial))sessionBaseline.delete(serial);
  if([...sessions.success].some(serial=>!sessionBaseline.has(serial))){discoveryWanted=false;pausedForSession=true;}
  for(const serial of sessions.success)sessionBaseline.add(serial);
  if(pausedForSession&&sessions.allGone){discoveryWanted=true;pausedForSession=false;sessionBaseline.clear();}
  sessionCount=sessions.owners.size;
 }
 async function findMore(){
  if(destroyed||scanning||using)return;
  // Existing sessions must not immediately pause the user's add-device intent.
  sessionBaseline=connectionSnapshot().owners;discoveryWanted=true;render();schedule(0);
  await refresh();if(destroyed||!visible()||!discoveryWanted)return;
  await scan(true);
 }
 function render(){
  syncDiscoveryIntent();
  const all=rows().slice().sort((a,b)=>(a.kind||'').localeCompare(b.kind||'')||(a.side||'').localeCompare(b.side||'')||sn(a).localeCompare(sn(b)));
  const busy=scanning||inventory?.scanning===true,globalBusy=window.WujiConnectionSwitch?.busy()===true,hasSelection=!!selected(),row=selected(),canUse=usable(row)&&!using&&!busy&&!globalBusy;
  if(busy&&document.activeElement?.name==='inventory-device'&&!scanFocus)scanFocus=document.activeElement.value;
  setText(title,root.closest('#page-home')?t('当前设备','Current devices'):t('发现你的设备','Your devices'));setText(description,t('自动查找手套与灵巧手，选中后再进入需要的功能。','Find gloves and hands automatically. Select one, then choose how to use it.'));
  const scanState=busy?'scanning':!online?'offline':discoveryHolds.size||!discoveryWanted?'paused':inventory?.scan_allowed!==true?'waiting':'ready';
  setText(discoveryState,busy?t('正在查找设备…','Finding devices…'):!online?t('服务未连接','Service offline'):!discoveryWanted?sessionCount?t('设备会话 · 查找已暂停','Device session · Discovery paused'):(consoleState?.unconfirmed_devices||[]).length?t('停用待确认 · 查找已暂停','Stop unconfirmed · Discovery paused'):t('会话状态待核对 · 查找已暂停','Session unverified · Discovery paused'):inventory?.scan_allowed!==true?t('等待可查找','Waiting to discover'):t('自动查找 · 每5秒','Auto-discovery · Every 5s'));
  if(discoveryHolds.size)setText(discoveryState,t('设备更新 · 查找已暂停','Device update · Discovery paused'));
  discoveryState.dataset.state=scanState;setText(scanText,busy?t('查找中…','Finding…'):!discoveryWanted?t('添加设备','Add device'):t('重新查找','Find devices'));scanButton.dataset.state=scanState;scanButton.setAttribute('aria-busy',String(busy));scanButton.setAttribute('aria-label',busy?t('正在查找设备','Finding devices'):!discoveryWanted?t('添加设备，恢复自动查找','Add device and resume automatic discovery'):t('重新查找设备','Find devices again'));scanButton.disabled=busy||using||!online||discoveryHolds.size>0||(discoveryWanted&&inventory?.scan_allowed!==true);
  scanButton.title=!online?t('正在读取设备状态','Reading device status'):!discoveryWanted?t('恢复自动查找，不影响当前设备会话','Resume automatic discovery without changing active sessions'):inventory?.scan_allowed!==true?(inventory?.blocked_reason||t('当前会话暂不允许查找','Discovery is unavailable during this session')):t('只读发现，不连接设备或启动动作','Discovery only; does not connect or move devices');
  for(const entry of statRows){setText(entry.value,String(all.filter(d=>d.kind===entry.kind&&d.side===entry.side).length));setText(entry.label,t((entry.side==='left'?'左':'右')+(entry.kind==='hand'?'灵巧手':'手套'),(entry.side==='left'?'Left ':'Right ')+(entry.kind==='hand'?'hand':'glove')));}
  filters.setAttribute('aria-label',t('筛选设备','Filter devices'));for(const button of filterButtons){setText(button,button.dataset.filter==='all'?t('全部','All'):button.dataset.filter==='hand'?t('灵巧手','Hands'):t('手套','Gloves'));button.setAttribute('aria-pressed',String(button.dataset.filter===filter));}
  const scanned=getTime(inventory?.scanned_at);setText(freshness,busy?t('正在更新列表','Updating devices'):scanned?t('上次查找 ','Last found ')+new Date(scanned).toLocaleTimeString(window.WujiLocale?.lang==='en'?'en-US':'zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):t('尚未查找','Not yet discovered'));
  const blocked=inventory?.scan_allowed===false?(inventory.blocked_reason||t('当前环境暂不能查找，请检查官方组件和控制环境。','Discovery is unavailable in the current environment. Check official components and the controller setup.')):'',error=localError||inventory?.error;
  setText(notice,error||blocked||(!online?t('正在读取设备状态…','Reading device status…'):busy?t('只读取设备身份，不会启动可视化、标定或灵巧手动作。','Reading device identity only. No Viewer, calibration or hand motion is started.'):''));notice.hidden=!notice.textContent;notice.dataset.error=String(!!error);
  const shown=all.filter(d=>filter==='all'||d.kind===filter);list.setAttribute('aria-label',t('已发现设备，按编号选择','Discovered devices; select by serial number'));
  const existing=new Set();for(let index=0;index<shown.length;index++){
   const item=shown[index],serial=sn(item);existing.add(serial);const itemCard=cards.get(serial)||createCard(serial),{card,radio,icon,name,meta,identity,status}=itemCard;
   radio.disabled=using||globalBusy||!usable(item);radio.checked=activeSerial===serial;card.dataset.selected=String(radio.checked);card.dataset.available=String(usable(item));card.dataset.kind=item.kind||'unknown';
   icon.hidden=true;icon.replaceChildren();icon.dataset.kind=item.kind||'unknown';
   const owner=ownerLabel(item);setText(name,deviceName(item));setText(meta,sideLabel(item)+(item.side_source==='serial_hint'?t(' · 身份提示，待设备核对',' · Identity hint; verify on connection'):item.side_source==='device'?t(' · 设备报告',' · Device reported'):'')+(owner&&!fresh(item)?t(' · 发现记录已过期',' · Discovery record expired'):''));setText(identity,serial);
   setText(status,owner||(busy?t('正在核对','Checking'):!fresh(item)?t('已过期','Stale'):item.kind==='unknown'?t('待识别','Unidentified'):item.selectable!==true?t('暂不可用','Unavailable'):t('已发现','Discovered')));status.dataset.state=owner||busy?'busy':!fresh(item)||item.kind==='unknown'?'muted':'ready';
   if(list.children[index]!==card)list.insertBefore(card,list.children[index]||null);
  }
  for(const [serial,value] of cards)if(!existing.has(serial)){value.card.remove();cards.delete(serial);}
  empty.hidden=shown.length>0;list.hidden=!shown.length;
  setText(emptyTitle,!online?t('正在读取设备','Reading device status'):busy?t('正在寻找设备','Finding your devices'):sessionCount?t('暂无新的设备发现','No new device discoveries'):blocked?t('查找暂不可用','Discovery unavailable'):filter!=='all'?t('此分类没有设备','No devices in this category'):t('尚未发现设备','No devices found yet'));
  setText(emptyNote,!online?t('服务就绪后会继续，不需要启动灵巧手。','The list resumes when the service is ready. No motor startup is needed.'):busy?'':sessionCount?t('已有会话仍在设备任务中。结束后继续自动查找。','Existing sessions remain in Device tasks. Automatic discovery resumes after they finish.'):blocked||error?t('按上方提示处理后，重新查找设备。','Resolve the issue above, then find devices again.'):t('接好电源与数据线后，工作台会自动查找；也可点击“重新查找”。','Connect power and the data cable. The Workbench finds devices automatically; you can also choose Find devices.'));emptyNote.hidden=!emptyNote.textContent;
  const selectedOwner=ownerLabel(row);setText(selectionTitle,hasSelection?brief(row):t('选择一个设备','Select a device'));setText(selectionNote,hasSelection?sn(row)+(selectedOwner?' · '+selectedOwner+(fresh(row)?'':t(' · 发现记录已过期，当前会话仍保留',' · Discovery record expired; current session retained')):fresh(row)?'':t(' · 已过期，请重新查找',' · Stale; find devices again'))+(row.generation==='hand1'?t(' · 官方画面支持 Hand 2 与 Glove；Hand 1 请使用灵巧手反馈。',' · Official view supports Hand 2 and Glove. Use hand feedback for Hand 1.'):''):t('设备身份、连接和动作是不同状态。发现设备后由你选择下一步。','Discovery, connection and motion are separate states. Choose the next step after finding a device.'));
  setText(moreLabel,t('更多','More'));setText(buttons.official,row?.kind==='glove'?t('连接手套','Connect glove'):t('原版画面','Official view'));setText(buttons.preview,t('遥操作','Teleoperation'));setText(buttons.hand,t('连接灵巧手','Connect hand'));setText(buttons.calibration,t('标定','Calibrate'));
  if(row?.kind==='glove'){if(buttons.official.parentElement!==actions)actions.prepend(buttons.official);}else if(buttons.official.parentElement!==moreActions)moreActions.prepend(buttons.official);
  buttons.hand.classList.toggle('primary',row?.kind==='hand');more.hidden=!hasSelection;
  buttons.official.classList.toggle('primary',row?.kind==='glove');
  buttons.official.hidden=hasSelection&&row.kind==='unknown';buttons.official.disabled=!canUse||row?.official_available!==true||row?.generation==='hand1';buttons.official.title=row?.generation==='hand1'?t('官方画面支持 Hand 2 与 Glove','Official view supports Hand 2 and Glove'):'';
  buttons.preview.hidden=!hasSelection||row.kind!=='glove';buttons.preview.disabled=!canUse;
  buttons.hand.hidden=!hasSelection||row.kind!=='hand';buttons.hand.disabled=!canUse;
  buttons.calibration.hidden=!hasSelection||row.kind!=='glove';buttons.calibration.disabled=!canUse;
  selectedArea.hidden=!hasSelection;
  const preparing={official:['正在准备官方画面…','Preparing official view…'],preview:['正在打开遥操作…','Opening teleoperation…'],hand:['正在连接所选灵巧手反馈…','Connecting the selected hand feedback…'],calibration:['正在打开所选手套的标定设置…','Opening calibration settings for the selected glove…']};
  setText(useStatus,consumerError||(using&&preparing[pendingMode]?t(...preparing[pendingMode]):''));useStatus.hidden=!useStatus.textContent;useStatus.dataset.error=String(!!consumerError);
  setText(advancedLabel,t('高级连接设置','Advanced connection settings'));setText(advancedNote,t('手动地址与兼容选项','Manual addresses and compatibility options'));advanced.hidden=!advancedBody.children.length;
  if(!busy&&scanFocus){const previous=cards.get(scanFocus)?.radio;if(previous&&!previous.disabled&&document.activeElement===document.body)previous.focus({preventScroll:true});scanFocus='';}
 }
 function schedule(delay=1000){clearTimeout(timer);if(!destroyed)timer=setTimeout(tick,delay);}
 async function refresh(){
  if(destroyed||loading)return loading;const own=serviceRevision;
  loading=(async()=>{
   try{const response=await fetch('/api/device-inventory',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!response.ok)throw Error(t('无法读取设备列表','Device list unavailable'));const value=await response.json();if(destroyed||own!==serviceRevision)return;if(!value||!Array.isArray(value.devices)||typeof value.scan_allowed!=='boolean')throw Error(t('设备列表不完整','Incomplete device list'));if(value.scanning!==true||value.devices.length||value.error)previousDisplayRows=value.error?[]:value.devices.filter(d=>d&&typeof(d.sn||d.serial)==='string');inventory=value;online=true;localError='';lastRead=Date.now();
    const errorKey=JSON.stringify([value.error,value.scanned_at]);if(value.error&&errorKey!==lastErrorKey){backoffUntil=Date.now()+5000;lastErrorKey=errorKey;}
    window.dispatchEvent(new CustomEvent('device-inventory-update',{detail:JSON.parse(JSON.stringify(value))}));
   }catch(error){if(!destroyed&&own===serviceRevision){online=false;localError=error.message;backoffUntil=Math.max(backoffUntil,Date.now()+5000);lastRead=Date.now();}}
   finally{loading=null;render();}
  })();return loading;
 }
 async function scan(manual=false){
  if(destroyed||discoveryHolds.size||scanning||using||loading||!online||inventory?.scanning||inventory?.scan_allowed!==true||(!manual&&Date.now()<backoffUntil))return false;
  const token=consoleState?.csrf||window.WujiConnection?.state()?.csrf;if(!token)return false;
  scanning=true;localError='';render();
  try{const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify({name:'device_inventory_scan'}),signal:AbortSignal.timeout(15000)}),value=await response.json();if(!response.ok||value.ok===false)throw Error(value.error||t('设备查找未完成','Discovery did not complete'));backoffUntil=0;await refresh();return true;}
  catch(error){localError=error.message;backoffUntil=Date.now()+5000;return false;}
  finally{scanning=false;render();schedule();}
 }
 async function tick(){
  if(destroyed)return;
  if(visible()){
   if(!inventory||Date.now()-lastRead>=(inventory.scanning?1000:5000))await refresh();
   if(destroyed||!visible()){schedule();return;}
   const elapsed=Date.now()-getTime(inventory?.scanned_at);
   if(discoveryWanted&&online&&(!inventory?.scanned_at||elapsed>=5000))await scan(false);
   render();
  }
  schedule();
 }
 scanButton.onclick=()=>discoveryWanted?scan(true):findMore();
 function visibilityChanged(){if(visible())schedule(0);}
 const observer=new MutationObserver(visibilityChanged);for(let element=host;element;element=element.parentElement)observer.observe(element,{attributes:true,attributeFilter:['hidden','class','style','data-page','data-hub-tab']});
 window.addEventListener('workspace-page',visibilityChanged);window.addEventListener('hashchange',visibilityChanged);document.addEventListener('visibilitychange',visibilityChanged);window.addEventListener('wuji-language',render);
 window.addEventListener('console-state',event=>{consoleState=event.detail;render();});window.addEventListener('device-use-update',render);
 window.addEventListener('console-offline',()=>{serviceRevision++;consoleState=null;online=false;localError=t('工作台服务已离线，连接状态未核对。','Workbench service offline. Connection state is unverified.');render();});
 window.WujiDeviceInventory={
  selection(kind){const serial=kind?selectedByKind.get(kind):activeSerial;const row=rows().find(item=>sn(item)===serial);return usable(row)?clone(row):null;},
  mount(target){if(target){if(root.parentElement!==target)target.append(root);}else if(root.previousSibling!==anchor)anchor.after(root);render();visibilityChanged();},
  select,refresh,findMore,scan:()=>scan(true),snapshot:()=>inventory?JSON.parse(JSON.stringify(inventory)):null,
  pauseDiscovery(reason){if(typeof reason!=='string'||!reason)return;const changed=!discoveryHolds.has(reason);discoveryHolds.add(reason);if(changed)render();},
  resumeDiscovery(reason){if(discoveryHolds.delete(reason)){render();schedule(0);}},
  show(kind='all'){filter=['hand','glove'].includes(kind)?kind:'all';location.hash='#home';window.WujiWorkspace.showPage();render();schedule(0);scanButton.focus();},
  setUseHandler(handler){if(typeof handler!=='function')throw TypeError('Device inventory requires a use handler');useHandler=handler;},
  showSettings(kind){location.hash='#settings';window.WujiWorkspace?.showPage();document.getElementById('settings-tab-devices')?.click();if(kind==='glove'){location.hash='#glove';window.WujiWorkspace?.showPage();document.getElementById('glove-device')?.focus({preventScroll:true});return;}advanced.open=true;advancedBody.querySelector('#connect')?.focus();advanced.scrollIntoView({block:'nearest'});},
  destroy(){destroyed=true;clearTimeout(timer);observer.disconnect();root.remove();}
 };
 render();visibilityChanged();
})();
