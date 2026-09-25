/* Playlist editing is separate from live playback. The existing backend owns all commands. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),L=window.WujiLocale,t=(zh,en)=>L.lang==='en'?en:zh;
 const speeds=[.1,.25,.5,.75,1,1.25,1.5,1.75,2,2.5,3,4];
 let entries=[],catalog=[],state=null,lease=null,pending=false,starting=false,leaseSeen=false,error='',loadError=false,sheet=null,mode='preview';
 const program=document.createElement('section');program.id='wb-program';program.className='pe-editor';
 program.innerHTML=`<div class="pe-queue">
  <div class="pe-section-heading"><h3 id="pe-queue-title"></h3><span id="wb-program-count"></span></div>
  <div class="pe-add"><label class="pe-sr" for="wb-dance-add" id="pe-add-label"></label><select id="wb-dance-add"></select><button type="button" id="wb-dance-add-button"></button></div>
  <div class="pe-add-meta"><span id="pe-autosave"></span><button type="button" id="wb-dance-all"></button></div>
  <p id="pe-catalog-error" role="status" hidden></p><button type="button" id="pe-retry" hidden></button>
  <div id="wb-entries"></div><p class="pe-sr" id="pe-announcement" aria-live="polite"></p>
 </div><aside class="pe-settings">
  <h3 id="pe-settings-title"></h3>
  <div class="pe-field"><label for="wb-order" id="wb-order-label"></label><select id="wb-order"><option value="sequence"></option><option value="shuffle"></option></select></div>
  <div class="pe-field"><label for="wb-repeats" id="wb-repeats-label"></label><input id="wb-repeats" required type="number" min="1" max="100" step="1" value="1"></div>
  <label class="pe-check"><input id="pe-forever" type="checkbox"><span id="pe-forever-label"></span></label>
  <details id="pe-random" hidden><summary id="pe-random-title"></summary><div class="pe-field"><label for="wb-seed" id="wb-seed-label"></label><input id="wb-seed" required type="number" min="0" max="4294967295" step="1" value="0"><p id="pe-seed-note"></p></div></details>
  <div class="pe-destination"><h3 id="pe-destination-title"></h3><div class="pe-mode" role="group" aria-labelledby="pe-destination-title"><button type="button" id="pe-mode-preview" aria-pressed="true"></button><button type="button" id="pe-mode-hardware" aria-pressed="false"></button></div>
   <p id="pe-destination-note"></p>
   <div id="pe-hardware-options" hidden><div class="pe-field"><label for="pe-amplitude" id="pe-amplitude-label"></label><select id="pe-amplitude"></select></div><label class="pe-check"><input id="pe-clear" type="checkbox"><span id="pe-clear-label"></span></label></div>
  </div>
 </aside>`;
 $('page-library').querySelector('.page-body').prepend(program);
 const footer=document.createElement('footer');footer.className='pe-footer';
 footer.innerHTML=`<div class="pe-footer-copy"><strong id="pe-summary"></strong><p id="pe-readiness"></p><p id="wb-program-status" role="status"></p></div><div class="pe-transport"><button type="button" id="wb-program-preview" class="primary"></button><button type="button" id="wb-program-hardware" class="primary" hidden></button><button type="button" id="wb-program-pause" hidden></button><button type="button" id="wb-program-stop" class="pe-stop" hidden></button></div>`;
 program.after(footer);
 const nameOf=id=>{const a=catalog.find(x=>x.id===id);return a?(L.lang==='en'?a.en:a.zh):id;};
 const announce=s=>{$('pe-announcement').textContent=s;};
 function persist(){try{localStorage.setItem('wuji-dance-program',JSON.stringify(entries));}catch{error=t('无法保存节目单，请检查应用存储。','Could not save the playlist. Check app storage.');}}
 try{const saved=JSON.parse(localStorage.getItem('wuji-dance-program')||'[]');if(Array.isArray(saved))entries=saved.filter(x=>x&&typeof x.action==='string'&&speeds.includes(x.speed)&&Number.isInteger(x.cycles)&&x.cycles>=1&&x.cycles<=20).slice(0,64);}catch{}
 try{const v=JSON.parse(localStorage.getItem('wuji-program-options')||'{}');
  if(['sequence','shuffle'].includes(v.order))$('wb-order').value=v.order;
  if(Number.isInteger(v.repeats)&&v.repeats>=0&&v.repeats<=100){$('pe-forever').checked=v.repeats===0;$('wb-repeats').value=String(v.repeats||1);}
  if(Number.isInteger(v.seed)&&v.seed>=0&&v.seed<=4294967295)$('wb-seed').value=String(v.seed);
 }catch{}
 const locked=()=>pending||starting||!!state?.program?.active;
 const rows=window.PlaylistRows.mount($('wb-entries'),{speeds,
  onEdit(index,field,value){if(locked()||!entries[index])return;entries[index][field]=value;persist();render();},
  onMove(index,delta){if(locked()||!entries[index+delta])return;[entries[index],entries[index+delta]]=[entries[index+delta],entries[index]];persist();render();announce(t(`已移至第 ${index+delta+1} 项`,`Moved to position ${index+delta+1}`));},
  onRemove(index){if(locked())return;entries.splice(index,1);persist();render();announce(t('已移除动作','Routine removed'));}
 });
 function render(){rows.render(entries,new Map(catalog.map(a=>[a.id,nameOf(a.id)])),L.lang);update();}
 function plan(){return {entries:entries.map(x=>({...x})),order:$('wb-order').value,repeats:$('pe-forever').checked?0:Number($('wb-repeats').value),seed:$('wb-order').value==='shuffle'?Number($('wb-seed').value):0};}
  function optionsChanged(){
  update();
  const p=plan(),seed=Number($('wb-seed').value);
  if(($('pe-forever').checked||$('wb-repeats').value.trim()&&Number.isInteger(p.repeats)&&p.repeats>=1&&p.repeats<=100)&&$('wb-seed').value.trim()&&Number.isInteger(seed)&&seed>=0&&seed<=4294967295){try{localStorage.setItem('wuji-program-options',JSON.stringify({order:p.order,repeats:p.repeats,seed}));}catch{}}
  update();
 }
 for(const id of ['wb-order','wb-repeats','wb-seed','pe-forever'])$(id).addEventListener('change',optionsChanged);
 const amplitude=$('trial-amplitude');
 $('pe-amplitude').replaceChildren(...[...amplitude.options].map(x=>new Option(x.textContent,x.value)));
 $('pe-amplitude').value=amplitude.value;
 $('pe-amplitude').onchange=()=>{amplitude.value=$('pe-amplitude').value;amplitude.dispatchEvent(new Event('change',{bubbles:true}));};
 amplitude.addEventListener('change',()=>{$('pe-amplitude').value=amplitude.value;});
 $('pe-clear').onchange=()=>{$('trial-clear').checked=$('pe-clear').checked;$('trial-clear').dispatchEvent(new Event('change',{bubbles:true}));update();};
 $('trial-clear').addEventListener('change',()=>{$('pe-clear').checked=$('trial-clear').checked;update();});
 for(const m of ['preview','hardware'])$('pe-mode-'+m).onclick=()=>{if(locked())return;mode=m;error='';update();};
 function blocker(){
  if(loadError)return t('动作目录暂不可用，请重试。','The routine catalog is unavailable. Retry loading it.');
  if(!entries.length)return t('先添加一个动作，即可开始预览。','Add a routine to start a preview.');
  if(!state)return t('正在读取工作台状态。','Reading workbench status.');
  if(state.hardware?.active!==false||state.glove?.busy)return t('请先结束当前实机动作或手套会话。','Finish the current hand action or glove session first.');
  if(mode==='preview')return '';
  if(state?.connection!=='connected')return t('机械手未连接。请先使用右上角的「连接」。','Hand disconnected. Use Connect in the top bar first.');
  if(state?.stale)return t('正在等待最新设备反馈。','Waiting for fresh device feedback.');
  if(state?.device_profile?.generation!=='hand2')return t('当前节目单实机播放支持二代手。','Playlist playback on hardware supports Hand 2.');
  if(!$('pe-clear').checked)return t('确认工作区已清空后，即可开始。','Confirm that the workspace is clear to start.');
  return '';
 }
 function update(){
  const active=!!state?.program?.active,busy=locked();
  if(active&&['preview','hardware'].includes(state.program.mode))mode=state.program.mode;
  const reason=blocker();
  $('wb-program-count').textContent=t(`${entries.length} 个动作`,`${entries.length} routines`);
  rows.setDisabled(busy);
  for(const el of program.querySelectorAll('.pe-settings input,.pe-settings select,.pe-mode button'))el.disabled=busy;
  $('wb-repeats').disabled=busy||$('pe-forever').checked;
  $('pe-random').hidden=$('wb-order').value!=='shuffle';
  $('wb-seed').disabled=busy||$('wb-order').value!=='shuffle';
  $('wb-dance-add').disabled=busy||!catalog.length;
  $('wb-dance-add-button').disabled=busy||!catalog.length||entries.length>=64;
  $('wb-dance-all').disabled=busy||!catalog.length||entries.length>=64||catalog.every(a=>entries.some(e=>e.action===a.id));
  for(const m of ['preview','hardware']){$('pe-mode-'+m).setAttribute('aria-pressed',String(mode===m));$('wb-program-'+m).hidden=active||mode!==m;$('wb-program-'+m).disabled=busy||!!reason;}
  $('pe-hardware-options').hidden=mode!=='hardware';
  $('pe-destination-note').textContent=mode==='preview'?t('仅播放屏幕里的手部模型。','Plays the hand model on screen.'):t('使用当前连接的机械手与参数。','Uses the connected hand and its current parameters.');
  $('wb-program-pause').hidden=$('wb-program-stop').hidden=!active;
  $('wb-program-pause').disabled=pending||!active;
  // Stop remains available throughout a pending pause/resume request.
  $('wb-program-stop').disabled=!active;
  $('wb-program-pause').textContent=state?.program?.paused?t('继续播放','Resume'):t('暂停','Pause');
  const order=$('wb-order').value==='shuffle'?t('随机顺序','Shuffle'):t('按列表顺序','In list order');
  const repeats=$('pe-forever').checked?t('持续循环','Continuous loop'):t(`整单 ${$('wb-repeats').value} 遍`,`${$('wb-repeats').value} ${Number($('wb-repeats').value)===1?'pass':'passes'}`);
  $('pe-summary').textContent=active?`${state.program.paused?t('已暂停','Paused'):t('播放中','Playing')} · ${nameOf(state.program.current?.action||'')}`:`${entries.length} ${t('个动作','routines')} · ${order} · ${repeats}`;
  $('pe-readiness').textContent=active?t('停止后可编辑节目单；关闭此窗口可查看展示。','Stop to edit the playlist. Close this window to see the hand.'):starting?t('已发送启动请求，等待播放状态。','Start requested. Waiting for playback status.'):reason||t('启动后将回到展示画面。','Starting returns you to the hand view.');
  $('wb-program-status').textContent=error||(!active?state?.program?.reason||'':'');
  $('wb-program-status').hidden=!$('wb-program-status').textContent;
  if(sheet)sheet.trigger.textContent=t(entries.length?`节目单 · ${entries.length}`:'节目单',entries.length?`Playlist · ${entries.length}`:'Playlist');
 }
 function add(all){
  if(locked())return;
  const ids=all?catalog.filter(a=>!entries.some(e=>e.action===a.id)).map(a=>a.id):[$('wb-dance-add').value];
  let count=0;for(const id of ids){if(entries.length>=64)break;if(catalog.some(a=>a.id===id)){entries.push({action:id,speed:1,cycles:1});count++;}}
  persist();render();announce(t(`已添加 ${count} 个动作`,`Added ${count} routines`));
 }
 $('wb-dance-add-button').onclick=()=>add(false);$('wb-dance-all').onclick=()=>add(true);
 async function refreshCatalog(){
  try{const r=await fetch('/api/catalog');if(!r.ok)throw Error();const c=await r.json();catalog=(c.actions||[]).filter(a=>a.group==='dance');loadError=false;
   const choice=$('wb-dance-add').value;$('wb-dance-add').replaceChildren(...catalog.map(a=>new Option(nameOf(a.id),a.id)));if(catalog.some(a=>a.id===choice))$('wb-dance-add').value=choice;
   // Never replace a saved queue on a transient empty response.
   if(catalog.length)entries=entries.filter(e=>catalog.some(a=>a.id===e.action));render();
  }catch{loadError=true;update();}
  $('pe-catalog-error').hidden=$('pe-retry').hidden=!loadError;
 }
 $('pe-retry').onclick=refreshCatalog;
 async function request(name,payload={}){
  if(!state?.csrf)throw Error(t('本机服务尚未就绪，请稍后重试。','Local service is not ready. Try again shortly.'));
  const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':state.csrf},body:JSON.stringify({name,...payload})});
  const v=await r.json();if(!r.ok||!v.ok)throw Error(v.error||'Request failed');return v;
 }
 async function command(name,payload={}){
  pending=true;error='';update();
  try{const v=await request(name,payload);if(v.program_lease){lease=v.program_lease;leaseSeen=false;}if(name==='program_stop')lease=null;
   if(name==='program_start'){starting=!state?.program?.active;$('mode-'+payload.mode)?.click();sheet?.close();}
  }catch(e){error=e.message;}finally{pending=false;update();}
 }
 function start(target){
  if(locked()||blocker())return;
  for(const id of ['wb-repeats','wb-seed'])if(!$(id).disabled&&!$(id).reportValidity())return;
  command('program_start',{mode:target,plan:plan(),amplitude:target==='hardware'?Number(amplitude.value||1):1,workspace_clear:target==='hardware'&&!!$('trial-clear').checked});
 }
 $('wb-program-preview').onclick=()=>start('preview');$('wb-program-hardware').onclick=()=>start('hardware');
 $('wb-program-pause').onclick=()=>command(state?.program?.paused?'program_resume':'program_pause');
 $('wb-program-stop').onclick=()=>command('program_stop');
 let beating=false;setInterval(async()=>{if(!lease||beating)return;beating=true;try{await request('program_keepalive',{lease});}catch(e){lease=null;starting=false;error=e.message;update();}finally{beating=false;}},250);
 window.addEventListener('pagehide',()=>{if(lease)request('program_stop').catch(()=>{});});
 window.addEventListener('console-state',e=>{state=e.detail;if(state?.program?.active){starting=false;leaseSeen=true;}else if(leaseSeen&&!pending){lease=null;starting=false;}update();});
 function labels(){
  const text={
   'pe-queue-title':['动作列表','Routines'],'pe-add-label':['添加动作','Add a routine'],'wb-dance-add-button':['添加','Add'],'wb-dance-all':['补齐全部动作','Add missing routines'],'pe-autosave':['编辑自动保存','Edits saved automatically'],
   'pe-settings-title':['播放设置','Playback settings'],'wb-order-label':['播放顺序','Play order'],'wb-repeats-label':['整单播放遍数','Playlist passes'],'pe-forever-label':['持续循环，直到停止','Loop until stopped'],
   'pe-random-title':['随机顺序高级设置','Advanced shuffle settings'],'wb-seed-label':['随机种子','Shuffle seed'],'pe-seed-note':['相同种子会得到相同的随机顺序。','The same seed produces the same shuffle order.'],
   'pe-destination-title':['播放到','Play on'],'pe-mode-preview':['屏幕预览','Screen preview'],'pe-mode-hardware':['真实手','Real hand'],'pe-amplitude-label':['实机动作幅度','Hand amplitude'],
   'pe-clear-label':['底座固定，周围无人无物','Base secured; workspace clear'],
   'wb-program-preview':['开始预览','Start preview'],'wb-program-hardware':['开始实机播放','Play on hand'],'wb-program-stop':['停止节目单','Stop playlist'],
   'pe-catalog-error':['动作目录加载失败，已保留原节目单。','Could not load routines. Your saved playlist is preserved.'],'pe-retry':['重新加载','Retry']
  };
  for(const [id,pair]of Object.entries(text))$(id).textContent=t(...pair);
  $('wb-order').options[0].textContent=t('按列表顺序','In list order');$('wb-order').options[1].textContent=t('随机顺序','Shuffle');
  $('pe-amplitude').replaceChildren(...[...amplitude.options].map(x=>new Option(x.textContent,x.value)));$('pe-amplitude').value=amplitude.value;
  render();
 }
 window.HandWorkbenchPlaylist=Object.freeze({attach(value){sheet=value;sheet.element.classList.add('pe-sheet');sheet.element.append(footer);update();},open(){sheet?.open();},close(){sheet?.close();},nameOf});
 window.addEventListener('wuji-language',()=>{labels();refreshCatalog();});
 labels();refreshCatalog();
})();
