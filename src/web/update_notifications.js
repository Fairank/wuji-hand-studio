/* A single reminder for verified updates. Reading this endpoint never checks or connects devices. */
(()=>{
 'use strict';
 const query=new URLSearchParams(location.search);
 if(query.get('embedded')==='1'||query.get('task_view')==='1'||window.parent!==window)return;
 const host=document.querySelector('#desktop-menu .desktop-menu-section'),menuButton=document.getElementById('desktop-menu-button'),Sheet=window.HandWorkbenchSheet;
 if(!host||!menuButton||!Sheet||document.getElementById('update-notifications'))return;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh;
 const sheet=Sheet.create({id:'update-notifications',title:t('可用更新','Available updates'),triggerLabel:t('可用更新','Available updates')});
 sheet.element.classList.add('un-sheet');const trigger=sheet.trigger;trigger.id='un-open';trigger.classList.add('un-trigger');trigger.hidden=true;
 const label=document.createElement('span'),count=document.createElement('span');label.className='un-label';count.className='un-count';count.setAttribute('aria-hidden','true');
 const badge=document.createElement('span');badge.className='un-menu-badge';badge.hidden=true;badge.setAttribute('aria-hidden','true');menuButton.classList.add('un-menu-anchor');menuButton.append(badge);
 const list=document.createElement('div');list.className='un-list';sheet.body.append(list);
 const live=document.createElement('span');live.id='un-menu-description';live.className='un-live';live.setAttribute('role','status');live.setAttribute('aria-live','polite');document.body.append(live);menuButton.setAttribute('aria-describedby',live.id);
 host.prepend(trigger);trigger.addEventListener('click',()=>document.getElementById('desktop-menu')?.close());
 let entries=[],revision='',online=false,loading=null,lastAttempt=0,destroyed=false,announced='',rowsKey='',epoch=0;
 const kinds=new Set(['workbench','cli','sdk','firmware']);
 const field=(v,max)=>typeof v==='string'&&v.length>0&&v.length<=max&&!/[\u0000-\u001f\u007f]/.test(v);
 function valid(value){return value&&typeof value==='object'&&!Array.isArray(value)&&field(value.revision,160)&&Array.isArray(value.items)&&value.items.length<=40&&value.items.every(row=>row&&typeof row==='object'&&!Array.isArray(row)&&field(row.id,160)&&kinds.has(row.kind)&&(row.version==null||field(row.version,80))&&(row.kind!=='firmware'||typeof row.serial==='string'&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(row.serial))&&(row.model==null||field(row.model,100)))&&new Set(value.items.map(row=>row.id)).size===value.items.length;}
 function title(row){return row.kind==='workbench'?t('工作台','Workbench'):row.kind==='sdk'?t('遥操作组件','Teleoperation components'):row.kind==='cli'?t('官方 CLI','Official CLI'):row.model||t('设备固件','Device firmware');}
 function render(){
  sheet.setLabels({title:t('可用更新','Available updates'),triggerLabel:t('可用更新','Available updates'),closeLabel:t('关闭','Close')});
  label.textContent=t('可用更新','Available updates');count.textContent=String(entries.length);trigger.replaceChildren(label,count);trigger.hidden=!online||!entries.length;badge.hidden=trigger.hidden;badge.textContent=entries.length>9?'9+':String(entries.length);
  trigger.setAttribute('aria-label',t('查看可用更新，共 ','View available updates: ')+entries.length+t(' 项',''));trigger.title=trigger.getAttribute('aria-label');
  const key=JSON.stringify([entries,window.WujiLocale?.lang]);if(key!==rowsKey){rowsKey=key;const rows=entries.map(row=>{const button=document.createElement('button');button.type='button';button.className='un-item';button.dataset.kind=row.kind;
   const copy=document.createElement('span'),name=document.createElement('strong'),identity=document.createElement('span'),action=document.createElement('span');copy.className='un-copy';name.textContent=title(row);copy.append(name);
   if(row.kind==='firmware'){identity.className='un-identity';identity.textContent=row.serial;copy.append(identity);}
   action.className='un-item-action';action.textContent=row.version||t('查看','View');button.append(copy,action);button.onclick=()=>open(row);return button;});list.replaceChildren(...rows);}
  if(!online||!entries.length){if(sheet.element.open)sheet.close();live.textContent='';}
  else if(announced!==revision){announced=revision;live.textContent=t('有可用更新','Updates are available');}
 }
 function open(row){
  sheet.close();
  if(row.kind==='workbench')window.WujiWorkbenchUpdate?.open();
  else if(row.kind==='sdk')window.WujiSDKMaintenance?.open();
  else if(row.kind==='cli')window.WujiOfficialTools?.open();
  else if(window.WujiDeviceMaintenance?.open(row.serial)!==true){window.WujiDeviceInventory?.show();window.WujiDeviceInventory?.select(row.serial);}
 }
 async function refresh(){
  if(destroyed||document.hidden)return;if(loading)return loading;lastAttempt=Date.now();const own=epoch;
  loading=(async()=>{try{const response=await fetch('/api/update-notifications',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!response.ok)throw Error('Update reminders unavailable');const value=await response.json();if(!valid(value))throw Error('Incomplete update reminders');if(own!==epoch||destroyed)return;entries=value.items;revision=value.revision;online=true;}catch(_){if(own===epoch)online=false;}finally{loading=null;render();}})();return loading;
 }
 function visible(){if(!document.hidden&&Date.now()-lastAttempt>=1000)refresh();}
 function stateChanged(){if(Date.now()-lastAttempt>=15000)refresh();}
 function offline(){epoch++;online=false;render();}
 const timer=setInterval(()=>{if(Date.now()-lastAttempt>=15000)refresh();},1000);
 window.addEventListener('wuji-language',render);document.addEventListener('visibilitychange',visible);window.addEventListener('console-state',stateChanged);window.addEventListener('console-offline',offline);
 window.WujiUpdateNotifications=Object.freeze({open(){if(online&&entries.length){document.getElementById('desktop-menu')?.close();sheet.open();}},refresh,snapshot:()=>({online,revision,items:entries.map(row=>({...row}))}),destroy(){destroyed=true;epoch++;clearInterval(timer);window.removeEventListener('wuji-language',render);document.removeEventListener('visibilitychange',visible);window.removeEventListener('console-state',stateChanged);window.removeEventListener('console-offline',offline);sheet.destroy();live.remove();badge.remove();menuButton.removeAttribute('aria-describedby');menuButton.classList.remove('un-menu-anchor');}});
 render();refresh();
})();
