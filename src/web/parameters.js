(() => {
  'use strict';
  const root=document.querySelector('#page-parameters .page-body');
  root.innerHTML=`<div class="parameter-layout"><div class="parameter-main"><div class="parameter-tabs" role="tablist" aria-label="参数类别"><button role="tab" id="tab-motor" data-group="motor" aria-selected="true" aria-controls="fields-motor">电机响应</button><button role="tab" id="tab-speed" data-group="speed" aria-selected="false" aria-controls="fields-speed" tabindex="-1">轨迹与速度</button><button role="tab" id="tab-time" data-group="time" aria-selected="false" aria-controls="fields-time" tabindex="-1">停留与时长</button></div><div id="parameter-fields"></div><p class="parameter-info">保存不启动灵巧手。同步时会断开并重新接入当前手，下一次手动播放才使用新设置。</p><p id="parameter-error" role="alert" hidden></p><div class="parameter-secondary"><button id="parameter-reload" type="button">重读本机保存值</button><button id="parameter-initial" type="button">填入初始值</button><span>填入后仍需保存</span></div></div><aside class="parameter-help"><h3>这些参数是什么？</h3><p>Kp决定追目标的响应强度，Kd提供阻尼。它们与速度、电流上限共同影响动作。</p><p>这里显示的是参数设置；实测电流与关节状态请看“实时反馈”。</p><a href="https://docs.wuji.tech/docs/en/wuji-hand/latest/control-guide/" target="_blank" rel="noopener">官方控制说明 ↗</a><dl><dt>本机保存</dt><dd id="parameter-local-state">正在读取</dd><dt>控制端同步</dt><dd id="parameter-remote-state">尚未读取</dd><dt>会话加载</dt><dd id="parameter-loaded-state">未连接</dd></dl></aside></div><div class="parameter-footer"><button id="parameter-save" class="primary" disabled>保存参数</button><button id="parameter-sync" disabled>同步到控制端</button><span id="parameter-status" role="status">正在读取参数</span><a id="parameter-connect" href="#home">前往连接</a></div>`;
  // Present the three different persistence stages before the form.
  const stages=root.querySelector('.parameter-help dl');stages.className='parameter-states';root.prepend(stages);
  const tools=document.createElement('details');tools.className='parameter-tools';tools.innerHTML='<summary>参数说明与更多操作</summary>';
  tools.append(root.querySelector('.parameter-help'),root.querySelector('.parameter-info'),root.querySelector('.parameter-secondary'));
  root.querySelector('.parameter-layout').after(tools);
  const search=document.createElement('div');search.className='parameter-searchbar';
  search.innerHTML='<label class="sr-only" for="parameter-search">搜索全部参数</label><input id="parameter-search" type="search" placeholder="搜索参数：Kp、电流、速度…" autocomplete="off"><button id="parameter-changed-only" aria-pressed="false">只看已修改 <span id="parameter-change-count">0</span></button>';
  root.querySelector('.parameter-tabs').before(search);
  const results=document.createElement('p');results.id='parameter-filter-state';results.className='hint';results.setAttribute('role','status');results.hidden=true;
  root.querySelector('#parameter-fields').before(results);
  const empty=document.createElement('div');empty.id='parameter-empty';empty.hidden=true;empty.innerHTML='<strong id="parameter-empty-text"></strong><button id="parameter-clear-filter">清除筛选</button>';
  root.querySelector('#parameter-fields').after(empty);
  const get=id=>document.getElementById(id);let config=null,revision=null,saved={},inputs={},rows={},loaded={},state=null,dirty=false,busy=false,fetching=false,group='motor',conflict=false,readVersion=0,query='',changedOnly=false,requestEpoch=0,postSlot=null,readSlot=null,configToken=null,syncErrorContext=null,canonical=null;
  const groupNames={motor:'电机响应',speed:'轨迹与速度',time:'停留与时长'};
  const changed=key=>inputs[key].value.trim()===''||!Number.isFinite(inputs[key].valueAsNumber)||inputs[key].valueAsNumber!==saved[key];
  function filter(){
    const searching=!!query||changedOnly;let total=0;
    for(const [key,name] of Object.entries(groupNames)){
      const panel=get('fields-'+key);if(!panel)continue;let count=0;
      for(const field of config.fields.filter(f=>f.group===key)){
        const match=(!changedOnly||changed(field.key))&&(!query||[field.key,field.label,field.description,field.advice,name].join(' ').toLocaleLowerCase().includes(query));
        rows[field.key].hidden=!match;count+=Number(match);
      }
      panel.hidden=searching?!count:key!==group;panel.querySelector('.parameter-group-title').hidden=!searching;
      if(!panel.hidden)total+=count;
    }
    for(const button of root.querySelectorAll('[data-group]')){button.setAttribute('aria-selected',String(!searching&&button.dataset.group===group));button.tabIndex=button.dataset.group===group?0:-1;}
    results.hidden=!searching;results.textContent=`全部类别 · 找到 ${total} 项${changedOnly?'已修改参数':'参数'}`;
    empty.hidden=total>0||!config;get('parameter-empty-text').textContent=changedOnly&&!query?'当前没有未保存的修改':'未找到匹配的设置项，请尝试其他关键词';
  }
  function error(message){get('parameter-error').textContent=message;get('parameter-error').hidden=!message;syncErrorContext=null;}
  function setGroup(next){group=next;query='';changedOnly=false;get('parameter-search').value='';get('parameter-changed-only').setAttribute('aria-pressed','false');filter();}
  get('parameter-search').addEventListener('input',e=>{query=e.target.value.trim().toLocaleLowerCase();filter();});
  get('parameter-changed-only').addEventListener('click',()=>{changedOnly=!changedOnly;get('parameter-changed-only').setAttribute('aria-pressed',String(changedOnly));filter();});
  get('parameter-clear-filter').addEventListener('click',()=>setGroup(group));
  for(const b of root.querySelectorAll('[data-group]'))b.addEventListener('click',()=>setGroup(b.dataset.group));
  root.querySelector('.parameter-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const tabs=[...root.querySelectorAll('[data-group]')],i=tabs.findIndex(b=>b.dataset.group===group);const n=e.key==='Home'?0:e.key==='End'?2:(i+(e.key==='ArrowRight'?1:2))%3;setGroup(tabs[n].dataset.group);tabs[n].focus();});
  function draft(){const v={};for(const [key,input] of Object.entries(inputs)){if(input.value.trim()===''||!Number.isFinite(input.valueAsNumber))throw new Error('请填写有效数字：'+input.getAttribute('aria-label'));v[key]=input.valueAsNumber;}return v;}
  function updateDirty(){try{const v=draft();dirty=Object.keys(v).some(k=>v[k]!==saved[k]);}catch{dirty=true;}render();}
  function build(){
    get('parameter-fields').replaceChildren();inputs={};rows={};
    for(const key of ['motor','speed','time']){
      const section=document.createElement('section');section.dataset.fields=key;section.id='fields-'+key;section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby','tab-'+key);
      section.innerHTML='<h3 class="parameter-group-title"></h3><div class="parameter-column-head"><span>参数 / 本机保存值</span><span>当前编辑值</span><span>会话已加载</span></div>';section.querySelector('h3').textContent=groupNames[key];
      for(const field of config.fields.filter(f=>f.group===key)){
        const row=document.createElement('div');row.className='parameter-row';rows[field.key]=row;
        const label=document.createElement('label');label.htmlFor='parameter-'+field.key;const strong=document.createElement('strong');strong.textContent=field.label;const desc=document.createElement('span');desc.textContent='本机 '+saved[field.key]+(field.unit?' '+field.unit:'');label.append(strong,desc);
        const box=document.createElement('div');box.className='parameter-input';const input=document.createElement('input');input.type='number';input.step='any';input.required=true;input.id='parameter-'+field.key;input.setAttribute('aria-label',field.label);input.setAttribute('aria-describedby','advice-'+field.key);input.value=config.values[field.key];input.addEventListener('input',()=>{error('');updateDirty();});inputs[field.key]=input;box.append(input);if(field.unit){const unit=document.createElement('span');unit.textContent=field.unit;box.append(unit);}
        const live=document.createElement('output');live.dataset.loaded=field.key;live.setAttribute('aria-label',field.label+'已加载值');live.textContent='—';
        const detail=document.createElement('details');detail.className='parameter-detail';const summary=document.createElement('summary');summary.textContent='说明';
        const help=document.createElement('p');help.id='advice-'+field.key;help.textContent=field.description+' '+field.advice;detail.append(summary,help);
        const restore=document.createElement('button');restore.type='button';restore.className='parameter-restore';restore.textContent='撤销修改';restore.title='将此项恢复为已保存的值（不是默认值）';restore.setAttribute('aria-label','撤销'+field.label+'的修改');restore.hidden=true;
        restore.addEventListener('click',()=>{input.value=saved[field.key];error('');updateDirty();});row.append(label,box,live,detail,restore);section.append(row);
      }get('parameter-fields').append(section);
    }filter();
  }
  function render(){
    const sync=state?.parameter_sync||config?.sync||{},syncing=sync.busy===true,cleanup=syncing&&sync.cleanup_required===true;
    const connected=state?.connection==='connected'&&!state.stale;
    loaded=connected?(state.hardware?.commissioning_policy?.editable_parameters||{}):{};
    for(const output of root.querySelectorAll('[data-loaded]'))output.textContent=Number.isFinite(loaded[output.dataset.loaded])?String(loaded[output.dataset.loaded]):'—';
    const count=Object.keys(inputs).filter(changed).length;
    for(const key of Object.keys(inputs)){const modified=changed(key);rows[key].classList.toggle('is-modified',modified);const restore=rows[key].querySelector('.parameter-restore');restore.hidden=!modified;restore.disabled=busy||syncing;}
    get('parameter-change-count').textContent=count;
    const nav=document.querySelector('.directory a[data-page="parameters"]');nav?.classList.toggle('has-draft',dirty);if(nav)nav.title=dirty?`${count}项参数尚未保存`:'参数调节';
    get('parameter-save').disabled=!config?.values||!state?.csrf||configToken!==state.csrf||busy||syncing||!dirty||conflict;
    get('parameter-save').textContent=count?`保存 ${count} 项修改`:'保存参数';
    const owner=state?.connection_switch?.current_owner,identity=state?.identified_device,exactHand=state?.connection==='connected'&&owner?.mode==='hand'&&owner.serial===state.device_id&&owner.session===state.session_generation&&identity?.source==='sdk_identity'&&identity.serial===owner.serial&&identity.profile===state.device_profile?.id;
    let validDraft=true;try{draft();}catch{validDraft=false;}
    get('parameter-sync').textContent=cleanup?'重试结束同步':'同步到控制端';
    get('parameter-sync').disabled=!config?.values||!state?.csrf||configToken!==state.csrf||busy||syncing&&!cleanup||!cleanup&&(!validDraft||conflict)||cleanup&&(!canonical?.values||!/^[a-f0-9]{64}$/.test(canonical.revision||''))||!/^[a-f0-9]{64}$/.test(state?.connection_switch?.expected_owner||'')||!(state.connection==='disconnected'||exactHand)||state.hardware?.active!==false||state.hardware?.stop_confirmed===false||state.recording?.active===true||state.program?.active===true||state.group?.active===true||state.glove?.busy===true;
    get('parameter-reload').disabled=busy||syncing;get('parameter-initial').disabled=!config?.values||busy||syncing;
    for(const input of Object.values(inputs))input.disabled=busy||syncing;
    get('parameter-local-state').textContent=conflict?'文件有外部更新':dirty?`${count}项未保存`:config?.values?'已保存':'读取失败';
    get('parameter-remote-state').textContent=!state?.csrf||configToken!==state.csrf?'尚未核对':syncing?'同步中':sync.state==='failed'?'同步失败':sync.revision===revision?sync.reconnect_state==='failed'?'已同步 · 上次重新接入未完成':'与本机一致':'待同步';
    get('parameter-loaded-state').textContent=!connected?'未连接':Object.keys(saved).every(k=>loaded[k]===saved[k])?'与本机一致':'使用另一组参数';
    get('parameter-status').textContent=!state?'本机服务未连接':configToken!==state.csrf?'正在核对参数状态':syncing?(sync.message||'正在同步并重新接入，不会启动动作'):conflict?'文件已更新；请先重读再修改':dirty?`${count}项修改尚未保存`:sync.state==='failed'?sync.message:sync.reconnect_state==='failed'?'参数已同步；上次重新接入未完成。':sync.revision===revision?'已同步':exactHand?'同步时将重新接入当前灵巧手':'已保存 · 待同步';filter();
  }
  function retireRequests(){requestEpoch++;readVersion++;postSlot=readSlot=null;busy=fetching=false;}
  function postOwn(own){return postSlot===own&&requestEpoch===own.epoch&&state?.csrf===own.token;}
  async function read(force=false){
    if(fetching||busy)return;const own={epoch:requestEpoch,version:readVersion,token:state?.csrf||null};readSlot=own;fetching=true;
    const owns=()=>readSlot===own&&own.epoch===requestEpoch&&own.version===readVersion&&(state?.csrf||null)===own.token;
    try{const r=await fetch('/api/parameters',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('参数读取失败');const next=await r.json();if(!owns())return;if(next.error)throw Error(next.error);
      canonical=next;let sameDraft=false;try{const current=draft();sameDraft=next.values&&Object.keys(current).length===Object.keys(next.values).length&&Object.keys(current).every(k=>current[k]===next.values[k]);}catch{}
      if(!config||force||next.revision!==revision&&(!dirty||sameDraft)){config=next;revision=next.revision;saved={...next.values};dirty=false;conflict=false;build();}else{conflict=dirty&&next.revision!==revision;config.sync=next.sync;}configToken=own.token;render();
    }catch(e){if(owns())error(e.message);}finally{if(readSlot===own){readSlot=null;fetching=false;}}
  }
  async function post(body,options){
    if(busy||postSlot||!state?.csrf||configToken!==state.csrf)return;const own={epoch:requestEpoch,token:state.csrf,owner:body.expected_owner||null};postSlot=own;busy=true;readVersion++;readSlot=null;fetching=false;error('');render();
    try{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':own.token},body:JSON.stringify(body),signal:AbortSignal.timeout(body.name==='parameters_sync_reconnect'?60000:6000)});const result=await r.json();if(!postOwn(own))return;
      if(own.owner&&state.connection_switch?.expected_owner!==own.owner&&state.connection_switch?.expected_owner!==result.connection_switch?.expected_owner)return;
      if(!r.ok||!result.ok)throw Error(result.error||'操作未完成');if(!result.parameters?.values||typeof result.parameters.revision!=='string')throw Error('参数结果尚未核对，请重新读取。');
      config=result.parameters;canonical=config;configToken=own.token;revision=config.revision;saved={...config.values};dirty=false;conflict=false;build();if(options?.preserveDraft){for(const [key,value]of Object.entries(options.preserveDraft))if(inputs[key])inputs[key].value=value;dirty=Object.keys(inputs).some(key=>inputs[key].value.trim()===''||!Number.isFinite(inputs[key].valueAsNumber)||inputs[key].valueAsNumber!==saved[key]);}if(state)state.parameter_sync=result.parameters.sync;render();
    }catch(e){if(postOwn(own)){error(e.message);if(body.name==='parameters_sync_reconnect')syncErrorContext={token:own.token,session:state.session_generation,serial:state.device_id};}}finally{if(postOwn(own)){postSlot=null;busy=false;render();if(body.name==='parameters_sync_reconnect')await read();}}
  }
  get('parameter-save').addEventListener('click',()=>{try{post({name:'parameters_save',values:draft(),revision});}catch(e){error(e.message);}});
  get('parameter-sync').addEventListener('click',()=>{try{if(get('parameter-sync').disabled)return;const cleanup=state.parameter_sync?.busy===true&&state.parameter_sync.cleanup_required===true,request_id=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');post({name:'parameters_sync_reconnect',request_id,expected_owner:state.connection_switch.expected_owner,revision:cleanup?canonical.revision:revision,values:cleanup?canonical.values:draft()},{preserveDraft:cleanup&&dirty?Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.value])):null});}catch(e){error(e.message);}});
  get('parameter-connect').addEventListener('click',event=>{event.preventDefault();if(window.WujiDeviceInventory)window.WujiDeviceInventory.show('hand');else{location.hash='#home';window.WujiWorkspace?.showPage();}});
  get('parameter-reload').addEventListener('click',()=>{error('');read(true);});
  get('parameter-initial').addEventListener('click',()=>{for(const field of config.fields)inputs[field.key].value=field.initial;updateDirty();});
  window.addEventListener('console-state',e=>{const next=e.detail;if(syncErrorContext&&(next?.csrf!==syncErrorContext.token||next?.connection==='connected'&&next.identified_device?.source==='sdk_identity'&&next.identified_device.serial===next.device_id&&(next.session_generation!==syncErrorContext.session||next.device_id!==syncErrorContext.serial)))error('');if(state?.csrf!==next?.csrf)retireRequests();state=next;render();if(document.body.dataset.page==='parameters'&&configToken!==state?.csrf)read();});window.addEventListener('console-offline',()=>{retireRequests();state=null;render();});
  window.addEventListener('workspace-page',e=>{if(e.detail==='parameters')read();else retireRequests();});
  setInterval(()=>{if(document.body.dataset.page==='parameters'&&!busy)read();},4000);read();
})();
