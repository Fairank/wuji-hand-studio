(() => {
  const ws=window.WujiWorkspace;if(!ws)return;
  const card=document.createElement('section');card.className='panel network-card';card.hidden=true;
  const title=document.createElement('h3'),message=document.createElement('p'),detail=document.createElement('p');
  const choice=document.createElement('select'),check=document.createElement('button'),repair=document.createElement('button');
  message.setAttribute('role','status');detail.className='hint';choice.setAttribute('aria-label','设备网卡 / Device adapter');
  check.type=repair.type='button';repair.className='primary';
  const buttons=document.createElement('div');buttons.className='button-row';buttons.append(check,repair);
  card.append(title,message,detail,choice,buttons);ws.views.connection.prepend(card);
  let current=null,mode=null,busy=false,errorText='',sheet=null,readOnly=false,returnParent=null,returnNext=null,lastState=null,offline=false,revision=0,slot=null,lastObservation='',currentScope=null,manual=null;
  const en=()=>document.documentElement.lang==='en',address=()=>document.getElementById('device-address').value.trim();
  // Fable drafted the generic bilingual states; reviewed before adoption.
  const copy={cable_unplugged:['设备网卡未检测到连接。','No connection is detected on the device network adapter.'],subnet_mismatch:['网卡与设备的网段不一致。','The network adapter and device are on different subnets.'],multiple_adapters:['检测到多张候选设备网卡，请选择要使用的一张。','Multiple candidate adapters were found; select one.'],subnet_ready:['设备网段已就绪，可以连接读取反馈。','Device subnet is ready; connect to read feedback.'],no_ethernet:['未找到设备用有线网卡。','No device Ethernet adapter was found.'],network_in_use:['网卡正在使用其他网络配置，请核对设备专用网卡。','The adapter has another network configuration; check the dedicated device adapter.'],external_controller:['网络由 Linux 控制端管理。','Device networking is managed by the Linux controller.'],usb_device:['一代手使用 USB 连接。','Hand 1 uses a USB connection.']};
  const text=code=>(copy[code]||[code,code])[en()?1:0];
  function render(){
    title.textContent=readOnly?(en()?'Check connection':'检查连接'):(en()?'Automatic device network setup':'自动配置设备网络');check.textContent=en()?'Check connection':'检查连接';repair.textContent=en()?'Set up this adapter':'配置这张网卡';
    check.disabled=busy;repair.disabled=busy||!current?.can_configure||!choice.value||current?.setup?.state==='pending';
    choice.hidden=repair.hidden=!current?.can_configure;
    if(!current){message.textContent=errorText||(busy?(en()?'Checking connection…':'正在检查连接…'):'');detail.textContent='';return;}
    const stage=current.setup?.state;
    message.textContent=errorText||(stage==='pending'?(en()?'Waiting for system setup…':'等待系统完成配置…'):stage==='failed'?current.setup.error:text(current.code));
    const adapters=Array.isArray(current.adapters)?current.adapters:current.adapter?[current.adapter]:[];
    detail.textContent=readOnly?adapters.map(a=>`${a.name||a.id||'—'} · ${Array.isArray(a.addresses)?a.addresses.join(', ')||'—':'—'}${typeof a.up==='boolean'?' · '+(a.up?(en()?'Link up':'链路已连接'):(en()?'Link down':'链路已断开')):''}`).join('； '):current.adapter?`${current.adapter.name} · ${current.adapter.addresses.join(', ')||'—'}`:(en()?'First setup may request administrator approval. Wi-Fi and the default gateway stay unchanged.':'首次配置可能需要系统管理员确认。无线网络和默认网关保持原样。');
    sheet?.setLabels({title:en()?'Check connection':'检查连接',description:en()?'Checking is read-only. Adapter setup starts only when you choose it.':'检查只读取状态；配置仅在你点击后执行。',closeLabel:en()?'Close':'关闭'});
  }
  const available=()=>typeof window.pywebview?.api?.network_status==='function';
  const context=()=>{const state=lastState||window.WujiConnection?.state();return !offline&&typeof state?.csrf==='string'&&state.csrf?JSON.stringify([state.csrf,state.device_profile?.id||'',address()]):null;};
  function retire(){revision++;slot=null;manual=null;busy=false;current=null;currentScope=null;choice.replaceChildren();errorText='';lastObservation='';render();}
  function relevant(){const root=document.getElementById('device-inventory');return !document.hidden&&['home','connection'].includes(document.body.dataset.page)&&root&&root.getClientRects().length>0&&!root.closest('[hidden]')&&getComputedStyle(root).visibility!=='hidden';}
  async function refresh(diagnostic=false,setupOwner=null){
    if(!available()||manual&&manual!==setupOwner)return null;
    const scope=context();if(diagnostic&&!scope)return null;
    if(slot&&slot.scope===scope&&slot.diagnostic===diagnostic)return slot.promise;
    const own={scope,diagnostic,revision};slot=own;const owns=()=>slot===own&&revision===own.revision&&context()===scope;
    busy=true;errorText='';render();
    own.promise=(async()=>{let timer;
      try{
        if(!diagnostic){const info=await(await fetch('/api/installation')).json();if(!owns())return null;mode=info.bridge.mode;card.hidden=!['wsl','macvm'].includes(mode);if(!['wsl','macvm'].includes(mode))return null;}
        const value=await Promise.race([window.pywebview.api.network_status(address()),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(en()?'Connection check timed out. Retry the check.':'连接检查超时，请重试检查。')),15000);})]);
        if(!owns())return null;if(!value||typeof value.code!=='string')throw Error(en()?'Incomplete network status':'网络状态不完整');
        current=value;currentScope=scope;card.hidden=false;const selected=choice.value;choice.replaceChildren();
        for(const a of current.candidates||[])choice.add(new Option(a.name,a.id));
        if([...choice.options].some(o=>o.value===selected))choice.value=selected;
        if(current.candidates?.length>1&&!selected)choice.prepend(new Option(en()?'Select adapter…':'选择网卡…','',true,true));
        return current;
      }catch(e){if(owns()){current=null;errorText=e.message;throw e;}return null;}
      finally{clearTimeout(timer);if(slot===own&&revision===own.revision){slot=null;busy=false;if(context()!==scope){current=null;currentScope=null;}render();}}
    })();return own.promise;
  }
  async function inspect(){
    if(!available()||!context()||!window.HandWorkbenchSheet)return null;
    if(!sheet){sheet=window.HandWorkbenchSheet.create({id:'device-network-sheet',title:en()?'Check connection':'检查连接',description:en()?'Checking is read-only. Adapter setup starts only when you choose it.':'检查只读取状态；配置仅在你点击后执行。'});sheet.element.addEventListener('close',()=>{if(sheet.element.open)return;retire();readOnly=false;if(returnParent)returnParent.insertBefore(card,returnNext?.parentElement===returnParent?returnNext:null);returnParent=returnNext=null;render();});}
    if(!sheet.element.open){returnParent=card.parentElement;returnNext=card.nextSibling;readOnly=true;sheet.body.append(card);card.hidden=false;sheet.open();}
    return refresh(true);
  }
  async function observe(value){
    if(manual||!available()||!context()||!relevant()||!value||value.scanning===true||!value.scanned_at&&!value.error||!Array.isArray(value.devices)||value.devices.length&&!value.error)return null;
    const key=JSON.stringify([context(),value.scanned_at,value.error||null,value.blocked_reason||null]);if(key===lastObservation)return slot?.promise||null;
    lastObservation=key;return refresh(true);
  }
  choice.onchange=render;check.onclick=()=>refresh(readOnly).catch(()=>{});
  repair.onclick=async()=>{
    if(manual||busy||!current?.can_configure||!choice.value||currentScope!==context()||current.setup?.state==='pending'||typeof window.pywebview?.api?.configure_device_network!=='function')return;
    const own={scope:context(),revision};manual=own;const owns=()=>manual===own&&own.revision===revision&&own.scope===context();
    busy=true;errorText='';render();
    try{
      await window.pywebview.api.configure_device_network(choice.value,address());
      if(!owns())return;
      for(let n=0;n<90;n++){await new Promise(r=>setTimeout(r,1000));if(!owns())return;const value=await window.pywebview.api.network_status(address());if(!owns())return;current=value;render();if(current.setup?.state!=='pending')break;}
      if(owns())await refresh(readOnly,own);
    }catch(e){if(owns())errorText=e.message;}finally{if(owns()){manual=null;busy=false;render();}}
  };
  window.WujiNetwork={available,inspect,observe,async prepare(value){
    const result=await refresh();if(!result||!['wsl','macvm'].includes(mode)||result.code==='usb_device')return value;
    if(result.setup?.state==='pending')throw Error(en()?'System network setup is still running.':'系统仍在配置网络，请稍候。');
    if(mode==='macvm'&&result.code==='subnet_mismatch'&&result.candidates?.length===1){
      const configured=await window.pywebview.api.configure_device_network(result.candidates[0].id,address());
      if(configured.code==='subnet_ready')return value;
    }
    if(result.code!=='subnet_ready')throw Error(text(result.code));return value;
  }};
  window.addEventListener('console-state',event=>{const before=slot?.scope||currentScope||context();lastState=event.detail;offline=false;if(before!==context())retire();});
  window.addEventListener('console-offline',()=>{offline=true;lastState=null;retire();});
  window.addEventListener('workspace-page',()=>{if(!relevant()){retire();sheet?.close();}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){retire();sheet?.close();}});
  window.addEventListener('wuji-language',render);render();
})();
