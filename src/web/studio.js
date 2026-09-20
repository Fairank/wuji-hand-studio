(() => {
  'use strict';
  const $=id=>document.getElementById(id), L=window.WujiLocale, ws=window.WujiWorkspace;
  const text=(zh,en)=>L.lang==='en'?en:zh;
  let catalog=null,state=null,mode='hardware';
  const tools=document.createElement('div');tools.className='studio-tools';
  const language=document.createElement('select');language.setAttribute('aria-label','语言 / Language');
  language.add(new Option('中文','zh'));language.add(new Option('English','en'));language.value=L.lang;
  language.addEventListener('change',()=>L.setLanguage(language.value));tools.append(language);document.querySelector('.top-actions').append(tools);
  const header=document.querySelector('.top h1');header.dataset.i18n='appTitle';
  for(const key of ['motion','parameters','feedback','capture','records','connection','library','interaction']){
    document.querySelector(`.directory [data-page="${key}"] span`).dataset.i18n=key;
    document.querySelector(`#page-${key} h2`).dataset.i18n=key;
  }
  const captions={connect:'connect',disconnect:'disconnect',record:'start',stop:'stop'};
  for(const [id,key] of Object.entries(captions))$(id).dataset.i18n=key;
  const library=ws.views.library,interaction=ws.views.interaction;
  library.innerHTML='<div id="compact-library"></div><details class="studio-info library-reference"><summary id="official-summary"></summary><ol class="official-list" id="official-list"></ol></details>';
  const motionLayout=document.querySelector('.motion-layout');library.prepend(motionLayout);
  const chooser=document.createElement('div');motionLayout.querySelector('.motion-controls').prepend(chooser);
  const picker=window.WujiActionPicker.create({root:chooser,text,onSelect:id=>selectMotion(id)});
  const modeBar=document.createElement('div');modeBar.className='playback-modes';modeBar.setAttribute('role','group');modeBar.setAttribute('aria-label','播放对象 / Playback target');
  modeBar.innerHTML='<button type="button" id="mode-hardware"></button><button type="button" id="mode-preview"></button>';
  chooser.after(modeBar);
  const previewFold=$('playback-panel').closest('details');previewFold.open=true;previewFold.classList.add('preview-mode-panel');
  const realAction=$('trial-action').closest('.motion-field');realAction.hidden=true;
  $('play-action').hidden=true;document.querySelector('label[for="play-action"]').hidden=true;
  const desktop=document.createElement('button');desktop.id='studio-desktop';desktop.type='button';ws.views.connection.querySelector('section').append(desktop);
  desktop.addEventListener('click',async()=>{try{const d=await post({name:'desktop_open'});if(!d.native_opened)throw Error(text('未找到桌面窗口运行环境，请继续使用网页。','Desktop runtime not found. Continue in the browser.'));}catch(e){notice(e);}});
  function setMode(next){
    mode=next;$('real-motion-panel').hidden=mode!=='hardware';previewFold.hidden=mode!=='preview';
    picker.setPreview(mode==='preview');
    $('mode-hardware').setAttribute('aria-pressed',String(mode==='hardware'));$('mode-preview').setAttribute('aria-pressed',String(mode==='preview'));
    if(mode==='preview'){$('play-action').value=picker.selected;$('play-action').dispatchEvent(new Event('change'));}
  }
  $('mode-hardware').addEventListener('click',()=>setMode('hardware'));$('mode-preview').addEventListener('click',()=>setMode('preview'));
  function modeLabels(){
    $('mode-hardware').textContent=text('真实手','Real hand');$('mode-preview').textContent=text('画面预览','Preview only');
    setMode(mode);
  }
  interaction.innerHTML='<div class="studio-info"><strong id="touch-heading"></strong><span id="touch-description"></span></div><div id="touch-grid" class="touch-grid"></div><p class="studio-status" id="touch-status"></p><p class="studio-small" id="touch-limit"></p><div class="button-row"><a class="connection-shortcut" href="#capture" id="touch-capture"></a><button id="touch-flow"></button></div><p class="studio-small" id="touch-flow-note"></p>';
  const node=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n;};
  async function post(body){
    if(!state?.csrf)throw Error(text('控制服务未连接','Console is not connected'));
    const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});
    const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Request failed');return d;
  }
  function notice(error){$('service-error').hidden=false;$('service-error').textContent=error.message;}
  function selectMotion(id){
    for(const key of ['trial-action','play-action']){const control=$(key);if([...control.options].some(o=>o.value===id)){control.value=id;control.dispatchEvent(new Event('change'));}}
  }
  function touchState(){
    const grid=$('touch-grid');grid.replaceChildren();
    const names=L.lang==='en'?['Thumb','Index','Middle','Ring','Little']:['拇指','食指','中指','无名指','小指'];
    const joints=new Map((state?.latest?.joints||[]).map(j=>[j.nid,j]));
    for(let f=0;f<5;f++){
      const box=document.createElement('div');box.className='touch-finger';box.append(node('strong',names[f]));
      const values=[1,2,3,4].map(j=>joints.get(f*5+j)?.effort_A);
      const current=state&&!state.stale&&values.every(Number.isFinite)?Math.max(...values.map(Math.abs)).toFixed(3)+' A':'—';
      box.append(node('span',text('最大关节电流：','Peak joint current: ')+current),node('span',text('模型判断：未启用','Model decision: unavailable')));grid.append(box);
    }
    $('touch-status').textContent=text('识别模型：未接入实机校准输入 · 原始抓取建议：无 · 实际抓取指令：无','Recognition: no calibrated real-device input · Raw grasp suggestion: none · Applied grasp command: none');
  }
  function render(){
    document.documentElement.lang=L.lang==='en'?'en':'zh-CN';language.value=L.lang;L.apply();
    document.title=L.text(document.body.dataset.page||'library')+' · Wuji Hand Studio';
    $('official-summary').textContent=text('官方资源兼容性与来源','Official resources and compatibility');
    desktop.textContent=text('打开桌面软件','Open desktop app');
    $('touch-heading').textContent=text('触碰识别与轻扣：实机尚未验收','Touch recognition and gentle grasp: not validated on hardware');
    $('touch-description').textContent=text('目标流程：感知被碰的手指 → 选择配合指 → 轻扣0.5秒 → 松开恢复；提前抽离则取消。','Target flow: detect the touched finger → select a partner → hold gently for 0.5 s → release and resume; cancel on early withdrawal.');
    $('touch-limit').textContent=text('下方是实测关节电流，不是接触力或识别概率。需要完成电流与负载标定、关节对应核对及抽离验收，才能将现有仿真识别器接入自动轻扣。','The readings are measured joint currents, not contact force or probabilities. Current/load calibration, joint mapping and withdrawal validation are needed before connecting the simulation recognizer to an automatic grasp.');
    $('touch-capture').textContent=text('进入触碰采集','Open touch capture');$('touch-flow').textContent=text('预览反应流程','Preview reaction sequence');
    $('touch-flow-note').textContent=text('流程预览仅为编排姿态：没有探棒碰撞、没有模型判断，不驱动实机。','Sequence preview is scripted kinematics: no probe collision, no model decision, no hardware movement.');
    for(const [id,isPreview] of [['trial-action',false],['play-action',true]]){
      const select=$(id),value=select.value;if(!catalog)continue;
      for(const item of catalog.actions){let o=[...select.options].find(o=>o.value===item.id);if(!o){o=new Option('',item.id);select.add(o);}o.textContent=L.lang==='en'?item.en:item.zh;}
      select.value=value;
    }
    if(catalog)picker.setCatalog([...catalog.actions,...[['joints','逐关节活动','Joint preview'],['thumb','拇指活动','Thumb preview'],['index','食指活动','Index preview'],['middle','中指活动','Middle preview'],['ring','无名指活动','Ring preview'],['little','小指活动','Little finger preview']].map(([id,zh,en])=>({id,zh,en,group:'preview',hardware:false}))]);touchState();modeLabels();
    if(catalog){$('official-list').replaceChildren(...catalog.official_inventory.map(item=>{const li=document.createElement('li');const a=node('a',item.name);a.href=item.url;a.target='_blank';a.rel='noopener';li.append(a,node('p',item.detail));return li;}));}
    staticEnglish();
  }
  $('touch-flow').addEventListener('click',async()=>{try{await post({name:'demo_start',action:'touch_flow',speed:1,cycles:1});location.hash='feedback';}catch(e){notice(e);}});
  const pose=$('pose-console'),actions=document.createElement('div');actions.className='viewer-actions';
  const float=node('button',''),pop=node('button','');actions.append(float,pop);pose.prepend(actions);
  window.wujiFloating=window.FloatingPanel.create({panel:pose,home:ws.viewerHome(),title:'MuJoCo · Hand Studio',storageKey:'wuji-viewer-layout-v12',initialRect:{x:20,y:150,width:440,height:360},minHeight:240,onChange:()=>{if(!window.wujiFloating?.isFloating())ws.showPage();}});
  float.addEventListener('click',()=>window.wujiFloating.toggle());
  pop.addEventListener('click',async()=>{try{const result=await post({name:'desktop_viewer'});if(!result.native_opened){const w=window.open('/viewer','wuji-pose-viewer','popup=yes,width=780,height=650,resizable=yes,scrollbars=yes');if(!w)throw Error(text('请允许本机网站弹窗，或直接打开 /viewer 页面。','Allow localhost popups, or open the /viewer page.'));}}catch(e){notice(e);}});
  function viewerLabels(){float.textContent=text('悬浮 / 停靠','Float / dock');pop.textContent=text('独立小窗','Pop out');}
  // Translate stable visible controls only; preserve native diagnostic text.
  const translations={
    '实机试运行循环':'Cycles','动作幅度':'Amplitude','真实手动作':'Hand action','实机播放速度':'Playback speed',
    '真实左手 · 动作播放':'Real left hand · Playback','开始真实手动作':'Start hand motion','暂停实机':'Pause hand','继续实机':'Resume hand','停止实机并停用电机':'Stop and disable motors',
    '底座固定，周围无人无物，开始低力度试运行':'Base fixed; workspace clear of people and objects',
    '动作来源与参数说明':'Sources and parameters','告警等级与完成条件':'Diagnostics and completion','官方告警分级':'Official diagnostics',
    '放大画面':'Enlarge view','查看同步画面 ↓':'Show live view ↓','动作预览':'Preview','跟随实机':'Live feedback','正面':'Front','背面':'Back','侧面':'Side','复位':'Reset',
    '播放仿真动作':'Play preview','停止并回到实机反馈':'Stop preview; show feedback','动作展示与循环':'Preview and repeat','暂停':'Pause','继续':'Resume','动作':'Action','速度':'Speed','循环':'Repeat',
    '实时反馈':'Live feedback','本机服务连接中':'Connecting to local service','关节数':'Joints','时间戳反馈率':'Device feedback rate','采集端接收率':'Host receive rate','数据新鲜度':'Data age',
    '触碰采样':'Touch sampling','只读检查':'Read-only check','采集设置':'Capture settings','标签':'Label','时长':'Duration','最近采集':'Recent captures','活动记录':'Activity log',
    '保存本机参数':'Save parameters','同步到控制端':'Sync to controller','电机与力度':'Gains and current','轨迹与速度':'Trajectory and speed','时长与停留':'Duration and holds',
    '连接设备':'Connect left hand','断开连接':'Disconnect','连接设备':'Connect device','未连接':'Disconnected','设备已连接':'Left hand connected','连接中':'Connecting','反馈过期':'Feedback stale','服务未连接':'Service offline',
    '更多工具':'More tools','三维显示映射（首次校准）':'Display mapping (initial setup)','退出展示':'Exit focus view',
    '本机值':'Local','已加载':'Loaded','说明与建议':'Notes','只看已修改':'Changed only','显示全部':'Show all','清空搜索':'Clear search','无触碰基线':'No-touch baseline',
    '拇指轻触':'Thumb touch','食指轻触':'Index touch','中指轻触':'Middle touch','无名指轻触':'Ring touch','小指轻触':'Little finger touch','轻触后抽离':'Touch then withdraw'};
  const originals=new WeakMap();
  Object.assign(translations,{
    '停止实机':'Stop hand','暂停会保持当前姿态；停止会请求电机停用。':'Pause holds the current pose; stop requests motor disable.',
    '当前无新鲜设备诊断；旧告警不作为放行依据。':'No fresh diagnostics. Historical warnings do not authorize motion.',
    '指令频率：重新连接后显示实际加载的发送节拍。':'Command rate: reconnect to see the loaded publish timing.',
    '指令完成不代表动作已验收，实际动作需另行确认。':'Command completion is not physical validation; confirm the observed motion separately.',
    'MuJoCo 动作预览 · 不驱动实机':'MuJoCo preview · no hardware movement','整套展示':'Combined preview','逐关节活动':'Per-joint preview',
    '拇指活动':'Thumb preview','食指活动':'Index preview','中指活动':'Middle preview','无名指活动':'Ring preview','小指活动':'Little finger preview',
    '电机响应':'Motor response','停留与时长':'Holds and duration','保存参数':'Save parameters','前往连接':'Open connection',
    '本机保存':'Local save','控制端同步':'Controller sync','会话加载':'Session loaded','已保存':'Saved','与本机一致':'Matches local','待同步':'Awaiting sync',
    '参数说明与更多操作':'Parameter help and more actions','重读本机保存值':'Reload saved values','填入初始值':'Fill initial values','填入后仍需保存':'Save is still required',
    '这些参数是什么？':'What do these parameters mean?','官方控制说明 ↗':'Official control guide ↗','说明':'Details','撤销修改':'Undo change','清除筛选':'Clear filters',
    '保存不启动机械手。同步后重新连接，下一次手动播放才使用新设置。':'Saving does not start the hand. Reconnect after sync; the next manually started motion uses the settings.',
    'Kp决定追目标的响应强度，Kd提供阻尼。它们与速度、电流上限共同影响动作。':'Kp controls response to position error; Kd provides damping. Speed and current settings also affect motion.',
    '这里显示的是参数设置；实测电流与关节状态请看“实时反馈”。':'These are configured values. Measured current and joint state are under Live feedback.',
    '已同步 · 重新连接后加载':'Synced · reconnect to load','已保存 · 待同步':'Saved · sync pending','先停止并断开，再同步新值':'Stop and disconnect before syncing new settings',
    '当前没有未保存的修改':'No unsaved changes','未找到匹配的设置项，请尝试其他关键词':'No matching settings; try another keyword',
    '选择动作、幅度与循环，画面跟随实际反馈。':'Choose a motion, amplitude and repeats. The view follows measured feedback.',
    '修改数值后保存，再同步到控制端。':'Edit and save values, then sync to the controller.',
    '查看左手姿态与每个关节的反馈。':'Inspect the left-hand pose and per-joint feedback.',
    '按手指标记采样；采集不驱动机械手。':'Label captures by finger. Recording does not drive the hand.',
    '查看实机动作结果、采集报告与活动记录。':'Review motion results, capture reports and activity.',
    '连接设备，检查显示映射与单关节对应。':'Connect the left hand and check the display/joint mapping.',
    '官方示例、数字、报时与字母造型。':'Official examples, digits, clock and letter shapes.',
    '真实反馈、识别状态与反应流程。':'Measured feedback, recognition status and reaction sequence.',
    '25% · 首轮':'25% · initial trial','1轮':'1 cycle','3轮':'3 cycles','1次':'Once','3次':'3 times','10次':'10 times','持续循环':'Repeat continuously',
    '0.25 × · 更慢':'0.25 ×','0.5 × · 慢速':'0.5 ×','1 × · 原有限速':'1 × configured speed',
    '未连接；连接后由你选择启动':'Disconnected; connect and start manually','设备反馈未连接，先连接设备':'Connect the left hand to receive feedback',
    '确认底座固定、周围清空后可启动':'Confirm the base is fixed and workspace clear to start','可以开始所选动作':'Ready to start the selected motion',
    '当前显示实机反馈或静态预览':'Showing measured feedback or static preview','尚未开始':'Not started',
    '正在读取':'Loading','尚未读取':'Not loaded','本机服务未连接':'Local service offline','读取失败':'Read failed',
    '最近实机试运行记录':'Recent hardware motion results','查看同步画面':'Show synchronized view',
    '编排动作预览，不是已学会的轻扣策略。':'Scripted preview; this is not a learned grasp policy.',
    '实机动作需先完成关节方向、限位与轨迹核验。仿真播放不会使真实手运动。':'Check joint direction, travel and trajectory before hardware use. A preview does not move the real hand.',
    '识别需完成实机反馈校准；自动轻扣尚未通过验收。':'Recognition needs real-device calibration. Automatic gentle grasp is not validated.',
    '识别与动作':'Recognition and action','待实机校准后开放':'Requires hardware calibration','查看关节位置、速度和电流':'Inspect joint position, velocity and current',
    '按手指标记并记录数据':'Label and record feedback by finger','10 秒':'10 s','30 秒':'30 s','60 秒':'60 s',
    '本机连接':'Local connection','设备地址':'Device address','设备 IP（留空自动发现）':'Device IP (empty for discovery)',
    '正在连接本机虚拟机并核对左手':'Connecting to the local VM and checking handedness',
    '已请求断开；不会自动重连':'Disconnect requested; automatic reconnect is disabled',
    '网页随实际关节反馈同步。动作仍处于实机试运行阶段。':'The view follows measured joints. Motions are still hardware trials.',
    '画面来源':'View source','观看角度':'Camera view','拖动旋转 · 滚轮/双指缩放 · 右键或 Shift 拖动平移':'Drag to orbit · Wheel/pinch to zoom · Right/Shift-drag to pan',
    '无新反馈':'No fresh feedback','待核对':'Unverified','编排姿态':'Scripted pose','尚无实机数据':'No measured data'});
  const parameterEnglish={
    KP:['Position gain Kp','Strength of response to position error. Official guidance: at least 3; no recommended maximum specified.'],
    KD:['Damping gain Kd','Damping against motion/oscillation, not a speed limit. Official recommendation: 0.01–0.05.'],
    CURRENT_LIMIT_A:['Current limit','Motor current cap in A, not contact force. Official recommendation ≤1.5 A; device ceiling 2 A.'],
    PATH_SPEED_RAD_S:['Trajectory planning speed','Determines segment duration from joint displacement. Smaller of this and command slew rate applies.'],
    COMMAND_SPEED_RAD_S:['Command slew rate','Maximum target-position change rate. Playback speed also scales it.'],
    COMMAND_RATE_HZ:['Command publish rate','Target commands per second, not display FPS or internal motor rate. Official PUB maximum: 1000 Hz.'],
    PROBE_SPEED_RAD_S:['Single-joint trial speed','Slew limit for the 3° round trip; does not automatically shorten its planned duration.'],
    MIN_TRANSITION_S:['Minimum transition time','Minimum duration for each pose transition. Displacement and speed may require longer.'],
    POSE_HOLD_S:['Pose hold duration','Hold at candidate poses. Slow playback scales duration. Digit/letter demos also include readable holds.'],
    OFFICIAL_ENDPOINT_HOLD_S:['Replay endpoint hold','Extra hold at the beginning/end of the official replay.'],
    OFFICIAL_RETURN_HOLD_S:['Replay return hold','Hold after returning to the measured start pose.'],
    MAX_TRIAL_DURATION_S:['Maximum planned duration','Total playback duration including repeats; a project setting, not an official fault threshold.']};
  function parameterLabels(){
    for(const [key,values] of Object.entries(parameterEnglish)){
      const input=$('parameter-'+key),label=document.querySelector(`label[for="parameter-${key}"] strong`),help=$('advice-'+key);
      if(!input||!label||!help)continue;
      if(!label.dataset.zh){label.dataset.zh=label.textContent;help.dataset.zh=help.textContent;}
      label.textContent=L.lang==='en'?values[0]:label.dataset.zh;input.setAttribute('aria-label',label.textContent);
      help.textContent=L.lang==='en'?values[1]:help.dataset.zh;
    }
    const search=$('parameter-search');search.placeholder=text('搜索参数：Kp、电流、速度…','Search parameters: Kp, current, speed…');
  }
  function staticEnglish(){
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let n;while(n=walker.nextNode()){
      const p=n.parentElement;if(!p||p.closest('script,style,[data-i18n],#activity,#sessions,#motion-history,#official-list,.compact-picker,.symbol-dialog'))continue;
      const raw=n.textContent.trim(),saved=originals.get(n);
      if(/^本机 [-+.\d]/.test(raw))translations[raw]=raw.replace(/^本机 /,'Local ');
      if(/^保存 \d+ 项修改$/.test(raw))translations[raw]='Save '+raw.match(/\d+/)[0]+' changes';
      if(/^\d+项未保存$/.test(raw))translations[raw]=raw.match(/\d+/)[0]+' unsaved';
      if(/^\d+项修改尚未保存$/.test(raw))translations[raw]=raw.match(/\d+/)[0]+' unsaved changes';
      const source=translations[raw]?raw:saved&&raw===translations[saved]?saved:null;
      if(source){originals.set(n,source);const v=L.lang==='en'?translations[source]:source;if(raw!==v)n.textContent=v;}
    }
    parameterLabels();
  }
  const speedInfo=document.createElement('details');speedInfo.className='speed-explanation';const speedSummaryLabel=document.createElement('summary'),speedBody=document.createElement('p');speedInfo.append(speedSummaryLabel,speedBody);ws.views.parameters.querySelector('.parameter-tools').append(speedInfo);
  async function speedSummary(){try{const p=await(await fetch('/api/parameters',{cache:'no-store'})).json();const v=p.values;const peak=Math.min(v.PATH_SPEED_RAD_S,v.COMMAND_SPEED_RAD_S);speedSummaryLabel.textContent=text('当前速度与频率说明','Current speed and rate');speedBody.textContent=text(`当前轨迹峰值设定 ${peak} rad/s ≈ ${(peak*180/Math.PI).toFixed(1)}°/s（1×档）。规划速度与指令变化速率取较小者；还受最短过渡 ${v.MIN_TRANSITION_S}s 和停留影响。数值框没有额外速度上限，但这不代表电机可达到任意速度；官方未在控制指南给出最高关节速度。1000Hz是发送频率。`,`Configured trajectory peak ${peak} rad/s ≈ ${(peak*180/Math.PI).toFixed(1)}°/s at 1×. The lower of planning/command speed applies, with ${v.MIN_TRANSITION_S}s minimum transition and pose holds. There is no additional speed cap in the input, but this is not an achievable motor-speed guarantee. The control guide does not specify a maximum joint speed. 1000 Hz is the command rate.`);}catch{speedBody.textContent=text('速度设置读取失败','Could not read speed settings');}}
  window.addEventListener('wuji-language',()=>{render();viewerLabels();speedSummary();});
  window.addEventListener('workspace-page',()=>{L.apply();staticEnglish();if(location.hash==='#parameters')speedSummary();});
  window.addEventListener('console-state',e=>{state=e.detail;picker.setBusy($('trial-action').disabled);$('warning-policy').dataset.hasWarnings=String(state.connection==='connected'&&!!state.hardware?.warnings?.length);if(location.hash==='#interaction')touchState();staticEnglish();});
  window.addEventListener('console-offline',()=>{state=null;touchState();});
  fetch('/api/catalog').then(r=>{if(!r.ok)throw Error('Action catalog unavailable');return r.json();}).then(data=>{catalog=data;render();}).catch(notice);
  render();viewerLabels();speedSummary();ws.showPage();
})();
