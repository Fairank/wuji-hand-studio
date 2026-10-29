(() => {
  'use strict';
  const english=()=>window.WujiLocale?window.WujiLocale.lang==='en':document.documentElement.lang==='en';
  const text=(zh,en)=>english()?en:zh;
  const fingers = ['拇指','食指','中指','无名指','小指'];
  const host = document.createElement('section');
  host.id = 'pose-console';
  host.innerHTML = `<div class="pose-heading"><div><h2>MuJoCo · 姿态同步</h2><p id="pose-message">正在载入原生手部模型</p></div><span id="render-rate">画面刷新 —</span></div>
    <div class="pose-screen" data-view-empty="true"><img id="pose-image" hidden alt="原生手部 MuJoCo 模型预览"><div id="pose-unavailable" role="status">正在载入原生手部模型</div><span id="pose-frame">正在准备画面</span></div>
    <div class="joint-legend"><strong>20个关节 · 点击定位</strong><span>灰：无反馈　橙：角度预览待核对　青：已核对同步</span></div>
    <div id="joint-grid"></div>
    <p class="rate-note">回报率按每个关节实际收到的数据统计。三维渲染目标20帧/秒，与设备反馈频率分开显示。</p>
    <details id="joint-detail" open><summary>每关节反馈与完整性</summary><p class="rate-note">缺项：收到整帧却没有该关节；序号缺口：包括传输中缺失的整帧。统计窗口为最近1秒。</p><div class="joint-table-scroll"><table><thead><tr><th>设备关节</th><th>收到 Hz</th><th>时间戳 Hz</th><th>新鲜度 ms</th><th>缺项 / 缺口</th><th>位置 rad</th><th>速度 rad/s</th><th>电流 A</th></tr></thead><tbody id="joint-detail-body"></tbody></table></div></details>
    <details id="mapping-editor"><summary>核对三维关节对应 · 只影响画面</summary><p>首次连接后，逐个核对设备编号对应哪个模型关节、运动方向和零位，再勾选保存。橙色为尚待核对的角度预览，灰色为缺少对应或反馈。此设置不向灵巧手发送动作。</p><p id="mapping-device">连接设备后可配置，映射按设备分别保存。</p><div class="joint-table-scroll"><table><thead><tr><th>模型关节</th><th>设备编号</th><th>方向</th><th>偏移 rad</th><th>已核对</th></tr></thead><tbody id="mapping-rows"></tbody></table></div><button id="save-mapping" type="button" disabled>保存显示映射</button><p id="mapping-notice" role="status"></p></details>`;
  const slot = document.getElementById('visual-slot') || document.querySelector('main') || document.body;
  slot.append(host);
  const byId = id => document.getElementById(id);
  let state = null, selected = null, mappingKey = '', mappingPending = false, decodedImage = null,serviceOffline=false;
  let hasImage=false,lastImageMode=null,lastImageScope=null,sourceScope=null,viewStatus={kind:'loading',message:'正在载入原生手部模型'};
  const sourceKind=mode=>mode==='glove_constrained_unavailable'?'glove_constrained':mode;
  const virtualTeleoperation=()=>location.hash==='#virtual-glove';
  const demonstrationProfile=()=>document.body.dataset.page==='library'?window.WujiDemonstration?.profile()||null:null;
  const demonstrationMode=()=>document.getElementById('mode-preview')?.getAttribute('aria-pressed')==='true'?'preview':'feedback';
  function stateScope(value){
    if(!value)return null;const g=value.glove||{},feedback=g.feedback||{};
    const desired=demonstrationProfile();if(desired){const mode=demonstrationMode(),run=value.demonstration_playback?.[desired];return JSON.stringify(['demonstration',desired,mode,run?.run_id,mode==='feedback'?[window.WujiDemonstration?.selectedHand?.(),value.device_id,value.device_profile?.id,value.connection]:null]);}
    return JSON.stringify([virtualTeleoperation(),value.view_source||'feedback',value.teleoperation_view_mode||'automatic',value.device_profile?.id||null,
      value.device_id||null,g.profile||null,g.session_id??null,g.glove_serial||null,g.user?.user_id??null,
      feedback.device_id||null,feedback.connection==='disconnected',g.model_provenance?.model_sha256||null,
      value.view_source==='glove'?g.connection==='disconnected':value.view_source!=='preview'&&value.connection==='disconnected']);
  }
  function payloadMatches(meta){
    if(!state)return false;const g=state.glove||{},proof=meta.collision_preview||{},kind=sourceKind(meta.mode);
    const desired=demonstrationProfile();if(desired){
      if(meta.ready===true&&meta.device_profile!==desired||meta.device_profile!=null&&meta.device_profile!==desired||kind?.startsWith('glove_')||kind==='virtual_idle')return false;
      const mode=demonstrationMode();if(meta.ready===true&&(meta.view_source!==mode||meta.demonstration_profile!==desired||meta.demonstration_display!==mode))return false;
      const selected=window.WujiDemonstration?.selectedHand?.();
      if(kind==='live'&&(mode!=='feedback'||state.connection!=='connected'||state.stale!==false||state.device_profile?.id!==desired||selected!==state.device_id||meta.device_id!==selected||meta.feedback?.device_id!==selected||meta.feedback?.stale!==false))return false;
      if(kind==='demo'){const run=state.demonstration_playback?.[desired];if(mode!=='preview'||run?.active!==true||typeof run.run_id!=='string'||!run.run_id||meta.playback?.run_id!==run.run_id||meta.playback?.display_profile!==desired||meta.playback?.active!==true||typeof run.running!=='boolean'||meta.playback?.running!==run.running)return false;}
      return mode!=='feedback'||kind!=='demo';
    }
    if(virtualTeleoperation()&&['glove_live','live','demo'].includes(kind))return false;
    if(kind?.startsWith('glove_')&&g.connection==='disconnected')return false;
    if(kind==='glove_live'&&(g.feedback?.connection==='disconnected'||meta.feedback?.connection==='disconnected'))return false;
    if(kind==='live'&&state.connection==='disconnected')return false;
    const matches=(value,current)=>value==null||value===current;
    return matches(meta.device_profile,state.device_profile?.id)&&matches(meta.view_source,virtualTeleoperation()?'glove':state.view_source)
      &&matches(proof.profile,g.profile)&&matches(proof.bridge_session_id,g.session_id)&&matches(proof.glove_serial,g.glove_serial)
      &&matches(meta.feedback?.device_id,kind==='glove_live'?g.feedback?.device_id:state.device_id);
  }
  function imageScope(meta){
    const proof=meta?.collision_preview||{};
    return JSON.stringify([sourceScope,sourceKind(meta?.mode),meta?.device_profile??null,meta?.view_source??null,
      proof.profile??null,proof.bridge_session_id??null,proof.glove_serial??null,meta?.feedback?.device_id??null]);
  }
  function discardImage(){hasImage=false;lastImageMode=null;lastImageScope=null;byId('pose-image').removeAttribute('src');}
  function syncSource(next){
    const key=stateScope(next);state=next;serviceOffline=false;if(key===sourceScope)return;
    sourceScope=key;viewRevision++;viewRequest?.abort();if(decodedImage)decodedImage.src='';
    discardImage();viewStatus={kind:'loading',message:text('正在准备画面','Preparing view'),meta:null};renderStatus();
  }
  const cells = [];
  for (let f=0;f<5;f++) {
    const column = document.createElement('div'); column.className='finger-column';
    const h=document.createElement('h3');h.textContent=fingers[f];column.append(h);
    for(let j=0;j<4;j++) {
      const index=f*4+j, button=document.createElement('button');button.type='button';button.className='joint-tile';
      const name=document.createElement('span');name.textContent=`${index+1} · J${j}`;
      const value=document.createElement('strong');value.textContent='—';
      const status=document.createElement('small');status.textContent='待核对';
      const bar=document.createElement('progress');bar.max=1100;bar.hidden=true;
      button.append(name,value,status,bar);button.setAttribute('aria-label',`${fingers[f]} 模型关节 J${j} 定位`);
      button.addEventListener('click',async()=>{try{await action({name:'display_selected',index});selected=index;cells.forEach((c,i)=>c.button.classList.toggle('selected',i===index));}catch(e){byId('mapping-notice').textContent=e.message;}});
      cells.push({button,value,status,bar});column.append(button);
    }
    byId('joint-grid').append(column);
  }
  async function action(body) {
    if(serviceOffline||!state?.csrf)throw new Error('本机控制服务未连接');
    const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});
    const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'操作未完成');
  }
  const fmt=(v,d=0)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
  function messageText(message){
    const value=String(message||'');if(!english())return value;
    const known={'正在载入原生手部模型':'Loading the native hand model','正在载入原生左手模型':'Loading the native left-hand model','正在载入原生右手模型':'Loading the native right-hand model',
      '三维画面暂不可用':'3D view unavailable','画面尚未就绪':'Waiting for a rendered frame','本机服务连接中断':'Local service disconnected',
      '画面请求超时，请检查本机控制服务':'View request timed out; check the local service','画面连接中断，请检查本机控制服务':'View connection interrupted; check the local service',
      '三维渲染已中断，画面不再代表当前姿态':'Rendering stopped; the image no longer represents the current pose',
      '模型预览 · 等待连接与关节对应核对':'Model preview · Waiting for connection and joint mapping',
      '实机同步 · 等待真实关节反馈':'Hand feedback · Waiting for measured joints','动作预览 · 请先选择并播放动作':'Action preview · Choose an action to play',
      '手套遥操作 · 实际灵巧手反馈':'Hand feedback','手套映射目标 · 非实测反馈':'Preview','手套遥操作 · 等待新鲜数据':'Waiting for fresh data','实机反馈已过期':'Hand feedback is stale'};
    if(known[value])return known[value];
    return value.replace(/^三维模型加载失败[：:]/,'Model loading failed: ')
      .replace(/^(\d+)\/20关节由实测反馈驱动$/,'$1/20 joints driven by measured feedback')
      .replace(/^(\d+)\/20实机角度预览 · 关节对应待核对$/,'$1/20 measured angle previews · Joint mapping unverified')
      .replace(/^仿真动作预览 · /,'Action preview · ').replace(/ · 播放中$/,' · Playing').replace(/ · 已暂停或完成$/,' · Paused or finished');
  }
  function renderStatus(){
    const meta=viewStatus.meta,image=byId('pose-image'),notice=byId('pose-unavailable'),screen=host.querySelector('.pose-screen');
    const ready=viewStatus.kind==='ready';image.hidden=!hasImage;image.classList.toggle('view-stale',hasImage&&!ready);
    screen.dataset.viewEmpty=String(!hasImage);screen.dataset.viewStatus=viewStatus.kind;
    const message=messageText(ready?meta.message:viewStatus.message);
    byId('pose-message').textContent=message;
    notice.hidden=ready;notice.textContent=viewStatus.kind==='unavailable'?text('画面暂不可用','View unavailable')+(message?' · '+message:''):message;
    byId('render-rate').textContent=ready?(english()?`MuJoCo rendering ${fmt(meta.render_hz,1)} fps`:`MuJoCo 渲染 ${fmt(meta.render_hz,1)} 帧/秒`):text('画面刷新 —','Rendering —');
    const source=meta?.mode||lastImageMode,constrained=source==='glove_constrained'||source==='glove_constrained_unavailable',glove=constrained||source==='glove_preview'||source==='glove_live';
    byId('pose-frame').title=constrained?text('显示姿态经过官方模型的几何约束；SDK 原始目标与实机指令未修改，不代表实物避碰。','The displayed pose uses geometry constraints from the official model. Original SDK targets and hardware commands are unchanged; this does not establish collision avoidance on hardware.') : '';
    byId('pose-frame').textContent=glove?(source==='glove_live'?text('实机反馈','Hand feedback'):constrained?text('映射预览 · 碰撞约束','Mapping preview · Collision constrained'):text('映射预览','Mapping preview'))+(ready?'':hasImage?text(' · 已过期',' · Stale'):viewStatus.kind==='loading'?text(' · 正在载入',' · Loading'):text(' · 暂不可用',' · Unavailable')):
      !ready?(hasImage?text('画面过期 · 不代表当前姿态','Stale image · Not the current pose'):viewStatus.kind==='loading'?text('正在准备画面','Preparing view'):text('尚无画面','No image received')):
      meta.mode==='demo'?text('动作预览 · 非实测反馈','Action preview · Not measured feedback'):meta.source_seq==null?text('模型预览 · 未接收实机姿态','Model preview · No measured hand pose'):
      english()?`Hand feedback frame ${meta.source_seq} · Chart and pose use the same frame`:`实机反馈帧 ${meta.source_seq} · 图表与姿态使用同一帧`;
    if(virtualTeleoperation()&&state?.glove?.stream?.fresh!==true)byId('pose-frame').textContent=text('虚拟手 · 未收到手套数据','Virtual hand · No glove data received');
  }
  function clearVisual(message,kind='error',meta=null) {
    // Stale frames belong to an exact source scope, never just a display mode.
    const nextMeta=meta||viewStatus.meta;
    if(!nextMeta||!payloadMatches(nextMeta)||lastImageScope!==imageScope(nextMeta))discardImage();
    viewStatus={kind,message,meta:nextMeta};renderStatus();
    for(const c of cells){c.value.textContent='—';c.status.textContent=text('无新反馈','No fresh feedback');c.bar.hidden=true;c.button.classList.remove('live','provisional');}
    byId('joint-detail-body').replaceChildren();
  }
  async function showFrame(payload, current) {
    if(!current())return;
    const meta=payload?.meta||{};
    if(!payloadMatches(meta)){discardImage();clearVisual(text('设备已切换，等待新画面','Device changed; waiting for a new view'),'unavailable',meta);return;}
    const scope=imageScope(meta);
    if(hasImage&&lastImageScope!==scope){discardImage();viewStatus={kind:'loading',message:text('正在准备画面','Preparing view'),meta};renderStatus();}
    if(meta.mode==='glove_live'&&(meta.feedback?.stale!==false||meta.source_seq==null)){clearVisual(meta.message||'实机反馈已过期','stale',meta);return;}
    if(meta.ready!==true||typeof payload.image!=='string'||!payload.image){clearVisual(meta.message||'画面尚未就绪',meta.mode==='loading'?'loading':meta.mode==='error'?'error':meta.mode==='stale'?'stale':'unavailable',meta);return;}
    const decoded=new Image();decodedImage=decoded;decoded.src=payload.image;
    try{await decoded.decode();}catch(error){throw new Error(text('画面图像无法解码：','View image could not be decoded: ')+String(error.message||error));}finally{if(decodedImage===decoded)decodedImage=null;}
    if(!current()||!payloadMatches(meta)||scope!==imageScope(meta))return;
    byId('pose-image').src=payload.image;
    hasImage=true;lastImageMode=meta.mode;lastImageScope=scope;viewStatus={kind:'ready',meta};renderStatus();
    const rates=new Map((meta.feedback?.joint_rates||[]).map(r=>[r.nid,r]));
    const readings=new Map((meta.feedback?.latest?.joints||[]).map(r=>[r.nid,r]));
    for(const j of meta.joints||[]) {
      const c=cells[j.index],r=rates.get(j.nid),live=['live','provisional'].includes(j.status);
      c.value.textContent=live?`${fmt(j.hz)} Hz`:'—';
      c.status.textContent=live?(english()?`ID ${j.nid}${j.status==='provisional'?' · Unverified':''}`:`编号 ${j.nid}${j.status==='provisional'?' · 待核对':''}`):({demo:text('编排姿态','Preview pose'),unmapped:text('待核对','Unverified'),stale:text('无新反馈','No fresh feedback'),out_of_range:text('角度超出模型范围','Angle outside model range')}[j.status]||text('待核对','Unverified'));
      c.button.classList.toggle('provisional',j.status==='provisional');
      c.button.classList.toggle('live',live);c.bar.hidden=!live||j.hz==null;if(!c.bar.hidden)c.bar.value=j.hz;
      c.button.title=english()?`${j.label}, ${c.status.textContent}, received ${c.value.textContent}`:`${j.label}，${c.status.textContent}，回报率 ${c.value.textContent}`;
    }
    const rows=[];
    for(const r of rates.values()) {
      const q=readings.get(r.nid),tr=document.createElement('tr');
      const values=[String(r.nid),fmt(r.host_hz),fmt(r.device_hz),fmt(r.age_ms,1),`${r.missing_in_received_frames??'—'} / ${r.missing_stream_slots??'—'}`,fmt(q?.position_rad,3),fmt(q?.velocity_rad_s,3),fmt(q?.effort_A,3)];
      for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}rows.push(tr);
    }
    if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=8;td.textContent=text('尚无实机数据','No measured hand data');tr.append(td);rows.push(tr);}
    byId('joint-detail-body').replaceChildren(...rows);
  }
  function mappingForm(next) {
    const nids=(next.joint_rates||[]).map(r=>r.nid).sort((a,b)=>a-b);
    const key=JSON.stringify([next.device_id,nids]);
    byId('save-mapping').disabled=next.connection!=='connected'||next.stale||mappingPending;
    if(key===mappingKey)return;mappingKey=key;
    byId('mapping-device').textContent=next.device_id?`已连接设备 · ${nids.length}个已观测关节`:'连接设备后可配置，映射按设备分别保存。';
    const same=next.mapping_device_id===next.device_id;
    const saved=new Map((same?next.mapping:[]).map(e=>[e.index,e]));
    const rows=[];
    for(let index=0;index<20;index++) {
      const tr=document.createElement('tr'),label=document.createElement('td');label.textContent=`${index+1} · ${fingers[Math.floor(index/4)]} J${index%4}`;tr.append(label);
      const select=document.createElement('select');select.dataset.field='nid';select.setAttribute('aria-label',label.textContent+' 设备编号');
      select.add(new Option('待核对',''));for(const nid of nids)select.add(new Option(String(nid),String(nid)));
      const sign=document.createElement('select');sign.dataset.field='sign';sign.setAttribute('aria-label',label.textContent+' 方向');sign.add(new Option('正向','1'));sign.add(new Option('反向','-1'));
      const offset=document.createElement('input');offset.type='number';offset.step='.01';offset.min='-7';offset.max='7';offset.value='0';offset.dataset.field='offset';offset.setAttribute('aria-label',label.textContent+' 偏移');
      const check=document.createElement('input');check.type='checkbox';check.dataset.field='verified';check.setAttribute('aria-label',label.textContent+' 已核对');
      const e=saved.get(index);if(e){select.value=String(e.nid);sign.value=String(e.sign);offset.value=String(e.offset);check.checked=e.verified===true;}
      for(const control of [select,sign,offset,check]){const td=document.createElement('td');td.append(control);tr.append(td);}rows.push(tr);
    }
    byId('mapping-rows').replaceChildren(...rows);
  }
  byId('save-mapping').addEventListener('click',async()=>{
    mappingPending=true;byId('save-mapping').disabled=true;
    try {
      const entries=[];
      [...byId('mapping-rows').rows].forEach((row,index)=>{
        const get=field=>row.querySelector(`[data-field="${field}"]`);
        if(!get('verified').checked)return;
        if(get('nid').value==='')throw new Error(`第${index+1}个模型关节尚未选择设备编号`);
        entries.push({index,nid:Number(get('nid').value),sign:Number(get('sign').value),offset:Number(get('offset').value),verified:true});
      });
      await action({name:'display_mapping',entries});byId('mapping-notice').textContent=`已保存${entries.length}个显示对应，没有发送灵巧手动作。`;
    }catch(e){byId('mapping-notice').textContent=e.message;}
    finally{mappingPending=false;}
  });
  window.addEventListener('console-state',event=>{syncSource(event.detail);mappingForm(state);});
  window.addEventListener('console-offline',()=>{serviceOffline=true;viewRevision++;viewRequest?.abort();if(decodedImage)decodedImage.src='';byId('save-mapping').disabled=true;clearVisual('本机服务连接中断');});
  window.addEventListener('wuji-language',renderStatus);
  renderStatus();
  // Display work only. Device feedback and all control keepalives use separate lanes.
  const embedded=new URLSearchParams(location.search).get('embedded')==='1';
  let parentVisible=true,viewVisible=false,viewTimer=0,viewRequest=null,viewPolling=false,viewRevision=0;
  function visible(){return parentVisible&&!document.hidden&&host.getClientRects().length>0&&getComputedStyle(host).visibility!=='hidden';}
  function scheduleView(delay=0){clearTimeout(viewTimer);if(!viewPolling&&visible())viewTimer=setTimeout(pollView,delay);}
  function syncView(){
    const next=visible();if(next===viewVisible)return;
    viewVisible=next;viewRevision++;clearTimeout(viewTimer);
    if(!next){viewRequest?.abort();if(decodedImage)decodedImage.src='';}
    else scheduleView();
  }
  async function pollView(){
    if(viewPolling||!visible()){syncView();return;}
    viewPolling=true;const revision=viewRevision,scope=sourceScope,controller=new AbortController();viewRequest=controller;
    const timeout=setTimeout(()=>controller.abort(),2500),current=()=>!serviceOffline&&revision===viewRevision&&scope===sourceScope&&visible();
    try{const desired=demonstrationProfile(),url=desired?'/api/view?profile='+encodeURIComponent(desired)+'&display='+demonstrationMode():virtualTeleoperation()?'/api/view?teleoperation=preview':'/api/view';const r=await fetch(url,{cache:'no-store',signal:controller.signal});if(!r.ok){let detail='';try{detail=(await r.json()).error||'';}catch{}throw Error('HTTP '+r.status+(detail?' · '+detail:''));}const payload=await r.json();if(current())await showFrame(payload,current);}
    catch(error){if(current())clearVisual(error.name==='AbortError'?'画面请求超时，请检查本机控制服务':String(error.message||'画面连接中断，请检查本机控制服务'));}
    finally{clearTimeout(timeout);if(viewRequest===controller)viewRequest=null;viewPolling=false;if(visible())scheduleView(revision===viewRevision?50:0);else syncView();}
  }
  const updateVisibility=()=>queueMicrotask(()=>{syncSource(state);syncView();});
  window.addEventListener('workspace-page',updateVisibility);window.addEventListener('workbench-session-visibility',updateVisibility);
  for(const event of ['demonstration-profile','demonstration-target','demonstration-mode'])window.addEventListener(event,updateVisibility);
  window.addEventListener('hashchange',updateVisibility);window.addEventListener('resize',updateVisibility);document.addEventListener('visibilitychange',syncView);
  new ResizeObserver(updateVisibility).observe(host);
  window.addEventListener('message',event=>{
    if(!embedded||event.source!==window.parent||!document.referrer||event.origin!==new URL(document.referrer).origin||event.data?.type!=='wb-visibility'||typeof event.data.visible!=='boolean')return;
    parentVisible=event.data.visible;syncView();
  });
  updateVisibility();
})();
