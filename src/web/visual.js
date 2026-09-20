(() => {
  'use strict';
  const fingers = ['拇指','食指','中指','无名指','小指'];
  const host = document.createElement('section');
  host.id = 'pose-console';
  host.innerHTML = `<div class="pose-heading"><div><h2>MuJoCo · 姿态同步</h2><p id="pose-message">正在载入原生左手模型</p></div><span id="render-rate">画面刷新 —</span></div>
    <div class="pose-screen"><img id="pose-image" alt="原生手部 MuJoCo 模型预览"><div id="pose-unavailable" hidden>画面连接中断</div><span id="pose-frame">未接收实机反馈</span></div>
    <div class="joint-legend"><strong>20个关节 · 点击定位</strong><span>灰：无反馈　橙：角度预览待核对　青：已核对同步</span></div>
    <div id="joint-grid"></div>
    <p class="rate-note">回报率按每个关节实际收到的数据统计。三维渲染目标20帧/秒，与设备反馈频率分开显示。</p>
    <details id="joint-detail" open><summary>每关节反馈与完整性</summary><p class="rate-note">缺项：收到整帧却没有该关节；序号缺口：包括传输中缺失的整帧。统计窗口为最近1秒。</p><div class="joint-table-scroll"><table><thead><tr><th>设备关节</th><th>收到 Hz</th><th>时间戳 Hz</th><th>新鲜度 ms</th><th>缺项 / 缺口</th><th>位置 rad</th><th>速度 rad/s</th><th>电流 A</th></tr></thead><tbody id="joint-detail-body"></tbody></table></div></details>
    <details id="mapping-editor"><summary>核对三维关节对应 · 只影响画面</summary><p>首次连接后，逐个核对设备编号对应哪个模型关节、运动方向和零位，再勾选保存。橙色为尚待核对的角度预览，灰色为缺少对应或反馈。此设置不向机械手发送动作。</p><p id="mapping-device">连接设备后可配置，映射按设备分别保存。</p><div class="joint-table-scroll"><table><thead><tr><th>模型关节</th><th>设备编号</th><th>方向</th><th>偏移 rad</th><th>已核对</th></tr></thead><tbody id="mapping-rows"></tbody></table></div><button id="save-mapping" type="button" disabled>保存显示映射</button><p id="mapping-notice" role="status"></p></details>`;
  const slot = document.getElementById('visual-slot') || document.querySelector('main') || document.body;
  slot.append(host);
  const byId = id => document.getElementById(id);
  let state = null, selected = null, mappingKey = '', mappingPending = false;
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
    if(!state?.csrf)throw new Error('本机控制服务未连接');
    const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});
    const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'操作未完成');
  }
  const fmt=(v,d=0)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
  function clearVisual(message) {
    byId('pose-message').textContent=message;
    byId('pose-unavailable').hidden=false;
    byId('pose-image').classList.add('view-stale');
    byId('pose-frame').textContent='画面过期 · 不代表当前姿态';
    byId('render-rate').textContent='画面刷新 —';
    for(const c of cells){c.value.textContent='—';c.status.textContent='无新反馈';c.bar.hidden=true;c.button.classList.remove('live','provisional');}
    byId('joint-detail-body').replaceChildren();
  }
  async function showFrame(payload) {
    const meta=payload.meta;
    if(!meta.ready||!payload.image){clearVisual(meta.message||'三维画面暂不可用');return;}
    const decoded=new Image();decoded.src=payload.image;await decoded.decode();
    byId('pose-image').src=payload.image;
    byId('pose-image').classList.remove('view-stale');
    byId('pose-unavailable').hidden=true;
    byId('pose-message').textContent=meta.message;
    byId('render-rate').textContent=`MuJoCo 渲染 ${fmt(meta.render_hz,1)} 帧/秒`;
    byId('pose-frame').textContent=meta.mode==='demo'?'仿真编排 · 不驱动实机':meta.source_seq==null?'模型预览 · 未接收实机姿态':`实机反馈帧 ${meta.source_seq} · 图表与姿态使用同一帧`;
    const rates=new Map((meta.feedback?.joint_rates||[]).map(r=>[r.nid,r]));
    const readings=new Map((meta.feedback?.latest?.joints||[]).map(r=>[r.nid,r]));
    for(const j of meta.joints) {
      const c=cells[j.index],r=rates.get(j.nid),live=['live','provisional'].includes(j.status);
      c.value.textContent=live?`${fmt(j.hz)} Hz`:'—';
      c.status.textContent=live?`编号 ${j.nid}${j.status==='provisional'?' · 待核对':''}`:({demo:'编排姿态',unmapped:'待核对',stale:'无新反馈',out_of_range:'角度超出模型范围'}[j.status]||'待核对');
      c.button.classList.toggle('provisional',j.status==='provisional');
      c.button.classList.toggle('live',live);c.bar.hidden=!live||j.hz==null;if(!c.bar.hidden)c.bar.value=j.hz;
      c.button.title=`${j.label}，${c.status.textContent}，回报率 ${c.value.textContent}`;
    }
    const rows=[];
    for(const r of rates.values()) {
      const q=readings.get(r.nid),tr=document.createElement('tr');
      const values=[String(r.nid),fmt(r.host_hz),fmt(r.device_hz),fmt(r.age_ms,1),`${r.missing_in_received_frames??'—'} / ${r.missing_stream_slots??'—'}`,fmt(q?.position_rad,3),fmt(q?.velocity_rad_s,3),fmt(q?.effort_A,3)];
      for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}rows.push(tr);
    }
    if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=8;td.textContent='尚无实机数据';tr.append(td);rows.push(tr);}
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
      await action({name:'display_mapping',entries});byId('mapping-notice').textContent=`已保存${entries.length}个显示对应，没有发送机械手动作。`;
    }catch(e){byId('mapping-notice').textContent=e.message;}
    finally{mappingPending=false;}
  });
  window.addEventListener('console-state',event=>{state=event.detail;mappingForm(state);});
  window.addEventListener('console-offline',()=>{state=null;byId('save-mapping').disabled=true;clearVisual('本机服务连接中断');});
  async function pollView(){
    try{const r=await fetch('/api/view',{cache:'no-store',signal:AbortSignal.timeout(2500)});if(!r.ok)throw Error();await showFrame(await r.json());}
    catch{clearVisual('画面连接中断，请检查本机控制服务');}
    finally{setTimeout(pollView,50);}
  }
  pollView();
})();
