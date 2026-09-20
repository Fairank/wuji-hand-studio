(() => {
  const ws=window.WujiWorkspace;if(!ws)return;
  const card=document.createElement('section');card.className='panel network-card';card.hidden=true;
  const title=document.createElement('h3'),message=document.createElement('p'),detail=document.createElement('p');
  const choice=document.createElement('select'),check=document.createElement('button'),repair=document.createElement('button');
  message.setAttribute('role','status');detail.className='hint';choice.setAttribute('aria-label','设备网卡 / Device adapter');
  check.type=repair.type='button';repair.className='primary';
  const buttons=document.createElement('div');buttons.className='button-row';buttons.append(check,repair);
  card.append(title,message,detail,choice,buttons);ws.views.connection.prepend(card);
  let current=null,mode=null,busy=false,errorText='';
  const en=()=>document.documentElement.lang==='en',address=()=>document.getElementById('device-address').value.trim();
  // Fable drafted the generic bilingual states; reviewed before adoption.
  const copy={cable_unplugged:['设备网卡未检测到连接。','No connection is detected on the device network adapter.'],subnet_mismatch:['网卡与设备的网段不一致。','The network adapter and device are on different subnets.'],multiple_adapters:['检测到多张候选设备网卡，请选择要使用的一张。','Multiple candidate adapters were found; select one.'],subnet_ready:['设备网段已就绪，可以连接读取反馈。','Device subnet is ready; connect to read feedback.'],no_ethernet:['未找到设备用有线网卡。','No device Ethernet adapter was found.'],network_in_use:['网卡正在使用其他网络配置，请核对设备专用网卡。','The adapter has another network configuration; check the dedicated device adapter.'],external_controller:['网络由 Linux 控制端管理。','Device networking is managed by the Linux controller.'],usb_device:['一代手使用 USB 连接。','Hand 1 uses a USB connection.']};
  const text=code=>(copy[code]||[code,code])[en()?1:0];
  function render(){
    title.textContent=en()?'Automatic device network setup':'自动配置设备网络';check.textContent=en()?'Check connection':'检查连接';repair.textContent=en()?'Set up this adapter':'配置这张网卡';
    check.disabled=busy;repair.disabled=busy||!current?.can_configure||!choice.value||current?.setup?.state==='pending';
    if(!current){message.textContent=errorText;return;}
    const stage=current.setup?.state;
    message.textContent=errorText||(stage==='pending'?(en()?'Waiting for system setup…':'等待系统完成配置…'):stage==='failed'?current.setup.error:text(current.code));
    detail.textContent=current.adapter?`${current.adapter.name} · ${current.adapter.addresses.join(', ')||'—'}`:(en()?'First setup may request administrator approval. Wi-Fi and the default gateway stay unchanged.':'首次配置可能需要系统管理员确认。无线网络和默认网关保持原样。');
    choice.hidden=repair.hidden=!current.can_configure;
  }
  async function refresh(){
    if(!window.pywebview?.api?.network_status)return null;
    busy=true;errorText='';render();
    try{
      const info=await(await fetch('/api/installation')).json();mode=info.bridge.mode;card.hidden=!['wsl','macvm'].includes(mode);if(!['wsl','macvm'].includes(mode))return null;
      current=await window.pywebview.api.network_status(address());const selected=choice.value;choice.replaceChildren();
      for(const a of current.candidates||[])choice.add(new Option(a.name,a.id));
      if([...choice.options].some(o=>o.value===selected))choice.value=selected;
      if(current.candidates?.length>1&&!selected){const blank=new Option(en()?'Select adapter…':'选择网卡…','',true,true);choice.prepend(blank);}
      return current;
    }catch(e){errorText=e.message;throw e;}finally{busy=false;render();}
  }
  choice.onchange=render;check.onclick=()=>refresh().catch(()=>{});
  repair.onclick=async()=>{
    busy=true;errorText='';render();
    try{
      await window.pywebview.api.configure_device_network(choice.value,address());
      for(let n=0;n<90;n++){await new Promise(r=>setTimeout(r,1000));current=await window.pywebview.api.network_status(address());render();if(current.setup.state!=='pending')break;}
      await refresh();
    }catch(e){errorText=e.message;}finally{busy=false;render();}
  };
  window.WujiNetwork={async prepare(value){
    const result=await refresh();if(!result||!['wsl','macvm'].includes(mode)||result.code==='usb_device')return value;
    if(result.setup?.state==='pending')throw Error(en()?'System network setup is still running.':'系统仍在配置网络，请稍候。');
    if(mode==='macvm'&&result.code==='subnet_mismatch'&&result.candidates?.length===1){
      const configured=await window.pywebview.api.configure_device_network(result.candidates[0].id,address());
      if(configured.code==='subnet_ready')return value;
    }
    if(result.code!=='subnet_ready')throw Error(text(result.code));return value;
  }};
  window.addEventListener('pywebviewready',()=>refresh().catch(()=>{}));
  window.addEventListener('workspace-page',e=>{if(e.detail==='connection')refresh().catch(()=>{});});
  window.addEventListener('wuji-language',render);refresh().catch(()=>{});
})();
