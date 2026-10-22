/* Two device entrances. Unconnected devices always open the shared directory. */
(() => {
  'use strict';
  const host=document.querySelector('.top-actions'),old=document.getElementById('shell-connection');if(!host||!old)return;
  document.querySelector('.connection-shortcut')?.remove();old.hidden=true;
  const group=document.createElement('div');group.className='top-connection';group.id='connection-toolbar';
  const status=document.createElement('button');status.type='button';status.className='top-connection-status';status.id='connection-details';status.setAttribute('aria-haspopup','dialog');status.setAttribute('aria-expanded','false');
  const dot=document.createElement('span');dot.className='connection-dot';dot.setAttribute('aria-hidden','true');
  const copy=document.createElement('span'),title=document.createElement('strong'),subtitle=document.createElement('small');copy.append(title,subtitle);status.append(dot,copy);
  const toggle=document.createElement('button');toggle.type='button';toggle.id='connection-toggle';toggle.className='top-connection-action';
  group.append(status,toggle);
  const gloveGroup=document.createElement('div');gloveGroup.className='top-connection top-glove-connection';gloveGroup.id='glove-connection-toolbar';
  const gloveStatus=document.createElement('button');gloveStatus.type='button';gloveStatus.className='top-connection-status';gloveStatus.id='glove-connection-details';
  const gloveDot=document.createElement('span');gloveDot.className='connection-dot';gloveDot.setAttribute('aria-hidden','true');
  const gloveCopy=document.createElement('span'),gloveTitle=document.createElement('strong'),gloveSubtitle=document.createElement('small');gloveCopy.append(gloveTitle,gloveSubtitle);gloveStatus.append(gloveDot,gloveCopy);
  const gloveToggle=document.createElement('button');gloveToggle.type='button';gloveToggle.className='top-connection-action';gloveToggle.id='glove-connection-toggle';gloveGroup.append(gloveStatus,gloveToggle);
  const cluster=document.createElement('div');cluster.className='top-device-cluster';cluster.append(group,gloveGroup);host.prepend(cluster);
  const pop=document.createElement('dialog');pop.id='connection-popover';pop.className='connection-popover';pop.setAttribute('aria-labelledby','connection-popover-title');
  const header=document.createElement('div');header.className='connection-popover-head';
  const heading=document.createElement('h3');heading.id='connection-popover-title';
  const dismiss=document.createElement('button');dismiss.type='button';dismiss.className='connection-dismiss';dismiss.onclick=()=>{close();status.focus();};
  header.append(heading,dismiss);
  const summary=document.createElement('p');summary.className='connection-summary';summary.setAttribute('role','status');
  const pendingNote=document.createElement('p');pendingNote.id='connection-pending-stop';pendingNote.className='connection-summary';pendingNote.setAttribute('role','status');pendingNote.hidden=true;
  const legacyNote=document.createElement('p');legacyNote.id='connection-legacy-session';legacyNote.className='connection-summary';legacyNote.hidden=true;
  const candidates=document.createElement('div');candidates.className='connection-candidates';
  const devices=document.createElement('div');devices.className='connection-device-summary';
  function deviceCard(id){const card=document.createElement('div');card.className='connection-device-card';card.id=id;const name=document.createElement('strong'),value=document.createElement('span');card.append(name,value);devices.append(card);return {card,name,value};}
  const handCard=deviceCard('connection-hand-summary'),gloveCard=deviceCard('connection-glove-summary');
  const gloveCli=document.createElement('small');gloveCli.id='connection-glove-cli';gloveCli.className='connection-cli-status';gloveCard.card.append(gloveCli);
  const routes=document.createElement('nav');routes.className='connection-routes';
  const routeSpecs=[['device','connection','设备','Devices','选择与接入设备','Choose and connect devices'],['visual','glove','遥操作','Teleoperation','选择手套与目标灵巧手','Choose a glove and target hand'],['official','glove','官方画面','Official view','查看手套原始数据','View original glove data'],['calibration','calibration','手套标定','Calibrate glove','个人档案与官方指导','Personal profile and official guidance'],['retarget','mapping','映射设置','Mapping settings','调整输出与求解参数','Adjust mapping and solver parameters']];
  const routeRows=routeSpecs.map(([tab,icon,label,en,detail,detailEn])=>{
    const row=window.WorkbenchActionRow.create({kind:'link',href:tab==='visual'?'#glove':'#connection',icon,label,detail,onActivate:()=>{close();window.WujiConnectionHub.show(tab);if(tab==='official')window.WujiOfficialViz?.showDeviceMode();else if(tab==='visual')document.getElementById('glove-title')?.focus({preventScroll:true});else document.getElementById('hub-tab-'+tab)?.focus();}});
    routes.append(row.element);return {row,label,en,detail,detailEn};
  });
  pop.append(header,devices,summary,pendingNote,legacyNote,candidates,routes);document.body.append(pop);
  let state=null,busy=false,online=false,key='',candidateRows=[];
  const text=(zh,en)=>document.documentElement.lang==='en'?en:zh;
  function pendingStops(){
    const rows=Array.isArray(state?.unconfirmed_devices)?state.unconfirmed_devices.filter(row=>row&&typeof row==='object').slice():[];
    const latest=new Map();
    for(const row of rows){
      if(!Number.isSafeInteger(row.session)||row.session<0)continue;
      const key=JSON.stringify([row.source,row.device_id]);latest.set(key,Math.max(latest.get(key)??-1,row.session));
    }
    const stops=rows.filter(row=>{
      if(!Number.isSafeInteger(row.session)||row.session<0)return true;
      const serial=row.source==='hand'?state?.device_id:row.source==='glove'?state?.glove?.feedback?.device_id:null;
      const active=row.source==='hand'?state?.hardware?.active:row.source==='glove'?state?.glove?.hardware?.active:null;
      return !(serial&&row.device_id===serial&&active===true&&row.session===latest.get(JSON.stringify([row.source,row.device_id])));
    });
    if(state?.glove?.hardware?.active===null&&!stops.some(row=>row.source==='glove'&&row.device_id===state.glove.feedback?.device_id))stops.push({source:'glove',device_id:state.glove.feedback?.device_id});
    return stops;
  }
  const serials=rows=>[...new Set(rows.map(row=>row.device_id||text('未识别设备','Unidentified device')))];
  function gloveStopNote(stops=pendingStops()){
    const rows=stops.filter(row=>row.source==='glove');if(!rows.length)return;
    const note=text('灵巧手停用待确认：','Hand disable unconfirmed: ')+serials(rows).join(' · ')+text('。重连对应设备后点击“停止实机”。','. Reconnect the matching device, then click Stop hand.');
    const command=document.getElementById('glove-command-status');if(command&&command.textContent!==note)command.textContent=note;
    const label=document.getElementById('glove-status');if(!state?.glove?.busy&&label&&label.textContent!==note)label.textContent=note;
  }
  // Legacy glove action handlers can repaint their cached text between polls.
  // Keep only these two labels consistent with the backend's unresolved rows.
  const gloveStatusObserver=new MutationObserver(()=>gloveStopNote());
  for(const id of ['glove-status','glove-command-status']){const node=document.getElementById(id);if(node)gloveStatusObserver.observe(node,{childList:true,characterData:true,subtree:true});}
  function close(){pop.close();status.setAttribute('aria-expanded','false');}
  function open(){if(!pop.open)pop.show();status.setAttribute('aria-expanded','true');render();}
  function routeInventory(kind){
    close();const directory=window.WujiDeviceInventory;
    if(typeof directory?.show==='function'){directory.show(kind);return true;}
    window.WujiConnectionHub?.show('device');
    document.querySelector('.di-filters [data-filter="'+kind+'"]')?.click();
    document.getElementById('di-scan')?.focus();return true;
  }
  function gloveOwner(){
    const s=state,g=s?.glove||{},v=s?.official_viz||{},device=v.device||{};
    if(calibrationSession())return 'calibration';
    if(online&&g.busy)return 'sdk';
    const serial=String(device.sn||device.serial||''),kind=['model','type','device_type','device_name'].map(k=>typeof device[k]==='string'?device[k]:'').join(' ');
    return online&&(/^WG/i.test(serial)||/glove/i.test(kind))&&(v.active===true||v.reserved||['starting','stopping'].includes(v.phase))?'official':null;
  }
  function calibrationSession(){const c=state?.calibration||{},run=c.run||c;return online&&typeof run.id==='string'&&!!run.id&&typeof run.serial==='string'&&!!run.serial&&(run.running===true||run.cleanup_required===true||c.cleanup_required===true)?run:null;}
  function routeGlove(){
    close();const owner=gloveOwner();
    if(owner==='calibration'){window.WujiConnectionHub?.show('calibration');return true;}
    if(owner==='official'){window.WujiConnectionHub?.show('official');window.WujiOfficialViz?.showDeviceMode();return true;}
    if(owner==='sdk'){window.WujiConnectionHub?.show('visual');return true;}
    return routeInventory('glove');
  }
  function pairedHand(){const g=state?.glove;return online&&g?.busy===true&&!!g.feedback?.device_id;}
  function render(){
    const s=state,connected=online&&s?.connection==='connected',connecting=online&&s?.connection==='connecting',fresh=connected&&!s.stale;
    const stops=pendingStops();
    const identity=s?.identified_device,identified=connected&&identity?.source==='sdk_identity'&&identity.serial===s.device_id&&identity.profile===s.device_profile?.id;
    const paired=pairedHand(),pair=s?.glove?.feedback,pairIdentity=pair?.identified_device,pairProfile=s?.glove?.profile;
    const verifiedPair=paired&&pair?.connection==='connected'&&pairIdentity?.source==='sdk_identity'&&pairIdentity.serial===pair.device_id&&pairIdentity.profile===pairProfile&&/^hand[12]_(left|right)$/.test(pairProfile||'');
    const handName=identified?'Hand '+(s.device_profile.generation==='hand1'?'1':'2')+' · '+text(s.device_profile.side==='left'?'左':'右',s.device_profile.side==='left'?'Left':'Right'):verifiedPair?'Hand '+(pairProfile.startsWith('hand1')?'1':'2')+' · '+text(pairProfile.endsWith('left')?'左':'右',pairProfile.endsWith('left')?'Left':'Right'):'Hand';
    title.textContent=handName;status.title=identified?s.device_id:verifiedPair?pair.device_id:'';
    subtitle.textContent=!online?text('软件服务离线','Service offline'):connecting?text('正在识别…','Discovering…'):connected?(fresh?text('已连接','Connected'):text('反馈已过期','Feedback stale')):s?.connection==='error'?text('连接未完成','Connection incomplete'):text('未连接','Not connected');
    if(paired&&!connected&&!connecting)subtitle.textContent=text('手套会话','Glove session');
    if(fresh&&Number.isFinite(s.metrics?.device_hz))subtitle.textContent+=' · '+Math.round(s.metrics.device_hz)+' Hz';
    if(stops.length)subtitle.textContent+=text(' · 停用待确认',' · Disable unconfirmed');
    group.dataset.state=fresh||verifiedPair&&!pair.stale?'connected':connecting?'connecting':connected||paired||s?.connection==='error'?'error':'disconnected';
    toggle.textContent=busy?text('请稍候','Wait'):connecting?text('取消','Cancel'):connected?(s.hardware?.active?text('停止并断开','End session'):text('断开','Disconnect')):paired?text('查看','View'):text('接入','Set up');
    toggle.dataset.compactLabel=busy?text('稍候','Wait'):connecting?text('取消','Cancel'):connected?text('断开','End'):paired?text('查看','View'):text('接入','Set up');
    toggle.disabled=busy||!online||((connected||connecting)&&!!s?.glove?.busy);
    toggle.title=connected?text('断开灵巧手；如有动作正在运行，沿用现有停止并确认流程。','Disconnect the hand; active motion follows the existing stop-and-confirm flow.'):connecting?text('取消本次灵巧手连接。','Cancel this hand connection.'):paired?text('查看拥有此灵巧手的手套会话；请在原会话中停止或断开。','View the glove session that owns this hand. Stop or disconnect in that session.'):text('前往设备目录选择灵巧手，不会自动连接。','Choose a hand in the device directory. This does not connect automatically.');
    pendingNote.hidden=!stops.length;pendingNote.textContent=stops.length?text('停用待确认：','Disable unconfirmed: ')+serials(stops).join(' · ')+text('。重连对应设备后，点击“停止实机”等待确认。','. Reconnect the matching device, then click Stop hand and wait for confirmation.'):'';
    legacyNote.hidden=!s?.official_studio?.reserved;legacyNote.textContent=s?.official_studio?.reserved?text('旧软件会话尚未结束，请先结束该会话后重试。','A legacy software session is still open. End that session before retrying.'):'';
    toggle.setAttribute('aria-label',toggle.textContent);
    heading.textContent=text('设备与连接','Devices & connections');dismiss.setAttribute('aria-label',text('关闭连接面板','Close connection panel'));dismiss.title=dismiss.getAttribute('aria-label');
    routes.setAttribute('aria-label',text('设备操作入口','Device destinations'));
    handCard.name.textContent=handName;handCard.value.textContent=subtitle.textContent+(identified?' · '+s.device_id:'');handCard.card.dataset.state=group.dataset.state;
    gloveCard.name.textContent='Wuji Glove';
    const glove=s?.glove||{};
    const gloveFresh=online&&glove.connection==='receiving'&&glove.stream?.fresh===true;
    gloveCard.value.textContent=text('SDK 预览：','SDK preview: ')+(!online?text('服务离线','Service offline'):glove.connection==='receiving'?(gloveFresh?text('正在接收','Receiving'):text('等待有效数据','Waiting for valid data')):glove.connection==='connecting'?text('正在连接','Connecting'):glove.connection==='ready'?text('已发现','Discovered'):glove.connection==='disconnected'||!glove.connection?text('未接入','Not connected'):text('连接未完成','Connection incomplete'));
    gloveCard.card.dataset.state=gloveFresh?'connected':glove.connection==='receiving'?'error':'disconnected';
    const viz=s?.official_viz||{},vizDevice=viz.device||{},vizSerial=String(vizDevice.sn||vizDevice.serial||''),vizKind=['model','type','device_type','device_name'].map(k=>typeof vizDevice[k]==='string'?vizDevice[k]:'').join(' ');
    const gloveSession=/^WG/i.test(vizSerial)||/glove/i.test(vizKind);
    const owner=gloveOwner(),gloveProfile=glove.profile||'',gloveSerial=typeof glove.glove_serial==='string'?glove.glove_serial:'',gloveRecord=window.WujiDeviceInventory?.snapshot()?.devices?.find(d=>(d.sn||d.serial)===gloveSerial&&d.kind==='glove');
    const calibration=calibrationSession(),side=owner==='calibration'?calibration.side:owner==='sdk'&&gloveSerial&&/^hand[12]_(left|right)$/.test(gloveProfile)?gloveProfile.split('_')[1]:owner==='official'?vizDevice.handedness||vizDevice.side:null;
    const generation=owner==='sdk'&&gloveRecord?.generation==='glove1'?' 1':owner==='official'&&/\bglove\s*1\b/i.test(vizKind)?' 1':'';
    const gloveName='Glove'+generation+(['left','right'].includes(side)?' · '+text(side==='left'?'左':'右',side==='left'?'Left':'Right'):'');
    gloveTitle.textContent=gloveName;gloveStatus.title=owner==='sdk'?gloveSerial:owner==='official'?vizSerial:text('打开手套设备目录','Open glove device directory');
    gloveSubtitle.textContent=!online?text('服务离线','Service offline'):owner==='sdk'?glove.connection==='receiving'?(gloveFresh?text('数据中','Streaming'):text('等待数据','Waiting for data')):glove.connection==='connecting'?text('准备中…','Preparing…'):glove.connection==='ready'?text('已发现','Discovered'):text('需要处理','Needs attention'):owner==='official'?viz.phase==='running'&&viz.active===true?text('画面运行','Viewer running'):viz.phase==='starting'?text('画面准备中','Viewer starting'):text('查看会话','View session'):text('未接入','Not connected');
    if(owner==='calibration'){gloveStatus.title=calibration.serial;gloveSubtitle.textContent=calibration.running===true?calibration.status==='starting'?text('标定准备中','Preparing calibration'):calibration.status==='solving'?text('正在生成手型','Creating model'):calibration.status==='cancelling'?text('正在结束标定','Ending calibration'):text('标定中','Calibrating'):text('会话待核对','Session needs verification');}
    gloveGroup.dataset.state=gloveFresh?'connected':owner==='sdk'&&glove.connection==='receiving'?'error':owner?'connecting':'disconnected';
    gloveToggle.textContent=owner?text('查看','View'):text('接入','Set up');gloveToggle.disabled=!online;
    gloveToggle.title=owner==='official'?text('查看官方画面会话；CLI 未报告数据新鲜度。','View the official Viewer session. The CLI does not report data freshness.'):owner==='sdk'?text('查看当前手套会话；停止与断开仍在原工作区。','View the current glove session. Stop and disconnect remain in its workspace.'):text('前往设备目录选择手套，不会自动接入或断开其他设备。','Choose a glove in the device directory. No device connects or disconnects automatically.');
    if(owner==='calibration')gloveToggle.title=text('查看正在进行的标定，或结束本会话。标定状态不代表实时数据流。','View calibration or end its session. Calibration state is not live-stream evidence.');
    gloveStatus.setAttribute('aria-label',gloveName+' · '+gloveSubtitle.textContent);gloveToggle.setAttribute('aria-label',(owner?text('查看手套会话','View glove session'):text('接入手套','Set up glove')));
    gloveCard.name.textContent=gloveName;
    const cliLabels={starting:['正在启动','Starting'],running:['CLI 运行中，数据新鲜度未报告','CLI running; data freshness not reported'],stopping:['正在停止','Stopping'],error:['需要处理，请查看官方画面','Needs attention; see the official view'],exited:['已退出','Exited']};
    gloveCli.textContent=text('官方手套画面：','Official glove view: ')+(!online||gloveSession&&viz.phase==='running'&&viz.active!==true?text('状态未核对','Status unverified'):gloveSession&&cliLabels[viz.phase]?text(...cliLabels[viz.phase]):text('未运行','Not running'));
    if(owner==='calibration')gloveCli.textContent=text('手型标定：','Hand-model calibration: ')+gloveSubtitle.textContent+' · '+calibration.serial;
    gloveCli.dataset.phase=online&&gloveSession&&typeof viz.phase==='string'?viz.phase:'unknown';
    const gloveStops=stops.filter(row=>row.source==='glove');
    if(gloveStops.length){
      gloveCard.value.textContent+=text(' · 停用待确认',' · Disable unconfirmed');gloveCard.card.dataset.state='error';
      gloveStopNote(stops);
    }
    for(const r of routeRows)r.row.setLabels({label:text(r.label,r.en),detail:text(r.detail,r.detailEn)});
    summary.textContent=s?.devices?.length?text('请选择要连接的灵巧手。','Select a hand to connect.'):connected?[s.device_profile?.[document.documentElement.lang==='en'?'en':'zh'],s.device_id].filter(Boolean).join(' · '):s?.connection==='error'?s.message:connecting?text('正在核对设备身份与反馈。','Checking device identity and feedback.'):'';
    summary.hidden=!summary.textContent;
    const next=JSON.stringify([s?.devices||[],document.documentElement.lang]);
    if(next!==key){key=next;candidateRows=(s?.devices||[]).map(d=>{const b=document.createElement('button');b.type='button';b.textContent=d.serial+' · '+d.generation+(d.side_hint?' · '+text(d.side_hint==='left'?'左手':'右手',d.side_hint):'');b.onclick=()=>{routeInventory('hand');window.WujiDeviceInventory?.select(d.serial);};return b;});candidates.replaceChildren(...candidateRows);}
    candidates.hidden=!candidateRows.length;
    candidateRows.forEach(b=>b.disabled=busy||connecting||connected);
  }
  async function run(operation,serial=''){
    if(operation==='connect')return routeInventory('hand');
    if(busy||!online)return false;busy=true;render();
    try{
      const api=window.WujiConnection;
      const ok=await api.disconnect();
      if(!ok)open();return ok;
    }finally{busy=false;state=window.WujiConnection.state();render();}
  }
  toggle.onclick=()=>!['connected','connecting'].includes(state?.connection)&&pairedHand()?routeGlove():run(['connected','connecting'].includes(state?.connection)?'disconnect':'connect');
  gloveStatus.onclick=gloveToggle.onclick=routeGlove;
  status.onclick=()=>pop.open?close():open();
  pop.addEventListener('cancel',()=>status.setAttribute('aria-expanded','false'));
  document.addEventListener('pointerdown',e=>{if(pop.open&&!pop.contains(e.target)&&!cluster.contains(e.target))close();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&pop.open){close();status.focus();}});
  window.addEventListener('console-state',e=>{state=e.detail;online=true;render();});
  window.addEventListener('console-offline',()=>{online=false;render();});
  window.addEventListener('wuji-language',render);
  // Fixed app-only test interface; the native dispatcher only allows open/closed/primary.
  window.WujiConnectionToolbar={open,close,primary:()=>!['connected','connecting'].includes(state?.connection)&&pairedHand()?routeGlove():run(['connected','connecting'].includes(state?.connection)?'disconnect':'connect'),routeHand:()=>routeInventory('hand'),routeGlove};
  state=window.WujiConnection?.state();online=!!state;render();
})();
