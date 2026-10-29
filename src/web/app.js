(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
  const rawUnconfirmed=value=>value?.raw_feedback_flush_confirmed===false||value?.raw_feedback_integrity==='unconfirmed';
  const rawWarning=()=>t('原始反馈完整性未确认，不能视为完整采集。','Raw feedback completeness is unconfirmed; this is not a verified complete recording.');
  const deviceChoice=document.createElement('select');deviceChoice.id='discovered-device';deviceChoice.hidden=true;
  deviceChoice.setAttribute('aria-label','选择发现的灵巧手 / Select discovered hand');el('device-address').after(deviceChoice);
  let deviceChoiceKey='';
  const labels={baseline:['无触碰基线','No-touch baseline'],thumb:['拇指轻触','Thumb touch'],index:['食指轻触','Index touch'],middle:['中指轻触','Middle touch'],ring:['无名指轻触','Ring touch'],little:['小指轻触','Little-finger touch'],withdrawal:['轻触后抽离','Touch and withdraw']};
  const reasons={completed:['时长完成','Duration reached'],user_stop:['手动停止','Stopped manually'],disconnected:['连接断开','Disconnected'],feedback_error:['反馈中断','Feedback interrupted']};
  const labelOf=value=>Object.hasOwn(labels,value)?t(...labels[value]):String(value??'');
  const reasonOf=value=>Object.hasOwn(reasons,value)?t(...reasons[value]):String(value??'');
  let state=null,pending=false,service=false,logsKey='',sessionsKey='',serviceRevision=0;
  // Reviewed adaptation of the delegated Fable UI: conservative state parsing
  // and a single polling lane with a post-action freshness barrier.
  let inFlight=false,pollTimer=0,pollId=0,waiter=null;
  function normalize(d){
    if(!d||typeof d!=='object'||!['disconnected','connecting','connected','error'].includes(d.connection))throw new Error('状态数据无效');
    const object=v=>v&&typeof v==='object'?v:{};
    const list=v=>Array.isArray(v)?v:[];
    const r=object(d.recording);
    return {...d,stale:d.stale!==false,metrics:object(d.metrics),recording:{...r,active:r.active===true},
      sessions:list(d.sessions),log:list(d.log),joint_rates:list(d.joint_rates),mapping:list(d.mapping),
      csrf:typeof d.csrf==='string'?d.csrf:''};
  }
  const fmt=(v,d=0)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
  function renderCapture(rec){
    const recording=t(`正在采集 ${labelOf(rec.label)} · ${fmt(rec.elapsed_s,1)} / ${rec.seconds} 秒 · ${rec.frames} 帧`,`Recording ${labelOf(rec.label)} · ${fmt(rec.elapsed_s,1)} / ${rec.seconds} s · ${rec.frames} frames`);
    el('record-progress').textContent=(rec.summary_pending===true?t('采集已停止，录制摘要尚未保存。检查存储后点击“重试保存”。','Capture stopped. The recording summary is not saved. Check storage, then choose Retry save.'):rec.active?recording:t('未在采集 · 可选择任务和时长','Not recording · Choose a task and duration'))+(rawUnconfirmed(rec)?' '+rawWarning():'');
    for(const option of el('annotation').options)if(Object.hasOwn(labels,option.value))option.textContent=labelOf(option.value);
    for(const option of el('duration').options)option.textContent=option.value+' '+t('秒','s');
  }
  function renderSessions(sessions){
    const key=JSON.stringify([window.WujiLocale?.lang,sessions]);if(key===sessionsKey)return;sessionsKey=key;
    const rows=sessions.map(item=>{const row=document.createElement('div');row.className='session-row';const p=document.createElement('p'),storage=item.workbench_storage,unsaved=storage?.saved===false,memory=storage?.memory_available===true;
      const saving=unsaved?' '+(memory?t('报告未保存到本机，可从内存导出。','Report not saved locally; export the in-memory report.'):t('报告未保存到本机，内存副本不可用。','Report not saved locally; the in-memory copy is unavailable.')):'';
      p.textContent=t(`${labelOf(item.label)} · ${item.frames} 帧 · ${fmt(item.seconds,1)} 秒 · ${reasonOf(item.reason)}`,`${labelOf(item.label)} · ${item.frames} frames · ${fmt(item.seconds,1)} s · ${reasonOf(item.reason)}`)+(rawUnconfirmed(item)?' '+rawWarning():'')+saving;
      const a=document.createElement('a');a.href='/api/report?id='+encodeURIComponent(item.id);a.textContent=t('导出报告','Export report');a.download='hand-report.json';a.hidden=unsaved&&!memory;row.append(p,a);return row;});el('sessions').replaceChildren(...rows);if(!rows.length)el('sessions').textContent=t('尚无采集记录','No capture records yet');
  }
  function controls(){
    const connected=service&&state?.connection==='connected',connecting=service&&state?.connection==='connecting';
    el('connect').disabled=!service||pending||connected||connecting;
    el('disconnect').disabled=!service||pending||(!connected&&!connecting&&state?.connection!=='error');
    el('record').disabled=!connected||state.stale||state.recording.active||state.recording.summary_pending===true||pending;
    el('stop').disabled=!service||!state?.recording.active&&state?.recording.summary_pending!==true||pending;
    el('stop').textContent=state?.recording.summary_pending===true?t('重试保存','Retry save'):(window.WujiLocale?.text?.('stop')||'停止采集');
    el('device-address').disabled=connected||connecting||pending;
    el('annotation').disabled=!!state?.recording.active||pending;
    el('duration').disabled=!!state?.recording.active||pending;
  }
  function showError(text){el('service-error').textContent=text;el('service-error').hidden=!text;}
  function offline(){serviceRevision++;service=false;state=null;controls();el('connection-state').textContent='本机服务未连接';el('connection-message').textContent='请通过“打开手部工作台”重新打开本机服务。';for(const id of ['joint-count','device-hz','host-hz','data-age'])el(id).textContent='—';el('record-progress').textContent=t('采集状态未知，请恢复连接后核对','Recording state is unknown. Reconnect to verify it.');window.dispatchEvent(new Event('console-offline'));}
  function render(next){
    if(state?.csrf&&next.csrf&&state.csrf!==next.csrf)offline();
    state=next;service=true;
    const devices=next.devices||[],key=JSON.stringify(devices);
    if(key!==deviceChoiceKey){deviceChoiceKey=key;deviceChoice.replaceChildren(new Option('选择设备 / Select device',''),...devices.map(d=>new Option(d.serial+' · '+d.generation+(d.side_hint?' · '+d.side_hint:''),d.serial)));}
    deviceChoice.hidden=!devices.length;
    deviceChoice.disabled=pending||next.connection==='connecting'||next.connection==='connected';
    el('connection-state').textContent=({disconnected:'未连接',connecting:'连接中',connected:next.stale?'反馈过期':'设备已连接',error:'连接未完成'})[next.connection]||'状态未知';
    el('connection-message').textContent=next.message;
    const fresh=next.connection==='connected'&&!next.stale;
    el('joint-count').textContent=fresh?`${next.latest?.joints?.length??0} / 20`:'—';
    el('device-hz').textContent=fresh?`${fmt(next.metrics.device_hz)} Hz`:'—';
    el('host-hz').textContent=fresh?`${fmt(next.metrics.host_hz)} Hz`:'—';
    el('data-age').textContent=next.metrics.age_ms==null?'—':`${fmt(next.metrics.age_ms)} ms`;
    const rec=next.recording;
    renderCapture(rec);
    const lk=JSON.stringify(next.log);
    if(lk!==logsKey){logsKey=lk;const rows=next.log.slice().reverse().map(item=>{const li=document.createElement('li');li.textContent=`${item.time}　${item.text}`;return li;});el('activity').replaceChildren(...rows);}
    renderSessions(next.sessions);
    controls();window.dispatchEvent(new CustomEvent('console-state',{detail:next}));
  }
  function freshState(){return new Promise(resolve=>{
    if(!waiter)waiter={after:pollId,resolves:[]};
    else waiter.after=Math.max(waiter.after,pollId);
    waiter.resolves.push(resolve);if(!inFlight)poll();
  });}
  async function action(body){
    if(pending||!service)return false;const own=serviceRevision,token=state.csrf;pending=true;controls();showError('');let succeeded=false;
    try{if(body.name==='connect'&&window.WujiNetwork)body.address=await window.WujiNetwork.prepare(body.address);if(own!==serviceRevision||!service||state?.csrf!==token)return false;const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});const result=await response.json();if(own!==serviceRevision)return false;if(!response.ok||!result.ok)throw new Error(result.error||'操作未完成');succeeded=true;}
    catch(error){if(own===serviceRevision)showError(error.message||'操作未完成');}
    finally{await freshState();pending=false;controls();}
    return succeeded;
  }
  // Shared by the connection page and the always-visible toolbar. No new motor API.
  window.WujiConnection={state:()=>state,available:()=>service&&!pending,
    async refresh(){await freshState();return service?state:null;},
    connect:(address='',serial='')=>action({name:'connect',auto_detect:true,address,serial}),
    async disconnect(){
      if(state?.hardware?.active){
        if(!await action({name:'hardware_stop'}))return false;
        const deadline=Date.now()+6000;
        while(Date.now()<deadline){await freshState();if(state?.hardware?.active===false&&state.hardware.stop_confirmed===true)break;await new Promise(r=>setTimeout(r,100));}
        if(state?.hardware?.active!==false||state.hardware.stop_confirmed!==true){showError('停止尚未确认，请检查设备 / Stop not confirmed');return false;}
      }
      return action({name:'disconnect'});
    }};
  el('connect').addEventListener('click',()=>{if(!deviceChoice.hidden&&!deviceChoice.value){showError('请选择一只灵巧手 / Select a hand');return;}action({name:'connect',auto_detect:true,serial:deviceChoice.hidden?'':deviceChoice.value,address:el('device-address').value.trim()});});
  el('disconnect').addEventListener('click',()=>action({name:'disconnect'}));
  el('record').addEventListener('click',()=>action({name:'record',label:el('annotation').value,seconds:Number(el('duration').value)}));
  el('stop').addEventListener('click',()=>action({name:'stop'}));
  window.addEventListener('wuji-language',()=>{controls();if(service&&state){renderCapture(state.recording);renderSessions(state.sessions);}else el('record-progress').textContent=t('采集状态未知，请恢复连接后核对','Recording state is unknown. Reconnect to verify it.');});
  document.querySelectorAll('input[name=task]').forEach(input=>input.addEventListener('change',()=>{const sampling=input.value==='sampling';el('annotation-note').textContent=sampling?'标签由你手动选择，不是模型的识别结果。每次触碰采集前先选择对应手指。':'先检查无触碰时的反馈。采集标签来自你的选择。';if(!sampling&&!state?.recording.active)el('annotation').value='baseline';}));
  el('presentation').addEventListener('click',()=>{const active=document.body.classList.toggle('presentation');el('presentation').textContent=active?'返回操作':'展示模式';el('presentation').setAttribute('aria-pressed',String(active));});
  async function poll(){
    if(inFlight)return;inFlight=true;clearTimeout(pollTimer);const id=++pollId;
    try{const r=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(3000)});if(!r.ok)throw Error();render(normalize(await r.json()));}
    catch{offline();}
    finally{inFlight=false;if(waiter&&id>waiter.after){const w=waiter;waiter=null;for(const resolve of w.resolves)resolve();}pollTimer=setTimeout(poll,waiter?0:500);}
  }
  poll();
})();
