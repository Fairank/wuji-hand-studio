/* Progressive disclosure only. Mode changes never invoke device controls. */
(()=>{
 'use strict';
 const ws=window.WujiWorkspace,$=id=>document.getElementById(id),nav=document.querySelector('.directory');if(!ws||!nav)return;
 const t=(zh,en)=>window.WujiLocale?.lang==='en'?en:zh,key='wuji-workbench-experience';
 let mode='simple',historyMounted=false;try{if(localStorage.getItem(key)==='full')mode='full';}catch{}
 const home=document.createElement('section');home.id='page-home';home.className='workspace-page experience-home';home.hidden=true;
 const heading=document.createElement('header');heading.className='page-heading';const title=document.createElement('h2'),description=document.createElement('p');title.tabIndex=-1;heading.append(title,description);
 const body=document.createElement('div');body.className='page-body';home.append(heading,body);document.querySelector('.page-area').prepend(home);ws.views.home=body;
 const link=document.createElement('a');link.href='#home';link.dataset.page='home';link.innerHTML='<svg viewBox="0 0 26 24" aria-hidden="true"><rect x="3" y="4" width="8" height="7" rx="2"/><rect x="15" y="4" width="8" height="7" rx="2"/><path d="M7 15v4h12v-4M13 19v3"/></svg><span></span>';nav.prepend(link);
 function button(parent,id,action){const b=document.createElement('button');b.type='button';b.id=id;b.onclick=action;parent.append(b);return b;}
 function destination(page){location.hash='#'+page;ws.showPage();}
 const browse=button(heading,'home-browse',()=>destination('library'));browse.className='experience-browse';
 const grid=document.createElement('div');grid.className='experience-device-center';body.append(grid);
 const historyDock=document.createElement('aside');historyDock.id='home-device-history';historyDock.className='experience-history-dock';
 const discoveryDock=document.createElement('section');discoveryDock.id='home-device-discovery';discoveryDock.className='experience-discovery-dock';grid.append(historyDock,discoveryDock);
 function mountShared(page=document.body.dataset.page){
  if(!historyMounted&&window.WujiDeviceHistory?.mount){window.WujiDeviceHistory.mount(historyDock);historyMounted=true;}
  window.WujiDeviceInventory?.mount?.(discoveryDock);
 }
 // One persistent selector. Existing controls and frames stay in their places.
 const switcher=document.createElement('section');switcher.className='experience-switcher';switcher.id='experience-switcher';const switchTitle=document.createElement('span');switchTitle.className='experience-switch-label';
 const segment=document.createElement('div');segment.className='experience-mode';segment.setAttribute('role','radiogroup');const fill=document.createElement('span');fill.className='experience-mode-fill';fill.setAttribute('aria-hidden','true');segment.append(fill);
 const choices={};for(const value of ['simple','full']){const b=document.createElement('button');b.type='button';b.id='experience-'+value;b.setAttribute('role','radio');b.onclick=()=>setMode(value);choices[value]=b;segment.append(b);}
 switcher.append(switchTitle,segment);nav.append(switcher);
 segment.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?'simple':event.key==='End'?'full':mode==='simple'?'full':'simple';setMode(next);choices[next].focus();});
 const settings=document.getElementById('settings-appearance');if(settings){const row=document.createElement('div');row.className='wb-row settings-row experience-settings';const label=document.createElement('span');label.id='experience-setting-label';const control=button(row,'experience-setting-toggle',()=>setMode(mode==='simple'?'full':'simple'));row.prepend(label);settings.prepend(row);}
 document.querySelector('#settings-tools>.settings-research')?.classList.add('experience-advanced');
 document.querySelectorAll('#settings-tools>.wb-card').forEach(c=>{if(c.id!=='workbench-update'&&c!==document.querySelector('#settings-tools>.wb-card:last-child'))c.classList.add('experience-advanced');});
 function setMode(value){if(!['simple','full'].includes(value))return;mode=value;document.documentElement.dataset.experience=mode;try{localStorage.setItem(key,mode);}catch{}labels();window.dispatchEvent(new CustomEvent('experience-mode',{detail:mode}));}
 function labels(){title.textContent=link.lastElementChild.textContent=t('设备','Devices');description.textContent=t('查看当前设备，选择后接入。','View available devices and choose one to connect.');browse.textContent=t('选择动作','Choose a motion');historyDock.setAttribute('aria-label',t('最近连接','Recent connections'));discoveryDock.setAttribute('aria-label',t('当前设备','Current devices'));switchTitle.textContent=t('工作台模式','Workbench mode');segment.setAttribute('aria-label',t('工作台模式','Workbench mode'));choices.simple.textContent=t('简单','Simple');choices.full.textContent=t('完整','Full');for(const [value,b]of Object.entries(choices)){b.setAttribute('aria-checked',String(mode===value));b.tabIndex=mode===value?0:-1;}
  const sl=$('experience-setting-label'),sb=$('experience-setting-toggle');if(sl)sl.textContent=t('工作台模式','Workbench mode');if(sb){sb.textContent=mode==='simple'?t('简单模式 · 切换到完整','Simple · Switch to full'):t('完整模式 · 切换到简单','Full · Switch to simple');sb.setAttribute('aria-label',sb.textContent);}
  const toolsTab=$('settings-tab-tools');if(toolsTab)toolsTab.textContent=t('关于与工具','About & tools');
  if(document.body.dataset.page==='home')document.title=t('设备','Devices')+' · Hand Workbench';
 }
 window.WujiExperience={setMode,mode:()=>mode,home:()=>destination('home')};
 window.addEventListener('wuji-language',labels);window.addEventListener('workspace-page',event=>{mountShared(event.detail);if(event.detail==='home')labels();});
 setMode(mode);if(!location.hash||location.hash==='#home'){location.hash='#home';ws.showPage();}mountShared();
})();
