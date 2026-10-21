/* Explicit uses of the shared device directory. No action runs on selection. */
(()=>{
 'use strict';
 const directory=window.WujiDeviceInventory;if(!directory)return;
 const $=id=>document.getElementById(id),t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 async function state(){const r=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!r.ok)throw Error(t('工作台服务未就绪','Workbench service unavailable'));return r.json();}
 async function post(name,extra={}){const s=await state();if(!s.csrf)throw Error(t('状态未核对','State unverified'));const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':s.csrf},body:JSON.stringify({name,...extra}),signal:AbortSignal.timeout(15000)}),value=await r.json();if(!r.ok||value.ok===false)throw Error(value.error||t('操作未完成','Operation failed'));return value;}
 function chooseGlove(device){window.dispatchEvent(new CustomEvent('device-inventory-selection',{detail:{device,serial:device.sn||device.serial,kind:'glove'}}));}
 directory.setUseHandler(async(mode,device)=>{
  // Re-read after the click. Retained cards are never sufficient authority.
  await directory.refresh();const current=directory.snapshot(),serial=device.sn||device.serial;
  const found=current?.devices?.find(row=>(row.sn||row.serial)===serial);
  if(!found?.selectable||!directory.select(serial))throw Error(t('设备列表已改变，请重新查找并选择。','The device list changed. Find and select the device again.'));
  if(mode==='official'){
   if(!found.official_available)throw Error(t('请先完成官方设备发现','Run official discovery first'));
   await post('official_viz_start',{serial,theme:document.documentElement.dataset.theme==='dark'?'dark':'light'});
   window.WujiConnectionHub.show('official');window.WujiOfficialViz.showDeviceMode();await window.WujiOfficialViz.selectDevice(serial);return;
  }
  if(mode==='hand'){
   if(found.kind!=='hand')throw Error(t('所选设备不是机械手','Selected device is not a hand'));
   // Preserve an advertised Ethernet locator, including changed factory IPs.
   // USB and serial URIs stay on the existing discovery route; never invent IPs.
   const locators=[found.address,...(Array.isArray(found.addresses)?found.addresses:[])];
   const address=locators.filter(value=>typeof value==='string').map(value=>value.replace(/^udp\//,'')).find(value=>/^\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?$/.test(value))||'';
   await post('connect',{serial,address,auto_detect:true});directory.showSettings('hand');return;
  }
  if(found.kind!=='glove')throw Error(t('请先选择手套','Select a glove first'));
  chooseGlove(found);
  if(mode==='calibration'){window.WujiConnectionHub.show('calibration');return;}
  if(mode!=='preview')throw Error('Unknown device workflow');
  let s=await state();
  if(s.connection!=='disconnected'||s.hardware?.active!==false)throw Error(t('先结束机械手会话，再设置手套预览。','End the hand session before setting up glove preview.'));
  const side=found.side,profile=s.device_profile;
  if(['left','right'].includes(side)&&profile?.side!==side){
   if(s.glove?.busy)throw Error(t('当前手套会话属于另一侧，请先断开。','The glove session uses the other side. Disconnect it first.'));
   await post('device_profile_select',{profile:(profile?.generation==='hand1'?'hand1':'hand2')+'_'+side});
  }
  if(!s.glove?.busy)await post('glove_scan');
  // Scanning the SDK prepares its users/pairing controls; opening is a separate
  // visible button and still performs typed SDK identity verification.
  const deadline=Date.now()+30000;
  while(Date.now()<deadline){
   s=await state();if(s.glove?.connection==='error')throw Error(s.glove.message||t('手套控制端未就绪','Glove controller not ready'));
   if(s.glove?.connection==='receiving'){
    const actual=s.glove.glove_serial;
    if(actual!==serial)throw Error(t('另一个手套预览正在运行，请先断开原会话。','A different glove preview is running. Disconnect it first.'));
    break;
   }
   if(s.glove?.connection==='ready'){
    if(!s.glove.devices?.some(row=>(row.serial||row.sn)===serial))throw Error(t('控制端未找到所选手套，请检查网络后重试。','The controller did not find this glove. Check the network and retry.'));
    break;
   }
   await new Promise(resolve=>setTimeout(resolve,250));
  }
  if(!['ready','receiving'].includes(s.glove?.connection))throw Error(t('准备超时；未自动断开或连接其他设备。','Setup timed out. No other device was disconnected or selected.'));
  chooseGlove(found);window.WujiConnectionHub.show('device');directory.showSettings('glove');
  const message=$('glove-command-status');if(message)message.textContent=t('已选中此手套。核对标定用户后，点击“连接手套并预览”。','Glove selected. Check the calibration user, then choose Connect glove and preview.');
 });
})();
