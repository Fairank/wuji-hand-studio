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
  const heading=document.createElement('h3');heading.id='connection-popover-title';
  const summary=document.createElement('p');summary.className='connection-summary';summary.setAttribute('role','status');
  const candidates=document.createElement('div');candidates.className='connection-candidates';
  const footer=document.createElement('div');footer.className='connection-footer';
  const settings=document.createElement('a');settings.href='#connection';settings.onclick=()=>close();
  const dismiss=document.createElement('button');dismiss.type='button';dismiss.onclick=()=>close();footer.append(settings,dismiss);
  const gloveBox=document.createElement('div');gloveBox.id='connection-glove-summary';
  const gloveStatus=document.createElement('p'),gloveLinks=document.createElement('div');gloveLinks.className='connection-glove-links';
  const gloveConnect=document.createElement('a'),gloveMapping=document.createElement('a');
  for(const [link,tab] of [[gloveConnect,'visual'],[gloveMapping,'retarget']]){link.href='#connection';link.onclick=event=>{event.preventDefault();close();window.WujiConnectionHub.show(tab);};gloveLinks.append(link);}
  gloveBox.append(gloveStatus,gloveLinks);pop.append(heading,summary,candidates,gloveBox,footer);document.body.append(pop);
  let state=null,busy=false,online=false,key='',candidateRows=[],announcedDevices='';
  const text=(zh,en)=>document.documentElement.lang==='en'?en:zh;
  function close(){pop.close();status.setAttribute('aria-expanded','false');}
  function open(){if(!pop.open)pop.show();status.setAttribute('aria-expanded','true');render();}
  function render(){
    const s=state,connected=online&&s?.connection==='connected',connecting=online&&s?.connection==='connecting',fresh=connected&&!s.stale;
    title.textContent=connected?(s.device_profile?.[document.documentElement.lang==='en'?'en':'zh']||text('机械手','Hand')):text('机械手','Hand');
    subtitle.textContent=!online?text('软件服务离线','Service offline'):connecting?text('正在识别…','Discovering…'):connected?(fresh?text('已连接','Connected'):text('反馈已过期','Feedback stale')):s?.connection==='error'?text('连接未完成','Connection incomplete'):text('未连接','Not connected');
    if(fresh&&Number.isFinite(s.metrics?.device_hz))subtitle.textContent+=' · '+Math.round(s.metrics.device_hz)+' Hz';
    group.dataset.state=fresh?'connected':connecting?'connecting':connected||s?.connection==='error'?'error':'disconnected';
    toggle.textContent=busy?text('请稍候','Please wait'):connecting?text('取消','Cancel'):connected?(s.hardware?.active?text('停止并断开','Stop & disconnect'):text('断开','Disconnect')):text('连接','Connect');
    toggle.disabled=busy||!online||!!s?.glove?.busy;
    toggle.setAttribute('aria-label',toggle.textContent+' '+text('机械手','hand'));
    heading.textContent=text('设备连接','Device connection');dismiss.textContent=text('收起','Done');settings.textContent=text('连接设置','Connection settings');
    const glove=s?.glove||{};
    gloveStatus.textContent=text('手套：','Glove: ')+(!online?text('服务离线','Service offline'):glove.connection==='disconnected'||!glove.connection?text('未连接','Not connected'):glove.message||glove.connection);
    gloveConnect.textContent=text('连接与遥操作','Connect & teleoperate');gloveMapping.textContent=text('调整映射','Adjust mapping');
    summary.textContent=s?.devices?.length?text('发现多只机械手，请选择要连接的一只。','Multiple hands found. Select one to connect.'):connected?[s.device_id,subtitle.textContent].filter(Boolean).join(' · '):s?.connection==='error'?s.message:connecting?text('正在检查设备身份与关节反馈。','Checking device identity and joint feedback.'):text('接好设备后，直接点击右上角连接。','Connect the device, then use Connect in the toolbar.');
    const next=JSON.stringify([s?.devices||[],document.documentElement.lang]);
    if(next!==key){key=next;candidateRows=(s?.devices||[]).map(d=>{const b=document.createElement('button');b.type='button';b.textContent=d.serial+' · '+d.generation+(d.side_hint?' · '+text(d.side_hint==='left'?'左手':'右手',d.side_hint):'');b.onclick=()=>run('connect',d.serial);return b;});candidates.replaceChildren(...candidateRows);}
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
