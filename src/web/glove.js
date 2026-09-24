(()=>{
 'use strict';
 const ws=window.WujiWorkspace,$=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale.lang==='en'?en:zh;
 const page=document.createElement('section');page.id='page-glove';page.className='workspace-page';page.hidden=true;
 page.innerHTML=`<header class="page-heading"><h2 id="glove-title"></h2><p id="glove-subtitle"></p></header>
 <div class="page-body"><div class="glove-layout"><section class="panel glove-controls">
 <div class="glove-step"><span>01</span><h3 id="glove-receive-title"></h3></div>
 <p class="hint" id="glove-profile"></p><div class="button-row"><button id="glove-scan"></button><a href="#connection" id="glove-config"></a></div>
 <label for="glove-device" id="glove-device-label"></label><select id="glove-device"></select>
 <label for="glove-user" id="glove-user-label"></label><select id="glove-user"></select>
 <label for="glove-hand-device" id="glove-hand-device-label"></label><select id="glove-hand-device"></select>
 <p id="glove-pair-note" class="hint"></p>
 <p class="hint" id="glove-user-note"></p>
 <label for="glove-timeout" id="glove-timeout-label"></label><div class="glove-input-unit"><input id="glove-timeout" type="number" min="100" max="2000" step="50" value="250"><span>ms</span></div>
 <div class="button-row"><button id="glove-open" class="primary"></button><button id="glove-disconnect"></button></div>
 <div class="glove-step"><span>02</span><h3 id="glove-follow-title"></h3></div>
 <p id="glove-follow-note" class="hint"></p>
 <label for="glove-hand-address" id="glove-address-label"></label><input id="glove-hand-address" autocomplete="off" spellcheck="false">
 <button id="glove-prepare"></button>
 <label class="glove-confirm"><input type="checkbox" id="glove-clear"><span id="glove-clear-label"></span></label>
 <div class="button-row"><button id="glove-follow" class="primary"></button><button id="glove-stop" class="danger"></button></div>
 <p id="glove-status" role="status" aria-live="polite"></p><p id="glove-command-status" role="alert"></p>
 <details class="motion-notes"><summary id="glove-parameters-title"></summary><p id="glove-parameters"></p><a href="#parameters" id="glove-parameters-link"></a></details>
 </section><div class="glove-display"><div class="glove-metrics"><div><span id="glove-rate-label"></span><strong id="glove-rate">—</strong></div><div><span id="glove-age-label"></span><strong id="glove-age">—</strong></div><div><span id="glove-send-label"></span><strong id="glove-send">—</strong></div></div><div id="glove-viewer-dock" class="viewer-dock"></div><p id="glove-rate-note" class="hint"></p></div></div>
 <details class="studio-info glove-platform"><summary id="glove-platform-title"></summary><p id="glove-platform-text"></p><p id="glove-cli-note"></p><div class="button-row"><a id="glove-sdk-doc" href="https://github.com/wuji-technology/wuji-sdk/blob/main/examples/python/retargeting/1.teleop_real.py" target="_blank" rel="noopener">SDK ↗</a><a id="glove-calib-doc" href="https://docs.wuji.tech/docs/en/wuji-glove/latest/sdk-data-reference/calibration/" target="_blank" rel="noopener"></a></div></details>
 <details class="studio-info glove-joints"><summary id="glove-joints-title"></summary><div class="glove-table-wrap"><table><thead><tr><th id="glove-joint-label"></th><th id="glove-target-label"></th><th id="glove-applied-label"></th><th id="glove-measured-label"></th></tr></thead><tbody id="glove-joint-rows"></tbody></table></div></details></div>`;
 document.querySelector('.page-area').append(page);ws.views.glove=page.querySelector('.page-body');
 const link=document.createElement('a');link.href='#glove';link.dataset.page='glove';link.innerHTML='<svg viewBox="0 0 26 24" aria-hidden="true"><path d="M5 19V10a2 2 0 0 1 4 0V5a2 2 0 0 1 4 0v5-7a2 2 0 0 1 4 0v9-5a2 2 0 0 1 4 0v9c0 5-3 7-7 7H9l-6-6"/></svg><span></span>';
 document.querySelector('.directory').insertBefore(link,document.querySelector('[data-page="feedback"]'));
 let state=null,pending=false,lease=null,leaseAt=0,seen=false,optionsKey='',lastMessage='',savedBinding=null;
 fetch('/api/retarget/context').then(r=>r.json()).then(c=>{savedBinding=c.binding;optionsKey='';render()}).catch(()=>{});
 const sdkHead=document.createElement('th');sdkHead.id='glove-sdk-target-label';$('glove-target-label').before(sdkHead);
 const f=(v,d=1)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
 const labels={
 'glove-hand-device-label':['配对机械手','Paired robot hand'],'glove-pair-note':['先选择手套、用户及机械手，再打开预览。多只同侧设备按编号区分；仅一只时可自动选择。','Choose glove, user and hand before preview. Multiple same-side devices are identified by serial; a single device can be selected automatically.'],'glove-sdk-target-label':['求解器原始角度','Raw solver angles'],
 'glove-title':['手套遥操作','Glove teleoperation'],'glove-subtitle':['手套接收 → 映射预览 → 真实手跟随','Glove input → Mapping preview → Real-hand follow'],
 'glove-receive-title':['连接与预览','Connect and preview'],'glove-scan':['扫描手套','Scan gloves'],'glove-config':['控制端设置','Controller settings'],
 'glove-device-label':['选择手套','Select glove'],'glove-user-label':['标定用户','SDK user profile'],'glove-user-note':['选择完成标定时使用的用户。Default 使用内置手型；选择具名用户也不代表已经完成标定。','Choose the profile used for calibration. Default uses the built-in hand model; a named profile alone does not prove calibration.'],
 'glove-timeout-label':['手套失联后停用','Disable after glove timeout'],'glove-open':['连接手套并预览','Connect glove and preview'],'glove-disconnect':['断开全部','Disconnect all'],
 'glove-follow-title':['机械手跟随','Real-hand follow'],'glove-follow-note':['先连接机械手反馈，检查手套与设备的左右手是否一致，再由你开启跟随。','Prepare hand feedback and verify matching hand sides before starting follow.'],
 'glove-address-label':['机械手地址（可空，按所选左右手发现）','Hand address (optional; discover by selected side)'],'glove-prepare':['连接机械手反馈','Prepare hand feedback'],
 'glove-clear-label':['底座已固定，机械手活动空间已清空','Base secured and robotic hand workspace clear'],
 'glove-follow':['开始跟随','Start follow'],'glove-stop':['停止跟随并停用','Stop follow and disable'],
 'glove-parameters-title':['当前控制端参数','Active controller parameters'],'glove-parameters-link':['前往调整参数','Adjust parameters'],
 'glove-rate-label':['映射更新率','Mapping rate'],'glove-age-label':['手套数据龄期','Glove data age'],'glove-send-label':['实际指令发送率','Measured command rate'],
 'glove-rate-note':['手套骨架标称 120Hz，机械手反馈可达 1000Hz。发送频率沿用你的参数设置，页面分别显示实测值；1000Hz发送不会产生更多手套采样。','Nominal glove skeleton rate is 120Hz; hand feedback can reach 1000Hz. Command timing follows your settings. Measured rates are separate; sending at 1000Hz creates no extra glove samples.'],
 'glove-platform-title':['是否需要 Ubuntu？','Do I need Ubuntu?'],
 'glove-platform-text':['Windows 使用工作台管理的内置 Linux 控制端；也可选择实体 Linux 控制电脑。Ubuntu 可本机运行。Mac 共用界面和 Linux 控制接口，本版未完成 Mac 实机验收。使用官方映射库，无需 ROS 2。一代 USB 手仍需先让 Linux 控制端能访问设备，不能仅凭 Windows 已插入就认定可用。','Windows uses the workbench-managed Linux runtime or an external Linux controller. Ubuntu runs locally. Mac shares the UI and Linux interface but is not hardware-validated in this release. Official mapping requires no ROS 2. A Hand 1 USB device must be accessible inside the Linux controller; plugging it into Windows alone is insufficient.'],
 'glove-cli-note':['Windows 官方 CLI 也可扫描、读取和标定手套；这与完整遥操作不同。当前官方 RetargetSession 支持 Linux x86_64 / ARM64。纯 Windows / Mac 实机跟随尚未实现。','The official Windows CLI can scan, read and calibrate a glove. Complete teleoperation is different: official RetargetSession currently supports Linux x86_64/ARM64. Native Windows/Mac hardware follow is not implemented.'],
 'glove-calib-doc':['官方标定说明 ↗','Official calibration guide ↗'],
 'glove-joints-title':['目标与反馈对照（rad）','Target and feedback comparison (rad)'],'glove-joint-label':['关节','Joint'],'glove-target-label':['手套映射目标','Glove target'],'glove-applied-label':['实际发送目标','Sent target'],'glove-measured-label':['实测角度','Measured angle']};
 function translate(){for(const [id,pair] of Object.entries(labels))$(id).textContent=t(...pair);link.lastElementChild.textContent=t('手套遥操作','Glove teleop');render();}
 function render(){
  const g=state?.glove||{},hw=g.hardware||{},stream=g.stream||{},feedback=g.feedback||{},receiving=g.connection==='receiving',connecting=g.connection==='connecting';
  $('glove-profile').textContent=state?.device_profile?.[window.WujiLocale.lang==='en'?'en':'zh']||'';
  const key=JSON.stringify([g.devices,g.hands,g.users,g.user?.user_id,state?.device_profile?.id]);
  if(key!==optionsKey){optionsKey=key;const old=$('glove-device').value;$('glove-device').replaceChildren(new Option(t('请选择手套','Select a glove'),''));for(const d of g.devices||[])$('glove-device').add(new Option(`${d.serial} · ${d.address}`,d.serial));if([...$('glove-device').options].some(o=>o.value===old))$('glove-device').value=old;else if(g.devices?.length===1)$('glove-device').value=g.devices[0].serial;
   const previousUser=$('glove-user').value;$('glove-user').replaceChildren();for(const u of g.users||[])$('glove-user').add(new Option(u.display_name+(u.is_default?' · Default':''),u.user_id));$('glove-user').value=previousUser||savedBinding?.sdk_user||g.user?.user_id||'';
   const handChoice=$('glove-hand-device').value||savedBinding?.hand_serial||'';
   const compatible=(g.hands||[]).filter(h=>h.generation===state?.device_profile?.generation&&(!h.side_hint||h.side_hint===state?.device_profile?.side));
   $('glove-hand-device').replaceChildren(new Option(t('自动选择单只设备','Auto-select a single device'),''),...compatible.map(d=>new Option(d.serial+' · '+d.address,d.serial)));
   if(handChoice&&![...$('glove-hand-device').options].some(o=>o.value===handChoice))$('glove-hand-device').add(new Option(handChoice+' · '+t('保存的配对，连接时验证','Saved pairing; verified on connect'),handChoice));
   $('glove-hand-device').value=handChoice;
  }
  if($('glove-device').options[0])$('glove-device').options[0].textContent=t('请选择手套','Select a glove');
  if(g.busy){$('shell-connection').href='#glove';$('shell-connection').textContent=hw.active?t('手套跟随中','Glove following'):receiving?t('手套已连接','Glove connected'):t('手套控制端','Glove controller');}
  else $('shell-connection').href='#connection';
  $('glove-scan').disabled=pending||connecting||receiving;
  $('glove-open').disabled=pending||g.connection!=='ready'||!$('glove-device').value;
  for(const id of ['glove-device','glove-user','glove-hand-device','glove-timeout'])$(id).disabled=pending||receiving||connecting;
  $('glove-disconnect').disabled=pending||!g.busy;
  $('glove-prepare').disabled=pending||!stream.fresh||!!feedback.device_id||hw.active!==undefined;
  $('glove-follow').disabled=pending||!!lease||!stream.fresh||feedback.stale||!hw.probe_ready||hw.active!==false||!$('glove-clear').checked;
  $('glove-stop').disabled=!g.busy||(!lease&&hw.active===false&&!hw.stop_confirmed);
  $('glove-rate').textContent=f(stream.retarget_hz)+' Hz';$('glove-age').textContent=f(stream.age_ms,0)+' ms';$('glove-send').textContent=f(hw.active?hw.command_timing?.host_publish_hz:null)+' Hz';
  $('glove-status').textContent=hw.active?t('正在跟随 · ','Following · ')+(hw.reason||''):lease?t('正在准备启动…','Starting…'):g.message||t('尚未连接；扫描只发现手套，不启用电机。','Not connected. Scanning never enables motors.');
  $('glove-command-status').textContent=lastMessage||((hw.reason&&hw.phase==='stopped')?hw.reason:'');
  const p=g.parameters;
  $('glove-parameters').textContent=p?`Kp ${f(p.kp,3)} · Kd ${f(p.kd,3)} · ${f(p.current_limit_A,2)} A · ${p.editable_parameters?.COMMAND_RATE_HZ??'—'} Hz · ${t('速度','Speed')} ${f(hw.max_velocity_rad_s??Math.min(p.editable_parameters?.COMMAND_SPEED_RAD_S,p.editable_parameters?.PATH_SPEED_RAD_S),3)} rad/s`:t('扫描后显示控制端实际加载的参数；不会替你更换增益。','Shows loaded controller parameters after scan; gains are not replaced with a preset.');
  if(state?.device_profile?.generation==='hand1')$('glove-parameters').textContent=`LowPass ${f(hw.lowpass_cutoff_hz,1)} Hz · ${f(hw.current_limit_A,2)} A · ${hw.command_timing?.requested_hz??'—'} Hz · ${t('速度','Speed')} ${f(hw.max_velocity_rad_s,3)} rad/s · ${t('Kp/Kd 不适用','Kp/Kd not applicable')}`;
  $('glove-follow-note').textContent=state?.device_profile?.generation==='hand1'?t('一代手通过官方 LowPass 接口跟随。该接口不提供二代手的 MIT 参数、电流反馈和关节诊断；软件测试不代表实机验收。','Hand 1 follows through official LowPass. Hand 2 MIT gains/current feedback/diagnostics are not available through this interface. Software tests do not establish hardware validation.'):t('先连接机械手反馈，检查手套与设备的左右手是否一致，再由你开启跟随。','Prepare hand feedback and verify matching hand sides before starting follow.');
  if(lease){if(hw.active)seen=true;else if(hw.active===false&&(seen||Date.now()-leaseAt>5000))lease=null;}
  const tbody=$('glove-joint-rows');if(tbody.closest('details').open){tbody.replaceChildren();const rows=new Map((feedback.latest?.joints||[]).map(j=>[j.nid,j.position_rad]));for(let i=0;i<20;i++){const tr=document.createElement('tr');const nid=state?.device_profile?.generation==='hand1'?i:Math.floor(i/4)*5+i%4+1;for(const text of [String(i+1),f(stream.fresh?stream.sdk_q?.[i]:null,3),f(stream.fresh?stream.q?.[i]:null,3),f(hw.active?hw.applied_target_rad?.[i]:null,3),f(!feedback.stale?rows.get(nid):null,3)]){const td=document.createElement('td');td.textContent=text;tr.append(td);}tbody.append(tr);}}
 }
 async function post(c){if(!state?.csrf)throw Error(t('服务未就绪','Service not ready'));const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify(c)});const d=await r.json();if(!r.ok)throw Error(d.error);return d;}
 async function act(c){pending=true;lastMessage='';render();try{
  if(c.name==='glove_open'){const profile=state.device_profile;const binding={generation:profile.generation,side:profile.side,glove_serial:c.serial,sdk_user:c.user_id,hand_serial:$('glove-hand-device').value};await post({name:'retarget_binding_save',binding});savedBinding=binding;window.dispatchEvent(new Event('retarget-binding-changed'));}
  const d=await post(c);if(d.lease){lease=d.lease;leaseAt=Date.now();seen=false;}if(c.name==='glove_stop'||c.name==='glove_disconnect')lease=null;}catch(e){lastMessage=e.message;}finally{pending=false;render();}}
 $('glove-scan').onclick=()=>act({name:'glove_scan'});
 $('glove-open').onclick=()=>act({name:'glove_open',serial:$('glove-device').value,user_id:$('glove-user').value,timeout_ms:Number($('glove-timeout').value)});
 $('glove-prepare').onclick=()=>act({name:'glove_prepare',address:$('glove-hand-address').value.trim(),serial:$('glove-hand-device').value});
 $('glove-follow').onclick=()=>act({name:'glove_follow',workspace_clear:$('glove-clear').checked});
 $('glove-stop').onclick=()=>act({name:'glove_stop'});$('glove-disconnect').onclick=()=>act({name:'glove_disconnect'});
 $('glove-device').onchange=render;$('glove-clear').onchange=render;
 window.addEventListener('console-state',e=>{state=e.detail;render();});window.addEventListener('wuji-language',translate);
 window.addEventListener('console-offline',()=>{lease=null;lastMessage=t('工作台服务已离线','Workbench service offline');render();});
 setInterval(async()=>{if(!lease)return;try{await post({name:'glove_keepalive',lease});}catch(e){lease=null;lastMessage=e.message;render();}},250);
 window.addEventListener('pagehide',()=>{if(lease)fetch('/api/action',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name:'glove_stop'})}).catch(()=>{});});
 translate();ws.showPage();
})();
