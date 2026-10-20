/* One connection control, present on every page; feedback only until explicit playback. */
(() => {
  'use strict';
  const host=document.querySelector('.top-actions'),old=document.getElementById('shell-connection');if(!host||!old)return;
  document.querySelector('.connection-shortcut')?.remove();old.hidden=true;
  const group=document.createElement('div');group.className='top-connection';group.id='connection-toolbar';
  const status=document.createElement('button');status.type='button';status.className='top-connection-status';status.id='connection-details';status.setAttribute('aria-haspopup','dialog');status.setAttribute('aria-expanded','false');
  const dot=document.createElement('span');dot.className='connection-dot';dot.setAttribute('aria-hidden','true');
  const copy=document.createElement('span'),title=document.createElement('strong'),subtitle=document.createElement('small');copy.append(title,subtitle);status.append(dot,copy);
  const toggle=document.createElement('button');toggle.type='button';toggle.id='connection-toggle';toggle.className='top-connection-action';
  group.append(status,toggle);host.prepend(group);
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
  const routes=document.createElement('nav');routes.className='connection-routes';
  const routeSpecs=[['device','connection','连接设备','Connect devices','机械手与数据手套','Robot hands and data gloves'],['calibration','calibration','手套标定','Calibrate glove','用户档案与官方六姿势流程','User profiles and six official poses'],['retarget','mapping','手套映射','Glove mapping','调整关键点比例与关节输出','Tune landmark scales and joint output']];
  const routeRows=routeSpecs.map(([tab,icon,label,en,detail,detailEn])=>{
    const row=window.WorkbenchActionRow.create({kind:'link',href:'#connection',icon,label,detail,onActivate:()=>{close();window.WujiConnectionHub.show(tab);document.getElementById('hub-tab-'+tab)?.focus();}});
    routes.append(row.element);return {row,label,en,detail,detailEn};
  });
  pop.append(header,devices,summary,pendingNote,legacyNote,candidates,routes);document.body.append(pop);
  let state=null,busy=false,online=false,key='',candidateRows=[],announcedDevices='';
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
    const note=text('机械手停用待确认：','Hand disable unconfirmed: ')+serials(rows).join(' · ')+text('。重连对应设备后点击“停止实机”。','. Reconnect the matching device, then click Stop hand.');
    const command=document.getElementById('glove-command-status');if(command&&command.textContent!==note)command.textContent=note;
    const label=document.getElementById('glove-status');if(!state?.glove?.busy&&label&&label.textContent!==note)label.textContent=note;
  }
  // Legacy glove action handlers can repaint their cached text between polls.
  // Keep only these two labels consistent with the backend's unresolved rows.
  const gloveStatusObserver=new MutationObserver(()=>gloveStopNote());
  for(const id of ['glove-status','glove-command-status']){const node=document.getElementById(id);if(node)gloveStatusObserver.observe(node,{childList:true,characterData:true,subtree:true});}
  function close(){pop.close();status.setAttribute('aria-expanded','false');}
  function open(){if(!pop.open)pop.show();status.setAttribute('aria-expanded','true');render();}
  function render(){
    const s=state,connected=online&&s?.connection==='connected',connecting=online&&s?.connection==='connecting',fresh=connected&&!s.stale;
    const stops=pendingStops();
    const identity=s?.identified_device,identified=connected&&identity?.source==='sdk_identity'&&identity.serial===s.device_id&&identity.profile===s.device_profile?.id;
    const handName=identified?'Wuji Hand '+(s.device_profile.generation==='hand1'?'1':'2')+' · '+text(s.device_profile.side==='left'?'左手':'右手',s.device_profile.side==='left'?'Left':'Right'):'Wuji Hand';
    title.textContent=handName;status.title=identified?s.device_id:'';
    subtitle.textContent=!online?text('软件服务离线','Service offline'):connecting?text('正在识别…','Discovering…'):connected?(fresh?text('已连接','Connected'):text('反馈已过期','Feedback stale')):s?.connection==='error'?text('连接未完成','Connection incomplete'):text('未连接','Not connected');
    if(fresh&&Number.isFinite(s.metrics?.device_hz))subtitle.textContent+=' · '+Math.round(s.metrics.device_hz)+' Hz';
    if(stops.length)subtitle.textContent+=text(' · 停用待确认',' · Disable unconfirmed');
    group.dataset.state=fresh?'connected':connecting?'connecting':connected||s?.connection==='error'?'error':'disconnected';
    toggle.textContent=busy?text('请稍候','Please wait'):connecting?text('取消','Cancel'):connected?(s.hardware?.active?text('停止并断开','Stop & disconnect'):text('断开','Disconnect')):text('连接','Connect');
    toggle.disabled=busy||!online||!!s?.glove?.busy;
    toggle.title='';
    pendingNote.hidden=!stops.length;pendingNote.textContent=stops.length?text('停用待确认：','Disable unconfirmed: ')+serials(stops).join(' · ')+text('。重连对应设备后，点击“停止实机”等待确认。','. Reconnect the matching device, then click Stop hand and wait for confirmation.'):'';
    legacyNote.hidden=!s?.official_studio?.reserved;legacyNote.textContent=s?.official_studio?.reserved?text('旧软件会话尚未结束，请先结束该会话后重试。','A legacy software session is still open. End that session before retrying.'):'';
    toggle.setAttribute('aria-label',toggle.textContent+' '+text('机械手','hand'));
    heading.textContent=text('设备与连接','Devices & connections');dismiss.setAttribute('aria-label',text('关闭连接面板','Close connection panel'));dismiss.title=dismiss.getAttribute('aria-label');
    routes.setAttribute('aria-label',text('设备操作入口','Device destinations'));
    handCard.name.textContent=handName;handCard.value.textContent=subtitle.textContent+(identified?' · '+s.device_id:'');handCard.card.dataset.state=group.dataset.state;
    gloveCard.name.textContent='Wuji Glove';
    const glove=s?.glove||{};
    const gloveFresh=online&&glove.connection==='receiving'&&glove.stream?.fresh===true;
    gloveCard.value.textContent=!online?text('服务离线','Service offline'):glove.connection==='receiving'?(gloveFresh?text('正在接收','Receiving'):text('等待有效数据','Waiting for valid data')):glove.connection==='connecting'?text('正在连接','Connecting'):glove.connection==='ready'?text('已发现','Discovered'):glove.connection==='disconnected'||!glove.connection?text('未连接','Not connected'):text('连接未完成','Connection incomplete');
    gloveCard.card.dataset.state=gloveFresh?'connected':glove.connection==='receiving'?'error':'disconnected';
    const gloveStops=stops.filter(row=>row.source==='glove');
    if(gloveStops.length){
      gloveCard.value.textContent+=text(' · 停用待确认',' · Disable unconfirmed');gloveCard.card.dataset.state='error';
      gloveStopNote(stops);
    }
    for(const r of routeRows)r.row.setLabels({label:text(r.label,r.en),detail:text(r.detail,r.detailEn)});
    summary.textContent=s?.devices?.length?text('请选择要连接的机械手。','Select a hand to connect.'):connected?[s.device_profile?.[document.documentElement.lang==='en'?'en':'zh'],s.device_id].filter(Boolean).join(' · '):s?.connection==='error'?s.message:connecting?text('正在核对设备身份与反馈。','Checking device identity and feedback.'):'';
    summary.hidden=!summary.textContent;
    const next=JSON.stringify([s?.devices||[],document.documentElement.lang]);
    if(next!==key){key=next;candidateRows=(s?.devices||[]).map(d=>{const b=document.createElement('button');b.type='button';b.textContent=d.serial+' · '+d.generation+(d.side_hint?' · '+text(d.side_hint==='left'?'左手':'右手',d.side_hint):'');b.onclick=()=>run('connect',d.serial);return b;});candidates.replaceChildren(...candidateRows);}
    candidates.hidden=!candidateRows.length;
    candidateRows.forEach(b=>b.disabled=busy||connecting||connected);
  }
  async function run(operation,serial=''){
    if(busy||!online)return false;busy=true;render();
    try{
      if(operation==='connect'&&state?.devices?.length&&!serial){open();return false;}
      const api=window.WujiConnection;
      const ok=operation==='connect'?await api.connect(document.getElementById('device-address').value.trim(),serial):await api.disconnect();
      if(!ok)open();return ok;
    }finally{busy=false;state=window.WujiConnection.state();render();}
  }
  toggle.onclick=()=>run(['connected','connecting'].includes(state?.connection)?'disconnect':'connect');
  status.onclick=()=>pop.open?close():open();
  pop.addEventListener('cancel',()=>status.setAttribute('aria-expanded','false'));
  document.addEventListener('pointerdown',e=>{if(pop.open&&!pop.contains(e.target)&&!group.contains(e.target))close();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&pop.open){close();status.focus();}});
  window.addEventListener('console-state',e=>{state=e.detail;online=true;render();const devices=JSON.stringify(state.devices||[]);if(state.devices?.length&&devices!==announcedDevices)open();announcedDevices=devices;});
  window.addEventListener('console-offline',()=>{online=false;render();});
  window.addEventListener('wuji-language',render);
  // Fixed app-only test interface; the native dispatcher only allows open/closed/primary.
  window.WujiConnectionToolbar={open,close,primary:()=>run(['connected','connecting'].includes(state?.connection)?'disconnect':'connect')};
  state=window.WujiConnection?.state();online=!!state;render();
})();
