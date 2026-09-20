(() => {
  const panel=document.createElement('section');panel.className='panel';panel.id='playback-panel';
  panel.innerHTML=`<h2>动作展示与循环</h2><p class="hint">编排动作预览，不是已学会的轻扣策略。</p><label for="play-action">动作</label><select id="play-action"><option value="all">整套展示</option><option value="open">张开</option><option value="fist">握拳</option><option value="opposition">依次对指</option><option value="splay">左右侧摆</option><option value="wave">逐指屈伸</option><option value="joints">逐关节活动</option><option value="thumb">拇指活动</option><option value="index">食指活动</option><option value="middle">中指活动</option><option value="ring">无名指活动</option><option value="little">小指活动</option></select><div class="field-row"><label for="play-speed">速度</label><select id="play-speed"><option value="0.25">0.25 ×</option><option value="0.5" selected>0.5 ×</option><option value="1">1 ×</option></select></div><div class="field-row"><label for="play-cycles">循环</label><select id="play-cycles"><option value="1">1次</option><option value="3">3次</option><option value="10">10次</option><option value="0">持续循环</option></select></div><div class="button-row"><button id="play-start" class="primary" disabled>播放仿真动作</button><button id="play-pause" disabled>暂停</button></div><button id="play-stop" disabled>停止并回到实机反馈</button><p id="play-status" class="hint" role="status">未播放</p><p class="hint">实机动作需先完成关节方向、限位与轨迹核验。仿真播放不会使真实手运动。</p>`;
  const get=id=>document.getElementById(id);let state=null,pending=false;
  const controls=document.querySelector('.controls');controls.insertBefore(panel,controls.lastElementChild);
  get('play-action').add(new Option('官方二代对指录制','official_opposition'));
  const hardware=document.createElement('div');hardware.className='hardware-controls';
  hardware.innerHTML=`<h3>真实左手同步展示</h3><p class="hint">使用上方动作、速度和循环设置。网页跟随实际反馈。</p><button id="hardware-start" class="primary" disabled>启动实机同步展示</button><button id="hardware-pause" disabled>暂停实机</button><button id="hardware-stop" disabled>停止实机并停用电机</button><p id="hardware-status" class="hint" role="status">等待连接及实机动作校准</p>`;
  panel.append(hardware);
  const probe=document.createElement('div');probe.className='hardware-controls';
  probe.innerHTML=`<h3>首次实机试动</h3><p class="hint">只启用选定关节：从当前位置缓慢移动3°，再返回并停用。最长15秒；采用官方初调示例0.5 A、Kp=5、Kd=0.01。用于验证真实动作，不会自动解锁整套展示。</p><label for="probe-joint">试动关节</label><select id="probe-joint"></select><label for="probe-direction">方向（SDK角度）</label><select id="probe-direction"><option value="1">正向 +3°</option><option value="-1">反向 −3°</option></select><label class="hint"><input id="probe-clear" type="checkbox"> 已固定底座，手指周围没有人或物体</label><button id="probe-start" class="primary" disabled>试动真实关节 · 3°往返</button><p id="probe-status" class="hint" role="status">连接设备后检查反馈</p><p id="probe-result" class="hint" role="status"></p>`;
  hardware.before(probe);
  ['拇指','食指','中指','无名指','小指'].forEach((finger,f)=>{for(let j=0;j<4;j++){const o=document.createElement('option');o.value=f*4+j;o.textContent=`${finger} S${j+1} · 节点${f*5+j+1}`;get('probe-joint').append(o);}});
  get('probe-joint').value='6';
  const trial=document.createElement('div');trial.className='hardware-controls';
  trial.innerHTML=`<h3>低力度整套试运行</h3><p class="hint">候选姿态尚未完成实机验收。先从25%幅度开始；官方初调示例0.5 A、慢速运行，按SDK故障等级处理。电流不是已标定的接触力。</p><label for="trial-action">实机试运行动作</label><select id="trial-action"><option value="fist">张开与轻握</option><option value="opposition">拇指依次对指姿态</option><option value="sequence">整套低力度展示</option></select><label for="trial-amplitude">动作幅度</label><select id="trial-amplitude"><option value="0.25">25% · 首轮</option><option value="0.5">50%</option><option value="0.75">75%</option><option value="1">100%</option></select><label for="trial-cycles">实机试运行循环</label><select id="trial-cycles"><option value="1">1轮</option><option value="3">3轮</option></select><label class="hint"><input id="trial-clear" type="checkbox"> 底座固定，周围无人无物，开始低力度试运行</label><button id="trial-start" class="primary" disabled>开始实机低力度试运行</button><p id="trial-status" class="hint" role="status">等待设备连接</p><p id="trial-result" class="hint" role="status"></p><p class="hint">姿态到位不等于指尖实际接触。可用下方“停止实机”随时终止；不自动增幅或加力。</p>`;
  hardware.before(trial);
  const officialOption=new Option('官方二代对指 · 限速适配','official_opposition');
  get('trial-action').add(officialOption,0);get('trial-action').value='official_opposition';
  const officialNote=document.createElement('p');officialNote.className='hint';
  officialNote.textContent='官方左手录制约30秒，实机按现有速度上限降速、按所选幅度缩放，并从当前姿态过渡。其余动作仍为原仿真候选；当前手尚未通过官方动作实测。';
  get('trial-action').after(officialNote);
  const history=document.createElement('div');history.className='hardware-controls';
  history.innerHTML='<h3>最近实机试运行记录</h3><p id="motion-history-summary" class="hint"></p><ol id="motion-history" class="hint"></ol>';
  trial.append(history);let historyKey='';
  // Keep one real-device command path; preview controls are separate and folded.
  const motionPanel=document.createElement('section');motionPanel.className='panel motion-panel';
  motionPanel.id='real-motion-panel';motionPanel.innerHTML='<h2>真实手 · 动作播放</h2><p class="hint">网页随实际关节反馈同步。动作仍处于实机试运行阶段。</p>';
  controls.insertBefore(motionPanel,controls.children[1]);motionPanel.append(trial);
  trial.querySelector('h3').remove();trial.querySelector('p').innerHTML='<span id="active-motion-parameters">尚未连接：连接后显示实际加载的Kp、Kd和电流设置。</span> 前往<a href="#parameters">参数调节</a>修改；<a href="https://docs.wuji.tech/docs/en/wuji-hand/latest/control-guide/" target="_blank" rel="noopener">官方参数说明</a>。电流A不等于接触力N。';
  get('trial-action').add(new Option('张开并返回','open'),1);
  get('trial-start').textContent='开始真实手动作';
  get('trial-action').previousElementSibling.textContent='真实手动作';
  const settings=document.createElement('div');settings.innerHTML='<label for="trial-speed">实机播放速度</label><select id="trial-speed"><option value="0.25">0.25 × · 更慢</option><option value="0.5">0.5 × · 慢速</option><option value="1" selected>1 × · 基准速度</option></select><p class="hint">1 × 仍为低速。参数只对下一次播放生效；单次总时长不超过10分钟。</p>';
  get('trial-cycles').after(settings);
  for(const id of ['trial-speed','play-speed']){for(const rate of [1.25,1.5,2])get(id).add(new Option(rate+' ×',String(rate)));}
  const rateSummary=document.createElement('p');rateSummary.id='playback-rate-summary';rateSummary.className='hint';settings.append(rateSummary);
  const tr=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
  function updateRateSummary(){const s=Number(get('trial-speed').value),p=state?.hardware?.commissioning_policy?.editable_parameters;rateSummary.textContent=p?tr(`当前 ${s}×：轨迹峰值设定 ${(Math.min(p.PATH_SPEED_RAD_S,p.COMMAND_SPEED_RAD_S)*s).toFixed(3)} rad/s。`,`At ${s}×: configured trajectory peak ${(Math.min(p.PATH_SPEED_RAD_S,p.COMMAND_SPEED_RAD_S)*s).toFixed(3)} rad/s.`)+tr(' 倍速同时缩短过渡和停留，不改变力度或发送频率。',' Scales transitions and holds, not gains or command rate.'):tr('倍速按已加载的轨迹速度缩放；2× 用时约为 1× 的一半，不改变力度。','Playback scales loaded trajectory speeds; 2× takes about half the time, without changing gains.');}
  get('trial-speed').addEventListener('change',updateRateSummary);window.addEventListener('wuji-language',updateRateSummary);updateRateSummary();
  const playButtons=document.createElement('div');playButtons.className='button-row';
  playButtons.append(get('trial-start'),get('hardware-pause'));settings.after(playButtons);
  const clearLabel=get('trial-clear').closest('label');playButtons.before(clearLabel);
  playButtons.after(get('hardware-stop'));get('hardware-stop').classList.add('stop-motion');
  const progress=document.createElement('div');progress.className='motion-progress';
  progress.innerHTML='<progress id="trial-progress" max="100" value="0" aria-label="实机动作进度"></progress><p id="trial-progress-text" class="hint">尚未开始</p><p id="motion-blocker" role="status"></p><div id="warning-policy" class="warning-policy"><strong>官方告警分级</strong><p class="hint">SDK判为Warning时记录并继续；其他故障等级或无法识别的设备错误请求停用。依据<a href="https://docs.wuji.tech/docs/en/wuji-hand/latest/troubleshooting/" target="_blank" rel="noopener">官方故障说明</a>与控制指南，不自动清除故障或恢复动作。</p><p id="motion-warnings" class="hint"></p><details><summary>网页控制与动作完成条件</summary><p class="hint">网页连接中断、反馈缺失或过期、无效数据时结束控制；这些是应用通信机制。速度、播放时长及回位误差用于执行和评估所选轨迹，不是官方硬件故障阈值。</p></details></div>';
  get('hardware-stop').after(progress);
  const timingLine=document.createElement('p');timingLine.id='command-timing';timingLine.className='hint';timingLine.setAttribute('role','status');progress.append(timingLine);
  const pauseNote=document.createElement('p');pauseNote.className='hint';pauseNote.textContent='暂停会保持当前姿态；停止会请求电机停用。';get('hardware-stop').after(pauseNote);
  // Basic copy reviewed from the local Fable task; it does not set control policy.
  const acceptanceNote=history.previousElementSibling;
  if(acceptanceNote?.tagName==='P')acceptanceNote.textContent='指令完成不代表动作已验收，实际动作需另行确认。';
  const historyFold=document.createElement('details');historyFold.innerHTML='<summary>查看历次实机结果</summary>';history.before(historyFold);historyFold.append(history);
  const previewFold=document.createElement('details');previewFold.className='panel folded-panel';previewFold.innerHTML='<summary>MuJoCo 动作预览 · 不驱动实机</summary>';panel.before(previewFold);previewFold.append(panel);panel.classList.remove('panel');
  const probeFold=document.createElement('details');probeFold.className='panel folded-panel';probeFold.innerHTML='<summary>单关节检查 · 3°往返</summary>';motionPanel.after(probeFold);probeFold.append(probe);
  const calibratedFold=document.createElement('details');calibratedFold.innerHTML='<summary>已验收动作通道（尚未开放）</summary>';hardware.before(calibratedFold);calibratedFold.append(hardware);
  const globalStop=document.createElement('button');globalStop.id='global-motion-stop';globalStop.className='stop-motion';globalStop.textContent='停止实机';globalStop.disabled=true;document.querySelector('.top-actions').prepend(globalStop);
  function renderMotionPanel(h,connected){
    const timing=h?.command_timing;
    const timingValue=(v,d=1)=>Number.isFinite(v)?v.toFixed(d):'—';
    get('command-timing').textContent=!connected?'指令频率：重新连接后显示实际加载的发送节拍。':!timing?'当前连接仍使用旧控制程序；停止并重新连接后加载可调频率版本。':
      `指令目标 ${timing.target_hz} Hz · ${h.active?'运行中':'未运行 / 上次统计'} · 实测发布 ${timingValue(timing.host_publish_hz)} Hz · 最长间隔 ${timingValue(timing.max_interval_ms,2)} ms。统计为本机SDK发布，不代表设备每帧均已执行。项目动作平滑起停，官方录制保留中间轨迹。`;
    const config=h?.commissioning_policy;
    get('active-motion-parameters').textContent=connected&&config?`已加载参数：Kp=${config.kp}，Kd=${config.kd}，电流上限=${config.current_limit_A} A。`:'尚未连接：连接后显示设备控制程序实际加载的Kp、Kd和电流设置。';
    const p=config?.editable_parameters;
    if(connected&&p){
      settings.querySelector('p').textContent=`1 × 路径速度${p.PATH_SPEED_RAD_S} rad/s，指令变化上限${p.COMMAND_SPEED_RAD_S} rad/s；单次计划不超过${p.MAX_TRIAL_DURATION_S}秒。参数只对下次播放生效。`;
      probe.querySelector('p').textContent=`只启用选定关节，3°往返。Kp=${config.kp}、Kd=${config.kd}、电流上限${config.current_limit_A} A；指令变化上限${p.PROBE_SPEED_RAD_S} rad/s。`;
    }else{
      settings.querySelector('p').textContent='速度和最长时长取自参数文件；连接后显示已加载值。修改文件不会改变正在进行的动作。';
      probe.querySelector('p').textContent='只启用选定关节，3°往返。连接后显示实际加载的增益、电流和速度参数。';
    }
    updateRateSummary();
    const first=state?.device_profile?.generation==='hand1';for(const o of get('trial-speed').options){if(Number(o.value)>1)o.disabled=first;}if(first&&Number(get('trial-speed').value)>1)get('trial-speed').value='1';
    const busy=!!h?.active||!!ownedLease||hardwarePending;
    for(const id of ['trial-action','trial-speed','trial-amplitude','trial-cycles','trial-clear'])get(id).disabled=busy;
    globalStop.disabled=get('hardware-stop').disabled;
    const elapsed=Number.isFinite(h?.elapsed_s)?h.elapsed_s:0, duration=Number.isFinite(h?.planned_duration_s)?h.planned_duration_s:0;
    const fraction=duration>0?Math.min(100,elapsed/duration*100):0;
    get('trial-progress').value=fraction;
    const phase={waiting_ready:'等待稳定（未启用）',enabling:'准备启用',approach:'过渡到起点',playing:'播放',returning:'检查回位',stopped:'已停止'}[h?.phase]||'尚未开始';
    get('trial-progress-text').textContent=duration>0?`${connected?'':'上次记录 · '}${phase} · ${elapsed.toFixed(1)} / ${duration.toFixed(1)} 秒 · ${fraction.toFixed(0)}%`:'尚未开始';
    let message=!state?'本机服务未连接':!connected?'设备反馈未连接，先连接设备':h?.active===null?'电机停用待确认，请先检查停止状态':state.recording?.active?'先停止数据采集':busy?(h?.reason||'准备动作'):!h?.trial_ready?(h?.probe_reason||'等待完整反馈与诊断'):!get('trial-clear').checked?'确认底座固定、周围清空后可启动':'可以开始所选动作';
    get('motion-blocker').textContent=message;
    const warnings=Array.isArray(h?.warnings)?h.warnings:[];
    const groups=new Map();for(const w of warnings){const key=`${w.code}:${w.name}`;const group=groups.get(key)||{...w,nodes:[]};group.nodes.push(w.nid);groups.set(key,group);}
    get('motion-warnings').textContent=!connected?'当前无新鲜设备诊断；旧告警不作为放行依据。':!warnings.length?'本次诊断未报告设备告警。':Array.from(groups.values()).map(w=>{
      const known=typeof w.name==='string'&&w.name.trim()&&!w.name.toLowerCase().startsWith('unknown')&&w.severity==='Warning';
      const policy=known?'官方Warning：记录并继续':'故障或未知错误：请求停用';
      return `${w.description||w.name||'未知告警'} · 代码${w.code} · ${w.severity||'未分类'} · ${w.nodes.length}个节点 · ${policy}`;
    }).join('；');
  }
  const degree=value=>Number.isFinite(value)?value.toFixed(2)+'°':'未记录';
  function stopExplanation(reason){
    const text=String(reason||'');
    if(text.includes('250毫秒内几乎未动'))return '本地250毫秒运动阈值触发（未标定）；未确认机械受阻';
    if(text.includes('持续受阻或跟随偏差较大'))return '本地跟随阈值触发（未标定）；未确认机械受阻';
    if(/\(0x[0-9a-f]+\)/i.test(text))return '设备告警触发上位机停止：'+text;
    return text;
  }
  function returnText(r){return r.return_evaluated===true||r.completed?`实际回位误差 ${degree(r.return_error_deg)}`:`未完成回位验收 · 中止时偏离起点 ${degree(r.displacement_at_stop_deg??r.return_error_deg)}`;}
  let ownedLease=null,hardwarePending=false,leaseDeadline=0,leaseSeenActive=false,hardwareGeneration=0;
  async function hardwarePost(body){
    const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify(body),signal:AbortSignal.timeout(2000)});
    const result=await r.json();if(!r.ok||!result.ok)throw Error(result.error||'实机操作失败');return result;
  }
  function renderHardware(){
    const h=state?.hardware,connected=state?.connection==='connected'&&!state?.stale;
    const supported=h?.actions?.includes(get('play-action').value);
    get('hardware-start').disabled=!connected||!h?.ready||!supported||h.active||ownedLease||state?.recording?.active||hardwarePending;
    get('hardware-pause').disabled=!h?.active||!ownedLease||hardwarePending;
    get('hardware-pause').textContent=h?.paused?'继续实机':'暂停实机';
    get('hardware-stop').disabled=!state||(h?.active===false&&!ownedLease&&!hardwarePending&&!state?.glove?.busy);
    get('probe-start').disabled=!connected||!h?.probe_ready||h?.active!==false||ownedLease||state?.recording?.active||hardwarePending||!get('probe-clear').checked;
    get('probe-status').textContent=!connected?'实机未连接':h?.active?`实机执行中 · ${h.reason}`:h?.probe_reason||'等待完整反馈与诊断';
    const r=h?.probe_result;
    get('probe-result').textContent=r?`${r.accepted?'已观测到往返动作':'试动未通过'} · 实际最大变化 ${r.peak_actual_delta_deg.toFixed(2)}° · 返回误差 ${r.return_error_deg.toFixed(2)}° · ${r.stop_confirmed?'停用请求已确认':'停用待确认'} · ${r.reason}`:'';
    get('trial-start').disabled=!connected||!h?.trial_ready||h?.active!==false||ownedLease||state?.recording?.active||hardwarePending||!get('trial-clear').checked;
    get('trial-status').textContent=!connected?'未连接；连接后由你选择启动':h?.active&&h?.source==='supervised_low_current_trial'?`${h.paused?'暂停':'试运行'} · 第${h.cycle}/${h.cycles}轮 · ${h.trial_phase} · ${h.elapsed_s.toFixed(1)}秒 / 计划${Math.ceil(h.planned_duration_s||0)}秒`:h?.probe_reason||'等待完整反馈与诊断';
    const tr=h?.trial_result;
    get('trial-result').textContent=tr?`${tr.accepted?'指令与回位检查通过':'本次试运行未通过'} · 完成${tr.finished_cycles}/${tr.requested_cycles}轮 · 实测峰值电流${tr.peak_current_A.toFixed(3)} A · ${returnText(tr)} · ${stopExplanation(tr.reason)} · 指尖接触尚待观察`:'';
    const records=(state?.motion_history||[]).filter(r=>r.kind==='low_current_showcase_trial');
    const key=JSON.stringify(records);
    if(key!==historyKey){historyKey=key;get('motion-history').replaceChildren();
      const passed=records.filter(r=>r.accepted===true).length;
      get('motion-history-summary').textContent=records.length?`最近 ${records.length} 次整套试运行：通过 ${passed} 次，未通过 ${records.length-passed} 次。单关节结果分开统计。`:'暂无整套实机记录';
      for(const r of records.slice(0,9)){const item=document.createElement('li');
        const name={fist:'张开与轻握',opposition:'依次对指',sequence:'整套展示',official_opposition:'官方二代对指（适配）'}[r.action]||r.action;
        const date=new Date(r.started_unix*1000).toLocaleTimeString('zh-CN',{hour12:false});
        item.textContent=`${date} · ${name} · ${r.amplitude*100}% · ${r.accepted?'通过':'未通过'} · 完成 ${r.finished_cycles}/${r.requested_cycles} 轮 · ${returnText(r)} · ${stopExplanation(r.reason)}`;
        if(stopExplanation(r.reason)!==r.reason){const raw=document.createElement('details');const title=document.createElement('summary');title.textContent='原始停止文本（保留）';const text=document.createElement('p');text.textContent=r.reason;raw.append(title,text);item.append(raw);}
        get('motion-history').append(item);}}
    get('hardware-status').textContent=!connected?'实机未连接，不能同步执行':h?.active?`${h.paused?'暂停':'执行中'} · ${h.trial_phase||h.action} · ${h.elapsed_s.toFixed(1)}秒 · ${h.reason}`:!h?.ready?(h?.reason||'等待实机动作校准'):!supported?'所选动作尚未完成实机核验':h.reason||'已就绪';
    get('runtime-mode').textContent=h?.active?'当前模式：实机展示':h?.active===null?'当前模式：实机状态待确认':'当前模式：只读';
    if(h?.active)get('play-start').disabled=true;
    renderMotionPanel(h,connected);
  }
  async function hardwareAction(name){
    if(!state||(hardwarePending&&name!=='hardware_stop'))return;
    const generation=++hardwareGeneration;hardwarePending=true;renderHardware();
    const body={name,lease:ownedLease};
    if(name==='hardware_start')Object.assign(body,{action:get('play-action').value,speed:Number(get('play-speed').value),cycles:Number(get('play-cycles').value)});
    if(name==='hardware_probe')Object.assign(body,{index:Number(get('probe-joint').value),direction:Number(get('probe-direction').value),workspace_clear:get('probe-clear').checked});
    if(name==='hardware_trial')Object.assign(body,{action:get('trial-action').value,amplitude:Number(get('trial-amplitude').value),speed:Number(get('trial-speed').value),cycles:Number(get('trial-cycles').value),workspace_clear:get('trial-clear').checked},window.WujiPerformancePayload?.()||{});
    try{
      const result=await hardwarePost(body);
      if(generation!==hardwareGeneration){if(result.lease)await hardwarePost({name:'hardware_stop'});return;}
      if(result.lease){ownedLease=result.lease;leaseDeadline=performance.now()+3000;leaseSeenActive=false;get('probe-clear').checked=false;get('trial-clear').checked=false;}
      if(name==='hardware_stop')ownedLease=null;
    }catch(e){document.getElementById('service-error').hidden=false;document.getElementById('service-error').textContent=e.message;}
    finally{hardwarePending=false;renderHardware();}
  }
  get('hardware-start').addEventListener('click',()=>hardwareAction('hardware_start'));
  get('probe-start').addEventListener('click',()=>hardwareAction('hardware_probe'));
  get('probe-clear').addEventListener('change',renderHardware);
  get('trial-start').addEventListener('click',()=>hardwareAction('hardware_trial'));
  get('trial-clear').addEventListener('change',renderHardware);
  get('hardware-pause').addEventListener('click',()=>hardwareAction(state?.hardware?.paused?'hardware_resume':'hardware_pause'));
  get('hardware-stop').addEventListener('click',()=>hardwareAction('hardware_stop'));
  globalStop.addEventListener('click',()=>hardwareAction('hardware_stop'));
  get('play-action').addEventListener('change',renderHardware);
  async function beat(){
    try{if(ownedLease&&state)await hardwarePost({name:'hardware_keepalive',lease:ownedLease});}
    catch{ownedLease=null;}
    finally{setTimeout(beat,200);}
  }
  beat();
  function render(){const p=state?.playback;get('play-start').disabled=!state||pending;get('play-pause').disabled=!p?.active||pending;get('play-stop').disabled=!p?.active||pending;get('play-pause').textContent=p?.running?'暂停':'继续';get('play-status').textContent=p?.active?`${p.label} · 第${p.cycle}${p.cycles?'/'+p.cycles:''}轮 · ${p.running?'播放中':'暂停或完成'} · ${p.elapsed_s.toFixed(1)}秒`:'当前显示实机反馈或静态预览';}
  async function send(body){if(!state||pending)return;pending=true;render();let error='';try{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'播放操作失败');}catch(e){error=e.message;}finally{pending=false;render();if(error)document.getElementById('service-error').textContent=error;document.getElementById('service-error').hidden=!error;}}
  get('play-start').addEventListener('click',()=>send({name:'demo_start',action:get('play-action').value,speed:Number(get('play-speed').value),cycles:Number(get('play-cycles').value),...(window.WujiPerformancePayload?.()||{})}));
  get('play-pause').addEventListener('click',()=>send({name:state.playback.running?'demo_pause':'demo_resume'}));
  get('play-stop').addEventListener('click',()=>send({name:'demo_stop'}));
  window.addEventListener('console-state',e=>{state=e.detail;if(state.hardware?.active)leaseSeenActive=true;else if(ownedLease&&(leaseSeenActive||performance.now()>leaseDeadline))ownedLease=null;render();renderHardware();});
  window.addEventListener('console-offline',()=>{state=null;ownedLease=null;render();renderHardware();});
})();
