(() => {
  const ws=window.WujiWorkspace;if(!ws)return;
  const card=document.createElement('section');card.className='panel network-card';card.hidden=true;
  const title=document.createElement('h3'),message=document.createElement('p'),detail=document.createElement('p');
  const diagnostics=document.createElement('details'),summary=document.createElement('summary'),adapterList=document.createElement('ul'),rawError=document.createElement('pre');
  const choice=document.createElement('select'),check=document.createElement('button'),repair=document.createElement('button');
  const targetChoice=document.createElement('select');
  targetChoice.id='device-network-target';
  message.setAttribute('role','status');message.className='network-state';detail.className='hint';diagnostics.className='network-link-details';adapterList.className='network-link-list';diagnostics.append(summary,adapterList,rawError);
  check.type=repair.type='button';repair.className='primary';
  const buttons=document.createElement('div');buttons.className='button-row';buttons.append(check,repair);
  card.append(title,message,detail,choice,targetChoice,diagnostics,buttons);ws.views.connection.prepend(card);
  let current=null,mode=null,busy=false,errorText='',sheet=null,readOnly=false,returnParent=null,returnNext=null,lastState=null,offline=false,revision=0,slot=null,lastObservation='',currentScope=null,manual=null;
  let selectedTarget='hand',availableTargets=[];
  const en=()=>document.documentElement.lang==='en',t=(zh,english)=>en()?english:zh,address=()=>document.getElementById('device-address').value.trim();
  const networkArgs=()=>selectedTarget==='hand'?[address()]:['',selectedTarget];
  const labels={hand:['灵巧手','Robotic hand'],glove_left:['左手套','Left glove'],glove_right:['右手套','Right glove']};
  const copy={
   cable_unplugged:['未检测到连接，请检查设备电源、网线和扩展坞。','No Ethernet link detected. Check device power, the cable, and the dock.'],
   subnet_mismatch:['网卡和设备使用不同网段。','The adapter and device use different subnets.'],
   multiple_adapters:['请选择接着这台设备的网卡。','Choose the adapter connected to this device.'],
   subnet_ready:['设备网络已就绪。','Device network is ready.'],
   no_ethernet:['未找到有线网卡。接好扩展坞后再检查。','No Ethernet adapter found. Connect the dock and check again.'],
   network_in_use:['这张网卡另有用途，请核对设备接入的网口。','This adapter is in use. Check which Ethernet port the device uses.'],
   external_controller:['请在 Linux 控制端检查设备网口。','Check the device adapter on the Linux controller.'],
   usb_device:['一代灵巧手通过 USB 连接。请检查设备的 USB 线。','Hand 1 connects by USB. Check its USB cable.'],
   route_required:['请选择接着这台设备的网卡。','Choose the adapter connected to this device.'],
   route_conflict:['设备地址与现有网络设置冲突。请核对设备网口后重新检查。','The device address conflicts with existing network settings. Check its adapter and try again.'],
  };
  function adapterRow(adapter){
   const row=document.createElement('li'),name=document.createElement('strong'),link=document.createElement('span');
   name.textContent=adapter.name||adapter.id||'—';
   const addresses=Array.isArray(adapter.addresses)?adapter.addresses.join(', '):'';
   const status=adapter.up===true?t('链路已连接','Link up'):adapter.up===false?t('链路已断开','Link down'):'';
   link.textContent=[addresses,status].filter(Boolean).join(' · ');
   row.append(name,link);return row;
  }
  function render(){
    title.hidden=readOnly;title.textContent=t('设备网络','Device network');check.textContent=t('检查连接','Check connection');repair.textContent=t('配置这张网卡','Set up this adapter');
    targetChoice.setAttribute('aria-label',t('检查设备','Device to check'));choice.setAttribute('aria-label',t('设备网卡','Device adapter'));
    check.disabled=busy;repair.disabled=busy||!current?.can_configure||!choice.value||current?.setup?.state==='pending';
    choice.hidden=repair.hidden=!current?.can_configure;
    targetChoice.hidden=!readOnly||availableTargets.length<2;
    targetChoice.disabled=!!manual;
    targetChoice.replaceChildren(...availableTargets.map(key=>new Option(labels[key][en()?1:0],key)));
    targetChoice.value=selectedTarget;
    const stage=current?.setup?.state;
    const stateCopy=current?.code&&Object.hasOwn(copy,current.code)?t(...copy[current.code]):'';
    const warning=!!errorText||stage==='failed'||['cable_unplugged','no_ethernet','route_conflict','network_in_use'].includes(current?.code);
    message.dataset.tone=warning?'warning':current?.code==='subnet_ready'&&stage!=='pending'?'success':'info';
    message.textContent=errorText?t('连接检查未完成。请重新检查。','Connection check did not finish. Check again.'):stage==='pending'?t('正在配置设备网口。','Setting up the device adapter.'):stage==='failed'?t('网口配置未完成。请检查网卡后重试。','Adapter setup did not finish. Review the adapter and try again.'):stateCopy||(busy?t('正在检查设备网络…','Checking the device network…'):current?t('暂时无法确认设备网络。请重新检查。','The device network could not be verified. Check again.'):'');
    detail.textContent=stage==='pending'?t('如系统请求授权，请确认后继续。','Approve the system prompt if one appears.'):current?.can_configure&&current.code==='subnet_mismatch'?t('选中设备网卡后，可配置这张网卡。','Select the device adapter to configure it.'):'';
    detail.hidden=!detail.textContent;
    const adapters=Array.isArray(current?.adapters)?current.adapters:current?.adapter?[current.adapter]:[];
    if(readOnly)adapterList.replaceChildren(...adapters.map(adapterRow));
    else adapterList.replaceChildren(...(current?.adapter?[adapterRow(current.adapter)]:[]));
    adapterList.hidden=!adapterList.children.length;
    rawError.textContent=errorText||current?.setup?.error||(!stateCopy&&current?.code)||'';
    rawError.hidden=!rawError.textContent;
    diagnostics.hidden=adapterList.hidden&&rawError.hidden;
    summary.textContent=t('网卡与诊断详情','Adapter and diagnostic details');
    sheet?.setLabels({title:t('检查连接','Check connection'),description:t('查看设备网络状态，按需配置网卡。','Review device networking and configure an adapter if needed.'),closeLabel:t('关闭','Close')});
  }
  const available=()=>typeof window.pywebview?.api?.network_status==='function';
  const context=()=>{const state=lastState||window.WujiConnection?.state();return !offline&&typeof state?.csrf==='string'&&state.csrf?JSON.stringify([state.csrf,state.device_profile?.id||'',...networkArgs()]):null;};
  function retire(){revision++;slot=null;manual=null;busy=false;current=null;currentScope=null;choice.replaceChildren();errorText='';lastObservation='';render();}
  function relevant(){const root=document.getElementById('device-inventory');return !document.hidden&&['home','connection'].includes(document.body.dataset.page)&&root&&root.getClientRects().length>0&&!root.closest('[hidden]')&&getComputedStyle(root).visibility!=='hidden';}
  async function refresh(diagnostic=false,setupOwner=null){
    if(!available()||manual&&manual!==setupOwner)return null;
    const scope=context();if(diagnostic&&!scope)return null;
    if(slot&&slot.scope===scope&&slot.diagnostic===diagnostic)return slot.promise;
    const args=networkArgs(),own={scope,diagnostic,revision};slot=own;const owns=()=>slot===own&&revision===own.revision&&context()===scope;
    busy=true;errorText='';render();
    own.promise=(async()=>{let timer;
      try{
        if(!diagnostic){const info=await(await fetch('/api/installation')).json();if(!owns())return null;mode=info.bridge.mode;card.hidden=!['wsl','macvm'].includes(mode);if(!['wsl','macvm'].includes(mode))return null;}
        const value=await Promise.race([window.pywebview.api.network_status(...args),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(en()?'Connection check timed out. Retry the check.':'连接检查超时，请重试检查。')),15000);})]);
        if(!owns())return null;if(!value||typeof value.code!=='string')throw Error(en()?'Incomplete network status':'网络状态不完整');
        availableTargets=Object.keys(labels).filter(key=>value.network_targets?.includes(key));
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
    if(!sheet){sheet=window.HandWorkbenchSheet.create({id:'device-network-sheet',title:t('检查连接','Check connection'),description:t('查看设备网络状态，按需配置网卡。','Review device networking and configure an adapter if needed.')});sheet.element.addEventListener('close',()=>{if(sheet.element.open)return;selectedTarget='hand';retire();readOnly=false;if(returnParent)returnParent.insertBefore(card,returnNext?.parentElement===returnParent?returnNext:null);returnParent=returnNext=null;render();});}
    if(!sheet.element.open){returnParent=card.parentElement;returnNext=card.nextSibling;readOnly=true;sheet.body.append(card);card.hidden=false;sheet.open();}
    return refresh(true);
  }
  async function observe(value){
    if(manual||!available()||!context()||!relevant()||!value||value.scanning===true||!value.scanned_at&&!value.error||!Array.isArray(value.devices)||value.devices.length&&!value.error)return null;
    const key=JSON.stringify([context(),value.scanned_at,value.error||null,value.blocked_reason||null]);if(key===lastObservation)return slot?.promise||null;
    lastObservation=key;return refresh(true);
  }
  choice.onchange=render;check.onclick=()=>refresh(readOnly).catch(()=>{});
  targetChoice.onchange=()=>{
    if(manual||!readOnly||!availableTargets.includes(targetChoice.value)||targetChoice.value===selectedTarget)return;
    selectedTarget=targetChoice.value;retire();refresh(true).catch(()=>{});
  };
  repair.onclick=async()=>{
    if(manual||busy||!current?.can_configure||!choice.value||currentScope!==context()||current.setup?.state==='pending'||typeof window.pywebview?.api?.configure_device_network!=='function')return;
    const args=networkArgs(),adapter=choice.value,own={scope:context(),revision};manual=own;const owns=()=>manual===own&&own.revision===revision&&own.scope===context();
    busy=true;errorText='';render();
    try{
      await window.pywebview.api.configure_device_network(adapter,...args);
      if(!owns())return;
      for(let n=0;n<90;n++){await new Promise(r=>setTimeout(r,1000));if(!owns())return;const value=await window.pywebview.api.network_status(...args);if(!owns())return;current=value;render();if(current.setup?.state!=='pending')break;}
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
