/* Local connection facts, separate from present discovery. No device commands. */
(()=>{
 'use strict';
 const key='wuji-device-history-v1',t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const root=document.createElement('details');root.className='dh-root';root.setAttribute('aria-labelledby','dh-title');
 const title=document.createElement('summary');title.id='dh-title';const note=document.createElement('p');note.className='dh-note';
 const list=document.createElement('div');list.className='dh-list';const empty=document.createElement('p');empty.className='dh-empty';root.append(title,note,list,empty);
 let inventory=null,consoleState=null,records=[],previous=new Set(),initialized=false,storageFailed=false;const cards=new Map();
 const serial=value=>typeof value==='string'&&value.length>0&&value.length<=128&&!/[\x00-\x1f]/.test(value)?value:'';
 const id=row=>row.kind+':'+row.serial;
 function clean(row){
  if(!row||!['hand','glove'].includes(row.kind)||!serial(row.serial)||!Number.isFinite(row.recorded_at)||row.recorded_at<=0)return null;
  return {kind:row.kind,serial:row.serial,side:['left','right'].includes(row.side)?row.side:null,generation:['hand1','hand2','glove1'].includes(row.generation)?row.generation:null,recorded_at:row.recorded_at};
 }
 try{const data=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(data)){const unique=new Map();for(const row of data.map(clean).filter(Boolean).sort((a,b)=>b.recorded_at-a.recorded_at))if(!unique.has(id(row)))unique.set(id(row),row);records=[...unique.values()].slice(0,12);}}catch{storageFailed=true;}
 const setText=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
 function detected(row){
  const found=inventory?.devices?.find(d=>d.kind===row.kind&&(d.sn||d.serial)===row.serial);if(!found||found.stale===true||found.selectable!==true||inventory?.scanning||inventory?.error)return null;
  const time=typeof found.last_seen==='number'?(found.last_seen<1e12?found.last_seen*1000:found.last_seen):Date.parse(found.last_seen||'');
  const age=Date.now()-time,ttl=Number(inventory.ttl_s)||30;return Number.isFinite(age)&&age>=0&&age<ttl*1000?found:null;
 }
 function name(row){return (row.kind==='hand'?'Wuji Hand':'Wuji Glove')+(row.generation==='hand2'?' 2':row.generation==='hand1'||row.generation==='glove1'?' 1':'')+(row.side?' · '+(row.side==='left'?t('左','Left'):t('右','Right')):'');}
 function render(){
  setText(title,t('最近连接','Recent connections'));setText(note,storageFailed?t('本机记录暂时无法保存','Local history could not be saved'):t('仅保存在此工作台','Stored on this workbench'));
  const retained=new Set();for(const [index,row]of records.entries()){
   const identity=id(row);retained.add(identity);let card=cards.get(identity);
   if(!card){const button=document.createElement('button');button.type='button';button.className='dh-device';const heading=document.createElement('strong'),sn=document.createElement('span'),meta=document.createElement('small');button.append(heading,sn,meta);button.onclick=()=>{const current=records.find(item=>id(item)===identity);if(current&&detected(current))window.WujiDeviceInventory?.select(current.serial);};card={button,heading,sn,meta};cards.set(identity,card);}
   const present=!!detected(row),s=consoleState||{},v=s.official_viz||{},g=s.glove||{},viewerSN=serial((v.reported_device||v.device)?.sn||(v.reported_device||v.device)?.serial),sessionRetained=viewerSN===row.serial&&(v.active===true||v.reserved===true)||s.device_id===row.serial&&s.connection!=='disconnected'||g.glove_serial===row.serial&&(g.busy===true||g.connection==='receiving');
   setText(card.heading,name(row));setText(card.sn,row.serial);setText(card.meta,present?t('本次已检测到','Detected now'):sessionRetained?t('会话保留在设备任务中','Session retained in Device tasks'):t('等待再次发现','Awaiting rediscovery'));
   card.button.disabled=!present;card.button.title=t('本机记录时间：','Recorded locally: ')+new Date(row.recorded_at).toLocaleString(window.WujiLocale?.lang==='en'?'en-US':'zh-CN');card.button.dataset.present=String(present);if(list.children[index]!==card.button)list.insertBefore(card.button,list.children[index]||null);
  }
  for(const [identity,card]of cards)if(!retained.has(identity)){card.button.remove();cards.delete(identity);}
  empty.hidden=records.length>0;setText(empty,t('成功接入设备后，记录会显示在这里。','Successfully connected devices appear here.'));
 }
 function observe(state){
  consoleState=state;
  const facts=[],profile=state?.device_profile,identity=state?.identified_device;
  if(state?.connection==='connected'&&identity?.source==='sdk_identity'&&identity.serial===state.device_id&&identity.profile===profile?.id&&serial(state.device_id))facts.push({kind:'hand',serial:state.device_id,side:profile.side,generation:profile.generation});
  const glove=state?.glove;
  if(glove?.connection==='receiving'&&glove.stream?.fresh===true&&serial(glove.glove_serial)){
   const observed=inventory?.devices?.find(d=>d.kind==='glove'&&(d.sn||d.serial)===glove.glove_serial);
   // The target hand profile is NOT the glove's measured identity. Use only a
   // reported original observation for side; otherwise leave it unspecified.
   const observation=observed?.observations?.find(o=>o.source==='official_cli'&&o.side_source==='device'&&['left','right'].includes(o.side));
   const actual=glove.identified_glove,verified=actual?.source==='sdk_identity'&&actual.serial===glove.glove_serial;
   facts.push({kind:'glove',serial:glove.glove_serial,side:verified?actual.side:observation?.side,generation:null});
  }
  // Only an exact original startup report records a Viewer connection. A scan
  // advertisement or retained process alone never creates a connection fact.
  const viewer=state?.official_viz,reported=viewer?.reported_device,sn=serial(reported?.sn||reported?.serial),selected=serial(viewer?.device?.sn||viewer?.device?.serial);
  if(viewer?.active===true&&viewer.phase==='running'&&sn&&sn===selected&&/^[a-f0-9]{32}$/.test(viewer.session_id||'')&&Number.isFinite(viewer.started_at)&&viewer.started_at>0){
   // Official startup may report only SN and handedness. The selected discovery
   // identity supplies type only after its exact SN matches that startup report.
   const kindText=[reported,viewer.device].flatMap(row=>[row.device_type,row.type,row.model,row.device_name]).filter(value=>typeof value==='string').join(' ').toLowerCase(),isHand=kindText.includes('hand'),isGlove=kindText.includes('glove'),side=String(reported.handedness||reported.side||'').toLowerCase().replace(/^handedness\./,'');
   if(isHand!==isGlove)facts.push({kind:isGlove?'glove':'hand',serial:sn,side:['left','right'].includes(side)?side:null,generation:isHand?(sn.startsWith('WH2')||/hand\s*2/.test(kindText)?'hand2':sn.startsWith('WH1')?'hand1':null):sn.startsWith('WG1')?'glove1':null});
  }
  const now=Date.now();let changed=false;for(const fact of facts){const identity=id(fact),exists=records.some(row=>id(row)===identity);if(!previous.has(identity)&&(!exists||initialized)){records=records.filter(row=>id(row)!==identity);records.unshift(clean({...fact,recorded_at:now}));changed=true;}}
  previous=new Set(facts.map(id));initialized=true;
  if(changed){records=records.filter(Boolean).sort((a,b)=>b.recorded_at-a.recorded_at).slice(0,12);try{localStorage.setItem(key,JSON.stringify(records));storageFailed=false;}catch{storageFailed=true;}}render();
 }
 window.addEventListener('console-state',event=>observe(event.detail));
 window.addEventListener('device-inventory-update',event=>{inventory=event.detail;render();});
 window.addEventListener('console-offline',()=>{inventory=null;render();});window.addEventListener('wuji-language',render);
 window.WujiDeviceHistory={mount(target){if(target&&root.parentElement!==target)target.append(root);render();},snapshot:()=>JSON.parse(JSON.stringify(records))};
 const initial=window.WujiConnection?.state();if(initial)observe(initial);render();
})();
