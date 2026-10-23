/* Explicit maintenance of one discovered device. Selection never connects or upgrades. */
(()=>{
 'use strict';
 const query=new URLSearchParams(location.search);
 if(query.get('embedded')==='1'||query.get('task_view')==='1'||window.parent!==window)return;
 const inventory=window.WujiDeviceInventory,Sheet=window.HandWorkbenchSheet;
 const actions=document.querySelector('#di-maintenance-actions');
 if(!inventory||!Sheet||!actions||document.getElementById('device-maintenance'))return;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const el=(tag,cls,parent)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(parent)parent.append(node);return node;};
 const text=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
 const sn=row=>row?.serial||row?.sn||'';
 const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
 const serialOK=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(value);
 const clock=value=>{if(typeof value==='number'&&Number.isFinite(value))return value<1e12?value*1000:value;return typeof value==='string'?Date.parse(value):NaN;};
 const sheet=Sheet.create({id:'device-maintenance',title:t('设备更新','Device update'),triggerLabel:t('设备更新','Device update')});
 sheet.element.classList.add('dm-sheet');const trigger=sheet.trigger;trigger.id='dm-open';actions.append(trigger);
 const resume=el('button','dm-resume',document.querySelector('#device-inventory .di-scan-controls'));resume.type='button';resume.id='dm-resume';resume.hidden=true;
 // Keep the shared sheet's native focus/close handling; this listener only loads metadata.
 trigger.addEventListener('click',event=>{if(!openSelection(false)){event.stopImmediatePropagation();return;}document.getElementById('di-device-more')?.removeAttribute('open');},true);
 const body=sheet.body,identity=el('header','dm-identity',body),name=el('h3','',identity),serial=el('p','dm-serial',identity);
 const versions=el('div','dm-versions',body),from=el('div','',versions),fromLabel=el('span','',from),fromValue=el('strong','',from),arrow=el('span','dm-arrow',versions),to=el('div','',versions),toLabel=el('span','',to),toValue=el('strong','',to);arrow.setAttribute('aria-hidden','true');text(arrow,'→');
 const status=el('p','dm-status',body);status.id='dm-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 const error=el('p','dm-error',body);error.id='dm-error';error.setAttribute('role','alert');
 const controls=el('div','dm-actions dm-main-actions',body),check=el('button','',controls),primary=el('button','primary',controls);check.type=primary.type='button';check.id='dm-check';primary.id='dm-start';
 const terminalHost=el('section','',body),terminal=window.OfficialTerminal?.mount(terminalHost,{endpoint:'/api/firmware-terminal',actionName:'firmware_terminal_input',title:()=>t('官方升级','Official update')});
 const details=el('details','dm-details',body),summary=el('summary','',details),records=el('pre','',details);records.id='dm-records';
 let selected=null,state=null,fresh=false,pending='',loading=null,epoch=0,timer=0,transportError='',actionError='',uncertain=false,openRequest=0;
 const busyPhases=new Set(['checking','preparing','downloading','upgrading','updating','flashing','reconnecting','awaiting_reconnect','verifying']);
 const recoveryPhases=new Set(['unknown','unconfirmed','recovery_required','reconnect_required']);
 function available(row){
  if(!row||!serialOK(sn(row))||row.selectable!==true||row.stale===true||!['hand','glove'].includes(row.kind)||row.official_available!==true)return false;
  const rows=Array.isArray(row.observations)?row.observations:[row];
  return rows.some(item=>item&&sn(item)===sn(row)&&typeof item.source==='string'&&item.source==='official_cli'&&item.stale!==true);
 }
 function same(){return !!selected&&sn(state?.device)===sn(selected);}
 function retainedRecord(){return serialOK(sn(state?.device))&&(state?.locked===true||state?.busy===true||!!state?.run?.id||clock(state?.checked_at)>Date.now()-86400000&&clock(state?.checked_at)<=Date.now()+30000);}
 function active(){return !!pending||fresh&&(state?.busy===true||busyPhases.has(state?.phase));}
 function preview(){const value=state?.preview;return fresh&&same()&&!active()&&!uncertain&&!state.error&&!state.locked&&state.supported!==false&&['ready','available'].includes(state.phase)&&value?.sn===sn(selected)&&value.mode==='official_confirmation'&&typeof value.token==='string'&&value.token.length>0&&value.token.length<=256&&clock(value.expires_at)>Date.now();}
 function valid(value){return object(value)&&typeof value.busy==='boolean'&&typeof value.phase==='string'&&value.phase.length<81&&(value.device==null||object(value.device))&&(value.error==null||typeof value.error==='string')&&(value.events==null||Array.isArray(value.events));}
 function accept(value){if(!valid(value))throw Error(t('设备更新状态不完整，请重新读取。','Device update status is incomplete. Reload it.'));state=value;fresh=true;transportError='';uncertain=false;}
 function label(row){const model=row?.model;return typeof model==='string'&&model.length<100?model:row?.kind==='glove'?'Wuji Glove':'Wuji Hand';}
 function humanError(value){
  const message=String(value||'');
  if(/idle|session|会话|disconnect|断开|workspace|工作区/i.test(message))return t('请先结束设备会话，再检查更新。','End device sessions before checking updates.');
  if(/expired|过期|token|preview/i.test(message))return t('更新方案已过期，请重新检查。','The update plan expired. Check again.');
  if(/unsupported|不支持|not supported|match|匹配|incompat/i.test(message))return t('暂不支持此设备的更新，请查看详细记录。','An update is unavailable for this device. See details.');
  if(/network|fetch|timeout|abort|网络|connect.*service/i.test(message))return t('暂时无法读取更新状态，请重试。','Update status is unavailable. Try again.');
  return t('操作未完成，请查看详细记录后重试。','The operation did not complete. Review details and retry.');
 }
 function render(){
  sheet.setLabels({title:t('设备更新','Device update'),triggerLabel:t('设备更新','Device update'),closeLabel:t('关闭','Close')});
  const row=inventory.selection(),retained=retainedRecord(),resumeSelected=retained&&sn(row)===sn(state.device);
  trigger.disabled=state?.supported===false&&!retained||!available(row)&&!resumeSelected;trigger.title=trigger.disabled?t('先选择刚发现的设备','Select a freshly discovered device'):'';
  const resumable=state?.locked===true||!!state?.run?.id||state?.busy===true&&state?.automatic!==true;
  resume.hidden=!resumable||!!row&&sn(row)===sn(state.device);text(resume,t('返回设备更新','Return to device update'));
  text(name,label(selected));text(serial,sn(selected));text(fromLabel,t('当前版本','Current version'));text(toLabel,t('可用版本','Available version'));
  const current=state?.current_version||(state?.result?.verdict==='recovery_verified'&&state.result.sn===sn(selected)?state.result.firmware_version:null);
  versions.hidden=!same()||(!current&&!state.target_version);from.hidden=!same()||!current;to.hidden=!same()||!state.target_version;arrow.hidden=from.hidden||to.hidden;
  text(fromValue,same()&&current||'—');text(toValue,same()&&state.target_version||'—');
  const phase=fresh&&same()?state.phase:'idle',busy=active(),other=fresh&&state?.busy===true&&!same();
  const messages={idle:['先检查设备，再打开官方升级。','Check the device, then open the official update.'],checking:['正在检查设备…','Checking the device…'],preparing:['正在打开官方升级…','Opening the official update…'],downloading:['请查看下方官方升级进度。','Follow the official update progress below.'],ready:['检查已完成。打开后跟随官方提示确认升级。','Device checked. Follow the official prompts after opening the update.'],available:['检查已完成。打开后跟随官方提示确认升级。','Device checked. Follow the official prompts after opening the update.'],up_to_date:['设备已是最新版本。','The device is up to date.'],upgrading:['请跟随下方官方提示，保持设备供电与连接。','Follow the official prompts below. Keep the device powered and connected.'],running:['请跟随下方官方提示，保持设备供电与连接。','Follow the official prompts below. Keep the device powered and connected.'],updating:['请跟随下方官方提示，保持设备供电与连接。','Follow the official prompts below. Keep the device powered and connected.'],flashing:['正在写入固件，请保持设备供电与连接。','Writing firmware. Keep the device powered and connected.'],reconnecting:['等待设备重新连接…','Waiting for the device to reconnect…'],awaiting_reconnect:['等待设备重新连接…','Waiting for the device to reconnect…'],verifying:['正在核对设备版本…','Checking the device version…'],succeeded:['官方流程已结束，请核对设备版本。','The official process ended. Check the device version.'],completed:['官方流程已结束，请核对设备版本。','The official process ended. Check the device version.'],unsupported:['此设备暂不支持在线更新。','Online updates are unavailable for this device.'],unconfirmed:['更新结果尚未确认，请核对设备。','The update result is unconfirmed. Check the device.'],recovery_required:['请核对设备的实际版本后继续。','Check the actual device version before continuing.'],reconnect_required:['请重新连接设备，再核对更新结果。','Reconnect the device, then verify the update.'],failed:['官方升级未完成，请查看详细记录。','The official update did not complete. See details.'],error:['操作未完成，请查看详细记录。','The operation did not complete. See details.']};
  const message=state?.supported===false?t('当前控制环境暂不支持官方升级。','Official updates are unavailable in the current controller environment.'):other?t('另一设备正在更新，请稍后再试。','Another device is updating. Try again shortly.'):phase==='unknown'||state?.locked===true?t('官方升级的结束状态尚未确认，请先核对设备。','The official update exit is unconfirmed. Check the device first.'):phase==='verified'?t('已核对设备版本。','The device version has been verified.'):uncertain?t('请求结果尚未确认，请先重新读取状态。','The request result is unconfirmed. Reload status first.'):pending==='firmware_upgrade'?t('正在打开官方升级…','Opening the official update…'):pending==='firmware_check'?t('正在检查设备…','Checking the device…'):pending==='firmware_recovery_check'?t('正在核对设备…','Checking the device…'):t(...(messages[phase]||['正在读取设备状态…','Reading device status…']));
  const problem=transportError||actionError||(same()&&state.error)||'';
  const viewer=state?.viewer,ownedPreview=viewer?.active===true&&viewer.phase==='running'&&viewer.serial===sn(selected)&&/^[0-9a-f]{32}$/.test(viewer.session_id||'');
  const checkFailure=['failed','check_failed'].includes(phase)&&state?.operation!=='upgrade';
  const reportMessage=checkFailure?t('版本检查未完成，可以重新检查。','Version check incomplete. Try again.'):phase==='available'?t('有适用于这台设备的新固件。','New firmware is available for this device.'):phase==='checked'?t('官方检查已结束，可打开官方流程查看详情。','Official check finished. Open the official flow for details.'):phase==='up_to_date'?t('固件已是最新版本。','Firmware is up to date.'):phase==='unknown'&&state?.operation==='check'?t('检查进程退出尚未确认，请先核对检查。','The check process exit is unconfirmed. Verify the check first.'):message;
  text(status,problem?humanError(problem):ownedPreview&&!active()?t('更新前需要结束这台设备的预览；仅在你继续后执行。','This device’s preview must end before an update. It ends only when you continue.'):reportMessage);status.classList.toggle('dm-status-error',!!problem);error.hidden=true;
  sheet.element.dataset.phase=phase;sheet.element.setAttribute('aria-busy',String(busy));
  text(check,!fresh||uncertain||state?.supported===false?t('重新读取','Reload status'):t('重新检查','Check again'));check.hidden=true;check.disabled=!!pending||busy;
  const recover=fresh&&same()&&(recoveryPhases.has(phase)||state.locked===true||state.recovery_required===true);text(primary,recover?t('核对设备','Check device'):preview()?t('打开官方升级','Open official update'):t('检查设备','Check device'));
  primary.disabled=!fresh||busy||other||uncertain||!!transportError||state?.supported===false;primary.hidden=['up_to_date','unsupported'].includes(phase);
  if(!fresh||uncertain||transportError){text(primary,t('重新读取','Reload status'));primary.disabled=!!pending||busy;primary.hidden=false;}
  else if(ownedPreview&&!recover&&!active()){text(primary,t('结束预览并检查更新','End preview and check'));primary.disabled=false;primary.hidden=false;}
  check.disabled=check.disabled||fresh&&state?.locked===true;
  const run=same()&&object(state.run)?state.run:{};controls.hidden=run.source==='official_tty'&&run.running===true;sheet.element.classList.toggle('dm-native',run.source==='official_tty'&&!!run.id);terminal?.render(run,{fresh:fresh&&same()});
  const lines=[];if(problem)lines.push(problem);if(same()){for(const [key,title] of [['check_report',t('官方设备检查','Official device check')],['catalog_report',t('官方固件列表','Official firmware list')]]){const report=state[key];if(object(report)){lines.push(title);if(typeof report.stdout==='string')lines.push(report.stdout);if(typeof report.stderr==='string')lines.push(report.stderr);}}
   for(const event of (state.events||[]).slice(-40)){if(typeof event==='string')lines.push(event);else if(object(event)){const value=event.message||event.text||event.event||event.phase;if(typeof value==='string')lines.push(value);}}}text(records,lines.map(line=>String(line).slice(0,12000)).join('\n'));details.hidden=!lines.length;text(summary,t('详细记录','Details'));
 }
 async function load(){
  if(loading)return loading;const version=epoch;
  loading=(async()=>{try{const response=await fetch('/api/device-maintenance',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!response.ok)throw Error('Update status unavailable');const value=await response.json();if(version!==epoch)return;accept(value);}catch(failure){if(version!==epoch)return;fresh=false;transportError=failure.message||'Update status unavailable';}finally{loading=null;render();schedule();}})();return loading;
 }
 async function request(command){
  if(pending)return;pending=command.name;epoch++;actionError='';transportError='';render();
  try{
   const session=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!session.ok)throw Error('Session unavailable');const token=(await session.json()).csrf;if(typeof token!=='string'||!token)throw Error('Session unavailable');
   const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify(command),signal:AbortSignal.timeout(command.name==='firmware_check'?60000:15000)}),result=await response.json();
   if(!response.ok||result.ok===false)throw Error(result.error||'Device update did not complete');
   const value=result.device_maintenance||result.maintenance;if(value)accept(value);else fresh=false;
  }catch(failure){fresh=false;actionError=failure.message||'Device update did not complete';if(command.name==='firmware_upgrade')uncertain=true;}
  finally{pending='';render();await load();}
 }
 async function checkSelected(){
  const number=sn(selected);
  if(!serialOK(number)){actionError=t('请选择要检查的设备。','Select the device to check.');render();return;}
  // The server refreshes expired discovery once and repeats exact identity.
  // A finished official process should not require a separate manual rescan.
  await request({name:'firmware_check',serial:number});
 }
 function schedule(){clearTimeout(timer);if(sheet.element.open)timer=setTimeout(()=>{if(!pending)load();else schedule();},active()?1000:5000);}
 function openSelection(show=true,saved=false){const row=inventory.selection(),previous=state?.device,retained=retainedRecord();if(saved&&retained)selected=previous;else if(available(row))selected=row;else if(retained&&(!row||sn(row)===sn(previous)))selected=previous;else return false;actionError='';transportError='';epoch++;fresh=false;if(show)sheet.open();render();load();return true;}
 check.onclick=()=>!fresh||uncertain||state?.supported===false?load():checkSelected();
 primary.onclick=()=>{if(!fresh||uncertain||transportError){load();return;}const viewer=state?.viewer;if(viewer?.active===true&&viewer.phase==='running'&&viewer.serial===sn(selected)&&/^[0-9a-f]{32}$/.test(viewer.session_id||'')){request({name:'firmware_check',serial:sn(selected),viewer_token:viewer.session_id});return;}if(preview())request({name:'firmware_upgrade',preview_token:state.preview.token,confirmed:true});else if(fresh&&same()&&(recoveryPhases.has(state.phase)||state.locked===true||state.recovery_required===true))request({name:'firmware_recovery_check'});else checkSelected();};
 resume.onclick=()=>openSelection(true,true);
 sheet.element.addEventListener('close',()=>clearTimeout(timer));
 window.addEventListener('device-inventory-selection',render);window.addEventListener('device-inventory-update',render);window.addEventListener('wuji-language',render);
 window.addEventListener('device-inventory-use',()=>{openRequest++;sheet.close();document.getElementById('di-device-more')?.removeAttribute('open');});
 window.addEventListener('console-offline',()=>{fresh=false;transportError=t('工作台服务已离线。','Workbench service is offline.');render();});
 async function openBySerial(number){const request=++openRequest;await load();if(request!==openRequest)return;const chosen=inventory.select(number)&&sn(inventory.selection())===number,retained=sn(state?.device)===number&&retainedRecord();if(chosen||retained)openSelection(true,!chosen&&retained);else{inventory.show();inventory.select(number);}}
 window.WujiDeviceMaintenance=Object.freeze({open(serial){if(serial!==undefined){if(!serialOK(serial))return false;openBySerial(serial);return true;}return openSelection();},refresh:load});render();load();
})();
