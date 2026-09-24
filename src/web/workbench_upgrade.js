/* One shared Windows/macOS interface. Extra device workspaces keep their own live pages. */
(()=>{
 'use strict';
 const ws=window.WujiWorkspace,L=window.WujiLocale,$=id=>document.getElementById(id),t=(zh,en)=>L.lang==='en'?en:zh;
 const embedded=new URLSearchParams(location.search).get('embedded')==='1';
 document.documentElement.dataset.embedded=String(embedded);
 let state=null,catalog=null,retarget=null,programLease=null,programPending=false;
 const speeds=[.1,.25,.5,.75,1,1.25,1.5,1.75,2,2.5,3,4];
 for(const id of ['trial-speed','play-speed']){
  const select=$(id),previous=select?.value;if(!select)continue;
  select.replaceChildren(...speeds.map(rate=>new Option(rate+'×',String(rate))));select.value=previous||'1';
 }
 function addPage(key,zh,en,description){
  const page=document.createElement('section');page.id='page-'+key;page.className='workspace-page';page.hidden=true;
  page.innerHTML='<header class="page-heading"><h2></h2><p></p></header><div class="page-body"></div>';
  document.querySelector('.page-area').append(page);ws.views[key]=page.querySelector('.page-body');
  const link=document.createElement('a');link.href='#'+key;link.dataset.page=key;link.innerHTML='<svg viewBox="0 0 26 24" aria-hidden="true"><circle cx="13" cy="12" r="8"/><circle cx="13" cy="12" r="2"/></svg><span></span>';
  document.querySelector('.directory').append(link);
  const label=()=>{page.querySelector('h2').textContent=link.lastElementChild.textContent=t(zh,en);page.querySelector('header p').textContent=description[L.lang]};
  label();window.addEventListener('wuji-language',label);return page.querySelector('.page-body');
 }
 const settings=addPage('settings','设置','Settings',{zh:'让工作台适合你的使用习惯。',en:'Make the workbench feel right for you.'});
 const devices=embedded?null:addPage('devices','多手设备','Devices',{zh:'每只手独立连接、播放和显示；切换视图不会结束其他设备的会话。',en:'Each hand has its own connection, playback and view. Switching views keeps other sessions running.'});
 settings.innerHTML=`<div class="wb-card"><h3 id="wb-appearance-title"></h3><p id="wb-appearance-note"></p><div class="wb-row"><label id="wb-theme-label" for="wb-theme"></label><select id="wb-theme"><option value="system"></option><option value="light"></option><option value="dark"></option></select></div><div class="wb-row"><label id="wb-auto-hand-label"><input type="checkbox" id="wb-auto-hand"><span></span></label></div><div class="wb-row"><label id="wb-auto-glove-label"><input type="checkbox" id="wb-auto-glove"><span></span></label></div></div>
 <div class="wb-card"><h3 id="wb-retarget-title"></h3><p id="wb-retarget-note"></p><div class="wb-row"><label for="wb-smoothing" id="wb-smoothing-label"></label><input id="wb-smoothing" type="number" min="0" max="1000" step="10" value="0"><span>ms</span></div><div id="wb-retarget-grid" class="wb-list"></div><div class="wb-actions"><button id="wb-retarget-default"></button><button id="wb-retarget-save" class="primary"></button></div><p id="wb-retarget-status" class="wb-status" role="status"></p></div>`;
 if(devices)devices.innerHTML=`<div class="wb-card"><h3 id="wb-device-title"></h3><p id="wb-device-note"></p><div class="wb-row"><label for="wb-device-name" id="wb-device-name-label"></label><input id="wb-device-name" maxlength="40"><label for="wb-device-profile" id="wb-device-profile-label"></label><select id="wb-device-profile"><option value="hand2_left">Hand 2 · Left</option><option value="hand2_right">Hand 2 · Right</option><option value="hand1_left">Hand 1 · Left</option><option value="hand1_right">Hand 1 · Right</option></select><button id="wb-device-add" class="primary"></button></div><p id="wb-device-status" role="status" class="wb-status"></p><div id="wb-device-list" class="wb-list"></div></div>`;
 const program=document.createElement('details');program.className='wb-card';program.id='wb-program';
 program.innerHTML=`<summary class="wb-program-summary"><span id="wb-program-title"></span><small id="wb-program-count"></small><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg></summary><div class="wb-program-body"><p id="wb-program-note"></p><div class="wb-row wb-add-row"><select id="wb-dance-add"></select><button id="wb-dance-add-button"></button><button id="wb-dance-all"></button></div><div id="wb-entries" class="wb-list"></div><div class="wb-row wb-order-row"><label for="wb-order" id="wb-order-label"></label><select id="wb-order"><option value="sequence"></option><option value="shuffle"></option></select><label for="wb-repeats" id="wb-repeats-label"></label><input id="wb-repeats" type="number" min="0" max="100" step="1" value="1"><label for="wb-seed" id="wb-seed-label"></label><input id="wb-seed" type="number" min="0" max="4294967295" step="1" value="0"></div><div class="wb-actions"><button id="wb-program-preview"></button><button id="wb-program-hardware" class="primary"></button><button id="wb-program-pause"></button><button id="wb-program-stop"></button></div><p id="wb-program-status" class="wb-status" role="status"></p></div>`;
 $('page-library').querySelector('.page-body').prepend(program);
 let entries=[];
 function newEntry(id){return {action:id,speed:1,cycles:1};}
 function persistEntries(){try{localStorage.setItem('wuji-dance-program',JSON.stringify(entries));}catch{}}
 try{const saved=JSON.parse(localStorage.getItem('wuji-dance-program')||'[]');if(Array.isArray(saved))entries=saved.filter(x=>x&&typeof x.action==='string'&&speeds.includes(x.speed)&&Number.isInteger(x.cycles)&&x.cycles>=1&&x.cycles<=20).slice(0,64);}catch{}
 function renderEntries(){
  const host=$('wb-entries');host.replaceChildren();const names=new Map((catalog?.actions||[]).map(x=>[x.id,L.lang==='en'?x.en:x.zh]));
  $('wb-program-count').textContent=entries.length?t(`已选 ${entries.length} 个动作`,`Selected ${entries.length} routines`):t('点击编排动作','Choose routines');
  entries.forEach((entry,index)=>{
   const row=document.createElement('div');row.className='wb-entry';
   const name=document.createElement('strong');name.textContent=`${index+1}. ${names.get(entry.action)||entry.action}`;
   const speed=document.createElement('select');speed.setAttribute('aria-label',t('此动作速度','Speed for this action'));
   speed.replaceChildren(...speeds.map(rate=>new Option(rate+'×',String(rate))));speed.value=String(entry.speed);
   speed.onchange=()=>{entry.speed=Number(speed.value);persistEntries()};
   const count=document.createElement('select');count.setAttribute('aria-label',t('此动作重复','Repeats for this action'));
   count.replaceChildren(...[1,2,3,5,10,20].map(n=>new Option(n+'×',String(n))));count.value=String(entry.cycles);
   count.onchange=()=>{entry.cycles=Number(count.value);persistEntries()};
   for(const [symbol,delta] of [['↑',-1],['↓',1],['×',null]]){
    const button=document.createElement('button');button.type='button';button.textContent=symbol;button.title=delta===null?t('移除','Remove'):delta<0?t('上移','Move up'):t('下移','Move down');
    button.disabled=delta!==null&&!(index+delta>=0&&index+delta<entries.length);
    button.onclick=()=>{if(delta===null)entries.splice(index,1);else [entries[index],entries[index+delta]]=[entries[index+delta],entries[index]];persistEntries();renderEntries()};
    row.append(button);
   }
   row.prepend(name,speed,count);host.append(row);
  });
  updateProgramControls();
 }
 function updateProgramControls(){
  $('wb-program-preview').disabled=programPending||!entries.length;
  $('wb-program-hardware').disabled=programPending||!entries.length||state?.connection!=='connected'||state?.stale||state?.device_profile?.generation!=='hand2'||state?.hardware?.active!==false;
 }
 function refreshCatalog(){
  fetch('/api/catalog').then(r=>r.json()).then(c=>{catalog=c;
   const choices=(c.actions||[]).filter(x=>x.group==='dance');
   $('wb-dance-add').replaceChildren(...choices.map(x=>new Option(L.lang==='en'?x.en:x.zh,x.id)));
   entries=entries.filter(e=>choices.some(x=>x.id===e.action));renderEntries();
  }).catch(()=>{});
 }
 $('wb-dance-add-button').onclick=()=>{if(entries.length<64){entries.push(newEntry($('wb-dance-add').value));persistEntries();renderEntries();}};
 $('wb-dance-all').onclick=()=>{entries=(catalog?.actions||[]).filter(x=>x.group==='dance').map(x=>newEntry(x.id));persistEntries();renderEntries()};
 function request(name,payload={}){
  if(!state?.csrf)return Promise.reject(Error(t('连接本机服务后重试','Connect to the local service first')));
  return fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name,...payload})})
   .then(async r=>{const value=await r.json();if(!r.ok||!value.ok)throw Error(value.error||'Request failed');return value});
 }
 async function programCommand(name,payload={}){
  programPending=true;updateProgramControls();
  try{const out=await request(name,payload);if(out.program_lease)programLease=out.program_lease;if(name==='program_stop')programLease=null;$('wb-program-status').textContent='';}
  catch(error){$('wb-program-status').textContent=error.message;}
  finally{programPending=false;updateProgramControls()}
 }
 function plan(){return {entries:entries.map(x=>({...x})),order:$('wb-order').value,repeats:Number($('wb-repeats').value),seed:Number($('wb-seed').value)}}
 $('wb-program-preview').onclick=()=>programCommand('program_start',{mode:'preview',plan:plan(),amplitude:1});
 $('wb-program-hardware').onclick=()=>programCommand('program_start',{mode:'hardware',plan:plan(),amplitude:Number($('trial-amplitude')?.value||1),workspace_clear:!!$('trial-clear')?.checked});
 $('wb-program-pause').onclick=()=>programCommand(state?.program?.paused?'program_resume':'program_pause');
 $('wb-program-stop').onclick=()=>programCommand('program_stop');
 // Keep playlist-level choices alongside the already-persistent entries.
 const programOptions=['wb-order','wb-repeats','wb-seed'];
 try{const saved=JSON.parse(localStorage.getItem('wuji-program-options')||'{}');
  if(['sequence','shuffle'].includes(saved.order))$('wb-order').value=saved.order;
  if(Number.isInteger(saved.repeats)&&saved.repeats>=0&&saved.repeats<=100)$('wb-repeats').value=saved.repeats;
  if(Number.isInteger(saved.seed)&&saved.seed>=0&&saved.seed<=4294967295)$('wb-seed').value=saved.seed;
 }catch{}
 for(const id of programOptions)for(const event of ['input','change'])$(id).addEventListener(event,()=>{try{localStorage.setItem('wuji-program-options',JSON.stringify({order:$('wb-order').value,repeats:Number($('wb-repeats').value),seed:Number($('wb-seed').value)}));}catch{}});
 let beating=false;setInterval(async()=>{if(!programLease||beating)return;beating=true;
  try{await request('program_keepalive',{lease:programLease})}catch(error){programLease=null;$('wb-program-status').textContent=error.message}finally{beating=false}
 },250);
 window.addEventListener('pagehide',()=>{if(programLease)request('program_stop').catch(()=>{})});
 const frames=new Map();let active='main',fleet=[];
 function themeApply(value){
  const theme=value==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):value;
  document.documentElement.dataset.theme=theme;
  $('wb-theme').value=value;
  if(!embedded)for(const frame of frames.values())frame.contentWindow?.postMessage({type:'wb-theme',value},'*');
 }
 let themeChoice=localStorage.getItem('wuji-workbench-theme')||'system';
 const fromParent=new URLSearchParams(location.search).get('theme');if(embedded&&['system','light','dark'].includes(fromParent))themeChoice=fromParent;
 themeApply(themeChoice);
 $('wb-theme').onchange=()=>{themeChoice=$('wb-theme').value;localStorage.setItem('wuji-workbench-theme',themeChoice);themeApply(themeChoice)};
 matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>themeApply(themeChoice));
 window.addEventListener('message',event=>{if(embedded&&document.referrer&&event.origin===new URL(document.referrer).origin&&event.data?.type==='wb-theme')themeApply(event.data.value)});
 const autoHand=$('wb-auto-hand'),autoGlove=$('wb-auto-glove');
 autoHand.checked=localStorage.getItem('wuji-auto-hand')==='true';autoGlove.checked=localStorage.getItem('wuji-auto-glove')==='true';
 autoHand.onchange=()=>localStorage.setItem('wuji-auto-hand',String(autoHand.checked));
 autoGlove.onchange=()=>localStorage.setItem('wuji-auto-glove',String(autoGlove.checked));
 let lastHand=0,lastGlove=0,openedGlove=false;
 setInterval(()=>{
  const now=Date.now();
  if(autoHand.checked&&!autoGlove.checked&&!state?.glove?.busy&&['disconnected','error'].includes(state?.connection)&&now-lastHand>15000){lastHand=now;request('connect',{address:'',auto_detect:true}).catch(()=>{})}
  if(autoGlove.checked&&state?.connection==='disconnected'&&!state?.glove?.busy&&now-lastGlove>15000){lastGlove=now;request('glove_scan').catch(()=>{})}
  const g=state?.glove||{};
  if(autoGlove.checked&&g.connection==='ready'&&g.devices?.length===1&&!openedGlove){openedGlove=true;
   request('glove_open',{serial:g.devices[0].serial,user_id:g.user?.user_id||'',timeout_ms:250}).catch(()=>{openedGlove=false});
  }
  if(g.connection==='disconnected')openedGlove=false;
 },1000);
 document.addEventListener('click',event=>{if(event.target?.id==='disconnect'||event.target?.id==='connection-toggle'&&state?.connection==='connected'){
  autoHand.checked=false;localStorage.setItem('wuji-auto-hand','false');
 }},true);
 const fingerNames=()=>L.lang==='en'?['Thumb','Index','Middle','Ring','Little']:['拇指','食指','中指','无名指','小指'];
 function retargetRender(){if(!retarget)return;
  $('wb-smoothing').value=String(retarget.smoothing_ms);
  const host=$('wb-retarget-grid');host.replaceChildren();
  const headers=[];
  for(let f=0;f<5;f++){
   const group=document.createElement('details');group.className='wb-finger-group';
   const title=document.createElement('summary');title.textContent=fingerNames()[f];group.append(title);
   const header=document.createElement('div');header.className='wb-entry wb-entry-head';header.style.gridTemplateColumns='minmax(120px,1fr) 100px 100px';
   for(const value of [t('关节','Joint'),t('幅度','Gain'),t('偏移 °','Offset °')]){const label=document.createElement('span');label.textContent=value;header.append(label)}
   group.append(header);headers.push(group);host.append(group);
  }
  for(let i=0;i<20;i++){
   const row=document.createElement('div');row.className='wb-entry';row.style.gridTemplateColumns='minmax(120px,1fr) 100px 100px';
   const label=document.createElement('strong');label.textContent=`${fingerNames()[Math.floor(i/4)]} ${i%4+1}`;
   const gain=document.createElement('input');gain.type='number';gain.min='0';gain.max='2';gain.step='.05';gain.value=retarget.gain[i];gain.setAttribute('aria-label',t('幅度','Gain'));
   const offset=document.createElement('input');offset.type='number';offset.min='-180';offset.max='180';offset.step='1';offset.value=retarget.offset_deg[i];offset.setAttribute('aria-label',t('角度偏移','Offset degrees'));
   gain.oninput=()=>{retarget.gain[i]=Number(gain.value)};offset.oninput=()=>{retarget.offset_deg[i]=Number(offset.value)};
   row.append(label,gain,offset);headers[Math.floor(i/4)].append(row);
  }
 }
 let mappingContext=null;
 const mappingCard=$('wb-retarget-title').closest('.wb-card');
 const bindingNote=document.createElement('p');bindingNote.className='wb-note';bindingNote.id='wb-mapping-binding';$('wb-retarget-note').after(bindingNote);
 const presetRow=document.createElement('div');presetRow.className='wb-row';
 const presetSelect=document.createElement('select');presetSelect.setAttribute('aria-label',t('映射预设','Mapping preset'));
 const presetName=document.createElement('input');presetName.maxLength=48;presetName.placeholder=t('预设名称','Preset name');presetName.setAttribute('aria-label',t('预设名称','Preset name'));
 const loadPreset=document.createElement('button'),savePreset=document.createElement('button'),deletePreset=document.createElement('button'),applyMapping=document.createElement('button');
 for(const button of [loadPreset,savePreset,deletePreset,applyMapping])button.type='button';
 presetRow.append(presetSelect,loadPreset,presetName,savePreset,deletePreset);mappingCard.append(presetRow);$('wb-retarget-save').after(applyMapping);
 function showContext(value,replace=true){mappingContext=value;if(replace){retarget=value.settings;retargetRender()}
  const b=value.binding;bindingNote.textContent=`${b.generation} · ${b.side} · ${t('手套','Glove')} ${b.glove_serial||'—'} → ${t('机械手','Hand')} ${b.hand_serial||t('连接时选择','Select on connect')} · ${t('用户','User')} ${b.sdk_user||'—'} · v${value.revision}`;
  const choice=presetSelect.value;presetSelect.replaceChildren(new Option(t('选择映射预设','Select a mapping preset'),''),...(value.presets||[]).map(p=>new Option(p.name,p.name)));presetSelect.value=choice;
 }
 async function mappingRefresh(){try{const response=await fetch('/api/retarget/context');if(!response.ok)throw Error(t('无法读取映射配置','Cannot load mapping configuration'));showContext(await response.json())}catch(error){$('wb-retarget-status').textContent=error.message}}
 window.addEventListener('retarget-binding-changed',mappingRefresh);mappingRefresh();
 $('wb-smoothing').oninput=()=>{if(retarget)retarget.smoothing_ms=Number($('wb-smoothing').value)};
 $('wb-retarget-default').onclick=()=>{retarget={gain:Array(20).fill(1),offset_deg:Array(20).fill(0),smoothing_ms:0};retargetRender()};
 $('wb-retarget-save').onclick=async()=>{if(!retarget)return;retarget.smoothing_ms=Number($('wb-smoothing').value);
  try{const out=await request('retarget_save',{values:retarget,revision:mappingContext?.revision});showContext(out.mapping);$('wb-retarget-status').textContent=t('已保存到当前设备配对。可应用到手套预览，或重新连接后生效。','Saved for this pairing. Apply in glove preview, or reconnect.')}catch(error){$('wb-retarget-status').textContent=error.message}
 };
 loadPreset.onclick=()=>{const preset=mappingContext?.presets?.find(p=>p.name===presetSelect.value);if(preset){retarget=structuredClone(preset.settings);retargetRender();$('wb-retarget-status').textContent=t('已填入预设；保存后生效。','Preset loaded; save to use it.')}};
 savePreset.onclick=async()=>{try{const out=await request('retarget_preset_save',{preset:presetName.value,values:retarget});showContext(out.mapping,false);$('wb-retarget-status').textContent=t('预设已保存','Preset saved')}catch(e){$('wb-retarget-status').textContent=e.message}};
 deletePreset.onclick=async()=>{try{const out=await request('retarget_preset_delete',{preset:presetSelect.value});showContext(out.mapping,false)}catch(e){$('wb-retarget-status').textContent=e.message}};
 applyMapping.onclick=async()=>{try{await request('retarget_apply');$('wb-retarget-status').textContent=t('已发送应用请求，请核对下方控制端状态。','Apply requested; check controller status below.')}catch(e){$('wb-retarget-status').textContent=e.message}};
 const mappingLive=document.createElement('p');mappingLive.className='wb-note';mappingCard.append(mappingLive);
 window.addEventListener('console-state',e=>{const g=e.detail?.glove||{};applyMapping.disabled=g.connection!=='receiving'||!!g.feedback?.device_id;
  const equal=JSON.stringify(g.stream?.retarget)===JSON.stringify(mappingContext?.settings);
  mappingLive.textContent=g.connection==='receiving'?(equal?t('控制端正在使用已保存的映射','Controller is using the saved mapping'):t('控制端映射与保存值不同；应用预览或重新连接','Controller mapping differs; apply in preview or reconnect')):t('连接手套后显示控制端实际使用的映射','Connect a glove to inspect the applied mapping');
 });
 const gloveLink=document.createElement('a');gloveLink.href='#settings';gloveLink.className='wb-note';gloveLink.id='wb-glove-settings-link';
 $('page-glove')?.querySelector('.glove-controls')?.append(gloveLink);
 // Parent device switch keeps all loopback child frames mounted and their leases alive.
 const selector=document.createElement('select');selector.id='wb-device-switch';selector.setAttribute('aria-label',t('切换设备工作区','Switch device workspace'));
 if(!embedded)document.querySelector('.top-actions').prepend(selector);
 function switchTo(id){active=id;document.body.classList.toggle('wb-child-active',id!=='main');
  for(const [key,frame] of frames)frame.hidden=key!==id;
  if(id!=='main'&&!frames.has(id)){
   const d=fleet.find(x=>x.id===id);if(!d)return;
   const frame=document.createElement('iframe');frame.id='wb-device-frame-'+id;frame.className='wb-device-frame';frame.title=d.label;
   frame.src=`http://127.0.0.1:${d.port}/?embedded=1&theme=${encodeURIComponent(themeChoice)}#library`;
   document.body.append(frame);frames.set(id,frame);
  }
  selector.value=id;
 }
 selector.onchange=()=>switchTo(selector.value);
 window.HandSessions={show:switchTo,refresh:()=>fleetRefresh()};
 let fleetBusy=false,fleetViewKey='',fleetChoicesKey='';
 const fleetStop=document.createElement('button');fleetStop.type='button';fleetStop.className='danger';
 if(devices){fleetStop.textContent=t('停止所有工作区动作','Stop motion in all workspaces');$('wb-device-status').before(fleetStop);
  fleetStop.onclick=async()=>{fleetStop.disabled=true;try{const out=await request('fleet_stop');$('wb-device-status').textContent=out.errors?.length?JSON.stringify(out.errors):t('已向全部工作区请求停止','Stop requested in every workspace')}catch(error){$('wb-device-status').textContent=error.message}finally{fleetStop.disabled=false}};
 }
 async function fleetRefresh(){if(embedded||fleetBusy)return;fleetBusy=true;try{
  const result=await (await fetch('/api/fleet')).json();fleet=result.devices||[];
  const choicesKey=JSON.stringify([fleet.map(({id,label})=>({id,label})),L.lang]);
  if(choicesKey!==fleetChoicesKey){fleetChoicesKey=choicesKey;
   selector.replaceChildren(new Option(t('主工作区','Main workspace'),'main'),...fleet.map(x=>new Option(x.label,x.id)));
   selector.value=fleet.some(x=>x.id===active)?active:'main';
  }
  if(active!=='main'&&!fleet.some(x=>x.id===active))switchTo('main');
  const viewKey=JSON.stringify([fleet.map(({id,label,profile,connection,hardware,glove,hand_serial,glove_serial,following,stale})=>({id,label,profile,connection,hardware,glove,hand_serial,glove_serial,following,stale})),L.lang]);
  if(viewKey===fleetViewKey)return;fleetViewKey=viewKey;
  $('wb-device-list')?.replaceChildren(...fleet.map(x=>{
   const row=document.createElement('article');row.className='wb-device-card';
   const name=document.createElement('strong');name.textContent=x.label;
   const detail=document.createElement('p');detail.textContent=`${x.profile} · ${x.following===true?t('手套跟随中','Glove following'):x.hardware===true?t('动作中','Moving'):x.connection==='connected'?t('反馈在线','Feedback online'):x.glove==='receiving'?t('手套预览','Glove preview'):t('未连接','Disconnected')}`;
   const pair=document.createElement('p');pair.textContent=t('机械手：','Hand: ')+(x.hand_serial||'—')+' · '+t('手套：','Glove: ')+(x.glove_serial||'—');
   const actions=document.createElement('div');actions.className='wb-actions';
   const button=document.createElement('button');button.textContent=t('打开','Open');button.onclick=()=>switchTo(x.id);
   const remove=document.createElement('button');remove.textContent=t('移除','Remove');
   remove.disabled=x.hardware===true||x.following===true||x.program?.active===true;
   const rename=document.createElement('button');rename.textContent=t('重命名','Rename');rename.onclick=async()=>{const label=prompt(t('工作区名称','Workspace name'),x.label);if(!label)return;try{await request('fleet_rename',{id:x.id,label});await fleetRefresh()}catch(error){$('wb-device-status').textContent=error.message}};
   remove.onclick=async()=>{try{const result=await request('fleet_remove',{id:x.id});if(!result.removed)throw Error(result.error||'Workspace is active');
    frames.get(x.id)?.remove();frames.delete(x.id);if(active===x.id)switchTo('main');await fleetRefresh();
   }catch(error){$('wb-device-status').textContent=error.message}};
   actions.append(button,rename,remove);row.append(name,detail,pair,actions);return row;
  }));
 }catch(error){if($('wb-device-status'))$('wb-device-status').textContent=error.message}finally{fleetBusy=false}}
 if(devices){$('wb-device-add').onclick=async()=>{const name=$('wb-device-name').value.trim();try{
  const response=await request('fleet_create',{label:name,profile:$('wb-device-profile').value});$('wb-device-name').value='';
  await fleetRefresh();switchTo(response.device.id);$('wb-device-status').textContent='';
 }catch(error){$('wb-device-status').textContent=error.message}};fleetRefresh();setInterval(fleetRefresh,2500);}
 let fleetBeating=false;
 if(!embedded)setInterval(async()=>{if(fleetBeating||!fleet.length)return;fleetBeating=true;
  try{await request('fleet_keepalive')}catch{}finally{fleetBeating=false}
 },250);
 function labels(){
  presetSelect.setAttribute('aria-label',t('映射预设','Mapping preset'));presetName.placeholder=t('预设名称','Preset name');presetName.setAttribute('aria-label',t('预设名称','Preset name'));
  loadPreset.textContent=t('填入预设','Load preset');savePreset.textContent=t('保存为预设','Save preset');deletePreset.textContent=t('删除预设','Delete preset');applyMapping.textContent=t('应用到手套预览','Apply to glove preview');
  fleetStop.textContent=t('停止所有工作区动作','Stop motion in all workspaces');
  selector.setAttribute('aria-label',t('切换设备工作区','Switch device workspace'));
  $('wb-appearance-title').textContent=t('外观','Appearance');
  $('wb-appearance-note').textContent=t('使用系统原生磨砂；不采集桌面、不绘制外部折射。','Native frosted material. No desktop capture or external refraction.');
  $('wb-theme-label').textContent=t('显示主题','Theme');
  for(const [id,zh,en] of [['system','跟随系统','System'],['light','白色','Light'],['dark','黑色','Dark']])$('wb-theme').querySelector(`[value=${id}]`).textContent=t(zh,en);
  $('wb-auto-hand-label').querySelector('span').textContent=t('自动发现并连接单只机械手（只读反馈）','Find and connect a single hand automatically (feedback only)');
  $('wb-auto-glove-label').querySelector('span').textContent=t('自动发现并连接单只手套（仅预览）','Find and connect a single glove automatically (preview only)');
  $('wb-retarget-title').textContent=t('手套 → 机械手映射','Glove → hand mapping');
  $('wb-retarget-note').textContent=t('官方 SDK 将 21 个关键点映射为 20 个关节角，再应用下方幅度、角度偏移和平滑。配置按左右手、代际、手套、机械手与标定用户分别保存。默认保持官方输出。','Official SDK maps 21 landmarks to 20 joint angles, followed by gain, offset and smoothing. Settings are separate for side, generation, glove, hand and SDK user. Defaults preserve SDK output.');
  $('wb-smoothing-label').textContent=t('输出平滑','Output smoothing');$('wb-retarget-default').textContent=t('恢复默认','Reset defaults');$('wb-retarget-save').textContent=t('保存映射','Save mapping');
  $('wb-glove-settings-link').textContent=t('调整手套映射 →','Adjust retargeting →');
  $('wb-program-title').textContent=t('手指舞节目单','Finger dance playlist');
  $('wb-program-note').textContent=t('添加动作，可逐条设置速度和重复，用上下箭头调整顺序。实机播放须由你明确启动。','Add routines, set speed and repeats per entry, and reorder with arrows. Real-hand playback starts only on your command.');
  $('wb-dance-add-button').textContent=t('加入','Add');$('wb-dance-all').textContent=t('全部加入','Add all');
  $('wb-order-label').textContent=t('播放顺序','Order');$('wb-order').querySelector('[value=sequence]').textContent=t('顺序','Sequence');$('wb-order').querySelector('[value=shuffle]').textContent=t('随机','Shuffle');
  $('wb-repeats-label').textContent=t('整单循环（0持续）','Playlist repeats (0 forever)');$('wb-seed-label').textContent=t('随机种子','Shuffle seed');
  $('wb-program-preview').textContent=t('画面预览','Preview');$('wb-program-hardware').textContent=t('真实手播放','Play on hand');
  $('wb-program-pause').textContent=state?.program?.paused?t('继续','Resume'):t('暂停','Pause');$('wb-program-stop').textContent=t('停止','Stop');
  if(devices){$('wb-device-title').textContent=t('独立设备会话','Independent hand sessions');$('wb-device-note').textContent=t('多手可加入同一个工作区，也可分开使用。','Hands can share a workspace or work independently.');$('wb-device-name-label').textContent=t('名称','Name');$('wb-device-profile-label').textContent=t('型号','Model');$('wb-device-add').textContent=t('添加工作区','Add workspace')}
  renderEntries();retargetRender();
 }
 window.addEventListener('console-state',event=>{state=event.detail;
  if(!state?.program?.active)programLease=null;
  if(state?.program?.active)program.open=true;
  $('wb-program-pause').disabled=!state?.program?.active;
  $('wb-program-stop').disabled=!state?.program?.active;
  $('wb-program-status').textContent=state?.program?.active?`${t('播放中','Playing')} · ${state.program.completed} · ${state.program.current?.action||''}`:state?.program?.reason||'';
  $('wb-program-pause').textContent=state?.program?.paused?t('继续','Resume'):t('暂停','Pause');
  updateProgramControls();
 });
 window.addEventListener('wuji-language',()=>{labels();refreshCatalog();fleetRefresh()});
 refreshCatalog();labels();ws.showPage();
})();
