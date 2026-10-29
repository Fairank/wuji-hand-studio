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
 const versionNote=el('p','dm-version-note',body);versionNote.id='dm-version-note';
 const status=el('p','dm-status',body);status.id='dm-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 const progress=el('progress','dm-progress',body);progress.hidden=true;progress.setAttribute('aria-labelledby','dm-status');
 const error=el('p','dm-error',body);error.id='dm-error';error.setAttribute('role','alert');
 const controls=el('div','dm-actions dm-main-actions',body),check=el('button','',controls),primary=el('button','primary',controls);check.type=primary.type='button';check.id='dm-check';primary.id='dm-start';
 const terminalHost=el('section','',body),terminal=window.OfficialTerminal?.mount(terminalHost,{endpoint:'/api/firmware-terminal',actionName:'firmware_terminal_input',title:()=>t('官方升级','Official update')});
 const details=el('details','dm-details',body),summary=el('summary','',details),records=el('pre','',details);records.id='dm-records';
 let selected=null,state=null,fresh=false,pending='',loading=null,epoch=0,timer=0,transportError='',actionError='',uncertain=false,openRequest=0,terminalPresentation='',catalogChoice=null;
 const busyPhases=new Set(['checking','preparing','downloading','upgrading','updating','flashing','reconnecting','awaiting_reconnect','verifying']);
 const recoveryPhases=new Set(['unknown','unconfirmed','recovery_required','reconnect_required']);
 function available(row){
  if(!row||!serialOK(sn(row))||row.selectable!==true||row.stale===true||!['hand','glove'].includes(row.kind)||row.official_available!==true)return false;
  const rows=Array.isArray(row.observations)?row.observations:[row];
  return rows.some(item=>item&&sn(item)===sn(row)&&typeof item.source==='string'&&item.source==='official_cli'&&item.stale!==true);
 }
 function same(){return !!selected&&sn(state?.device)===sn(selected);}
 function deviceType(){return selected?.kind==='glove'?'wuji_glove':selected?.generation==='hand2'?'wuji_hand_2':selected?.generation==='hand1'?'wuji_hand':null;}
 function failedCatalog(){const value=state?.check_observation;return fresh&&same()&&object(value)&&value.serial===sn(selected)&&value.device_type===deviceType()&&value.status==='failed'&&value.catalog_available===false&&typeof value.error==='string'&&value.error&&value.error===state.catalog_error?value:null;}
 function catalog(){const value=state?.notification;return fresh&&same()&&!state.error&&!state.locked&&!['check_failed','failed','error',...recoveryPhases].includes(state.phase)&&!failedCatalog()&&object(value)&&value.serial===sn(selected)&&value.device_type===deviceType()&&typeof value.current==='string'&&typeof value.latest==='string'&&/^\d+\.\d+\.\d+$/.test(value.current)&&/^\d+\.\d+\.\d+$/.test(value.latest)&&typeof value.update_available==='boolean'?value:null;}
 const catalogPhases=new Set(['idle','checking','listed','empty','failed','downloading','package_ready']);
 const catalogTime=value=>typeof value==='number'&&Number.isFinite(value)&&value>0?value*1000:NaN;
 const firmwareVersion=value=>typeof value==='string'&&/^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})$/.test(value);
 const compareVersions=(left,right)=>{const a=left.split('.').map(Number),b=right.split('.').map(Number);for(let i=0;i<3;i++)if(a[i]!==b[i])return a[i]>b[i]?1:-1;return 0;};
 const catalogAction=()=>['firmware_catalog_check','firmware_package_prepare','firmware_package_upgrade'].includes(pending);
 function officialCatalog(){
  const value=state?.official_catalog;
  return selected?.kind==='glove'&&object(value)&&value.serial===sn(selected)&&value.device_type===deviceType()&&typeof value.busy==='boolean'&&catalogPhases.has(value.phase)&&value.compatibility_verified===false&&value.can_upgrade===false&&(value.error==null||typeof value.error==='string')?value:null;
 }
 function listedMetadata(value){
  const record=value?.catalog_metadata,stamp=catalogTime(value?.checked_at);
  if(!object(record)||!Array.isArray(record.entries)||record.entries.length>1000||!Number.isFinite(stamp)||stamp<=0||stamp>Date.now()+30000)return null;
  const current=value.current_version,latest=record.latest_listed;
  if(current!==null&&!firmwareVersion(current)||latest!==null&&!firmwareVersion(latest)||!!record.entries.length!==!!latest)return null;
  const higher=current===null?null:!!latest&&compareVersions(latest,current)>0;
  return record.higher_listed===higher?record:null;
 }
 function currentObservation(value){
  const stamp=catalogTime(value?.current_observed_at);
  return firmwareVersion(value?.current_version)&&Number.isFinite(stamp)&&stamp>0&&stamp<=Date.now()+30000&&typeof value.current_source==='string'&&value.current_source.length>0&&value.current_source.length<=120?value.current_version:null;
 }
 function catalogToken(value){const expires=catalogTime(value?.catalog_expires_at),stamp=catalogTime(value?.checked_at);return fresh&&typeof value?.catalog_token==='string'&&value.catalog_token.length>0&&value.catalog_token.length<=256&&Number.isFinite(expires)&&expires>Date.now()&&expires<=Date.now()+600000&&expires>stamp&&expires<=stamp+600000?value.catalog_token:null;}
 function preparedPackage(value){
  const pkg=value?.package,metadata=listedMetadata(value),expires=catalogTime(pkg?.expires_at);
  return fresh&&value?.phase==='package_ready'&&metadata&&object(pkg)&&typeof pkg.token==='string'&&pkg.token.length>0&&pkg.token.length<=256&&firmwareVersion(pkg.version)&&pkg.version===metadata.latest_listed&&typeof pkg.sha256==='string'&&/^[0-9a-f]{64}$/.test(pkg.sha256)&&Number.isInteger(pkg.size)&&pkg.size>0&&pkg.size<=536870912&&Number.isFinite(expires)&&expires>Date.now()&&expires<=Date.now()+600000?pkg:null;
 }
 function writerMarker(){return JSON.stringify([sn(state?.device),state?.mode??null,state?.finished_at??null,state?.run?.id??null,state?.result?.verdict??null]);}
 function useOfficialCatalog(){
  if(selected?.kind!=='glove'||!object(state?.official_catalog)||state.busy===true||busyPhases.has(state.phase)||state.locked===true||state.recovery_required===true||recoveryPhases.has(state.phase)||preview())return false;
  const value=officialCatalog(),finished=clock(state.finished_at),hasOutcome=same()&&state.operation==='upgrade'&&(object(state.result)||Number.isFinite(finished)&&['failed','completed','verified'].includes(state.phase));
  // A preceding catalogue response must never replace the result of a writer.
  if(hasOutcome&&state.operation_generation!==undefined){
   const generation=state.operation_generation;
   if(!Number.isSafeInteger(generation)||generation<0)return false;
   const follows=Number.isSafeInteger(value?.follows_operation_generation)&&value.follows_operation_generation===generation;
   const requested=pending==='firmware_catalog_check'&&catalogChoice?.serial===sn(selected)&&catalogChoice.generation===generation;
   return follows||requested;
  }
  // Compatibility with older snapshots that did not publish a causal counter.
  const chosen=catalogChoice?.serial===sn(selected)&&catalogChoice.writer===writerMarker();
  return !hasOutcome||chosen||catalogAction()||value?.busy===true||Number.isFinite(finished)&&catalogTime(value?.checked_at)>finished;
 }
 function byteLabel(value){return value<1048576?(value/1024).toFixed(1)+' KB':(value/1048576).toFixed(1)+' MB';}
 function retainedRecord(){return serialOK(sn(state?.device))&&(state?.locked===true||state?.busy===true||!!state?.run?.id||clock(state?.checked_at)>Date.now()-86400000&&clock(state?.checked_at)<=Date.now()+30000);}
 function active(){return !!pending||fresh&&(state?.busy===true||busyPhases.has(state?.phase)||state?.official_catalog?.busy===true);}
 function syncDiscovery(){
  const hold=sheet.element.open||!!pending||state?.busy===true||state?.locked===true||state?.official_catalog?.busy===true||busyPhases.has(state?.phase);
  if(hold)inventory.pauseDiscovery?.('device-maintenance');else inventory.resumeDiscovery?.('device-maintenance');
 }
 function preview(){const value=state?.preview,release=catalog(),manual=state?.phase==='ready'&&!state?.notification&&!state?.check_observation;return (release?.update_available===true||manual)&&state?.can_upgrade===true&&fresh&&same()&&!active()&&!uncertain&&!state.error&&!state.locked&&!state.recovery_required&&state.supported!==false&&['ready','available'].includes(state.phase)&&value?.sn===sn(selected)&&value.mode==='official_confirmation'&&typeof value.token==='string'&&value.token.length>0&&value.token.length<=256&&clock(value.expires_at)>Date.now();}
 function valid(value){return object(value)&&typeof value.busy==='boolean'&&typeof value.phase==='string'&&value.phase.length<81&&(value.device==null||object(value.device))&&(value.error==null||typeof value.error==='string')&&(value.events==null||Array.isArray(value.events));}
 function accept(value){if(!valid(value))throw Error(t('设备更新状态不完整，请重新读取。','Device update status is incomplete. Reload it.'));state=value;fresh=true;transportError='';uncertain=false;}
 function label(row){const model=row?.model;return typeof model==='string'&&model.length<100?model:row?.kind==='glove'?'Wuji Glove':'Wuji Hand';}
 function humanError(value){
  const message=String(value||'');
  if(/HOME not set; cannot locate firmware cache/i.test(message))return t('升级工具找不到固件保存目录。','The update tool could not locate its firmware download folder.');
  if(/User home must be an absolute path|用户目录必须是有效绝对路径/i.test(message))return t('用户目录配置无效，需要使用绝对路径。','The user home setting must be an absolute path.');
  const official=same()&&state?.result?.sn===sn(selected)&&Number.isInteger(state?.result?.returncode)&&state.result.returncode!==0&&state?.result?.official_error;
  if(typeof official==='string'&&official&&message===official)return t('官方升级失败：','Official update failed: ')+official;
  if(/firmware completion is unconfirmed|固件升级结束状态未确认/i.test(message))return t('本次升级的结束状态尚未确认。请先核对设备，勿重复启动升级。','This update’s completion is unconfirmed. Check the device before starting another update.');
  if(/official flow ended|device version was read|未能读回设备版本|未匹配本次更新目标/i.test(message))return t('官方流程已结束，设备版本回读尚未确认。请重新核对设备。','The official flow ended; device-version readback is unverified. Check the device again.');
  if(/idle|session|会话|disconnect|断开|workspace|工作区/i.test(message))return t('请先结束设备会话，再检查更新。','End device sessions before checking updates.');
  if(/expired|过期|token|preview/i.test(message))return t('更新方案已过期，请重新检查。','The update plan expired. Check again.');
  if(/unsupported|不支持|not supported|match|匹配|incompat/i.test(message))return t('暂不支持此设备的更新，请查看详细记录。','An update is unavailable for this device. See details.');
  if(/network|fetch|timeout|abort|offline|离线|网络|connect.*service/i.test(message))return t('暂时无法读取更新状态，请重试。','Update status is unavailable. Try again.');
  return t('操作未完成，请查看详细记录后重试。','The operation did not complete. Review details and retry.');
 }
 function renderOfficialCatalog(){
  const value=officialCatalog(),metadata=listedMetadata(value),current=currentObservation(value),pkg=preparedPackage(value),token=catalogToken(value);
  const phase=value?.phase||'idle',busy=!!pending||value?.busy===true,other=state.official_catalog.busy===true&&state.official_catalog.serial!==sn(selected);
  const viewer=state?.viewer,endsPreview=viewer?.active===true&&viewer.phase==='running'&&viewer.serial===sn(selected)&&/^[0-9a-f]{32}$/.test(viewer.session_id||'');
  const latest=metadata?.latest_listed||null,downloadable=!!latest&&(value.current_version===null||metadata.higher_listed===true)&&!!token;
  const expired=!!metadata&&['listed','package_ready'].includes(phase)&&!token&&!pkg;
  versions.hidden=!metadata&&!current;from.hidden=to.hidden=false;arrow.hidden=true;versions.dataset.layout='comparison';
  text(fromLabel,t('上次读取版本','Last observed version'));text(toLabel,t('官方目录版本','Official catalog version'));
  text(fromValue,current||t('尚未读取','Not read'));text(toValue,latest||t('暂无版本','No listing'));
  fromValue.title=current?t('读取时间：','Observed: ')+new Date(catalogTime(value.current_observed_at)).toLocaleString(window.WujiLocale?.lang==='en'?'en-US':'zh-CN'):'';
  text(versionNote,!fresh?t('正在显示上次记录。','Showing the previous record.'):'');versionNote.hidden=!versionNote.textContent||versions.hidden;
  let message=t('查看可更新的固件版本。','Check for firmware updates.'),caption=t('检查更新','Check updates'),problem=false;
  if(other){message=t('另一设备的更新任务正在处理。','Another device update task is in progress.');}
  else if(!fresh||transportError||actionError){message=t('暂时无法读取更新状态，请重试。','Update status is unavailable. Try again.');caption=t('重新读取','Reload status');problem=true;}
  else if(pending==='firmware_package_upgrade'){message=t('正在打开官方安装…','Opening the official installer…');caption=t('正在打开…','Opening…');}
  else if(phase==='downloading'||pending==='firmware_package_prepare'){
   const bytes=value?.progress,validBytes=object(bytes)&&Number.isInteger(bytes.downloaded)&&bytes.downloaded>=0&&(bytes.total===null||Number.isInteger(bytes.total)&&bytes.total>0&&bytes.downloaded<=bytes.total);
   const verifying=['verifying','cache_verified'].includes(bytes?.stage);
   message=verifying?t('正在验证官方更新包…','Verifying the official package…'):validBytes?t('正在下载官方更新包…','Downloading the official package…'):t('正在准备官方更新包…','Preparing the official package…');caption=verifying?t('正在验证…','Verifying…'):validBytes?t('正在下载…','Downloading…'):t('正在准备…','Preparing…');
   if(validBytes){if(!verifying)message=t('正在下载官方更新包 · ','Downloading the official package · ')+byteLabel(bytes.downloaded)+(bytes.total===null?'':' / '+byteLabel(bytes.total));if(bytes.total!==null&&!verifying){progress.max=bytes.total;progress.value=bytes.downloaded;}}
  }
  else if(phase==='checking'||pending==='firmware_catalog_check'){message=t('正在检查更新…','Checking for updates…');caption=t('正在检查…','Checking…');}
  else if(pkg){message=endsPreview?t('官方更新包已下载。安装前将结束这只手套的预览，再由你确认。','The official package is ready. This glove’s preview will close before you confirm installation.'):t('官方更新包已下载。安装前会核对设备，并由你确认。','The official package is ready. The device is checked before you confirm installation.');caption=t('打开官方安装','Open official installer');}
  else if(phase==='package_ready'){message=Number.isFinite(catalogTime(value?.package?.expires_at))&&catalogTime(value.package.expires_at)<=Date.now()?t('更新包已过期，请重新检查。','The package expired. Check again.'):t('更新包记录尚未确认，请重新检查。','The package record is unverified. Check again.');caption=t('重新检查','Check again');problem=true;}
  else if(phase==='failed'){message=t('更新检查未完成，请重试。','The update check did not finish. Try again.');caption=t('重新检查','Check again');problem=true;}
  else if(expired){message=t('目录记录已过期，请重新检查。','The catalog record expired. Check again.');caption=t('重新检查','Check again');}
  else if(phase==='empty'&&metadata){message=t('官方目录暂无更新包。','No update package is listed in the official catalog.');caption=t('重新检查','Check again');}
  else if(metadata&&latest){
   message=current&&metadata.higher_listed===false?t('目录暂无更高版本。','No higher version is listed in the catalog.'):current?t('官方目录有较新版本。','A newer version is listed in the official catalog.'):t('目录版本已读取，设备版本待核对。','The catalog version is available; the device version has not been verified.');
   caption=downloadable?t('下载官方更新包','Download official package'):t('重新检查','Check again');
  }
  else if(phase!=='idle'){message=t('更新记录尚未确认，请重新检查。','The update record is unverified. Check again.');caption=t('重新检查','Check again');}
  text(status,message);status.classList.toggle('dm-status-error',problem);error.hidden=true;
  sheet.element.dataset.catalogPhase=phase;sheet.element.setAttribute('aria-busy',String(busy||other));progress.hidden=!(busy||other);
  controls.hidden=false;check.hidden=true;text(primary,caption);primary.hidden=false;primary.disabled=busy||other;
  sheet.element.classList.remove('dm-native');
  // Keep diagnostics available without exposing one-use action tokens in a
  // copied report or mistaking an SN listing for hardware compatibility.
  if(value){const record={serial:value.serial,device_type:value.device_type,phase,checked_at:value.checked_at,current_version:value.current_version,current_observed_at:value.current_observed_at,current_source:value.current_source,catalog_metadata:value.catalog_metadata,compatibility_verified:false,error:value.error,progress:value.progress,package:value.package?{version:value.package.version,sha256:value.package.sha256,size:value.package.size,expires_at:value.package.expires_at}:null};text(records,[records.textContent,t('官方目录记录','Official catalog record'),JSON.stringify(record,null,2).slice(0,12000)].filter(Boolean).join('\n'));details.hidden=false;}
 }
 function render(){
  syncDiscovery();
  sheet.setLabels({title:t('设备更新','Device update'),triggerLabel:t('设备更新','Device update'),closeLabel:t('关闭','Close')});
  const row=inventory.selection(),retained=retainedRecord(),resumeSelected=retained&&sn(row)===sn(state.device);
  trigger.disabled=state?.supported===false&&!retained||!available(row)&&!resumeSelected;trigger.title=trigger.disabled?t('先选择刚发现的设备','Select a freshly discovered device'):'';
  const resumable=state?.locked===true||!!state?.run?.id||state?.busy===true&&state?.automatic!==true;
  resume.hidden=!resumable||!!row&&sn(row)===sn(state.device);text(resume,t('返回设备更新','Return to device update'));
  const failure=failedCatalog(),release=catalog(),current=state?.current_version||failure?.current||(state?.result?.verdict==='recovery_verified'&&state.result.sn===sn(selected)?state.result.firmware_version:null);
  const target=state?.operation==='upgrade'?(state.target_version||(state.result?.sn===sn(selected)?state.result.expected_version:null)):null,latest=release?.latest||null,shownTarget=!latest&&!!target;
  text(name,label(selected));text(serial,sn(selected));text(fromLabel,t('当前版本','Current version'));text(toLabel,shownTarget?t('本次更新目标','Target for this update'):t('可用版本','Available version'));
  versions.hidden=!same()||(!current&&!target&&!latest&&!failure&&!clock(state?.checked_at)&&!recoveryPhases.has(state?.phase));from.hidden=!same();to.hidden=!same()||!!latest&&current===latest;arrow.hidden=from.hidden||to.hidden||!current||!latest;
  text(fromValue,same()&&current||t('尚未读取','Not read'));text(toValue,same()&&(latest||target)||t('尚未确认','Unverified'));versions.dataset.layout=to.hidden?'single':arrow.hidden?'comparison':'arrow';
  text(versionNote,!fresh?t('版本显示上次记录，恢复连接后再核对。','Version values are from the previous status. Reconnect to verify.'):!latest?t('可用版本尚未确认。','The available version is unverified.'):'');versionNote.hidden=versions.hidden||!versionNote.textContent;
  const phase=fresh&&same()?state.phase:'idle',busy=active(),other=fresh&&!same()&&(state?.busy===true||state?.locked===true||state?.recovery_required===true||recoveryPhases.has(state?.phase));
  const fileReadback=state?.mode==='official_file_confirmation'&&state.result?.source==='official_resource_readback';
  const fileRecovered=fileReadback&&phase==='verified'&&state.result?.verdict==='recovery_verified'&&state.result.sn===sn(selected)&&firmwareVersion(state.result.firmware_version)&&state.result.firmware_version===current;
  const verified=fileRecovered||(!failure||fileReadback)&&phase==='verified'&&state.result?.verdict==='target_version_verified'&&state.result.version_verified===true&&state.result.sn===sn(selected)&&state.result.firmware_version===state.result.expected_version&&state.result.firmware_version===current;
  const notStarted=state?.mode==='official_file_confirmation'&&phase==='completed'&&state.result?.verdict==='official_write_not_started'&&state.result.sn===sn(selected)&&state.result.version_verified!==true;
  const fileMismatch=fileReadback&&phase==='completed'&&state.result?.verdict==='version_not_matched'&&state.result.version_verified===false&&state.result.sn===sn(selected)&&firmwareVersion(state.result.firmware_version)&&firmwareVersion(state.result.expected_version)&&state.result.firmware_version!==state.result.expected_version;
  if(state?.mode==='official_file_confirmation')versionNote.hidden=true;
  const unchanged=!failure&&phase==='completed'&&state.result?.verdict==='unchanged'&&state.result.sn===sn(selected)&&state.result.version_verified===false;
  const messages={idle:['先检查设备，再打开官方升级。','Check the device, then open the official update.'],checking:['正在检查设备…','Checking the device…'],preparing:['正在打开官方升级…','Opening the official update…'],downloading:['请查看下方官方升级进度。','Follow the official update progress below.'],ready:['检查已完成。打开后跟随官方提示确认升级。','Device checked. Follow the official prompts after opening the update.'],available:['检查已完成。打开后跟随官方提示确认升级。','Device checked. Follow the official prompts after opening the update.'],up_to_date:['设备已是最新版本。','The device is up to date.'],upgrading:['请跟随下方官方提示，保持设备供电与连接。','Follow the official prompts below. Keep the device powered and connected.'],running:['请跟随下方官方提示，保持设备供电与连接。','Follow the official prompts below. Keep the device powered and connected.'],updating:['请跟随下方官方提示，保持设备供电与连接。','Follow the official prompts below. Keep the device powered and connected.'],flashing:['正在写入固件，请保持设备供电与连接。','Writing firmware. Keep the device powered and connected.'],reconnecting:['等待设备重新连接…','Waiting for the device to reconnect…'],awaiting_reconnect:['等待设备重新连接…','Waiting for the device to reconnect…'],verifying:['正在核对设备版本…','Checking the device version…'],succeeded:['官方流程已结束，请核对设备版本。','The official process ended. Check the device version.'],completed:['官方流程已结束，请核对设备版本。','The official process ended. Check the device version.'],unsupported:['此设备暂不支持在线更新。','Online updates are unavailable for this device.'],unconfirmed:['更新结果尚未确认，请核对设备。','The update result is unconfirmed. Check the device.'],recovery_required:['请核对设备的实际版本后继续。','Check the actual device version before continuing.'],reconnect_required:['请重新连接设备，再核对更新结果。','Reconnect the device, then verify the update.'],failed:['官方升级未完成，请查看详细记录。','The official update did not complete. See details.'],error:['操作未完成，请查看详细记录。','The operation did not complete. See details.']};
  const message=state?.supported===false?t('当前控制环境暂不支持官方升级。','Official updates are unavailable in the current controller environment.'):other?t('另一设备正在更新，请稍后再试。','Another device is updating. Try again shortly.'):phase==='unknown'||state?.locked===true?t('官方升级的结束状态尚未确认，请先核对设备。','The official update exit is unconfirmed. Check the device first.'):phase==='verified'?t('已核对设备版本。','The device version has been verified.'):uncertain?t('请求结果尚未确认，请先重新读取状态。','The request result is unconfirmed. Reload status first.'):pending==='firmware_upgrade'?t('正在打开官方升级…','Opening the official update…'):pending==='firmware_check'?t('正在检查设备…','Checking the device…'):pending==='firmware_recovery_check'?t('正在核对设备…','Checking the device…'):t(...(messages[phase]||['正在读取设备状态…','Reading device status…']));
  const problem=transportError||actionError||(same()&&state.error)||'';
  const viewer=state?.viewer,ownedPreview=viewer?.active===true&&viewer.phase==='running'&&viewer.serial===sn(selected)&&/^[0-9a-f]{32}$/.test(viewer.session_id||'');
  const checkFailure=['failed','check_failed'].includes(phase)&&state?.operation!=='upgrade';
  const reportMessage=notStarted?t('官方安装尚未启动，可以重新准备。','Official installation did not start. You can prepare again.'):fileMismatch?t('设备当前版本未匹配本次安装目标。','The device version does not match this installation target.'):verified?(fileReadback?t('已核对设备版本。','Device version verified.'):t('更新已确认。','Update confirmed.')):state?.mode==='official_file_confirmation'&&phase==='verified'?t('设备版本尚未核对，请重新检查。','The device version is unverified. Check again.'):unchanged?t('设备版本未变化，可重新检查。','The device version is unchanged. You can check again.'):checkFailure?t('版本检查未完成，可以重新检查。','Version check incomplete. Try again.'):phase==='available'?(release?t('有适用于这台设备的新固件。','New firmware is available for this device.'):t('可用版本尚未确认，请重新检查。','The available version is unverified. Check again.')):phase==='checked'?t('官方检查已结束，可打开官方流程查看详情。','Official check finished. Open the official flow for details.'):phase==='up_to_date'?(release?t('固件已是最新版本。','Firmware is up to date.'):t('可用版本尚未确认，请重新检查。','The available version is unverified. Check again.')):phase==='unknown'&&state?.operation==='check'?t('检查进程退出尚未确认，请先核对检查。','The check process exit is unconfirmed. Verify the check first.'):message;
  const recover=fresh&&same()&&(recoveryPhases.has(phase)||state.locked===true||state.recovery_required===true);
  const recoveryMessage=recover?(state.operation==='check'?t('检查会话的退出或释放尚未确认，请先核对检查。','The check session exit or release is unconfirmed. Verify the check first.'):t('官方升级会话的退出或释放尚未确认，请先核对设备，不要再次升级。','The official update session exit or release is unconfirmed. Verify the device before another update.')):'';
  const catalogMessage=failure&&!fileReadback&&!recover&&!transportError&&!actionError?(/(?:\bHTTP\s*404\b|\b404\b)/i.test(failure.error)&&/hardware version.*not found/i.test(failure.error)?t('兼容固件列表查询失败；官方服务未找到对应硬件版本（404）。可用版本尚未确认，可稍后重新检查。','Compatible firmware list lookup failed; the official service did not find this hardware version (404). The available version is unverified. Check again later.'):t('兼容固件列表查询失败，可用版本尚未确认。可稍后重新检查。','Compatible firmware list lookup failed. The available version is unverified. Check again later.')):'';
  text(status,recoveryMessage||(notStarted||fileMismatch?reportMessage:catalogMessage||(problem?humanError(problem):ownedPreview&&!active()?t('更新前需要结束这台设备的预览；仅在你继续后执行。','This device’s preview must end before an update. It ends only when you continue.'):reportMessage)));status.classList.toggle('dm-status-error',!!problem&&!notStarted||!!failure&&!fileReadback);error.hidden=true;
  sheet.element.dataset.phase=phase;delete sheet.element.dataset.catalogPhase;sheet.element.setAttribute('aria-busy',String(busy));progress.hidden=!busy;progress.removeAttribute('value');progress.removeAttribute('max');
  text(check,!fresh||uncertain||state?.supported===false?t('重新读取','Reload status'):t('重新检查','Check again'));check.hidden=true;check.disabled=!!pending||busy;
  text(primary,recover?t('核对设备','Check device'):preview()?t('打开官方升级','Open official update'):t('检查设备','Check device'));
  primary.disabled=!fresh||busy||other||uncertain||!!transportError||state?.supported===false;primary.hidden=!failure&&(phase==='unsupported'||phase==='up_to_date'&&!!release);
  if(!fresh||uncertain||transportError){text(primary,t('重新读取','Reload status'));primary.disabled=!!pending||busy;primary.hidden=false;}
  else if(ownedPreview&&!recover&&!active()){text(primary,t('结束预览并检查更新','End preview and check'));primary.disabled=false;primary.hidden=false;}
  if(fresh&&same()&&state.mode==='official_file_confirmation'&&!recover&&!busy){text(primary,t('检查更新','Check updates'));primary.hidden=false;primary.disabled=false;}
  check.disabled=check.disabled||fresh&&state?.locked===true;
  const run=same()&&object(state.run)?state.run:{},runningTTY=run.source==='official_tty'&&run.running===true;
  const archivedTTY=run.source==='official_tty'&&!!run.id&&run.running===false;
  const presentation=archivedTTY?run.id+':'+phase:'';
  controls.hidden=runningTTY;sheet.element.classList.toggle('dm-native',runningTTY);
  if(archivedTTY){
   if(terminalHost.parentNode!==details)details.insertBefore(terminalHost,records);
   if(presentation!==terminalPresentation){details.open=false;body.scrollTop=0;requestAnimationFrame(()=>{body.scrollTop=0;});}
  }else if(terminalHost.parentNode!==body)body.insertBefore(terminalHost,details);
  terminalPresentation=presentation;terminal?.render(run,{fresh:fresh&&same()});
  const lines=[];if(problem)lines.push(problem);if(same()){for(const [key,title] of [['check_report',t('官方设备检查','Official device check')],['verification_report',t('更新后的版本核对','Version check after update')],['catalog_report',t('官方固件列表','Official firmware list')]]){const report=state[key];if(object(report)){lines.push(title);if(typeof report.stdout==='string')lines.push(report.stdout);if(typeof report.stderr==='string')lines.push(report.stderr);}}
   for(const event of (state.events||[]).slice(-40)){if(typeof event==='string')lines.push(event);else if(object(event)){const value=event.message||event.text||event.event||event.phase;if(typeof value==='string')lines.push(value);}}}text(records,lines.map(line=>String(line).slice(0,12000)).join('\n'));details.hidden=!lines.length&&!archivedTTY;text(summary,t('详细记录','Details'));
  if(useOfficialCatalog())renderOfficialCatalog();
 }
 async function load(){
  if(loading)return loading;const version=epoch;
  loading=(async()=>{try{const response=await fetch('/api/device-maintenance',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!response.ok)throw Error('Update status unavailable');const value=await response.json();if(version!==epoch)return;accept(value);}catch(failure){if(version!==epoch)return;fresh=false;transportError=failure.message||'Update status unavailable';}finally{loading=null;render();schedule();}})();return loading;
 }
 async function request(command){
  if(pending)return;
  if(command.name==='firmware_catalog_check')catalogChoice={serial:command.serial,generation:state?.operation_generation,writer:writerMarker()};
  else if(command.name==='firmware_package_upgrade')catalogChoice=null;
  pending=command.name;const version=++epoch;actionError='';transportError='';syncDiscovery();render();
  try{
   const session=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!session.ok)throw Error('Session unavailable');const token=(await session.json()).csrf;if(version!==epoch)return;if(typeof token!=='string'||!token)throw Error('Session unavailable');
   const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify(command),signal:AbortSignal.timeout(command.name==='firmware_check'?60000:15000)}),result=await response.json();
   if(version!==epoch)return;if(!response.ok||result.ok===false)throw Error(result.error||'Device update did not complete');
   const value=result.device_maintenance||result.maintenance;if(value)accept(value);else fresh=false;
  }catch(failure){if(version===epoch){fresh=false;actionError=failure.message||'Device update did not complete';if(command.name==='firmware_upgrade')uncertain=true;}}
  finally{pending='';syncDiscovery();render();if(version===epoch)await load();syncDiscovery();}
 }
 async function checkSelected(){
  const number=sn(selected);
  if(!serialOK(number)){actionError=t('请选择要检查的设备。','Select the device to check.');render();return;}
  // The server refreshes expired discovery once and repeats exact identity.
  // A finished official process should not require a separate manual rescan.
  await request({name:'firmware_check',serial:number});
 }
 function schedule(){clearTimeout(timer);if(sheet.element.open||pending||state?.busy===true||state?.locked===true||state?.official_catalog?.busy===true||busyPhases.has(state?.phase))timer=setTimeout(()=>{if(!pending)load();else schedule();},active()?1000:5000);}
 function openSelection(show=true,saved=false){const row=inventory.selection(),previous=state?.device,retained=retainedRecord();if(saved&&retained)selected=previous;else if(available(row))selected=row;else if(retained&&(!row||sn(row)===sn(previous)))selected=previous;else return false;actionError='';transportError='';epoch++;fresh=false;if(show)sheet.open();render();load();return true;}
 check.onclick=()=>!fresh||uncertain||state?.supported===false?load():checkSelected();
 primary.onclick=()=>{
  if(!fresh||uncertain||transportError||actionError){actionError='';load();return;}
  if(same()&&(recoveryPhases.has(state.phase)||state.locked===true||state.recovery_required===true)){request({name:'firmware_recovery_check'});return;}
  if(selected?.kind==='glove'&&same()&&state.mode==='official_file_confirmation'&&!state.busy&&!busyPhases.has(state.phase)&&!useOfficialCatalog()){request({name:'firmware_catalog_check',serial:sn(selected)});return;}
  if(useOfficialCatalog()){
   const value=officialCatalog(),metadata=listedMetadata(value),pkg=preparedPackage(value),token=catalogToken(value);
   if(pkg)request({name:'firmware_package_upgrade',serial:sn(selected),package_token:pkg.token,confirmed:true});
   else if(value?.phase==='listed'&&metadata?.latest_listed&&token&&(value.current_version===null||metadata.higher_listed===true))request({name:'firmware_package_prepare',serial:sn(selected),catalog_token:token});
   else request({name:'firmware_catalog_check',serial:sn(selected)});
   return;
  }
  const viewer=state?.viewer;if(viewer?.active===true&&viewer.phase==='running'&&viewer.serial===sn(selected)&&/^[0-9a-f]{32}$/.test(viewer.session_id||'')){request({name:'firmware_check',serial:sn(selected),viewer_token:viewer.session_id});return;}
  if(preview())request({name:'firmware_upgrade',preview_token:state.preview.token,confirmed:true});else checkSelected();
 };
 resume.onclick=()=>openSelection(true,true);
 const discoveryObserver=new MutationObserver(syncDiscovery);discoveryObserver.observe(sheet.element,{attributes:true,attributeFilter:['open']});
 sheet.element.addEventListener('close',()=>{syncDiscovery();schedule();});
 window.addEventListener('device-inventory-selection',render);window.addEventListener('device-inventory-update',render);window.addEventListener('wuji-language',render);
 window.addEventListener('device-inventory-use',()=>{openRequest++;sheet.close();document.getElementById('di-device-more')?.removeAttribute('open');});
 window.addEventListener('console-offline',()=>{epoch++;fresh=false;transportError=t('工作台服务已离线。','Workbench service is offline.');render();});
 async function openBySerial(number){
  const request=++openRequest;await load();if(request!==openRequest)return;
  const discovered=inventory.snapshot?.()?.devices?.some(row=>sn(row)===number&&available(row));
  const chosen=discovered&&inventory.select(number)&&sn(inventory.selection())===number,retained=sn(state?.device)===number&&retainedRecord();
  if(chosen||retained){openSelection(true,!chosen&&retained);return;}
  const history=window.WujiDeviceHistory?.snapshot?.()?.find(row=>row.kind==='glove'&&row.serial===number&&/^WG1[JK][A-Z0-9]{12}$/.test(row.serial)&&(row.generation===null||row.generation==='glove1'));
  if(history){
   // A saved connection fact permits an independent catalogue lookup only.
   // It is deliberately absent from discovery and never selects/connects a
   // device. Installation repeats the host's fresh exact-device checks.
   selected={sn:number,serial:number,kind:'glove',generation:'glove1',side:history.side,model:'Wuji Glove 1',history_only:true};
   actionError='';transportError='';epoch++;fresh=false;sheet.open();render();load();return;
  }
  inventory.show();
 }
 window.WujiDeviceMaintenance=Object.freeze({open(serial){if(serial!==undefined){if(!serialOK(serial))return false;openBySerial(serial);return true;}return openSelection();},refresh:load});render();load();
})();
