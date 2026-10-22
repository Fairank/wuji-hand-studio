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
   if(found.kind!=='hand')throw Error(t('所选设备不是灵巧手','Selected device is not a hand'));
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
  // Choose the source, then open its workspace. Connecting remains a separate
  // explicit action; visiting teleoperation never starts a controller or motor.
  location.hash='#glove';window.WujiWorkspace.showPage();return;
 });
})();
